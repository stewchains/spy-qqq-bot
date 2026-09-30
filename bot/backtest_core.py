"""Backtest engine for the share day-trading version of the strategy.

Uses the exact same signal code as the live bot. Entries at the NEXT candle's open (no peeking),
stop/target checked candle by candle (stop assumed first if both touched), everything flat by the close.
"""
from datetime import timedelta

import numpy as np
import pandas as pd

from .exits import _t
from .indicators import add_all
from .strategy import daily_with_live, evaluate


def simulate(symbol: str, intraday: pd.DataFrame, daily: pd.DataFrame, cfg: dict,
             equity: float = 30000.0, slippage: float = 0.01) -> pd.DataFrame:
    df = add_all(intraday)
    sch, risk = cfg["schedule"], cfg["risk"]
    bar_min = cfg["data"]["bar_minutes"]
    t_start, t_end, t_flat = _t(sch["no_entries_before"]), _t(sch["no_entries_after"]), _t(sch["flatten_shares_at"])
    max_hold = cfg["options"]["max_hold_minutes"] * 1.5
    idx = df.index
    days = pd.Series(idx.date, index=idx)
    trades = []
    pos = None
    trades_today, cur_day, cool_until = 0, None, None

    for i in range(30, len(df) - 1):
        ts = idx[i]
        decided_at = ts + timedelta(minutes=bar_min)
        if ts.date() != cur_day:
            cur_day, trades_today, cool_until = ts.date(), 0, None
        bar = df.iloc[i]

        if pos:  # ---- manage open trade on this candle
            long = pos["dir"] == "long"
            exit_px, why = None, None
            if long and bar.low <= pos["stop"]:
                exit_px, why = min(bar.open, pos["stop"]), "stop"
            elif not long and bar.high >= pos["stop"]:
                exit_px, why = max(bar.open, pos["stop"]), "stop"
            elif long and bar.high >= pos["target"]:
                exit_px, why = max(bar.open, pos["target"]), "target"
            elif not long and bar.low <= pos["target"]:
                exit_px, why = min(bar.open, pos["target"]), "target"
            elif decided_at.time() >= t_flat or days.iloc[i + 1] != ts.date():
                exit_px, why = bar.close, "end of day"
            elif (decided_at - pos["t"]).total_seconds() / 60 >= max_hold:
                exit_px, why = bar.close, "time stop"
            if exit_px is not None:
                sign = 1 if long else -1
                exit_px -= sign * slippage
                pnl = sign * (exit_px - pos["entry"]) * pos["qty"]
                trades.append({"symbol": symbol, "opened": pos["t"], "closed": decided_at, "dir": pos["dir"],
                               "entry": round(pos["entry"], 2), "exit": round(exit_px, 2), "qty": pos["qty"],
                               "R": round(sign * (exit_px - pos["entry"]) / pos["risk"], 2),
                               "pnl": round(pnl, 2), "why": why})
                if pnl < 0:
                    cool_until = decided_at + timedelta(minutes=risk["cooldown_after_loss_min"])
                pos = None
            continue

        # ---- look for entries
        if not (t_start <= decided_at.time() < t_end) or trades_today >= risk["max_trades_per_day"]:
            continue
        if cool_until and decided_at < cool_until:
            continue
        if days.iloc[i + 1] != ts.date():
            continue
        d = daily_with_live(daily, float(bar.close), ts.date())
        sig = evaluate(symbol, df.iloc[max(0, i - 60): i + 1], d, cfg, prepared=True)
        if not sig.direction:
            continue
        nxt = df.iloc[i + 1]
        sign = 1 if sig.direction == "long" else -1
        entry = nxt.open + sign * slippage
        r = abs(sig.entry - sig.stop)
        qty = int(min(equity * risk["risk_per_trade_pct"] / r, equity * risk["max_share_position_pct"] / entry))
        if qty < 1:
            continue
        pos = {"dir": sig.direction, "entry": entry, "stop": entry - sign * r,
               "target": entry + sign * r * cfg["strategy"]["reward_risk"], "risk": r, "qty": qty,
               "t": idx[i + 1]}
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
