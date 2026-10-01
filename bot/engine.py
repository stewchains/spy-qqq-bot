"""Main trading loop: news -> exits -> entries, once a minute during market hours."""
import csv
import json
import logging
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from .broker import Broker
from .condor import (choose_expiries, condor_contracts, condor_exit_reason, condor_mark, fill_missing_deltas,
                     pick_condor, range_filter)
from .events import EventCalendar
from .exits import option_exit_reason, session_time, _t
from .news import NewsMonitor
from .options_selector import pick_contract
from .risk import RiskManager
from .indicators import add_all
from .strategy import daily_with_live, evaluate

ET = ZoneInfo("America/New_York")
log = logging.getLogger("bot")
ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / "state.json"
JOURNAL = ROOT / "trades.csv"
JOURNAL_COLS = ["opened", "closed", "kind", "symbol", "underlying", "direction", "qty", "entry", "exit",
                "pnl", "pnl_pct", "why_in", "why_out"]


def use_bot_files(name: str | None):
    """Each bot (e.g. the 0DTE bot and the 30-45 day bot) keeps its own state and trade journal."""
    global STATE, JOURNAL
    suffix = f"_{name}" if name else ""
    STATE = ROOT / f"state{suffix}.json"
    JOURNAL = ROOT / f"trades{suffix}.csv"


class TradingBot:
    def __init__(self, cfg: dict, key: str, secret: str):
        self.cfg = cfg
        self.broker = Broker(key, secret, cfg)
        self.news = NewsMonitor(cfg, key, secret)
        self.events = EventCalendar(str(ROOT / cfg["events"]["file"]), cfg["events"]["minutes_before"],
                                    cfg["events"]["minutes_after"])
        self.risk = RiskManager(cfg)
        self.state = json.loads(STATE.read_text()) if STATE.exists() else {}
        self._daily_cache: dict = {}
        self._last_signal_log: dict = {}
        self._last_exit: dict = {}
        self.close_et = None  # today's market close (earlier on half days)
        self._condors_today: dict = {}
        self.condor_mode = cfg["strategy"].get("type", "directional") == "iron_condor"

    # ------------------------------------------------------------------ persistence
    def save(self):
        STATE.write_text(json.dumps(self.state, indent=2, default=str))

    def journal(self, pos: dict, sym: str, exit_px: float, why_out: str, now: datetime):
        if pos["kind"] == "condor":
            return self._journal_condor(pos, sym, exit_px, why_out, now)
        mult = 100 if pos["kind"] == "option" else 1
        sign = 1 if (pos["kind"] == "option" or pos["direction"] == "long") else -1
        pnl = (exit_px - pos["entry_price"]) * pos["qty"] * mult * sign if exit_px else 0.0
        new = not JOURNAL.exists()
        with JOURNAL.open("a", newline="") as f:
            w = csv.writer(f)
            if new:
                w.writerow(JOURNAL_COLS)
            w.writerow([pos["entry_time"], now.isoformat(timespec="seconds"), pos["kind"], sym, pos["underlying"],
                        pos["direction"], pos["qty"], pos["entry_price"], exit_px, round(pnl, 2),
                        round(sign * (exit_px / pos["entry_price"] - 1), 4) if exit_px else "", pos.get("why", ""), why_out])
        log.info("CLOSED %s %s x%s  entry %.2f exit %.2f  P&L $%.2f  (%s)", pos["kind"], sym, pos["qty"],
                 pos["entry_price"], exit_px, pnl, why_out)
        self.risk.record_close(pnl, now)

    def _journal_condor(self, pos, sym, debit, why_out, now):
        """Condor P&L = (credit received - debit paid to close) x 100 x qty."""
        pnl = (pos["credit"] - debit) * 100 * pos["qty"]
        new = not JOURNAL.exists()
        with JOURNAL.open("a", newline="") as f:
            w = csv.writer(f)
            if new:
                w.writerow(JOURNAL_COLS)
            w.writerow([pos["entry_time"], now.isoformat(timespec="seconds"), "condor", sym, pos["underlying"],
                        "neutral", pos["qty"], pos["credit"], debit, round(pnl, 2),
                        round((pos["credit"] - debit) / pos["max_loss"], 4) if pos.get("max_loss") else "",
                        pos.get("why", ""), why_out])
        log.info("CLOSED condor %s x%s  credit %.2f  closed for %.2f  P&L $%.2f  (%s)", sym, pos["qty"],
                 pos["credit"], debit, pnl, why_out)
        self.risk.record_close(pnl, now)

    # ------------------------------------------------------------------ main loop
    def run(self):
        for attempt in range(1, 21):  # ride out brief network/Alpaca hiccups at startup (up to ~10 minutes)
            try:
                acct = self.broker.account()
                break
            except Exception as e:  # noqa: BLE001
                log.warning("Can't reach Alpaca yet (attempt %d/20): %s — retrying in 30s", attempt, str(e)[:120])
                time.sleep(30)
        else:
            raise SystemExit("Couldn't connect to Alpaca after 10 minutes. Check your internet and API keys.")
        log.info("Connected to Alpaca %s account. Equity $%.2f, options level %s",
                 "PAPER" if self.broker.paper else "*** LIVE ***", acct["equity"], acct["options_level"])
        if acct["blocked"]:
            raise SystemExit("Account is blocked from trading. Check your Alpaca dashboard.")
        try:
            self.reconcile()
        except Exception:  # noqa: BLE001 — will reconcile again on the next cycle
            log.exception("startup reconcile failed; will retry in the main loop")
        traded_today = False
        while True:
            try:
                clock = self.broker.clock()
                if not clock.is_open:
                    if traded_today and self.cfg["schedule"].get("exit_after_close", True):
                        self.reconcile()
                        log.info("Market closed for the day. Bot exiting (it starts fresh next morning).")
                        return
                    self.sleep_until_open(clock)
                    continue
                traded_today = True
                self.close_et = clock.next_close.astimezone(ET)
                self.cycle()
            except KeyboardInterrupt:
                raise
            except Exception:  # noqa: BLE001 — keep the bot alive, log the error, try again next minute
                log.exception("cycle error")
            time.sleep(self.cfg["schedule"]["loop_seconds"])

    def sleep_until_open(self, clock):
        if self.state:
            self.reconcile()
        wait = (clock.next_open - clock.timestamp).total_seconds()
        log.info("Market closed. Next open %s ET (%.1f h). Sleeping.",
                 clock.next_open.astimezone(ET).strftime("%a %b %d %H:%M"), wait / 3600)
        time.sleep(max(60, min(wait - 120, 1800)))

    def reconcile(self):
        """Drop tracked positions that no longer exist at the broker (closed by bracket, expired, or manually)."""
        live = self.broker.positions()
        now = datetime.now(ET)
        for sym in list(self.state):
            pos = self.state[sym]
            if pos["kind"] == "condor":
                held = [l["symbol"] for l in pos["legs"] if l["symbol"] in live]
                if not held:
                    self.state.pop(sym)
                    self.journal(pos, sym, 0.0, "closed at broker (expired worthless, liquidated, or manual) — "
                                 "check Alpaca for the exact price", now)
                elif len(held) < 4:
                    self._note(f"partial-{sym}", f"WARNING {sym}: only {len(held)} of 4 legs still open at Alpaca")
                continue
            if sym not in live:
                pos = self.state.pop(sym)
                px = self.broker.last_exit_price(sym)
                self.journal(pos, sym, px, "closed at broker (bracket stop/target, expiry, or manual)", now)
        tracked = set(self.state) | {l["symbol"] for p in self.state.values() if p["kind"] == "condor" for l in p["legs"]}
        others = [s for s in live if s not in tracked]
        if others:
            self._note("others", "Ignoring positions the bot didn't open: " + ", ".join(others))
        self.save()

    def cycle(self):
        now = datetime.now(ET)
        acct = self.broker.account()
        self.risk.new_day(now.date(), acct["last_equity"] or acct["equity"])  # yesterday's close, survives restarts
        self.news.poll(now.astimezone(timezone.utc))
        self.reconcile()
        self.manage_exits(now)

        sch = self.cfg["schedule"]
        last_entry = _t(sch["no_entries_after"])
        if self.close_et is not None:  # never open new trades in the last hour (matters on half days)
            last_entry = min(last_entry, (self.close_et - timedelta(hours=1)).time())
        if not (_t(sch["no_entries_before"]) <= now.time() < last_entry):
            return
        ok, why = self.risk.can_open(now, acct["equity"], len(self.state))
        if not ok:
            return self._note("gate", why)
        ev = self.events.blocking(now) if self.cfg["events"].get("enabled", True) else None
        if ev:
            return self._note("event", f"no entries: {ev} window")
        if self.news.paused(now.astimezone(timezone.utc)):
            return self._note("shock", f"no entries: news shock — {self.news.shock_headline[:100]}")

        for sym in self.cfg["symbols"]:
            if any(p["underlying"] == sym for p in self.state.values()):
                continue
            last = self._last_exit.get(sym)
            if last and (now - last).total_seconds() < 600:  # no instant re-entry after an exit
                continue
            if self.condor_mode:
                self.try_condor(sym, now, acct)
            else:
                self.try_entry(sym, now, acct)
            ok, _ = self.risk.can_open(now, self.broker.account()["equity"], len(self.state))
            if not ok:
                break

    def _note(self, key, msg):
        if self._last_signal_log.get(key) != msg:
            log.info(msg)
            self._last_signal_log[key] = msg

    # ------------------------------------------------------------------ entries
    def try_entry(self, sym: str, now: datetime, acct: dict):
        intraday = self.broker.bars(sym, self.cfg["data"]["bar_minutes"], days=5)
        if intraday.empty:
            return
        if self._daily_cache.get(sym, (None,))[0] != now.date():
            self._daily_cache[sym] = (now.date(), self.broker.daily(sym))
        price = float(intraday["close"].iloc[-1])
        daily = daily_with_live(self._daily_cache[sym][1], price, now.date())
        sig = evaluate(sym, intraday, daily, self.cfg, news_score=self.news.score)
        self._note(f"sig-{sym}", f"[{now:%H:%M}] {sig} | news {self.news.score:+.2f}")
        if not sig.direction:
            return
        why = f"score {sig.score}: {', '.join(sig.reasons)}"

        if self.cfg["mode"]["trade_options"]:
            spot = self.broker.last_price(sym)
            cands = self.broker.option_candidates(sym, sig.direction, spot)
            best, msg = pick_contract(cands, sig.direction, spot, self.cfg)
            log.info("%s option pick: %s", sym, msg)
            if best:
                qty = self.risk.option_contracts(acct["equity"], best["mid"])
                if qty < 1:
                    log.info("Position size rounds to 0 contracts at $%.2f — skipping", best["mid"])
                else:
                    filled, avg = self.broker.buy_option(best["symbol"], qty, self.cfg["options"]["limit_retries"])
                    if filled:
                        self.state[best["symbol"]] = {
                            "kind": "option", "underlying": sym, "direction": sig.direction, "qty": filled,
                            "entry_price": avg, "peak": avg, "entry_time": now.isoformat(timespec="seconds"),
                            "und_stop": sig.stop, "und_target": sig.target,
                            "expiration": best["expiration"].isoformat(), "why": why}
                        self.risk.record_open(); self.save()
                        log.info("OPENED %s x%d @ %.2f (%s)", best["symbol"], filled, avg, why)
                    else:
                        log.info("Option order didn't fill — skipped")

        if self.cfg["mode"]["trade_shares"] and len(self.state) < self.cfg["risk"]["max_open_positions"]:
            qty = self.risk.share_qty(acct["equity"], sig.entry, sig.stop, acct["buying_power"])
            if qty >= 1:
                px = self.broker.bracket_shares(sym, qty, sig.direction, sig.stop, sig.target)
                if px:
                    self.state[sym] = {"kind": "shares", "underlying": sym, "direction": sig.direction, "qty": qty,
                                       "entry_price": px, "entry_time": now.isoformat(timespec="seconds"),
                                       "und_stop": sig.stop, "und_target": sig.target, "why": why}
                    self.risk.record_open(); self.save()
                    log.info("OPENED %s %d shares @ %.2f stop %.2f target %.2f", sig.direction, qty, px, sig.stop, sig.target)

    def try_condor(self, sym: str, now: datetime, acct: dict):
        k = self.cfg["condor"]
        key = (now.date(), sym)
        if self._condors_today.get(key, 0) >= k["max_per_symbol_per_day"]:
            return self._note(f"cnt-{sym}", f"{sym}: already traded {k['max_per_symbol_per_day']} condor(s) today")
        swing = k.get("mode", "0dte") == "swing"
        df = self.broker.daily(sym) if swing else self.broker.bars(sym, self.cfg["data"]["bar_minutes"], days=5)
        if df.empty:
            return
        df = add_all(df)
        ok, reasons = range_filter(df, self.cfg)
        self._note(f"sig-{sym}", f"[{now:%H:%M}] {sym}: {'RANGE-BOUND' if ok else 'no condor'} ({'; '.join(reasons)})")
        if not ok:
            return
        spot = self.broker.last_price(sym)
        if swing:
            exps = self.broker.expirations(sym, now.date() + timedelta(days=k["min_dte"]),
                                           now.date() + timedelta(days=k["max_dte"]), spot)
            expiries = choose_expiries(exps, now.date(), self.cfg)[:3]
            if not expiries:
                return self._note(f"chain-{sym}", f"{sym}: no expiration {k['min_dte']}-{k['max_dte']} days out")
        else:
            expiries = [now.date()]
        condor = None
        for expiry in expiries:        # try the best expiration first, fall back to the next ones
            chain = self.broker.chain_for_expiry(sym, spot, expiry, pct=k.get("chain_pct", 0.05))
            if not chain:
                msg = f"no options for {expiry}"
                continue
            if any(c.get("delta") is None for c in chain):
                close_dt = datetime(expiry.year, expiry.month, expiry.day, 16, 0, tzinfo=ET)
                filled = fill_missing_deltas(chain, spot, (close_dt - now).total_seconds() / (365 * 24 * 3600))
                if filled:
                    self._note(f"delta-{sym}", f"{sym}: feed had no greeks for {expiry}; estimated {filled} deltas from prices")
            condor, msg = pick_condor(chain, spot, self.cfg)
            log.info("%s condor pick (%s): %s", sym, expiry, msg)
            if condor:
                break
        if not condor:
            # save the option chain once per day per symbol so problems can be diagnosed from the logs folder
            dbg = ROOT / "logs" / f"chaindebug_{self.cfg.get('bot_name') or 'main'}_{sym}_{now:%Y%m%d}.json"
            if not dbg.exists() and chain:
                dbg.write_text(json.dumps({"spot": spot, "expiry": str(expiry), "reason": msg,
                                           "stats": getattr(self.broker, "last_chain_stats", None),
                                           "chain": sorted(chain, key=lambda c: (c["type"], c["strike"]))},
                                          indent=1, default=str))
            return
        qty = condor_contracts(acct["equity"], condor["max_loss"], self.cfg)
        if qty < 1:
            return log.info("%s: position size rounds to 0 condors — skipping", sym)
        min_credit = k["min_credit_pct"] * condor["width"]
        filled, credit = self.broker.open_condor(condor, qty, k["limit_retries"], min_credit)
        if not filled:
            return log.info("%s: condor order didn't fill — skipped", sym)
        cid = f"IC-{sym}-{now:%Y%m%d-%H%M}"
        self.state[cid] = {
            "kind": "condor", "underlying": sym, "direction": "neutral", "qty": filled, "credit": round(credit, 2),
            "entry_price": round(credit, 2), "legs": condor["legs"], "short_put": condor["short_put"],
            "short_call": condor["short_call"], "width": condor["width"],
            "max_loss": round(condor["width"] - credit, 2), "expiration": expiry.isoformat(),
            "entry_time": now.isoformat(timespec="seconds"), "why": "; ".join(reasons) + f" | {msg}"}
        self._condors_today[key] = self._condors_today.get(key, 0) + 1
        self.risk.record_open(); self.save()
        log.info("OPENED %d iron condor(s) on %s for $%.2f credit each (max loss $%.0f total) | %s",
                 filled, sym, credit, (condor["width"] - credit) * 100 * filled, msg)

    # ------------------------------------------------------------------ exits
    def manage_exits(self, now: datetime):
        sch = self.cfg["schedule"]
        prices = {}
        for sym, pos in list(self.state.items()):
            und = pos["underlying"]
            if und not in prices:
                try:
                    prices[und] = self.broker.last_price(und)
                except Exception:  # noqa: BLE001
                    prices[und] = 0.0
            if pos["kind"] == "condor":
                q = self.broker.quotes([l["symbol"] for l in pos["legs"]])
                if any(q.get(l["symbol"], (0, 0))[1] <= 0 for l in pos["legs"]):
                    continue  # missing quote this minute — check again next cycle
                mark = condor_mark(pos["legs"], q)
                te = self.cfg["condor"].get("time_exit")
                t_exit = session_time(te, self.close_et) if te else None
                reason = condor_exit_reason(pos, mark, prices[und], now, self.cfg, t_exit)
                if reason:
                    debit = self.broker.close_condor(pos["legs"], pos["qty"])
                    live = self.broker.positions() if not self.broker.dry else {}
                    if any(l["symbol"] in live for l in pos["legs"]):
                        log.warning("%s: condor close not fully filled — retrying next minute", sym)
                        continue
                    self.state.pop(sym, None); self.save()
                    self._last_exit[und] = now
                    self.journal(pos, sym, debit or mark, reason, now)
                continue
            if pos["kind"] == "shares":
                held = (now - datetime.fromisoformat(pos["entry_time"])).total_seconds() / 60
                reason = None
                if now.time() >= session_time(sch["flatten_shares_at"], self.close_et):
                    reason = "end-of-day flatten"
                elif held >= self.cfg["options"]["max_hold_minutes"] * 1.5:
                    reason = f"time stop ({held:.0f} min)"
                if reason:
                    px = self.broker.close_shares(sym)
                    self._finish_exit(sym, pos, px, reason, now)
                continue
            bid, ask = self.broker.option_quote(sym)
            mark = (bid + ask) / 2 if bid > 0 else 0.0
            reason = option_exit_reason(pos, mark, prices[und], now, self.cfg,
                                        flatten_at=session_time(sch["flatten_options_at"], self.close_et))
            if reason:
                px = self.broker.sell_option(sym, pos["qty"])
                self._finish_exit(sym, pos, px or mark, reason, now)
            else:
                self.save()  # persist updated peak

    def _finish_exit(self, sym, pos, px, reason, now):
        live = self.broker.positions() if not self.broker.dry else {}
        if sym in live:
            left = abs(int(float(live[sym].qty)))
            done = pos["qty"] - left
            if done > 0:
                self.journal({**pos, "qty": done}, sym, px, reason + " (partial)", now)
            pos["qty"] = left
            log.warning("%s: exit only partly filled, %d still open — will retry next cycle", sym, left)
            self.save()
            return
        self.state.pop(sym, None); self.save()
        self._last_exit[pos["underlying"]] = now
        self.journal(pos, sym, px, reason, now)
