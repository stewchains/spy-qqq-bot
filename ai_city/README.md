# Read-only bot snapshot for AI City

This optional tool exports a **local** JSON snapshot from the existing SPY/QQQ bot project. It does not alter the trading loop, modify configs, submit orders, or send data anywhere.

## Local-only mode (default)

From the repository root:

```bash
python ai_city/snapshot_bot.py --config config.yaml
python ai_city/snapshot_bot.py --config config_swing.yaml
```

It reads the config, matching local state file, and trade journal. It reports strategy/mode metadata, local position count, and realized-trade journal statistics.

## Optional read-only Alpaca account snapshot

```bash
python ai_city/snapshot_bot.py --config config.yaml --account
```

This loads the already configured Alpaca credentials from the local `.env` and calls only the existing broker wrapper's `account()` and `positions()` methods. It does **not** place, replace, cancel, or close orders. Review the selected config first: if it uses live credentials, the report will read live account information.

Snapshots are written under `reports/ai_city/`, which the repository ignores. Keep that folder private. No API key or secret is written to the JSON report. No OpenAI API is used.

## Cautions

- The committed `config.yaml` currently defaults to paper mode, but that is not proof of what a separate running process uses. This script reads the config file you specify, not running process memory.
- Missing state/journal files are reported as unavailable; the tool will not infer that a bot is stopped just because the local process can't be inspected.
- The tool does not monitor another machine. For remote monitoring, later add authenticated telemetry after reviewing the security and data-minimization model.
- Do not connect an AI agent to order-execution methods. Strategy changes must be tested outside the live account first.


## Optional dashboard heartbeat

After the separate Local Fitted City service has been deployed and its heartbeat endpoint is secured:

1. Copy the service HTTPS origin, e.g. `https://your-service.onrender.com`, into the bot host's local `.env` as `AI_CITY_TELEMETRY_URL`.
2. Copy the service's **`LOCALFITTED_BOT_TELEMETRY_TOKEN`** value into the bot host's `AI_CITY_TELEMETRY_TOKEN`. Do **not** put the commerce/admin token in the trading bot.
3. Restart one bot in a controlled test window. It posts a heartbeat on the main loop and wakes about once per minute when the market is closed so stale processes can be detected.
4. Confirm its heartbeat appears in the city's dashboard; then repeat for the other bots only after they each get a reviewed adapter.

The sender requires HTTPS and catches request failures so telemetry cannot intentionally change trading decisions. It may send bot ID, paper/live mode flag from the selected config, strategy label, symbols, tracked-position count, market-open status, and selected account metrics (equity/buying power) when available. Do not enable it until you have reviewed what the dashboard stores and who has access.

This branch is a draft, not deployed. It only covers the SPY/QQQ bot, not the user's crypto bots. Keep the `main` branch and any running live bot unchanged until the pull request is reviewed and tested in the actual bot environment.

### Reuse the generic client for crypto bots

The reusable `post_heartbeat()` helper can be called from a different bot project once that repository is available. Example:

```python
from ai_city.telemetry import post_heartbeat

post_heartbeat(
    bot_id="crypto-paper-1",
    system="crypto",
    mode="paper",  # use "live" for the live bot
    status="healthy",
    metrics={"equity": 10000, "realized_pnl": 12.50, "open_positions": 1},
)
```

Do not include API keys, order payloads, wallet addresses, exchange tokens, or credentials in metrics. The helper strips secret-like metric keys as an extra safeguard, requires HTTPS, has a short timeout, and returns `False` rather than raising when telemetry is disabled or fails. This sends telemetry only; it does not place, cancel, or alter orders. It can be copied into another repo, but each actual crypto bot needs a small adapter and tests appropriate to its code structure.
