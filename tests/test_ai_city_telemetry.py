from __future__ import annotations

from types import SimpleNamespace

from ai_city.telemetry import send_bot_heartbeat


def test_heartbeat_disabled_when_no_settings(monkeypatch):
    monkeypatch.delenv("AI_CITY_TELEMETRY_URL", raising=False)
    monkeypatch.delenv("AI_CITY_TELEMETRY_TOKEN", raising=False)
    assert send_bot_heartbeat(bot_id="paper-test", cfg={}, state={}, account=None,
                              status="healthy", market_open=False) is False


def test_heartbeat_refuses_non_https_url(monkeypatch):
    monkeypatch.setenv("AI_CITY_TELEMETRY_URL", "http://localhost:8000")
    monkeypatch.setenv("AI_CITY_TELEMETRY_TOKEN", "test-token")
    assert send_bot_heartbeat(bot_id="paper-test", cfg={}, state={}, account=None,
                              status="healthy", market_open=False) is False


def test_heartbeat_sends_only_non_secret_read_metrics(monkeypatch):
    monkeypatch.setenv("AI_CITY_TELEMETRY_URL", "https://city.example/")
    monkeypatch.setenv("AI_CITY_TELEMETRY_TOKEN", "test-token")
    captured = {}

    def fake_post(url, headers, json, timeout):
        captured.update(url=url, headers=headers, payload=json, timeout=timeout)
        return SimpleNamespace(raise_for_status=lambda: None)

    import requests
    monkeypatch.setattr(requests, "post", fake_post)

    ok = send_bot_heartbeat(
        bot_id="crypto-paper-test",
        cfg={"mode": {"paper": True}, "strategy": {"type": "simulation"}, "symbols": ["BTC/USD"]},
        state={"position-1": {"qty": 1}},
        account={"equity": 10500.123, "buying_power": 9000, "api_key": "must-not-be-sent"},
        status="healthy",
        market_open=True,
    )
    assert ok is True
    assert captured["url"] == "https://city.example/api/bots/heartbeat"
    assert captured["headers"] == {"X-API-Key": "test-token"}
    assert captured["payload"]["mode"] == "paper"
    assert captured["payload"]["metrics"]["equity"] == 10500.12
    assert "api_key" not in captured["payload"]["metrics"]
