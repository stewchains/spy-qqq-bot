"""SPY / QQQ options + day-trading bot."""
import logging
import os
import socket
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


def setup_logging(name: str | None = None):
    logs = ROOT / "logs"
    logs.mkdir(exist_ok=True)
    prefix = f"bot-{name}" if name else "bot"
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)-6s %(message)s", datefmt="%H:%M:%S",
        handlers=[logging.StreamHandler(),
                  logging.FileHandler(logs / f"{prefix}-{datetime.now():%Y-%m-%d}.log", encoding="utf-8")])
    for noisy in ("urllib3", "websockets", "alpaca"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def single_instance(port: int = 47823):
    """Returns a held socket if this is the only copy of the bot running, else None.
    Windows releases the port automatically when the bot exits or crashes, so it can never get stuck."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):  # Windows: don't let a second process share the port
        s.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
    try:
        s.bind(("127.0.0.1", port))
        s.listen(1)
        return s
    except OSError:
        s.close()
        return None
