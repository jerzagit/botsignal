"""
Flexible BUY/SELL signal parser used by signal providers with similar formats.

It accepts common Telegram variations while requiring all execution-critical
fields: symbol, direction, entry, SL, and at least one real TP price.
"""

import re
import unicodedata
from typing import Optional

from core.signal import Signal

SYMBOL_ALIASES = {
    "gold": "XAUUSD",
    "xauusd": "XAUUSD",
}

PRICE_RE = r"\d{3,5}(?:[.,]\d+|-\d)?(?!\d)"
SYMBOL_RE = r"gold|xauusd|[a-z]{3}usd|usd[a-z]{3}"


def sanitize_text(text: str) -> str:
    """Remove decorative symbols while preserving words, numbers, and separators."""
    text = unicodedata.normalize("NFKC", text)
    text = text.replace("–", "-").replace("—", "-")
    text = text.replace("，", ",").replace("：", ":")
    return "".join(
        ch
        for ch in text
        if unicodedata.category(ch) not in {"Cf", "So", "Sk"}
    )


def normalize_symbol(value: str) -> str:
    raw = value.strip().lower()
    return SYMBOL_ALIASES.get(raw, raw.upper())


def normalize_price(raw: str) -> float:
    value = raw.strip().replace(",", ".")
    if re.fullmatch(r"\d{3,5}-\d", value):
        value = value.replace("-", ".", 1)
    return float(value)


def _entry_range_pattern() -> str:
    return (
        rf"({PRICE_RE})"
        rf"(?:\s*(?:-|to|until)\s*({PRICE_RE}))?"
    )


def _find_header(text: str):
    entry = _entry_range_pattern()
    patterns = [
        rf"\b({SYMBOL_RE})\s+(buy|sell)(?:\s+now)?(?:\s+(?:at|entry))?\s*@?\s*{entry}",
        rf"\b(buy|sell)\s+({SYMBOL_RE})(?:\s+now)?(?:\s+(?:at|entry))?\s*@?\s*{entry}",
        rf"\b(buy|sell)\s+now\s+({SYMBOL_RE})(?:\s+(?:at|entry))?\s*@?\s*{entry}",
    ]
    for index, pattern in enumerate(patterns):
        match = re.search(pattern, text, re.IGNORECASE)
        if not match:
            continue
        groups = match.groups()
        if index == 0:
            symbol, direction, entry_a, entry_b = groups
        else:
            direction, symbol, entry_a, entry_b = groups
        return symbol, direction, entry_a, entry_b
    return None


def parse_signal(text: str) -> Optional[Signal]:
    original = text
    normalized = sanitize_text(text).strip().lower()

    header = _find_header(normalized)
    if not header:
        return None

    symbol_raw, direction_raw, entry_a_raw, entry_b_raw = header
    symbol = normalize_symbol(symbol_raw)
    direction = direction_raw.lower()
    entry_a = normalize_price(entry_a_raw)
    entry_b = normalize_price(entry_b_raw) if entry_b_raw else entry_a
    entry_low = min(entry_a, entry_b)
    entry_high = max(entry_a, entry_b)
    entry_mid = round((entry_low + entry_high) / 2, 5)

    sl_match = re.search(
        rf"\b(?:sl|s/l|stop\s*loss|stoploss|s)\s*[:=\-]?\s*({PRICE_RE})",
        normalized,
        re.IGNORECASE,
    )
    if not sl_match:
        return None
    sl = normalize_price(sl_match.group(1))

    tps = [
        normalize_price(value)
        for value in re.findall(
            rf"\b(?:tp|take\s*profit|target)\s*(?:#?\d+\s*[:=\-]\s*)?({PRICE_RE})",
            normalized,
            re.IGNORECASE,
        )
    ]
    if not tps:
        return None

    if direction == "sell":
        if not (sl > entry_mid and all(tp < entry_mid for tp in tps)):
            return None
    else:
        if not (sl < entry_mid and all(tp > entry_mid for tp in tps)):
            return None

    return Signal(
        symbol=symbol,
        direction=direction,
        entry_low=entry_low,
        entry_high=entry_high,
        sl=sl,
        tps=tps,
        raw_text=original,
    )
