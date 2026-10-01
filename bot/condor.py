"""Same-day (0DTE) iron condor strategy — pure logic, no broker calls (easy to test).

An iron condor = sell an OTM put + sell an OTM call (collect premium), and buy a further-OTM put + call
("wings") so the most you can lose is capped. It makes money when SPY/QQQ stays between the short strikes.

Defaults come from published research (see README "Iron condor research"):
  * short strikes ~12 delta (practitioner 0DTE studies sell 10-15 delta)
  * $3 wings on SPY/QQQ (~30-35 SPX points, the width used in those studies)
  * take profit at 50% of the credit, stop when the loss reaches ~1x the credit
  * close early if price reaches a short strike, and always well before Alpaca's 3:15 PM ET
    cutoff for expiring options
Technical analysis decides WHEN: only open a condor when the 5-minute chart looks range-bound
(weak trend, RSI near 50, price near VWAP, Bollinger Bands not expanding).
"""
from datetime import date, datetime, time as dtime


# ----------------------------------------------------------------------------- technical filter
def range_filter(df, cfg: dict) -> tuple[bool, list]:
    """df: 5-min candles with indicators (indicators.add_all). True if the market looks range-bound."""
    c = cfg["condor"]["ta"]
    if df is None or len(df) < 30:
        return False, ["not enough candles yet"]
    b = df.iloc[-1]
    anchor = c.get("anchor", "vwap")          # "vwap" for 5-min candles, "ema21" for daily candles
    label = "VWAP" if anchor == "vwap" else "21-day EMA"
    checks = [
        (b.adx <= c["max_adx"], f"ADX {b.adx:.0f} (max {c['max_adx']})"),
        (c["rsi_range"][0] <= b.rsi <= c["rsi_range"][1], f"RSI {b.rsi:.0f} (want {c['rsi_range'][0]}-{c['rsi_range'][1]})"),
        (abs(b.close - b[anchor]) <= c["max_vwap_dist_atr"] * b.atr,
         f"price {abs(b.close - b[anchor]) / b.atr if b.atr else 0:.1f} ATR from {label} (max {c['max_vwap_dist_atr']})"),
        (b.bb_ratio <= c["max_bb_expansion"], f"Bollinger width {b.bb_ratio:.2f}x normal (max {c['max_bb_expansion']})"),
    ]
    failed = [txt for ok, txt in checks if not ok]
    if failed:
        return False, failed
    return True, [txt for _, txt in checks]


# ----------------------------------------------------------------------------- strike selection
def _mid(c):
    return (c["bid"] + c["ask"]) / 2


def pick_condor(chain: list[dict], spot: float, cfg: dict) -> tuple[dict | None, str]:
    """chain: same-expiry contracts with symbol, type ('call'/'put'), strike, delta, bid, ask.
    Returns (condor dict or None, reason)."""
    k = cfg["condor"]
    lo, hi = k["short_delta_range"]
    # long wings only need an ask (far-out options often show a $0 bid); shorts need a real two-sided quote
    quoted = [c for c in chain if c.get("ask", 0) > 0 and c.get("ask", 0) >= c.get("bid", 0)]
    puts = sorted([c for c in quoted if c["type"] == "put"], key=lambda c: c["strike"])
    calls = sorted([c for c in quoted if c["type"] == "call"], key=lambda c: c["strike"])
    pct = k.get("max_leg_spread_pct", 0.0)

    def tight(c):
        return (c["ask"] - c["bid"]) <= max(k["max_leg_spread"], pct * _mid(c))

    def best_short(side_list, otm):
        cands = [c for c in side_list if otm(c) and c["bid"] > 0 and c.get("delta") is not None
                 and lo <= abs(c["delta"]) <= hi and tight(c)]
        return min(cands, key=lambda c: abs(abs(c["delta"]) - k["short_delta"])) if cands else None

    sp = best_short(puts, lambda c: c["strike"] < spot)
    sc = best_short(calls, lambda c: c["strike"] > spot)
    if not sp or not sc:
        side = "put" if not sp else "call"
        near = [c for c in (puts if side == "put" else calls) if c.get("delta") is not None and lo <= abs(c["delta"]) <= hi]
        detail = (f"{len(near)} {side}s in the delta range, tightest spread "
                  f"${min(c['ask'] - c['bid'] for c in near):.2f}") if near else f"no {side}s in the delta range"
        return None, f"no short {side} with the right delta / tight enough spread ({detail})"
    # wings: nearest strike at least `wing_width` further out
    lp = max((c for c in puts if c["strike"] <= sp["strike"] - k["wing_width"]), key=lambda c: c["strike"], default=None)
    lc = min((c for c in calls if c["strike"] >= sc["strike"] + k["wing_width"]), key=lambda c: c["strike"], default=None)
    if not lp or not lc:
        return None, (f"no {'put' if not lp else 'call'} wing ${k['wing_width']:g} beyond the short strike "
                      f"(short put {sp['strike']:g}, short call {sc['strike']:g})")
    credit = _mid(sp) + _mid(sc) - _mid(lp) - _mid(lc)
    width = max(sp["strike"] - lp["strike"], lc["strike"] - sc["strike"])
    if credit < k["min_credit_pct"] * width:
        return None, (f"credit ${credit:.2f} too small for ${width:g} wings "
                      f"(need {k['min_credit_pct']:.0%} = ${k['min_credit_pct'] * width:.2f})")
    condor = {
        "legs": [
            {"symbol": sp["symbol"], "side": "sell", "type": "put", "strike": sp["strike"]},
            {"symbol": lp["symbol"], "side": "buy", "type": "put", "strike": lp["strike"]},
            {"symbol": sc["symbol"], "side": "sell", "type": "call", "strike": sc["strike"]},
            {"symbol": lc["symbol"], "side": "buy", "type": "call", "strike": lc["strike"]},
        ],
        "short_put": sp["strike"], "short_call": sc["strike"], "long_put": lp["strike"], "long_call": lc["strike"],
        "credit_mid": round(credit, 2), "width": width,
        "max_loss": round(width - credit, 2),
    }
    return condor, (f"{lp['strike']:g}/{sp['strike']:g}p - {sc['strike']:g}/{lc['strike']:g}c "
                    f"(Δ {abs(sp['delta']):.2f}/{abs(sc['delta']):.2f}) credit ${credit:.2f} on ${width:g} wings")


# ----------------------------------------------------------------------------- monitoring
def condor_mark(legs: list[dict], quotes: dict) -> float:
    """Cost to buy the condor back now (mid prices). quotes: symbol -> (bid, ask)."""
    total = 0.0
    for leg in legs:
        bid, ask = quotes.get(leg["symbol"], (0.0, 0.0))
        mid = (bid + ask) / 2
        total += mid if leg["side"] == "sell" else -mid
    return max(total, 0.0)


def condor_exit_reason(pos: dict, mark: float, und_price: float, now_et: datetime, cfg: dict,
                       time_exit: dtime | None) -> str | None:
    """pos: credit, short_put, short_call, expiration (iso date). mark: current cost to close."""
    k = cfg["condor"]
    credit = pos["credit"]
    if time_exit is not None and now_et.time() >= time_exit:
        return "time exit (before expiration risk)"
    if k.get("close_at_dte") is not None and pos.get("expiration"):
        dte = (date.fromisoformat(pos["expiration"]) - now_et.date()).days
        if dte <= k["close_at_dte"]:
            return f"{dte} days to expiration left (close at {k['close_at_dte']} DTE)"
    if mark <= credit * (1 - k["take_profit_pct"]):
        return f"take profit (kept {1 - mark / credit:.0%} of ${credit:.2f} credit)"
    if mark >= credit * (1 + k["stop_loss_mult"]):
        return f"stop loss (cost to close ${mark:.2f} vs ${credit:.2f} credit)"
    if und_price and k.get("breach_buffer_pct") is not None:
        buf = k["breach_buffer_pct"] * und_price
        if und_price <= pos["short_put"] + buf:
            return f"price {und_price:.2f} hit short put {pos['short_put']:g}"
        if und_price >= pos["short_call"] - buf:
            return f"price {und_price:.2f} hit short call {pos['short_call']:g}"
    return None


def condor_contracts(equity: float, max_loss_per: float, cfg: dict) -> int:
    """How many condors so the max possible loss stays within risk_per_trade_pct of the account."""
    if max_loss_per <= 0:
        return 0
    n = int((equity * cfg["risk"]["risk_per_trade_pct"]) // (max_loss_per * 100))
    return max(0, min(n, cfg["condor"]["max_contracts"]))


def choose_expiry(expirations: list, today: date, cfg: dict):
    """Swing mode: the expiration closest to target_dte within [min_dte, max_dte]."""
    k = cfg["condor"]
    ok = [e for e in expirations if k["min_dte"] <= (e - today).days <= k["max_dte"]]
    return min(ok, key=lambda e: abs((e - today).days - k["target_dte"])) if ok else None
