# AI City Integration Plan for the Existing SPY/QQQ Bot

## Findings from the connected repository

- The accessible repository is `stewchains/spy-qqq-bot`, a Python project using `alpaca-py`.
- Its README describes two option-bot configurations: `config.yaml` and `config_swing.yaml`; the latter uses a separate Alpaca paper-account key suffix.
- The committed `config.yaml` sets `mode.paper: true`. This is only the repository default; it does not reveal local config changes, local environment variables, or running process state.
- The existing broker wrapper includes read-only `account()` and `positions()` methods alongside order-execution methods. The starter snapshot uses only the read methods when explicitly requested.
- This repository is an options bot. The five paper crypto bots and one live crypto bot previously mentioned were not visible in the repositories returned by the connected GitHub account.

## Stage 1: observe only

1. Run `python ai_city/snapshot_bot.py --config config.yaml` to summarize local config metadata, local state-file record count, and realized-trade journal statistics. No broker API call is made.
2. Optionally run with `--account` to fetch account/positions through read-only broker methods. This uses local credentials and writes a private local JSON report.
3. Compare the snapshot with the existing daily report. Verify config/account mode first.
4. Do not upload the report until an authenticated, secure telemetry transport and data-minimization rules are reviewed.

## Stage 2: monitoring dashboard

- Ingest snapshots into a secured backend over TLS.
- Track account/bot identifiers, heartbeat freshness, P&L, drawdown, exposure, errors, last successful cycle, and data freshness.
- Alert on stale telemetry, failed API calls, unexpected process exits, risk-limit events, or mismatched config.
- Keep credentials in the bot host/secrets manager; never send API keys to the dashboard.

## Stage 3: research and controlled improvement

- Establish frozen baselines and appropriate benchmarks for every strategy.
- Record hypothesis, parameter set, commit, data range, fees/slippage assumptions, and outcome for every experiment.
- Require out-of-sample testing and subsequent paper-trading validation.
- Agents may suggest changes and open pull requests; they must not deploy to or modify the live bot without explicit approval.
- Keep the kill switch, position limits, daily loss rules, and rollback path independent of agents.

## Separate Local Fitted City

The GitHub connection currently exposes no Local Fitted source repository and no repository for the six crypto bots. The public Netlify URL alone is not enough to safely modify the website backend. Needed artifacts are the Local Fitted source repo or a redacted archive, inventory spreadsheet export, and the missing crypto-bot source/config formats. Never paste secrets or API keys into chat.
