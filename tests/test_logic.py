"""Offline tests (no API keys needed):  python -m pytest -q"""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from bot import load_config  # noqa: E402
from bot.backtest_core import simulate, stats, verdict  # noqa: E402
from bot.events import EventCalendar  # noqa: E402
from bot.exits import option_exit_reason  # noqa: E402
from bot.indicators import add_all, rsi  # noqa: E402
from bot.news import NewsMonitor, is_shock, score_text  # noqa: E402
from bot.options_selector import pick_contract  # noqa: E402
from bot.risk import RiskManager  # noqa: E402
from bot.strategy import daily_dates, daily_with_live, evaluate  # noqa: E402

ET = ZoneInfo("America/New_York")
CFG = load_config()
# Tests use fixed baseline strategy settings so tuning config.yaml never breaks them.
CFG["strategy"].update({"min_score": 4, "rsi_long_range": [50, 70], "rsi_short_range": [30, 50], "min_adx": 18,
                        "allow_shorts": True, "require_daily_trend": True, "atr_stop_mult": 1.5, "reward_risk": 2.0})
CFG["schedule"].update({"no_entries_before": "09:45", "no_entries_after": "15:00"})


def make_day(path, day="2026-09-28", start=600.0, vol=1e5):
    idx = pd.date_range(f"{day} 09:30", periods=len(path), freq="5min", tz=ET)
    close = start + np.array(path)
    open_ = np.r_[start, close[:-1]]
    return pd.DataFrame({"open": open_, "high": np.maximum(open_, close) + 0.1,
                         "low": np.minimum(open_, close) - 0.1, "close": close,
                         "volume": np.full(len(path), vol)}, index=idx)


def daily_trend(up=True, n=120):
    idx = pd.date_range("2026-04-01", periods=n, freq="B", tz="UTC")
    c = 500 + np.arange(n) * (1 if up else -1)
    return pd.DataFrame({"open": c, "high": c + 1, "low": c - 1, "close": c, "volume": 1e7}, index=idx)


def dip_then_rip(sign=1):
    # 25 candles drifting down, then a steady rally (mirror for sign=-1)
    wiggle = np.tile([0.15, -0.15], 20)
    p = list(np.linspace(0, -2, 25) + wiggle[:25]) + list(-2 + np.linspace(0.1, 1.5, 15) + wiggle[:15])
    return [sign * x for x in p]


def test_rsi_bounds():
    s = pd.Series(np.cumsum(np.random.default_rng(1).normal(size=500)) + 100)
    r = rsi(s)
    assert r.between(0, 100).all()
    assert rsi(pd.Series(np.arange(50.0))).iloc[-1] == 100


def test_vwap_resets_daily():
    a, b = make_day([0.1] * 20, "2026-09-28"), make_day([0.1] * 20, "2026-09-29", start=700)
    df = add_all(pd.concat([a, b]))
    first_b = df.loc[df.index.date == pd.Timestamp("2026-09-29").date()].iloc[0]
    assert abs(first_b.vwap - (first_b.high + first_b.low + first_b.close) / 3) < 1e-9


def test_long_signal_in_uptrend():
    df = make_day(dip_then_rip())
    # find a bar where a long fires
    fired = [evaluate("SPY", df.iloc[: i + 1], daily_trend(True), CFG) for i in range(29, len(df))]
    longs = [s for s in fired if s.direction == "long"]
    assert longs, [str(s) for s in fired]
    s = longs[0]
    assert s.stop < s.entry < s.target
    assert not any(s.direction == "short" for s in fired)


def test_short_signal_in_downtrend_and_trend_filter():
    df = make_day(dip_then_rip(-1))
    shorts = [evaluate("SPY", df.iloc[: i + 1], daily_trend(False), CFG) for i in range(29, len(df))]
    assert any(s.direction == "short" and s.target < s.entry < s.stop for s in shorts)
    # same tape but daily trend is bullish -> shorts are filtered out
    blocked = [evaluate("SPY", df.iloc[: i + 1], daily_trend(True), CFG) for i in range(29, len(df))]
    assert not any(s.direction == "short" for s in blocked)


def test_news_blocks_longs():
    df = make_day(dip_then_rip())
    sigs = [evaluate("SPY", df.iloc[: i + 1], daily_trend(True), CFG, news_score=-0.8) for i in range(29, len(df))]
    assert not any(s.direction == "long" for s in sigs)
    assert any("news" in " ".join(s.reasons) for s in sigs)


def test_flat_market_no_trade():
    df = make_day([0.0] * 40)
    assert evaluate("SPY", df, daily_trend(True), CFG).direction is None


def test_news_scoring_and_shock():
    assert score_text("Nvidia beats estimates, shares surge to record high") > 0.5
    assert score_text("Stocks plunge as recession fears grow") < -0.5
    assert score_text("Analyst issues forward guidance award") == 0  # 'war' inside words must not match
    assert is_shock("Powell says Fed ready to act") and is_shock("Hotter CPI print rattles markets")
    assert not is_shock("Apple unveils new iPhone colors")
    # real market movers still pause trading
    for h in ("Fed holds rates steady, signals two cuts", "FOMC statement: Federal Reserve cuts interest rates by 25 bps",
              "Nonfarm payrolls rise 250K, beating estimates", "Market-wide circuit breaker triggered as S&P 500 drops 7%",
              "Trump announces new tariffs on all imports", "Russia launches missile strike on Kyiv"):
        assert is_shock(h), h
    # headlines that wrongly paused trading on Oct 1, 2026 must not
    for h in ("Fed's Schmid Says Trying To See In Data How Much Inflation Is Due To Demand, How Much Is Driven By Supply Shocks",
              "Trading Halt: Halt status updated at 9:30:00 AM ET: Quotation Resumption: IPO security released for quotation",
              "Diesel Prices May Stay Elevated For 'More Than Four Quarters,' Dallas Fed Survey Finds, As Trump Weighs Export Ban",
              "Analysts warn of a price war among streaming services", "Nexalin Technology Shares Halted On Circuit Breaker To The Upside, Stock Now Up 92.76%", "XYZ Corp shares halted pending news"):
        assert not is_shock(h), h


def test_news_monitor_pause():
    cfg = {**CFG, "news": {**CFG["news"], "enabled": False, "use_claude": False}}
    nm = NewsMonitor(cfg, "k", "s")
    now = datetime(2026, 9, 30, 15, 0, tzinfo=timezone.utc)
    nm.ingest([{"id": 1, "headline": "Fed announces emergency rate cut", "summary": "", "symbols": [],
                "created_at": now - timedelta(minutes=2), "source": "x"},
               {"id": 2, "headline": "Microsoft beats estimates", "summary": "", "symbols": ["MSFT"],
                "created_at": now - timedelta(minutes=30), "source": "x"}], now)
    assert nm.paused(now) and not nm.paused(now + timedelta(minutes=20))
    assert nm.score > 0


def test_option_picker():
    cands = [
        {"symbol": "A", "type": "call", "strike": 601, "dte": 3, "delta": 0.46, "bid": 2.00, "ask": 2.06, "open_interest": 5000},
        {"symbol": "B", "type": "call", "strike": 600, "dte": 3, "delta": 0.52, "bid": 2.40, "ask": 2.90, "open_interest": 5000},  # wide
        {"symbol": "C", "type": "call", "strike": 605, "dte": 3, "delta": 0.20, "bid": 0.50, "ask": 0.52, "open_interest": 5000},  # low delta
        {"symbol": "D", "type": "call", "strike": 601, "dte": 0, "delta": 0.45, "bid": 1.00, "ask": 1.02, "open_interest": 5000},  # 0DTE
        {"symbol": "E", "type": "put", "strike": 599, "dte": 3, "delta": -0.45, "bid": 2.0, "ask": 2.05, "open_interest": 5000},
    ]
    best, _ = pick_contract(cands, "long", 600, CFG)
    assert best["symbol"] == "A"
    best, _ = pick_contract(cands, "short", 600, CFG)
    assert best["symbol"] == "E"
    none, why = pick_contract(cands[1:4], "long", 600, CFG)
    assert none is None and "spread" in why


def test_exit_rules():
    now = datetime(2026, 9, 30, 11, 0, tzinfo=ET)
    base = {"entry_price": 2.0, "peak": 2.0, "direction": "long", "und_stop": 595, "und_target": 610,
            "entry_time": (now - timedelta(minutes=10)).isoformat(), "expiration": "2026-10-02"}
    assert option_exit_reason(dict(base), 2.0, 600, now, CFG) is None
    assert "take profit" in option_exit_reason(dict(base), 3.3, 600, now, CFG)
    assert "stop loss" in option_exit_reason(dict(base), 1.2, 600, now, CFG)
    assert "underlying hit stop" in option_exit_reason(dict(base), 1.9, 594, now, CFG)
    p = dict(base); option_exit_reason(p, 2.8, 600, now, CFG)  # peak +40%
    assert "trailing" in option_exit_reason(p, 2.2, 600, now, CFG)
    late = now.replace(hour=15, minute=41)
    assert "end-of-day" in option_exit_reason(dict(base), 2.0, 600, late, CFG)
    old = dict(base, entry_time=(now - timedelta(minutes=130)).isoformat())
    assert "time stop" in option_exit_reason(old, 2.1, 600, now, CFG)


def test_risk_sizing_and_gates():
    r = RiskManager(CFG)
    # $30k acct, 1% risk = $300; $2.00 premium, 35% stop -> $70/contract -> 4; cap 5% = $1500 -> 7
    assert r.option_contracts(30000, 2.00) == 4
    assert r.option_contracts(30000, 20.0) == 0
    now = datetime(2026, 9, 30, 10, 0, tzinfo=ET)
    r.new_day(now.date(), 30000)
    assert r.can_open(now, 30000, 0)[0]
    assert not r.can_open(now, 29300, 0)[0]  # -2.3% -> halted
    r2 = RiskManager(CFG); r2.new_day(now.date(), 30000)
    r2.record_close(-100, now)
    assert not r2.can_open(now + timedelta(minutes=5), 30000, 0)[0]
    assert r2.can_open(now + timedelta(minutes=25), 30000, 0)[0]
    assert not r2.can_open(now, 30000, 2)[0]


def test_event_blackout(tmp_path):
    f = tmp_path / "e.yaml"
    f.write_text('- {date: 2026-10-14, time: "08:30", name: CPI}\n- {date: 2026-10-28, time: "14:00", name: FOMC, minutes_after: 60}\n')
    cal = EventCalendar(str(f), 15, 30)
    assert cal.blocking(datetime(2026, 10, 14, 8, 50, tzinfo=ET)) == "CPI"
    assert cal.blocking(datetime(2026, 10, 14, 9, 45, tzinfo=ET)) is None
    assert cal.blocking(datetime(2026, 10, 28, 14, 55, tzinfo=ET)) == "FOMC"


def test_daily_with_live_excludes_today():
    d = daily_trend(True)
    today = daily_dates(d)[-1]
    out = daily_with_live(d, 999.0, today)
    assert out["close"].iloc[-1] == 999.0 and len(out) == len(d)


def test_backtest_runs_and_no_overnight():
    rng = np.random.default_rng(7)
    days = pd.bdate_range("2026-03-02", periods=60)
    frames, px = [], 600.0
    for d in days:
        drift = rng.choice([-0.05, 0.05])
        path = np.cumsum(rng.normal(drift, 0.35, 78))
        frames.append(make_day(list(path), str(d.date()), start=px, vol=rng.uniform(5e4, 2e5)))
        px = frames[-1].close.iloc[-1]
    intraday = pd.concat(frames)
    daily = intraday.resample("1D").agg({"open": "first", "high": "max", "low": "min", "close": "last",
                                        "volume": "sum"}).dropna()
    daily.index = daily.index.tz_convert("UTC")
    t = simulate("SPY", intraday, daily, CFG)
    assert not t.empty
    assert (t.opened.dt.date == t.closed.dt.date).all()          # always flat overnight
    assert (t.closed.dt.time <= pd.Timestamp("15:55").time()).all()
    assert t.groupby(t.opened.dt.date).size().max() <= CFG["risk"]["max_trades_per_day"]
    s = stats(t, 30000)
    assert s["trades"] == len(t)
    assert isinstance(verdict(t, 30000), str)


def _random_market(days=80, seed=11):
    rng = np.random.default_rng(seed)
    frames, px = [], 600.0
    for d in pd.bdate_range("2026-01-05", periods=days):
        drift = rng.choice([-0.06, 0.0, 0.06])
        path = np.cumsum(rng.normal(drift, 0.35, 78))
        frames.append(make_day(list(path), str(d.date()), start=px, vol=rng.uniform(5e4, 2e5)))
        px = frames[-1].close.iloc[-1]
    intraday = pd.concat(frames)
    daily = intraday.resample("1D").agg({"open": "first", "high": "max", "low": "min", "close": "last",
                                        "volume": "sum"}).dropna()
    daily.index = daily.index.tz_convert("UTC")
    return intraday, daily


def test_fast_signals_match_live_strategy():
    """The vectorized backtest signals must produce exactly the same trades as the live bot's code."""
    intraday, daily = _random_market()
    for overrides in ({}, {"min_score": 5, "allow_shorts": False}, {"min_adx": 25, "atr_stop_mult": 2.5}):
        cfg = {**CFG, "strategy": {**CFG["strategy"], **overrides}}
        fast = simulate("SPY", intraday, daily, cfg)
        slow = simulate("SPY", intraday, daily, cfg, reference=True)
        assert len(fast) > 10
        pd.testing.assert_frame_equal(fast.reset_index(drop=True), slow.reset_index(drop=True))
        if overrides.get("allow_shorts") is False:
            assert (fast.dir == "long").all()


def test_optimizer_rejects_random_data(tmp_path):
    """On a pure random walk (no real edge exists) the strategy search must NOT report a pass."""
    import optimize as O
    monkeypatch_dir = tmp_path / "data_cache"
    monkeypatch_dir.mkdir()
    O.CACHE = monkeypatch_dir
    O.ROOT = tmp_path
    O.GRID = {"min_score": [4, 6], "atr_stop_mult": [1.5, 3.0], "reward_risk": [1.0, 2.0], "min_adx": [18, 25],
              "allow_shorts": [True, False], "no_entries_after": ["15:00", "11:30"]}
    for k, sym in enumerate(["SPY", "QQQ"]):
        rng = np.random.default_rng(100 + k)
        frames, px = [], 500.0
        for d in pd.bdate_range("2023-10-02", "2026-09-29"):
            frames.append(make_day(list(np.cumsum(rng.normal(0, .35, 78))), str(d.date()), start=px,
                                   vol=rng.uniform(5e4, 2e5)))
            px = frames[-1].close.iloc[-1]
        i = pd.concat(frames)
        d = i.resample("1D").agg({"open": "first", "high": "max", "low": "min", "close": "last",
                                  "volume": "sum"}).dropna()
        d.index = d.index.tz_convert("UTC")
        i.tz_convert("UTC").to_csv(monkeypatch_dir / f"{sym}_5m_3y.csv")
        d.to_csv(monkeypatch_dir / f"{sym}_1d_3y.csv")
    import io, contextlib
    out = io.StringIO()
    sys.argv = ["optimize.py"]
    with contextlib.redirect_stdout(out):
        O.main()
    assert "NOTHING PASSED" in out.getvalue(), out.getvalue()[-1500:]


def test_single_instance_lock():
    from bot import single_instance
    first = single_instance(47899)
    assert first is not None
    assert single_instance(47899) is None      # second copy is refused
    first.close()
    again = single_instance(47899)             # released when the first copy exits
    assert again is not None
    again.close()


def test_half_day_flatten_times():
    from datetime import time as dtime
    from bot.exits import session_time
    normal = datetime(2026, 11, 25, 16, 0, tzinfo=ET)
    half = datetime(2026, 11, 27, 13, 0, tzinfo=ET)          # day after Thanksgiving: 1:00 PM ET close
    assert session_time("15:40", normal) == dtime(15, 40)
    assert session_time("15:40", half) == dtime(12, 40)       # options closed 20 min before the early close
    assert session_time("15:50", half) == dtime(12, 50)       # shares 10 min before
    assert session_time("15:40", None) == dtime(15, 40)
    pos = {"entry_price": 2.0, "peak": 2.0, "direction": "long", "und_stop": 590, "und_target": 610,
           "entry_time": datetime(2026, 11, 27, 10, 0, tzinfo=ET).isoformat(), "expiration": "2026-12-04"}
    now = datetime(2026, 11, 27, 12, 45, tzinfo=ET)
    assert "end-of-day" in option_exit_reason(dict(pos), 2.0, 600, now, CFG, flatten_at=session_time("15:40", half))


# ----------------------------------------------------------------------------- iron condor
def _chain(spot=600.0):
    """Synthetic 0DTE chain: delta falls off with distance from spot."""
    out = []
    for k in range(int(spot) - 15, int(spot) + 16):
        d = max(0.01, 0.5 - abs(k - spot) * 0.05)
        prem = round(max(0.02, d * 2.4), 2)
        typ_put = k < spot
        for typ in ("put", "call"):
            otm = (typ == "put" and k < spot) or (typ == "call" and k > spot)
            delta = d if otm else 1 - d
            px = prem if otm else round(prem + abs(k - spot), 2)
            out.append({"symbol": f"SPY{typ[0].upper()}{k}", "type": typ, "strike": float(k),
                        "delta": -delta if typ == "put" else delta, "bid": px, "ask": round(px + 0.02, 2)})
    return out


def test_condor_pick_strikes_and_credit():
    from bot.condor import pick_condor
    c, msg = pick_condor(_chain(), 600.0, CFG)
    assert c, msg
    assert c["long_put"] < c["short_put"] < 600 < c["short_call"] < c["long_call"]
    assert c["short_put"] - c["long_put"] >= CFG["condor"]["wing_width"]
    assert c["credit_mid"] > 0 and abs(c["max_loss"] - (c["width"] - c["credit_mid"])) < 0.011
    assert [l["side"] for l in c["legs"]] == ["sell", "buy", "sell", "buy"]


def test_condor_rejects_thin_credit():
    from bot.condor import pick_condor
    cheap = [{**x, "bid": 0.01, "ask": 0.02} for x in _chain()]
    c, msg = pick_condor(cheap, 600.0, CFG)
    assert c is None and "credit" in msg


def test_condor_mark_and_exits():
    from datetime import time as dtime
    from bot.condor import condor_exit_reason, condor_mark
    legs = [{"symbol": "a", "side": "sell"}, {"symbol": "b", "side": "buy"},
            {"symbol": "c", "side": "sell"}, {"symbol": "d", "side": "buy"}]
    q = {"a": (0.30, 0.32), "b": (0.05, 0.07), "c": (0.30, 0.32), "d": (0.05, 0.07)}
    assert abs(condor_mark(legs, q) - 0.50) < 1e-9
    pos = {"credit": 0.50, "short_put": 595, "short_call": 605}
    now = datetime(2026, 10, 2, 11, 0, tzinfo=ET)
    t = dtime(15, 0)
    assert condor_exit_reason(pos, 0.45, 600, now, CFG, t) is None
    assert "take profit" in condor_exit_reason(pos, 0.24, 600, now, CFG, t)
    assert "stop loss" in condor_exit_reason(pos, 1.01, 600, now, CFG, t)
    assert "short put" in condor_exit_reason(pos, 0.60, 595.1, now, CFG, t)
    assert "short call" in condor_exit_reason(pos, 0.60, 604.9, now, CFG, t)
    assert "time exit" in condor_exit_reason(pos, 0.40, 600, now.replace(hour=15, minute=1), CFG, t)


def test_condor_sizing():
    from bot.condor import condor_contracts
    # $100k x 1% = $1,000 risk; max loss $2.60/condor = $260 -> 3 condors
    assert condor_contracts(100000, 2.60, CFG) == 3
    assert condor_contracts(100000, 0.10, CFG) == CFG["condor"]["max_contracts"]
    assert condor_contracts(1000, 2.60, CFG) == 0


def test_range_filter():
    from bot.condor import range_filter
    calm = make_day(list(np.random.default_rng(1).normal(0, 0.15, 60)))  # chop around one price (seed 1 = calm)
    ok, why = range_filter(add_all(calm), CFG)
    assert ok, why
    trend = make_day(list(np.linspace(0, 6, 60)))               # steady run-up
    ok, why = range_filter(add_all(trend), CFG)
    assert not ok and any("ADX" in w or "RSI" in w or "VWAP" in w for w in why), why


def test_swing_condor_expiry_and_dte_exit():
    import copy
    import yaml
    from datetime import date, time as dtime
    from bot.condor import choose_expiry, condor_exit_reason
    sw = yaml.safe_load((Path(__file__).resolve().parent.parent / "config_swing.yaml").read_text())
    today = date(2026, 10, 2)
    exps = [today + timedelta(days=d) for d in (7, 24, 31, 38, 44, 52)]
    assert choose_expiry(exps, today, sw) == today + timedelta(days=44)
    assert choose_expiry([today + timedelta(days=60)], today, sw) is None
    pos = {"credit": 1.50, "short_put": 570, "short_call": 630, "expiration": (today + timedelta(days=30)).isoformat()}
    now = datetime(2026, 10, 2, 11, 0, tzinfo=ET)
    assert condor_exit_reason(pos, 1.40, 569, now, sw, None) is None          # no breach exit in swing mode
    assert "take profit" in condor_exit_reason(pos, 0.70, 600, now, sw, None)
    later = now + timedelta(days=9)                                            # 21 DTE
    assert "days to expiration" in condor_exit_reason(pos, 1.40, 600, later, sw, None)
    assert sw["bot_name"] == "swing" and sw["lock_port"] != CFG.get("lock_port", 47823)
