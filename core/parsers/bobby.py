"""
Bobby Live Trade parser.

Supported examples:
    Gold sell now @4078
    S:4090
    Tp-4072
    Tp-4066
"""

import re
from typing import Optional

from core.signal import Signal

SYMBOL_ALIASES = {
    "gold": "XAUUSD",
    "xauusd": "XAUUSD",
}


def _normalize_symbol(value: str) -> str:
    raw = value.strip().lower()
    return SYMBOL_ALIASES.get(raw, raw.upper())


def parse_signal(text: str) -> Optional[Signal]:
    original = text
    normalized = text.strip().lower()

    header = re.search(
        r"\b(gold|xauusd)\s+(buy|sell)\s+now\s+@?\s*([\d.]+)",
        normalized,
        re.IGNORECASE,
    )
    if not header:
        return None

    symbol = _normalize_symbol(header.group(1))
    direction = header.group(2).lower()
    entry = float(header.group(3))

    sl_match = re.search(r"\b(?:s|sl)\s*[:\-]?\s*([\d.]+)", normalized, re.IGNORECASE)
    if not sl_match:
        return None
    sl = float(sl_match.group(1))

    tps = [
        float(v)
        for v in re.findall(r"\btp\s*[:\-]?\s*([\d.]+)", normalized, re.IGNORECASE)
    ]
    if not tps:
        return None

    if direction == "sell":
        if not (all(tp < entry for tp in tps) and sl > entry):
            return None
    else:
        if not (all(tp > entry for tp in tps) and sl < entry):
            return None

    return Signal(
        symbol=symbol,
        direction=direction,
        entry_low=entry,
        entry_high=entry,
        sl=sl,
        tps=tps,
        raw_text=original,
    )
