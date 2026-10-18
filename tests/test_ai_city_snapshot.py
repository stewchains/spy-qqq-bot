import csv
import json
from pathlib import Path

from ai_city.snapshot_bot import summarize_journal, summarize_state


def test_summarize_journal_with_wins_losses_and_drawdown(tmp_path: Path):
    path = tmp_path / "trades.csv"
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["pnl"])
        writer.writeheader()
        writer.writerows([{"pnl": "10"}, {"pnl": "-4"}, {"pnl": "5"}, {"pnl": "-20"}])
    result = summarize_journal(path)
    assert result["available"] is True
    assert result["trade_count"] == 4
    assert result["realized_pnl_sum_usd"] == -9.0
    assert result["win_rate"] == 0.5
    assert result["profit_factor"] == 0.75
    assert result["max_realized_pnl_drawdown_usd"] == -20.0


def test_summarize_journal_missing_file(tmp_path: Path):
    result = summarize_journal(tmp_path / "missing.csv")
    assert result["available"] is False
    assert result["trade_count"] == 0


def test_summarize_state_counts_records(tmp_path: Path):
    path = tmp_path / "state.json"
    path.write_text(json.dumps({"A": {"qty": 1}, "B": {"qty": 2}}), encoding="utf-8")
    result = summarize_state(path)
    assert result["available"] is True
    assert result["open_position_records"] == 2


def test_summarize_state_missing_file(tmp_path: Path):
    result = summarize_state(tmp_path / "missing.json")
    assert result["available"] is False
