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
