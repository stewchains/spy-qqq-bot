"""Optional outbound health telemetry for the AI City dashboard.

No-op unless AI_CITY_TELEMETRY_URL and AI_CITY_TELEMETRY_TOKEN are configured.
Sends status/metrics only. Never sends credentials, orders, or strategy signals.
"""
from __future__ import annotations

import logging
import os
from typing import Any

log = logging.getLogger("ai_city.telemetry")


def _sanitize_metrics(value: Any) -> Any:
    """Remove secret-like keys from telemetry objects before anything leaves the bot host."""
    blocked = ("secret", "api_key", "access_token", "refresh_token", "password", "credential", "authorization", "private_key", "token")
    if isinstance(value, dict):
        return {
            str(key): _sanitize_metrics(child)
            for key, child in value.items()
            if not any(term in str(key).lower() for term in blocked)
        }
    if isinstance(value, list):
        return [_sanitize_metrics(item) for item in value[:100]]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)[:500]


def post_heartbeat(
    *,
    bot_id: str,
    system: str,
    mode: str,
    status: str,
    metrics: dict[str, Any] | None = None,
) -> bool:
    """Generic best-effort heartbeat client for any bot repo; disabled without env vars."""
    url = os.getenv("AI_CITY_TELEMETRY_URL", "").strip().rstrip("/")
    token = os.getenv("AI_CITY_TELEMETRY_TOKEN", "").strip()
    if not url or not token:
        return False
    if not url.startswith("https://"):
        log.warning("AI City heartbeat disabled: telemetry URL must use HTTPS")
        return False

    safe_metrics = _sanitize_metrics(metrics or {})
    if len(__import__("json").dumps(safe_metrics, separators=(",", ":"), default=str)) > 9000:
        log.warning("AI City heartbeat skipped: sanitized metrics exceed 9 KB")
        return False
    payload = {
        "bot_id": str(bot_id)[:100],
        "system": str(system)[:80],
        "mode": mode if mode in {"paper", "live", "monitor_only", "unknown"} else "unknown",
        "status": status if status in {"healthy", "degraded", "stopped", "unknown"} else "unknown",
        "metrics": safe_metrics,
    }
    try:
        import requests

        timeout = min(max(float(os.getenv("AI_CITY_TELEMETRY_TIMEOUT", "1.0")), 0.5), 3.0)
        response = requests.post(
            f"{url}/api/bots/heartbeat",
            headers={"X-API-Key": token},
            json=payload,
            timeout=timeout,
        )
        response.raise_for_status()
        return True
    except Exception as exc:  # Telemetry failure must never interrupt a bot's operation.
        log.warning("AI City heartbeat failed (%s); bot behavior unchanged", type(exc).__name__)
        return False


def send_bot_heartbeat(
    *,
    bot_id: str,
    cfg: dict[str, Any],
    state: dict[str, Any],
    account: dict[str, Any] | None,
    status: str,
    market_open: bool | None,
) -> bool:
    """Adapter for the existing SPY/QQQ bot's state/config structure."""
    mode_cfg = cfg.get("mode", {})
    paper_value = mode_cfg.get("paper")
    mode = "paper" if paper_value is True else "live" if paper_value is False else "unknown"
    metrics: dict[str, Any] = {
        "strategy_type": cfg.get("strategy", {}).get("type", "unknown"),
        "symbols": cfg.get("symbols", []),
        "tracked_strategy_positions": len(state),
        "market_open": market_open,
    }
    if account:
        for field in ("equity", "last_equity", "buying_power"):
            value = account.get(field)
            if isinstance(value, (int, float)):
                metrics[field] = round(float(value), 2)
    return post_heartbeat(
        bot_id=bot_id, system="alpaca_options", mode=mode, status=status, metrics=metrics
    )
