"""Run this first:  python check_setup.py
Verifies your keys, account, market data, option chain and news feed — without placing any trades."""
from datetime import datetime, timezone

from bot import load_config, load_keys, setup_logging
from bot.broker import Broker
from bot.news import NewsMonitor
from bot.options_selector import pick_contract
from bot.strategy import daily_trend, evaluate


def step(name, fn):
    try:
        out = fn()
        print(f"  OK   {name}" + (f": {out}" if out is not None else ""))
        return True
    except Exception as e:  # noqa: BLE001
        print(f"  FAIL {name}: {e}")
        return False


def main():
    setup_logging()
    cfg = load_config()
    key, secret = load_keys()
    b = Broker(key, secret, cfg)
    print(f"\nChecking Alpaca ({'PAPER' if b.paper else 'LIVE'})...")
    ok = step("account", lambda: b.account())
    ok &= step("market clock", lambda: f"open={b.clock().is_open}")
    for sym in cfg["symbols"]:
        ok &= step(f"{sym} 5-min candles", lambda s=sym: f"{len(b.bars(s, 5))} candles")
        ok &= step(f"{sym} daily trend", lambda s=sym: daily_trend(b.daily(s)))
        ok &= step(f"{sym} signal right now", lambda s=sym: str(evaluate(s, b.bars(s, 5), b.daily(s), cfg)))
        spot = b.last_price(sym)
        ok &= step(f"{sym} option chain", lambda s=sym: pick_contract(b.option_candidates(s, "long", spot), "long", spot, cfg)[1])
    nm = NewsMonitor(cfg, key, secret)
    ok &= step("news feed", lambda: (nm.poll(datetime.now(timezone.utc), force=True), f"{len(nm.items)} headlines, mood {nm.score:+.2f}")[1])
    print("\nAll good — run: python backtest.py  then  python run_bot.py" if ok else
          "\nFix the FAIL lines above (see README troubleshooting).")


if __name__ == "__main__":
    main()
