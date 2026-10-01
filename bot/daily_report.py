"""End-of-day report for one bot / account.

Each bot builds its report right after the market closes:
  * saves reports/<bot>-YYYY-MM-DD.html in the bot folder (and opens it on the PC)
  * posts a summary to Discord if DISCORD_WEBHOOK_URL (or DISCORD_WEBHOOK_URL_SWING for the swing bot) is in .env
Run it by hand any time:  python daily_report.py  /  python daily_report.py --config config_swing.yaml
"""
import csv
import html
import json
import logging
import os
import sys
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .condor import condor_mark

ET = ZoneInfo("America/New_York")
ROOT = Path(__file__).resolve().parent.parent
log = logging.getLogger("report")


def bot_label(cfg: dict) -> str:
    if cfg.get("report_label"):
        return cfg["report_label"]
    mode = cfg.get("condor", {}).get("mode")
    if cfg.get("strategy", {}).get("type") == "iron_condor":
        return "30-45 Day Iron Condors" if mode == "swing" else "0DTE Iron Condors"
    return "Directional Options"


def _f(x, default=0.0):
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def read_journal(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(newline="") as f:
        return list(csv.DictReader(f))


def build(cfg: dict, acct: dict, state: dict, journal: list[dict], quotes: dict, spots: dict, day: date) -> dict:
    """Pure function: everything the report shows. quotes: option symbol -> (bid, ask); spots: underlying -> price."""
    today = [t for t in journal if str(t.get("closed", ""))[:10] == day.isoformat()]
    opened_today = [p for p in state.values() if str(p.get("entry_time", ""))[:10] == day.isoformat()]
    opened_today += [t for t in today if str(t.get("opened", ""))[:10] == day.isoformat()]
    realized_today = sum(_f(t["pnl"]) for t in today)

    open_pos = []
    for pid, p in state.items():
        row = {"id": pid, "underlying": p.get("underlying", pid), "qty": p.get("qty", 0),
               "expiration": p.get("expiration", ""), "opened": str(p.get("entry_time", ""))[:16].replace("T", " ")}
        if p.get("kind") == "condor":
            mark = condor_mark(p["legs"], quotes) if quotes else None
            row.update(strikes=f"{_leg(p, 'buy', 'put')}/{p['short_put']:g}p – {p['short_call']:g}/{_leg(p, 'buy', 'call')}c",
                       credit=p["credit"], mark=mark,
                       unrealized=None if mark is None else round((p["credit"] - mark) * 100 * p["qty"], 2),
                       max_loss=round(p.get("max_loss", 0) * 100 * p["qty"], 2),
                       spot=spots.get(p.get("underlying")))
            if p.get("expiration"):
                row["dte"] = (date.fromisoformat(p["expiration"]) - day).days
        else:
            row.update(strikes=pid, credit=p.get("entry_price"), mark=None, unrealized=None, max_loss=None)
        open_pos.append(row)

    wins = [t for t in journal if _f(t["pnl"]) > 0]
    losses = [t for t in journal if _f(t["pnl"]) <= 0]
    gross_loss = abs(sum(_f(t["pnl"]) for t in losses))
    eq, last = acct.get("equity"), acct.get("last_equity")
    return {
        "label": bot_label(cfg), "day": day.isoformat(), "paper": cfg["mode"]["paper"],
        "equity": eq, "day_change": (eq - last) if (eq is not None and last) else None,
        "day_change_pct": ((eq - last) / last) if (eq is not None and last) else None,
        "realized_today": round(realized_today, 2), "closed_today": today, "opened_today": len(opened_today),
        "open_positions": open_pos,
        "unrealized_total": round(sum(r["unrealized"] or 0 for r in open_pos), 2),
        "all_time": {
            "trades": len(journal), "win_rate": (len(wins) / len(journal)) if journal else None,
            "pnl": round(sum(_f(t["pnl"]) for t in journal), 2),
            "profit_factor": (sum(_f(t["pnl"]) for t in wins) / gross_loss) if gross_loss else None,
            "best": max((_f(t["pnl"]) for t in journal), default=None),
            "worst": min((_f(t["pnl"]) for t in journal), default=None),
        },
    }


def _leg(p, side, typ):
    for l in p.get("legs", []):
        if l["side"] == side and l["type"] == typ:
            return f"{l['strike']:g}"
    return "?"


def money(x, sign=True):
    if x is None:
        return "—"
    s = f"${abs(x):,.2f}"
    return (("+" if x >= 0 else "−") + s) if sign else s


def _why(t):
    return str(t.get("why_out", "")).split("(")[0].strip() or "—"


# ----------------------------------------------------------------------------- HTML
def render_html(r: dict) -> str:
    e = html.escape
    def cls(x):
        return "" if x is None else ("pos" if x >= 0 else "neg")
    at = r["all_time"]
    closed = "".join(
        f"<tr><td>{e(t['underlying'])}</td><td>{e(str(t['opened'])[11:16])}</td><td>{e(str(t['closed'])[11:16])}</td>"
        f"<td>{e(str(t['qty']))}</td><td>${_f(t['entry']):.2f}</td><td>${_f(t['exit']):.2f}</td>"
        f"<td class='{cls(_f(t['pnl']))}'>{money(_f(t['pnl']))}</td><td>{e(_why(t))}</td></tr>"
        for t in r["closed_today"]) or "<tr><td colspan=8 class=muted>No trades closed today.</td></tr>"
    openp = "".join(
        f"<tr><td>{e(p['underlying'])}</td><td>{e(p['strikes'])}</td><td>{e(p['expiration'])}"
        f"{' (' + str(p['dte']) + 'd)' if p.get('dte') is not None else ''}</td><td>{p['qty']}</td>"
        f"<td>${_f(p['credit']):.2f}</td><td>{money(p['mark'], False)}</td>"
        f"<td class='{cls(p['unrealized'])}'>{money(p['unrealized'])}</td><td>{money(p['max_loss'], False)}</td></tr>"
        for p in r["open_positions"]) or "<tr><td colspan=8 class=muted>No open positions.</td></tr>"
    wr = "—" if at["win_rate"] is None else f"{at['win_rate']:.0%}"
    pf = "—" if at["profit_factor"] is None else f"{at['profit_factor']:.2f}"
    dcp = "" if r["day_change_pct"] is None else f" ({r['day_change_pct']:+.2%})"
    return f"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{e(r['label'])} — {r['day']}</title><style>
:root{{--bg:#0f1115;--card:#181b22;--ink:#e8eaf0;--muted:#8b93a7;--line:#2a2f3a;--pos:#3fb950;--neg:#f85149}}
body{{margin:0;background:var(--bg);color:var(--ink);font:15px/1.45 system-ui,Segoe UI,Roboto,sans-serif;padding:24px 16px}}
main{{max-width:980px;margin:0 auto}} h1{{font-size:22px;margin:0}} h2{{font-size:15px;margin:28px 0 10px;color:var(--muted);font-weight:600;text-transform:uppercase;letter-spacing:.04em}}
.sub{{color:var(--muted);margin:4px 0 20px}} .tiles{{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:12px}}
.tile{{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px}} .tile b{{display:block;font-size:20px;margin-top:4px}}
.tile span{{color:var(--muted);font-size:13px}} table{{width:100%;border-collapse:collapse;background:var(--card);border:1px solid var(--line);border-radius:10px;overflow:hidden}}
th,td{{padding:8px 10px;border-bottom:1px solid var(--line);text-align:left;font-variant-numeric:tabular-nums}} th{{color:var(--muted);font-weight:600;font-size:13px}}
.wrap{{overflow-x:auto}} .pos{{color:var(--pos)}} .neg{{color:var(--neg)}} .muted{{color:var(--muted)}}
</style></head><body><main>
<h1>{e(r['label'])}</h1><div class="sub">{r['day']} · {'Paper' if r['paper'] else 'LIVE'} account</div>
<div class="tiles">
<div class="tile"><span>Account equity</span><b>{money(r['equity'], False)}</b></div>
<div class="tile"><span>Today's change</span><b class="{cls(r['day_change'])}">{money(r['day_change'])}{dcp}</b></div>
<div class="tile"><span>Realized today</span><b class="{cls(r['realized_today'])}">{money(r['realized_today'])}</b></div>
<div class="tile"><span>Open P&amp;L</span><b class="{cls(r['unrealized_total'])}">{money(r['unrealized_total'])}</b></div>
<div class="tile"><span>Trades opened / closed today</span><b>{r['opened_today']} / {len(r['closed_today'])}</b></div>
</div>
<h2>Closed today</h2><div class="wrap"><table><tr><th>Symbol</th><th>Opened</th><th>Closed</th><th>Qty</th><th>Credit</th><th>Paid to close</th><th>P&amp;L</th><th>Exit reason</th></tr>{closed}</table></div>
<h2>Open positions</h2><div class="wrap"><table><tr><th>Symbol</th><th>Strikes</th><th>Expires</th><th>Qty</th><th>Credit</th><th>Cost to close</th><th>Open P&amp;L</th><th>Max loss</th></tr>{openp}</table></div>
<h2>All time (this bot)</h2><div class="tiles">
<div class="tile"><span>Trades</span><b>{at['trades']}</b></div>
<div class="tile"><span>Win rate</span><b>{wr}</b></div>
<div class="tile"><span>Total realized P&amp;L</span><b class="{cls(at['pnl'])}">{money(at['pnl'])}</b></div>
<div class="tile"><span>Profit factor</span><b>{pf}</b></div>
<div class="tile"><span>Best / worst trade</span><b>{money(at['best'])} / {money(at['worst'])}</b></div>
</div></main></body></html>"""


# ----------------------------------------------------------------------------- Discord
def discord_payload(r: dict) -> dict:
    at = r["all_time"]
    dcp = "" if r["day_change_pct"] is None else f" ({r['day_change_pct']:+.2%})"
    closed = "\n".join(f"{t['underlying']} x{t['qty']}: {money(_f(t['pnl']))} — {_why(t)}"
                       for t in r["closed_today"][:10]) or "None"
    openp = "\n".join(
        f"{p['underlying']} {p['strikes']} exp {p['expiration']}"
        f"{' (' + str(p['dte']) + 'd)' if p.get('dte') is not None else ''} x{p['qty']}: {money(p['unrealized'])}"
        for p in r["open_positions"][:10]) or "None"
    color = 0x3FB950 if (r["day_change"] or 0) >= 0 else 0xF85149
    wr = "—" if at["win_rate"] is None else f"{at['win_rate']:.0%}"
    return {"username": "SPY/QQQ Bot", "embeds": [{
        "title": f"{r['label']} — {r['day']}" + ("" if r["paper"] else " (LIVE)"),
        "color": color,
        "fields": [
            {"name": "Equity", "value": money(r["equity"], False), "inline": True},
            {"name": "Today", "value": f"{money(r['day_change'])}{dcp}", "inline": True},
            {"name": "Realized today", "value": money(r["realized_today"]), "inline": True},
            {"name": f"Closed today ({len(r['closed_today'])})", "value": closed[:1000], "inline": False},
            {"name": f"Open positions ({len(r['open_positions'])})", "value": openp[:1000], "inline": False},
            {"name": "All time", "value": f"{at['trades']} trades · win rate {wr} · P&L {money(at['pnl'])}",
             "inline": False},
        ]}]}


def post_discord(url: str, payload: dict) -> bool:
    import requests
    r = requests.post(url, json=payload, timeout=15)
    if r.status_code >= 300:
        log.warning("Discord post failed: HTTP %s %s", r.status_code, r.text[:200])
        return False
    return True


# ----------------------------------------------------------------------------- glue
def generate(cfg: dict, broker, state: dict, journal_path: Path, open_file: bool | None = None) -> Path:
    """Fetch live data, write the HTML report, post to Discord. Returns the HTML path."""
    rcfg = cfg.get("report", {})
    day = datetime.now(ET).date()
    acct = broker.account()
    legs = [l["symbol"] for p in state.values() if p.get("kind") == "condor" for l in p["legs"]]
    quotes, spots = {}, {}
    if legs:
        try:
            quotes = broker.quotes(legs)
        except Exception as e:  # noqa: BLE001
            log.warning("report: couldn't get option quotes: %s", e)
    for u in {p.get("underlying") for p in state.values() if p.get("underlying")}:
        try:
            spots[u] = broker.last_price(u)
        except Exception:  # noqa: BLE001
            pass
    r = build(cfg, acct, state, read_journal(journal_path), quotes, spots, day)

    out = ROOT / "reports"
    out.mkdir(exist_ok=True)
    name = cfg.get("bot_name") or "0dte"
    path = out / f"{name}-{day.isoformat()}.html"
    path.write_text(render_html(r), encoding="utf-8")
    (out / f"{name}-{day.isoformat()}.json").write_text(json.dumps(r, indent=1, default=str))
    log.info("Daily report saved: %s", path)

    sfx = f"_{cfg['alpaca_keys'].upper()}" if cfg.get("alpaca_keys") else ""
    url = os.getenv(f"DISCORD_WEBHOOK_URL{sfx}") or os.getenv("DISCORD_WEBHOOK_URL")
    if url and rcfg.get("discord", True):
        if post_discord(url, discord_payload(r)):
            log.info("Daily report posted to Discord")
    elif not url:
        log.info("No DISCORD_WEBHOOK_URL in .env — skipped the Discord post")

    if (rcfg.get("open_on_pc", True) if open_file is None else open_file) and sys.platform.startswith("win"):
        try:
            os.startfile(str(path))  # noqa: S606 — opens in the default browser
        except OSError:
            pass
    return path
