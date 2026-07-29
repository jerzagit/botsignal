"""
Parser profile registry for Telegram signal sources.
"""

from typing import Optional

from core.signal import Signal, parse_signal


def parse_with_profile(profile: str, text: str) -> Optional[Signal]:
    """
    Route a Telegram message through the parser configured for its source.

    New provider formats can be added here without changing listener or trade
    execution code. For now, default/hafiz use the existing parser.
    """
    normalized = (profile or "hafiz").lower()
    if normalized in {"hafiz", "default"}:
        return parse_signal(text)
    if normalized == "bobby":
        from core.parsers.bobby import parse_signal as parse_bobby_signal
        return parse_bobby_signal(text)
    return parse_signal(text)
