"""Exit rules for open option positions (pure logic, easy to test)."""
from datetime import datetime, time as dtime


def _t(s: str) -> dtime:
    h, m = map(int, s.split(":"))
    return dtime(h, m)


def session_time(cfg_time: str, close_et: datetime | None) -> dtime:
    """A config time (written for a normal 4:00 PM ET close) shifted earlier on half days.
    e.g. flatten at "15:40" = 20 min before the close -> 12:40 when the market closes at 1:00 PM."""
    t = _t(cfg_time)
    if close_et is None or close_et.time() >= dtime(16, 0):
        return t
    offset = datetime.combine(close_et.date(), dtime(16, 0)) - datetime.combine(close_et.date(), t)
    return min(t, (datetime.combine(close_et.date(), close_et.time()) - offset).time())


def option_exit_reason(pos: dict, mark: float, und_price: float, now_et: datetime, cfg: dict,
                       flatten_at: dtime | None = None):
    """pos keys: entry_price, peak, direction, und_stop, und_target, entry_time (iso), expiration (iso date).
    Returns a reason string if the position should be closed now, else None. Updates pos['peak']."""
    o, sch = cfg["options"], cfg["schedule"]
    if mark <= 0:
        return None
    pos["peak"] = max(pos.get("peak", pos["entry_price"]), mark)
    entry = pos["entry_price"]
    pnl = mark / entry - 1
    peak_pnl = pos["peak"] / entry - 1

    if now_et.time() >= (flatten_at or _t(sch["flatten_options_at"])):
        return "end-of-day flatten"
    if pos.get("expiration") == now_et.date().isoformat() and now_et.time() >= dtime(15, 0):
        return "expires today"
    if pnl >= o["take_profit_pct"]:
        return f"take profit {pnl:+.0%}"
    if pnl <= -o["stop_loss_pct"]:
        return f"stop loss {pnl:+.0%}"
    if peak_pnl >= o["trail_after_pct"] and mark <= pos["peak"] * (1 - o["trail_giveback_pct"]):
        return f"trailing stop (peak {peak_pnl:+.0%}, now {pnl:+.0%})"
    long = pos["direction"] == "long"
    if und_price:
        if (long and und_price <= pos["und_stop"]) or (not long and und_price >= pos["und_stop"]):
            return f"underlying hit stop {pos['und_stop']:.2f}"
        if (long and und_price >= pos["und_target"]) or (not long and und_price <= pos["und_target"]):
            return f"underlying hit target {pos['und_target']:.2f}"
    held = (now_et - datetime.fromisoformat(pos["entry_time"])).total_seconds() / 60
    if held >= o["max_hold_minutes"] and pnl < o["trail_after_pct"]:
        return f"time stop ({held:.0f} min)"
    return None
