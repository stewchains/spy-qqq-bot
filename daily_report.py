"""Make the end-of-day report by hand (the bots also do this automatically right after the close):
    python daily_report.py                              # 0DTE bot (account 1)
    python daily_report.py --config config_swing.yaml   # 30-45 day bot (account 2)
Saves reports/<bot>-YYYY-MM-DD.html, opens it, and posts to Discord if DISCORD_WEBHOOK_URL is in .env."""
import argparse
import json
import os

from bot import load_config, load_keys, setup_logging
from bot import engine
from bot.broker import Broker
from bot.daily_report import generate


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--no-open", action="store_true", help="don't open the HTML report")
    args = ap.parse_args()
    cfg = load_config(os.path.join(os.path.dirname(os.path.abspath(__file__)), args.config))
    setup_logging(f"report-{cfg.get('bot_name') or '0dte'}")
    engine.use_bot_files(cfg.get("bot_name"))
    key, secret = load_keys(cfg.get("alpaca_keys"))
    state = json.loads(engine.STATE.read_text()) if engine.STATE.exists() else {}
    path = generate(cfg, Broker(key, secret, cfg), state, engine.JOURNAL, open_file=not args.no_open)
    print(f"Report saved: {path}")


if __name__ == "__main__":
    main()
