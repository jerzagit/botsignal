"""Replay notification collector — never sends Telegram."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


# Inventory: only mark PRODUCTION_NOTIFICATION when live code actually sends.
PRODUCTION_MAPPED_EVENTS = {
    # Strategy path today mainly returns strings / remembers decision;
    # execute_trade success returns Telegram-formatted text to callers that may send.
    # Conservative: candidate block/execute from execute_trade return path are
    # PRODUCTION_NOTIFICATION only when watcher/strategy would notify — strategy
    # scan_once does NOT always Telegram on WAIT/SKIP. Mark trade lifecycle as BACKTEST_ONLY
    # unless clearly wired.
}


@dataclass
class CapturedNote:
    timestamp: str
    event: str
    classification: str  # PRODUCTION_NOTIFICATION | BACKTEST_ONLY_EVENT
    message: str
    related_candidate: str | None = None
    related_trade: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "event": self.event,
            "classification": self.classification,
            "message": self.message,
            "related_candidate": self.related_candidate,
            "related_trade": self.related_trade,
            "meta": self.meta,
            "would_send": self.classification == "PRODUCTION_NOTIFICATION",
            "sent": False,
        }


class ReplayNotificationCollector:
    def __init__(self) -> None:
        self.notes: list[CapturedNote] = []

    def emit(
        self,
        timestamp: str,
        event: str,
        message: str,
        *,
        classification: str = "BACKTEST_ONLY_EVENT",
        candidate_id: str | None = None,
        trade_id: str | None = None,
        **meta: Any,
    ) -> None:
        self.notes.append(
            CapturedNote(
                timestamp=timestamp,
                event=event,
                classification=classification,
                message=message,
                related_candidate=candidate_id,
                related_trade=trade_id,
                meta=meta,
            )
        )
