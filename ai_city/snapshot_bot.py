"""Read-only local snapshot for the AI City monitoring project.

Default mode reads local config/state/journal files only.
With --account, calls only existing Broker.account() and Broker.positions(), both read-only.
Never submits, changes, cancels, or closes orders. Never uploads the snapshot.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]


def _number(value: Any) -> float | None:
    try:
        out = float(value)
        return out if math.isfinite(out) else None
    except (TypeError, ValueError):
        return None


def summarize_journal(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"available": False, "trade_count": 0, "reason": "journal_not_found"}
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    pnls = [_number(row.get("pnl")) for row in rows]
    pnls = [p for p in pnls if p is not None]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    gross_loss = abs(sum(losses))
    cumulative = 0.0
    peak = 0.0
    max_drawdown = 0.0
    for pnl in pnls:
        cumulative += pnl
        peak = max(peak, cumulative)
        max_drawdown = min(max_drawdown, cumulative - peak)
    return {
        "available": True,
        "trade_count": len(pnls),
        "realized_pnl_sum_usd": round(sum(pnls), 2),
        "win_rate": round(len(wins) / len(pnls), 4) if pnls else None,
        "profit_factor": round(sum(wins) / gross_loss, 4) if gross_loss else None,
        "max_realized_pnl_drawdown_usd": round(max_drawdown, 2),
        "note": "Closed-trade journal only; not account equity drawdown or a performance guarantee.",
    }


def summarize_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"available": False, "open_position_records": None, "reason": "state_file_not_found"}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"available": False, "open_position_records": None, "reason": f"state_unreadable:{type(exc).__name__}"}
    return {
        "available": True,
        "open_position_records": len(raw) if isinstance(raw, dict) else None,
        "note": "Counts local bot-state records; compare with broker positions to find reconciliation differences.",
    }


def _serialize_position(symbol: str, pos: Any) -> dict[str, Any]:
    def value(name: str) -> Any:
        raw = getattr(pos, name, None)
        if raw is None:
            return None
        if name == "side":
            return str(raw)
        return _number(raw)
    return {
        "symbol": symbol,
        "qty": value("qty"),
        "side": value("side"),
        "market_value": value("market_value"),
        "unrealized_pl": value("unrealized_pl"),
        "current_price": value("current_price"),
    }


def build_snapshot(config_path: Path, include_account: bool = False) -> dict[str, Any]:
    try:
        import yaml
    except ImportError as exc:
        raise SystemExit("PyYAML is required; install project dependencies with: pip install -r requirements.txt") from exc

    cfg = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    name = cfg.get("bot_name")
    suffix = f"_{name}" if name else ""
    state_path = ROOT / f"state{suffix}.json"
    journal_path = ROOT / f"trades{suffix}.csv"
    mode = cfg.get("mode", {})
    strategy = cfg.get("strategy", {})
    snap: dict[str, Any] = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "source": "local_files",
        "bot": {
            "config_file": config_path.name,
            "bot_name": name or "default",
            "paper_mode_in_config": mode.get("paper"),
            "dry_run_in_config": mode.get("dry_run"),
            "strategy_type": strategy.get("type", "unknown"),
            "symbols": cfg.get("symbols", []),
        },
        "local_state": summarize_state(state_path),
        "trade_journal": summarize_journal(journal_path),
        "broker_account": None,
        "broker_positions": None,
        "read_only": True,
        "openai_api_used": False,
    }

    if include_account:
        try:
            from bot import load_keys
            from bot.broker import Broker
            key, secret = load_keys(cfg.get("alpaca_keys"))
            broker = Broker(key, secret, cfg)
            account = broker.account()
            positions = broker.positions()
            snap["source"] = "local_files_plus_read_only_alpaca_api"
            snap["broker_account"] = {
                "equity": _number(account.get("equity")),
                "last_equity": _number(account.get("last_equity")),
                "buying_power": _number(account.get("buying_power")),
                "blocked": account.get("blocked"),
                "options_level": account.get("options_level"),
            }
            snap["broker_positions"] = [
                _serialize_position(symbol, pos) for symbol, pos in positions.items()
            ]
            snap["account_read_only_notice"] = (
                "Only account and positions read methods were called; no orders were submitted or modified. "
                "Verify configured account mode and local credentials before running."
            )
        except SystemExit as exc:
            snap["broker_account_error"] = f"credentials_not_available:{str(exc)[:160]}"
        except Exception as exc:  # noqa: BLE001 - avoid exposing headers/tokens in errors
            snap["broker_account_error"] = f"read_failed:{type(exc).__name__}"
    return snap


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="config.yaml", help="Config path relative to repository root unless absolute")
    parser.add_argument("--output", help="Output JSON path; defaults to reports/ai_city/<config-stem>-snapshot.json")
    parser.add_argument("--account", action="store_true", help="Optionally query Alpaca account/positions using read-only calls")
    args = parser.parse_args()

    config_path = Path(args.config)
    if not config_path.is_absolute():
        config_path = ROOT / config_path
    if not config_path.exists():
        print(f"Config file not found: {config_path}", file=sys.stderr)
        return 2

    snapshot = build_snapshot(config_path, include_account=args.account)
    output_path = Path(args.output) if args.output else ROOT / "reports" / "ai_city" / f"{config_path.stem}-snapshot.json"
    if not output_path.is_absolute():
        output_path = ROOT / output_path
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(snapshot, indent=2, default=str) + "
", encoding="utf-8")
    print(f"Wrote read-only snapshot: {output_path}")
    print(f"Strategy: {snapshot['bot']['strategy_type']} | paper_mode_in_config={snapshot['bot']['paper_mode_in_config']}")
    print(f"Journal trades: {snapshot['trade_journal']['trade_count']} | realized P&L: {snapshot['trade_journal'].get('realized_pnl_sum_usd')}")
    print("No orders were submitted, modified, or canceled. Snapshot was not uploaded.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
