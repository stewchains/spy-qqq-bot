"""Optional outbound health telemetry for the AI City dashboard.

No-op unless AI_CITY_TELEMETRY_URL and AI_CITY_TELEMETRY_TOKEN are configured.
Sends status/metrics only. Never sends credentials, orders, or strategy signals.
"""
from __future__ import annotations

import logging
import os
from typing import Any

log = logging.getLogger("ai_city.telemetry")


def send_bot_heartbeat(
    *,
    bot_id: str,
    cfg: dict[str, Any],
    state: dict[str, Any],
    account: dict[str, Any] | None,
    status: str,
    market_open: bool | None,
) -> bool:
    url = os.getenv("AI_CITY_TELEMETRY_URL", "").strip().rstrip("/")
    token = os.getenv("AI_CITY_TELEMETRY_TOKEN", "").strip()
    if not url or not token:
        return False

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
        # Only non-secret, read-only account summary fields are sent.
        for field in ("equity", "last_equity", "buying_power"):
            value = account.get(field)
            if isinstance(value, (int, float)):
                metrics[field] = round(float(value), 2)
    payload = {
        "bot_id": bot_id,
        "system": "alpaca_options",
        "mode": mode,
        "status": status if status in {"healthy", "degraded", "stopped"} else "unknown",
        "metrics": metrics,
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
    except Exception as exc:  # Telemetry failure must never interrupt trading logic.
        log.warning("AI City heartbeat failed (%s); trading behavior unchanged", type(exc).__name__)
        return False
