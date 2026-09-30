"""Start the bot:  python run_bot.py      (Ctrl+C to stop)"""
import os
import sys

from bot import load_config, load_keys, setup_logging, single_instance
from bot.engine import TradingBot


def main():
    lock = single_instance()
    if lock is None:
        print("The bot is already running in another window. Not starting a second copy.")
        sys.exit(0)
    setup_logging()
    cfg = load_config()
    key, secret = load_keys()
    if not cfg["mode"]["paper"] and os.getenv("I_ACCEPT_LIVE_TRADING") != "YES":
        sys.exit("config.yaml has paper: false (REAL MONEY). To confirm, set I_ACCEPT_LIVE_TRADING=YES in .env "
                 "and use your LIVE api keys. See README 'Going live'.")
    bot = TradingBot(cfg, key, secret)
    try:
        bot.run()
    except KeyboardInterrupt:
        print("\nStopped. Open positions (if any) are still at Alpaca — the bot resumes managing them next start.")


if __name__ == "__main__":
    main()
