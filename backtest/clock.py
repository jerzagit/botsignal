"""
Controlled clocks for LIVE vs historical replay.

LIVE behaviour today still uses datetime.now()/time.time() directly in many
modules. These classes are scaffolding only until Phase A wires them in.

ReplayClock must never expose a timestamp beyond the current replay cursor.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import time
from typing import Protocol


class Clock(Protocol):
    def now(self) -> datetime: ...
    def time(self) -> float: ...
    def utc_hour(self) -> int: ...


class LiveClock:
    """Wall-clock time — equivalent to production behaviour."""

    def now(self) -> datetime:
        return datetime.now(timezone.utc)

    def time(self) -> float:
        return time.time()

    def utc_hour(self) -> int:
        return time.gmtime().tm_hour


@dataclass
class ReplayClock:
    """
    Deterministic historical clock.

    `cursor` is the current replay timestamp (UTC). Strategy code may only
    observe this moment — never future bars beyond the cursor.
    """

    cursor: datetime

    def __post_init__(self) -> None:
        if self.cursor.tzinfo is None:
            self.cursor = self.cursor.replace(tzinfo=timezone.utc)

    def now(self) -> datetime:
        return self.cursor

    def time(self) -> float:
        return self.cursor.timestamp()

    def utc_hour(self) -> int:
        return self.cursor.astimezone(timezone.utc).hour

    def advance_to(self, ts: datetime) -> None:
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        if ts < self.cursor:
            raise ValueError(
                f"ReplayClock cannot move backwards: {ts.isoformat()} < {self.cursor.isoformat()}"
            )
        self.cursor = ts
