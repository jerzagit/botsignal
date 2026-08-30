"""
Provider interfaces for historical replay (scaffolding only).

Concrete LIVE adapters will wrap existing MT5/Telegram code later.
Concrete REPLAY adapters must be physically unable to call production sinks.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol


@dataclass(frozen=True)
class Candle:
    time: int          # unix seconds (bar open) — UTC epoch
    open: float
    high: float
    low: float
    close: float
    timeframe: str = ""  # e.g. M15, H1, H4
    spread: int | None = None  # MT5 points; None if absent
    tick_volume: int | None = None
    real_volume: int | None = None


@dataclass
class SimulatedTrade:
    trade_id: str
    symbol: str
    direction: str
    entry: float
    sl: float
    tp: float | None
    lot: float
    opened_at: datetime
    setup_id: str | None = None
    candidate_id: str | None = None
    reason: str = ""
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class CapturedNotification:
    timestamp: datetime
    notification_type: str
    message: str
    trigger: str = ""
    recipient_scope: str = "YOUR_CHAT_ID"
    related_setup: str | None = None
    related_candidate: str | None = None
    related_trade: str | None = None
    would_send: bool = True
    sent: bool = False  # always False in replay collector


class MarketDataProvider(Protocol):
    def get_candles(self, symbol: str, timeframe: str, count: int) -> list[Candle]: ...
    def get_tick(self, symbol: str) -> tuple[float, float]: ...  # bid, ask


class ExecutionProvider(Protocol):
    def open_trade(self, **kwargs) -> SimulatedTrade | str: ...
    def close_trade(self, trade_id: str, **kwargs) -> str: ...
    def modify_sl_tp(self, trade_id: str, sl: float | None, tp: float | None) -> str: ...


class NotificationProvider(Protocol):
    def send(self, notification_type: str, message: str, **kwargs) -> None: ...


class EventJournal(Protocol):
    def emit(self, event: str, **payload) -> None: ...
