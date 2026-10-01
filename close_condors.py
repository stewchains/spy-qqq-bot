"""Close every iron condor a bot is tracking (buys them back at the market's current prices).

    python close_condors.py --config config_swing.yaml     # the 30-45 day bot
    python close_condors.py                                # the 0DTE bot

Stop that bot first. Results are written to its trade log like a normal exit."""
import argparse
import os
from datetime import datetime
from zoneinfo import ZoneInfo

from bot import engine, load_config, load_keys, setup_logging
from bot.condor import condor_mark

ap = argparse.ArgumentParser()
ap.add_argument("--config", default="config.yaml")
args = ap.parse_args()
cfg = load_config(os.path.join(os.path.dirname(os.path.abspath(__file__)), args.config))
name = cfg.get("bot_name")
setup_logging(name)
engine.use_bot_files(name)
bot = engine.TradingBot(cfg, *load_keys(cfg.get("alpaca_keys")))
condors = {k: v for k, v in bot.state.items() if v["kind"] == "condor"}
if not condors:
    raise SystemExit("No open condors tracked by this bot.")
if not bot.broker.clock().is_open:
    raise SystemExit("Market is closed — run this during market hours.")
now = datetime.now(ZoneInfo("America/New_York"))
for cid, pos in condors.items():
    q = bot.broker.quotes([l["symbol"] for l in pos["legs"]])
    mark = condor_mark(pos["legs"], q)
    print(f"Closing {cid}: {pos['qty']} condor(s), credit ${pos['credit']:.2f}, cost to close now ~${mark:.2f}")
    debit = bot.broker.close_condor(pos["legs"], pos["qty"])
    live = bot.broker.positions()
    if any(l["symbol"] in live for l in pos["legs"]):
        print(f"  WARNING: {cid} not fully closed — check the Alpaca dashboard")
        continue
    bot.state.pop(cid)
    bot.save()
    bot.journal(pos, cid, debit or mark, "closed manually (moving bot to another account)", now)
print("Done.")
input("Press Enter to close this window...")
