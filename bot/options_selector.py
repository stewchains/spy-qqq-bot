"""Pick the best option contract for a signal: right expiry, right delta, tight spread, liquid."""
from typing import Optional


def pick_contract(cands: list[dict], direction: str, spot: float, cfg: dict) -> tuple[Optional[dict], str]:
    """cands: dicts with symbol, type ('call'/'put'), strike, dte, delta, bid, ask, open_interest.

    Returns (best_contract or None, reason).
    """
    o = cfg["options"]
    want = "call" if direction == "long" else "put"
    lo, hi = o["delta_range"]
    kept, why = [], {"type/dte": 0, "no quote": 0, "spread": 0, "open interest": 0, "delta": 0}
    for c in cands:
        if c["type"] != want or not (o["min_dte"] <= c["dte"] <= o["max_dte"]):
            why["type/dte"] += 1; continue
        bid, ask = c.get("bid") or 0, c.get("ask") or 0
        if bid <= 0 or ask <= 0 or ask < bid:
            why["no quote"] += 1; continue
        mid = (bid + ask) / 2
        spread_pct = (ask - bid) / mid
        if spread_pct > o["max_spread_pct"]:
            why["spread"] += 1; continue
        oi = c.get("open_interest")
        if oi is not None and oi < o["min_open_interest"]:
            why["open interest"] += 1; continue
        d = c.get("delta")
        if d is None:  # no greeks from the feed -> approximate using moneyness (0-1.5% OTM ~ 0.35-0.55 delta)
            otm = (c["strike"] - spot) / spot if want == "call" else (spot - c["strike"]) / spot
            if not (-0.003 <= otm <= 0.015):
                why["delta"] += 1; continue
            d_abs = 0.5 - otm * 10
        else:
            d_abs = abs(d)
            if not (lo <= d_abs <= hi):
                why["delta"] += 1; continue
        kept.append((abs(d_abs - o["target_delta"]), c["dte"], spread_pct, {**c, "mid": round(mid, 2),
                     "spread_pct": round(spread_pct, 4), "delta_used": round(d_abs, 3)}))
    if not kept:
        return None, "no contract passed filters (" + ", ".join(f"{k}:{v}" for k, v in why.items() if v) + ")"
    kept.sort(key=lambda x: (round(x[0], 2), x[1], x[2]))
    best = kept[0][3]
    return best, f"{best['symbol']} Δ{best['delta_used']} {best['dte']}DTE mid {best['mid']} spread {best['spread_pct']:.1%}"
