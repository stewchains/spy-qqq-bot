"""SPY / QQQ options + day-trading bot."""
import logging
import os
from datetime import datetime
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


def load_config(path: str | None = None) -> dict:
    return yaml.safe_load(Path(path or ROOT / "config.yaml").read_text())


def load_keys():
    try:
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env")
    except ImportError:
        pass
    key, secret = os.getenv("ALPACA_API_KEY"), os.getenv("ALPACA_SECRET_KEY")
    if not key or not secret or "your_" in key:
        raise SystemExit("Missing Alpaca keys. Copy .env.example to .env and paste your keys (see README).")
    return key, secret


def setup_logging():
    logs = ROOT / "logs"
    logs.mkdir(exist_ok=True)
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)-6s %(message)s", datefmt="%H:%M:%S",
        handlers=[logging.StreamHandler(),
                  logging.FileHandler(logs / f"bot-{datetime.now():%Y-%m-%d}.log", encoding="utf-8")])
    for noisy in ("urllib3", "websockets", "alpaca"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
