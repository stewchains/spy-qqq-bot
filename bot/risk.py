"""Risk manager: position sizing and the circuit breakers that stop the bot on bad days."""
import math
from datetime import datetime, timedelta


class RiskManager:
    def __init__(self, cfg: dict):
        self.r = cfg["risk"]
        self.o = cfg["options"]
        self.day = None
        self.start_equity = 0.0
        self.trades_today = 0
        self.last_loss_at: datetime | None = None
        self.halted_reason = ""

    def new_day(self, day, equity: float):
        if day != self.day:
            self.day, self.start_equity = day, equity
            self.trades_today, self.last_loss_at, self.halted_reason = 0, None, ""

    # ---------------------------------------------------------------- gates
    def can_open(self, now: datetime, equity: float, open_positions: int) -> tuple[bool, str]:
        if self.halted_reason:
            return False, self.halted_reason
        if self.start_equity and (equity - self.start_equity) / self.start_equity <= -self.r["daily_max_loss_pct"]:
            self.halted_reason = f"daily loss limit hit ({(equity / self.start_equity - 1):.2%}) — done for today"
            return False, self.halted_reason
        if self.trades_today >= self.r["max_trades_per_day"]:
            return False, "max trades for today reached"
        if open_positions >= self.r["max_open_positions"]:
            return False, "max open positions"
        if self.last_loss_at and now < self.last_loss_at + timedelta(minutes=self.r["cooldown_after_loss_min"]):
            return False, "cooling down after a loss"
        return True, ""

    def record_open(self):
        self.trades_today += 1

    def record_close(self, pnl: float, now: datetime):
        if pnl < 0:
            self.last_loss_at = now

    # ---------------------------------------------------------------- sizing
    def option_contracts(self, equity: float, premium: float) -> int:
        """Contracts so that hitting the option stop loses <= risk_per_trade, and cost <= max_position."""
        if premium <= 0:
            return 0
        cost = premium * 100
        by_risk = (equity * self.r["risk_per_trade_pct"]) / (cost * self.o["stop_loss_pct"])
        by_cap = (equity * self.r["max_position_pct"]) / cost
        return max(0, math.floor(min(by_risk, by_cap)))

    def share_qty(self, equity: float, entry: float, stop: float, buying_power: float) -> int:
        per_share = abs(entry - stop)
        if per_share <= 0 or entry <= 0:
            return 0
        by_risk = (equity * self.r["risk_per_trade_pct"]) / per_share
        by_cap = min(equity * self.r["max_share_position_pct"], buying_power * 0.95) / entry
        return max(0, math.floor(min(by_risk, by_cap)))
