"""Backtest engine for the share day-trading version of the strategy.

Signals come from fast_signals (a vectorized copy of the live bot's strategy.evaluate; the tests check
they match trade-for-trade). Entries at the NEXT candle's open (no peeking),
stop/target checked candle by candle (stop assumed first if both touched), everything flat by the close.
"""
from datetime import timedelta

import numpy as np
import pandas as pd

from .exits import _t
from .fast_signals import compute
from .indicators import add_all
from .strategy import daily_with_live, evaluate


def simulate(symbol: str, intraday: pd.DataFrame, daily: pd.DataFrame, cfg: dict,
             equity: float = 30000.0, slippage: float = 0.01, prepared: pd.DataFrame | None = None,
             reference: bool = False, trend=None) -> pd.DataFrame:
    """prepared: intraday with indicators already added (saves time when testing many settings).
    reference: use the slow bar-by-bar strategy.evaluate() instead of fast_signals (for testing)."""
    df = prepared if prepared is not None else add_all(intraday)
    sch, risk = cfg["schedule"], cfg["risk"]
    bar_min = cfg["data"]["bar_minutes"]
    mins = lambda t: t.hour * 60 + t.minute  # noqa: E731
    t_start, t_end, t_flat = (mins(_t(sch[k])) for k in ("no_entries_before", "no_entries_after", "flatten_shares_at"))
    max_hold = cfg["options"]["max_hold_minutes"] * 1.5
    rr = cfg["strategy"]["reward_risk"]
    idx = df.index
    o, h, l, c = (df[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    days = np.array(idx.date)
    decided = idx + pd.Timedelta(minutes=bar_min)
    dmin = np.asarray(decided.hour * 60 + decided.minute)
    epoch, sec = pd.Timestamp(0, tz="UTC"), pd.Timedelta(seconds=1)
    dts = np.asarray((decided - epoch) / sec)  # seconds since 1970, for fast time-stop math
    ots = np.asarray((idx - epoch) / sec)
    if not reference:
        sig_dir, _, sig_stop = compute(df, daily, cfg, trend=trend)

    trades, pos = [], None
    trades_today, cur_day, cool_until = 0, None, None
    for i in range(30, len(df) - 1):
        if days[i] != cur_day:
            cur_day, trades_today, cool_until = days[i], 0, None

        if pos:  # ---- manage open trade on this candle
            long = pos["dir"] == "long"
            exit_px = why = None
            if long and l[i] <= pos["stop"]:
                exit_px, why = min(o[i], pos["stop"]), "stop"
            elif not long and h[i] >= pos["stop"]:
                exit_px, why = max(o[i], pos["stop"]), "stop"
            elif long and h[i] >= pos["target"]:
                exit_px, why = max(o[i], pos["target"]), "target"
            elif not long and l[i] <= pos["target"]:
                exit_px, why = min(o[i], pos["target"]), "target"
            elif dmin[i] >= t_flat or days[i + 1] != days[i]:
                exit_px, why = c[i], "end of day"
            elif (dts[i] - pos["t0"]) / 60 >= max_hold:
                exit_px, why = c[i], "time stop"
            if exit_px is not None:
                sign = 1 if long else -1
                exit_px -= sign * slippage
                pnl = sign * (exit_px - pos["entry"]) * pos["qty"]
                trades.append({"symbol": symbol, "opened": pos["t"], "closed": decided[i], "dir": pos["dir"],
                               "entry": round(pos["entry"], 2), "exit": round(exit_px, 2), "qty": pos["qty"],
                               "R": round(sign * (exit_px - pos["entry"]) / pos["risk"], 2),
                               "pnl": round(pnl, 2), "why": why})
                if pnl < 0:
                    cool_until = decided[i] + timedelta(minutes=risk["cooldown_after_loss_min"])
                pos = None
            continue

        # ---- look for entries
        if not (t_start <= dmin[i] < t_end) or trades_today >= risk["max_trades_per_day"]:
            continue
        if cool_until is not None and decided[i] < cool_until:
            continue
        if days[i + 1] != days[i]:
            continue
        if reference:
            d = daily_with_live(daily, float(c[i]), days[i])
            sig = evaluate(symbol, df.iloc[max(0, i - 60): i + 1], d, cfg, prepared=True)
            if not sig.direction:
                continue
            direction, r = sig.direction, abs(sig.entry - sig.stop)
        else:
            if sig_dir[i] == 0:
                continue
            direction, r = ("long" if sig_dir[i] == 1 else "short"), float(sig_stop[i])
        if not r > 0:
            continue
        sign = 1 if direction == "long" else -1
        entry = o[i + 1] + sign * slippage
        qty = int(min(equity * risk["risk_per_trade_pct"] / r, equity * risk["max_share_position_pct"] / entry))
        if qty < 1:
            continue
        pos = {"dir": direction, "entry": entry, "stop": entry - sign * r, "target": entry + sign * r * rr,
               "risk": r, "qty": qty, "t": idx[i + 1], "t0": ots[i + 1]}
        trades_today += 1
    return pd.DataFrame(trades)


def stats(t: pd.DataFrame, equity: float) -> dict:
    if t.empty:
        return {"trades": 0}
    wins, losses = t[t.pnl > 0], t[t.pnl <= 0]
    eq = equity + t.pnl.cumsum()
    dd = ((eq - eq.cummax()) / eq.cummax()).min()
    return {"trades": len(t), "win_rate": round(len(wins) / len(t), 3),
            "avg_R": round(float(t.R.mean()), 3),
            "profit_factor": round(float(wins.pnl.sum() / abs(losses.pnl.sum())), 2) if losses.pnl.sum() else float("inf"),
            "total_pnl": round(float(t.pnl.sum()), 2), "return_pct": round(float(t.pnl.sum()) / equity * 100, 2),
            "max_drawdown_pct": round(float(dd) * 100, 2),
            "days_traded": t.opened.dt.date.nunique()}


def verdict(t: pd.DataFrame, equity: float) -> str:
    """Conservative rule of thumb for whether share day-trading is worth turning on."""
    if len(t) < 40:
        return "NOT ENOUGH TRADES to judge (need 40+). Test a longer period."
    t = t.sort_values("opened")
    half = len(t) // 2
    a, b = stats(t.iloc[:half], equity), stats(t.iloc[half:], equity)
    s = stats(t, equity)
    if s["profit_factor"] >= 1.3 and a["total_pnl"] > 0 and b["total_pnl"] > 0 and s["max_drawdown_pct"] > -15:
        return ("EDGE FOUND: profitable in both halves of the test with profit factor "
                f"{s['profit_factor']}. Reasonable to enable trade_shares in PAPER mode and confirm for a few weeks.")
    if s["profit_factor"] >= 1.1:
        return (f"MARGINAL (profit factor {s['profit_factor']}; first half ${a['total_pnl']:,.0f}, "
                f"second half ${b['total_pnl']:,.0f}). Not strong enough to trust yet — keep trade_shares off.")
    return f"NO EDGE (profit factor {s['profit_factor']}). Keep trade_shares: false."
