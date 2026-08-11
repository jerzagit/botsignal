"""TradeWhatYouSee parser profile."""

from typing import Optional

from core.signal import Signal


def parse_signal(text: str) -> Optional[Signal]:
    from core.parsers.flexible import parse_signal as parse_flexible_signal

    return parse_flexible_signal(text)
