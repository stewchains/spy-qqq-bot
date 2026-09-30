"""Test the strategy on past data before risking anything.

    python backtest.py             # last 12 months, SPY and QQQ
    python backtest.py --days 730  # last 2 years

It answers the question "does day trading the shares with these signals actually make money?"
Results go to backtest_trades.csv. (Options can't be backtested accurately with free data — use
paper trading for that; the signal quality measured here is what drives the options trades too.)
"""
import argparse
from datetime import datetime, timedelta, timezone

import pandas as pd
from alpaca.data.enums import DataFeed
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

from bot import load_config, load_keys
from bot.backtest_core import simulate, stats, verdict


def fetch(client, sym, tf, start, feed):
    df = client.get_stock_bars(StockBarsRequest(symbol_or_symbols=sym, timeframe=tf, start=start,
                                                end=datetime.now(timezone.utc) - timedelta(minutes=20), feed=feed)).df
    if isinstance(df.index, pd.MultiIndex):
        df = df.xs(sym, level=0)
    return df[["open", "high", "low", "close", "volume"]].astype(float)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=365)
    ap.add_argument("--equity", type=float, default=30000)
    args = ap.parse_args()
    cfg = load_config()
    key, secret = load_keys()
    client = StockHistoricalDataClient(key, secret)
    feed = DataFeed.SIP if cfg["data"]["stock_feed"] == "sip" else DataFeed.IEX
    start = datetime.now(timezone.utc) - timedelta(days=args.days)
    m = cfg["data"]["bar_minutes"]
    allt = []
    for sym in cfg["symbols"]:
        print(f"Downloading {sym} {m}-minute candles for {args.days} days...")
        intraday = fetch(client, sym, TimeFrame(m, TimeFrameUnit.Minute), start, feed)
        intraday = intraday.tz_convert("America/New_York").between_time("09:30", "15:59")
        daily = fetch(client, sym, TimeFrame.Day, start - timedelta(days=120), feed)
        t = simulate(sym, intraday, daily, cfg, equity=args.equity)
        print(f"  {sym}: {stats(t, args.equity)}")
        allt.append(t)
    t = pd.concat(allt, ignore_index=True).sort_values("opened")
    if t.empty:
        print("No trades generated.")
        return
    t.to_csv("backtest_trades.csv", index=False)
    print("\nCOMBINED:", stats(t, args.equity))
    print("Longs :", stats(t[t.dir == "long"], args.equity))
    print("Shorts:", stats(t[t.dir == "short"], args.equity))
    print("Exits :", t.why.value_counts().to_dict())
    print("\nVERDICT:", verdict(t, args.equity))
    print("\nNote: results include 1 cent/share slippage each way but no fees. IEX volume is a fraction of the "
          "full market, so the volume filter is approximate. Past results don't guarantee future ones.")


if __name__ == "__main__":
    main()
