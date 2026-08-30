"""Timeframe period helpers for historical replay.

MT5 rate['time'] is the bar OPEN time (Unix seconds, UTC basis — see
docs/BACKTEST_TIMEZONE.md). A bar is CLOSED at open_time + period_seconds.
"""

from __future__ import annotations

PERIOD_SECONDS: dict[str, int] = {
    "M1": 60,
    "M5": 300,
    "M15": 900,
    "M30": 1800,
    "H1": 3600,
    "H4": 14400,
    "D1": 86400,
}


def period_seconds(timeframe: str) -> int:
    key = timeframe.strip().upper()
    if key not in PERIOD_SECONDS:
        raise ValueError(f"Unsupported timeframe: {timeframe!r}")
    return PERIOD_SECONDS[key]


def bar_close_unix(open_time: int, timeframe: str) -> int:
    return int(open_time) + period_seconds(timeframe)


def is_bar_closed(open_time: int, timeframe: str, cursor_unix: float | int) -> bool:
    """True iff the bar that opened at open_time is fully closed by cursor."""
    return bar_close_unix(open_time, timeframe) <= int(cursor_unix)
