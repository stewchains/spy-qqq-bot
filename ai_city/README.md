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
