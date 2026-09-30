"""Strategy search: tries many versions of the strategy and checks the best ones on data they never saw.

    python optimize.py              # 3 years of data: tune on the first 2, test on the last 1
    python optimize.py --years 4    # more history (slower download, more reliable)

How it avoids fooling itself
----------------------------
If you try 200+ settings on the same data, some will look great by pure luck. So:
  1. TRAIN period (older data): every setting is scored here, and the top 5 are picked.
  2. TEST period (the most recent year, never used for picking): only those top 5 are checked.
  3. A setting "passes" only if it has profit factor >= 1.3 in BOTH periods, 60+ test trades, and made
     money on BOTH SPY and QQQ in BOTH periods. (Checked against pure random data: nothing passes.)
It also shows how the current config.yaml settings did, and what fraction of ALL settings made money
in the test year (if that's ~50%, wins are mostly luck).

Downloaded candles are cached in data_cache/ so re-runs are fast. Results: optimize_results.csv
"""
import argparse
import copy
import itertools
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from bot import load_config, load_keys
from bot.backtest_core import simulate, stats
from bot.fast_signals import daily_trend_per_bar
from bot.indicators import add_all

ROOT = Path(__file__).resolve().parent
CACHE = ROOT / "data_cache"
EQUITY = 30000

GRID = {
    "min_score":         [4, 5, 6],
    "atr_stop_mult":     [1.5, 2.0, 3.0],
    "reward_risk":       [1.0, 1.5, 2.0],
    "min_adx":           [18, 25],
    "allow_shorts":      [True, False],
    "no_entries_after":  ["15:00", "11:30"],   # "11:30" = morning session only
}
MIN_TRAIN_TRADES = 100
MIN_TEST_TRADES = 60
PASS_PF = 1.3  # profit factor needed in BOTH training and the unseen test year


def load_bars(sym: str, years: int, cfg: dict):
    CACHE.mkdir(exist_ok=True)
    f_i, f_d = CACHE / f"{sym}_5m_{years}y.csv", CACHE / f"{sym}_1d_{years}y.csv"
    fresh = lambda f: f.exists() and datetime.now().timestamp() - f.stat().st_mtime < 86400  # noqa: E731
    if fresh(f_i) and fresh(f_d):
        rd = lambda f: pd.read_csv(f, index_col=0, parse_dates=[0])  # noqa: E731
        i, d = rd(f_i), rd(f_d)
        i.index = pd.to_datetime(i.index, utc=True).tz_convert("America/New_York")
        d.index = pd.to_datetime(d.index, utc=True)
        return i, d
    from alpaca.data.enums import DataFeed
    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
    key, secret = load_keys()
    client = StockHistoricalDataClient(key, secret)
    feed = DataFeed.SIP if cfg["data"]["stock_feed"] == "sip" else DataFeed.IEX
    end = datetime.now(timezone.utc) - timedelta(minutes=20)
    start = end - timedelta(days=365 * years)

    def get(tf, s):
        df = client.get_stock_bars(StockBarsRequest(symbol_or_symbols=sym, timeframe=tf, start=s, end=end, feed=feed)).df
        if isinstance(df.index, pd.MultiIndex):
            df = df.xs(sym, level=0)
        return df[["open", "high", "low", "close", "volume"]].astype(float)

    print(f"  downloading {sym} ({years} years of 5-min candles, takes a minute)...")
    i = get(TimeFrame(cfg["data"]["bar_minutes"], TimeFrameUnit.Minute), start)
    i = i.tz_convert("America/New_York").between_time("09:30", "15:59")
    d = get(TimeFrame.Day, start - timedelta(days=120))
    i.to_csv(f_i); d.to_csv(f_d)
    return i, d


def apply(cfg: dict, combo: dict) -> dict:
    c = copy.deepcopy(cfg)
    for k, v in combo.items():
        if k == "no_entries_after":
            c["schedule"][k] = v
        else:
            c["strategy"][k] = v
    return c


def run_both(data: dict, cfg: dict, split, end) -> tuple[dict, dict]:
    """Simulate once per symbol, then score trades opened before / after the split separately."""
    per = {"train": {}, "test": {}}
    pooled = {"train": [], "test": []}
    for sym, (prep, daily, trend) in data.items():
        t = simulate(sym, None, daily, cfg, equity=EQUITY, prepared=prep, trend=trend)
        for p, m in (("train", lambda x: x.opened < split), ("test", lambda x: (x.opened >= split) & (x.opened < end))):
            part = t[m(t)] if not t.empty else t
            per[p][sym] = stats(part, EQUITY) if not part.empty else {"trades": 0}
            pooled[p].append(part)
    for p in per:
        parts = [x for x in pooled[p] if len(x)]
        per[p]["ALL"] = stats(pd.concat(parts), EQUITY) if parts else {"trades": 0}
    return per["train"], per["test"]


def row(label, combo, tr, te, syms):
    r = {"label": label, **combo}
    for p, res in (("train", tr), ("test", te)):
        a = res["ALL"]
        r[f"{p}_trades"] = a.get("trades", 0)
        r[f"{p}_pf"] = a.get("profit_factor", 0)
        r[f"{p}_pnl"] = a.get("total_pnl", 0)
        r[f"{p}_dd%"] = a.get("max_drawdown_pct", 0)
        for s in syms:
            r[f"{p}_{s}_pnl"] = res[s].get("total_pnl", 0)
    return r


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", type=int, default=3)
    args = ap.parse_args()
    cfg = load_config()
    syms = cfg["symbols"]

    print("Loading data...")
    data, first, last = {}, None, None
    for s in syms:
        intraday, daily = load_bars(s, args.years, cfg)
        prep = add_all(intraday)
        data[s] = (prep, daily, daily_trend_per_bar(prep, daily))  # trend doesn't depend on settings
        first = intraday.index[0] if first is None else max(first, intraday.index[0])
        last = intraday.index[-1] if last is None else min(last, intraday.index[-1])
    split = last - pd.Timedelta(days=365)
    print(f"TRAIN {first:%Y-%m-%d} -> {split:%Y-%m-%d}   TEST {split:%Y-%m-%d} -> {last:%Y-%m-%d}\n")

    combos = [dict(zip(GRID, v)) for v in itertools.product(*GRID.values())]
    print(f"Testing {len(combos)} strategy versions (a few minutes)...")
    rows = []
    current = {"min_score": cfg["strategy"]["min_score"], "atr_stop_mult": cfg["strategy"]["atr_stop_mult"],
               "reward_risk": cfg["strategy"]["reward_risk"], "min_adx": cfg["strategy"]["min_adx"],
               "allow_shorts": cfg["strategy"].get("allow_shorts", True),
               "no_entries_after": cfg["schedule"]["no_entries_after"]}
    end = last + pd.Timedelta(minutes=5)
    for n, combo in enumerate(combos, 1):
        c = apply(cfg, combo)
        tr, te = run_both(data, c, split, end)
        rows.append(row("current" if combo == current else "", combo, tr, te, syms))
        if n % 25 == 0:
            print(f"  {n}/{len(combos)} done")
    res = pd.DataFrame(rows)
    res.to_csv(ROOT / "optimize_results.csv", index=False)

    cols = list(GRID) + ["train_trades", "train_pf", "test_trades", "test_pf", "test_pnl", "test_dd%"] + \
        [f"test_{s}_pnl" for s in syms]
    pd.set_option("display.width", 250); pd.set_option("display.max_columns", 30)

    cur = res[res.label == "current"]
    if len(cur):
        print("\nYOUR CURRENT SETTINGS:")
        print(cur[cols].to_string(index=False))

    eligible = res[res.train_trades >= MIN_TRAIN_TRADES].sort_values("train_pf", ascending=False)
    top = eligible.head(5)
    print(f"\nTOP 5 BY TRAINING RESULTS (need {MIN_TRAIN_TRADES}+ trades), and how they did on the unseen test year:")
    print(top[cols].to_string(index=False))

    profitable_share = (res.test_pnl > 0).mean()
    print(f"\n{profitable_share:.0%} of all {len(res)} versions made money in the test year "
          "(near 50% = results are mostly luck).")

    per_sym = [f"{p}_{s}_pnl" for p in ("train", "test") for s in syms]
    passed = top[(top.train_pf >= PASS_PF) & (top.test_pf >= PASS_PF) & (top.test_trades >= MIN_TEST_TRADES) &
                 top[per_sym].gt(0).all(axis=1)]
    print("\nVERDICT:")
    if passed.empty:
        print("  NOTHING PASSED. None of the best training versions held up on the unseen year for both symbols.\n"
              "  This style of indicator strategy doesn't show a reliable edge on SPY/QQQ. Keep the bot on paper\n"
              "  (or off) and don't put real money behind it.")
    else:
        b = passed.iloc[0]
        print(f"  PASSED: profit factor {b.train_pf} in training and {b.test_pf} on the unseen year "
              f"(test P&L ${b.test_pnl:,.0f} on a ${EQUITY:,} account).\n"
              "  To use it, set these in config.yaml, then paper trade for 4-6 weeks to confirm:")
        print(f"    strategy.min_score: {b.min_score}\n    strategy.atr_stop_mult: {b.atr_stop_mult}\n"
              f"    strategy.reward_risk: {b.reward_risk}\n    strategy.min_adx: {b.min_adx}\n"
              f"    strategy.allow_shorts: {str(bool(b.allow_shorts)).lower()}\n"
              f"    schedule.no_entries_after: \"{b.no_entries_after}\"")
        print("  Reminder: this tests share trades. Options add time decay and wider spreads, so options results "
              "will be weaker than this.")
    print("\nFull results: optimize_results.csv")


if __name__ == "__main__":
    main()
