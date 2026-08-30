"""Isolated simulated trading account (no live MT5 / DB)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone


@dataclass
class SimulatedAccount:
    initial_balance: float
    currency: str = "USD"
    balance: float = 0.0
    equity: float = 0.0
    realized_pnl: float = 0.0
    peak_equity: float = 0.0
    max_drawdown_usd: float = 0.0
    max_drawdown_pct: float = 0.0
    daily_realized_loss: float = 0.0
    daily_loss_day: date | None = None
    margin_used: float = 0.0

    def __post_init__(self) -> None:
        if self.initial_balance <= 0:
            raise ValueError("initial_balance must be > 0")
        if self.balance == 0.0 and self.equity == 0.0:
            self.balance = float(self.initial_balance)
            self.equity = float(self.initial_balance)
            self.peak_equity = float(self.initial_balance)

    def _roll_daily(self, ts: datetime) -> None:
        d = ts.astimezone(timezone.utc).date()
        if self.daily_loss_day != d:
            self.daily_loss_day = d
            self.daily_realized_loss = 0.0

    def ensure_day(self, ts: datetime) -> None:
        """Roll daily-loss accumulator to the UTC calendar day of `ts`."""
        self._roll_daily(ts)

    def free_margin(self) -> float:
        return max(0.0, self.equity - self.margin_used)

    def margin_level(self) -> float:
        if self.margin_used <= 0:
            return 0.0  # production treats margin==0 as skip (always allow)
        return (self.equity / self.margin_used) * 100.0

    def mark_equity(self, floating_pnl: float, ts: datetime | None = None) -> None:
        self.equity = self.balance + floating_pnl
        if self.equity > self.peak_equity:
            self.peak_equity = self.equity
        dd = self.peak_equity - self.equity
        if dd > self.max_drawdown_usd:
            self.max_drawdown_usd = dd
        if self.peak_equity > 0:
            pct = 100.0 * dd / self.peak_equity
            if pct > self.max_drawdown_pct:
                self.max_drawdown_pct = pct

    def realize(self, pnl: float, ts: datetime) -> None:
        self._roll_daily(ts)
        self.balance += pnl
        self.realized_pnl += pnl
        if pnl < 0:
            self.daily_realized_loss += abs(pnl)
        self.mark_equity(0.0, ts)  # floating applied by caller after

    def snapshot(self) -> dict:
        return {
            "balance": round(self.balance, 2),
            "equity": round(self.equity, 2),
            "realized_pnl": round(self.realized_pnl, 2),
            "daily_realized_loss": round(self.daily_realized_loss, 2),
            "margin_used": round(self.margin_used, 2),
            "margin_level": round(self.margin_level(), 2),
            "peak_equity": round(self.peak_equity, 2),
            "max_drawdown_usd": round(self.max_drawdown_usd, 2),
            "max_drawdown_pct": round(self.max_drawdown_pct, 4),
        }
