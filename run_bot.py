"""Start a bot:
    python run_bot.py                          # 0DTE iron condor bot (config.yaml)
    python run_bot.py --config config_swing.yaml   # 30-45 day iron condor bot
Ctrl+C to stop."""
import argparse
import os
import sys

from bot import load_config, load_keys, setup_logging, single_instance
from bot import engine
from bot.engine import TradingBot


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config.yaml")
    args = ap.parse_args()
    cfg = load_config(os.path.join(os.path.dirname(os.path.abspath(__file__)), args.config))
    name = cfg.get("bot_name")
    lock = single_instance(cfg.get("lock_port", 47823))
    if lock is None:
        print(f"The {name or 'main'} bot is already running in another window. Not starting a second copy.")
        sys.exit(0)
    setup_logging(name)
    engine.use_bot_files(name)
    key, secret = load_keys()
    if not cfg["mode"]["paper"] and os.getenv("I_ACCEPT_LIVE_TRADING") != "YES":
        sys.exit("config has paper: false (REAL MONEY). To confirm, set I_ACCEPT_LIVE_TRADING=YES in .env "
                 "and use your LIVE api keys. See README 'Going live'.")
    bot = TradingBot(cfg, key, secret)
    try:
        bot.run()
    except KeyboardInterrupt:
        print("\nStopped. Open positions (if any) are still at Alpaca — the bot resumes managing them next start.")


if __name__ == "__main__":
    main()
