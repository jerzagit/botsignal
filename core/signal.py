"""
core/signal.py
Signal data model + parser for mentor's message format.

Supported format:
    xauusd sell @5096-5100
    sl 5103
    tp 5092
    tp 5090
    Trade At Your Own Risk
"""

import re
import time
import json
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Signal:
    symbol:     str
    direction:  str         # "buy" | "sell"
    entry_low:  float
    entry_high: float
    sl:         float
    tps:        list        # [tp1, tp2, ...]
    raw_text:   str
    created_at: float = field(default_factory=time.time)

    @property
    def entry_mid(self) -> float:
        return round((self.entry_low + self.entry_high) / 2, 5)

    @property
    def sl_pips(self) -> float:
        """Approximate SL distance in price units (not broker pips)."""
        ref = self.entry_mid
        return abs(ref - self.sl)

    @property
    def is_range_entry(self) -> bool:
        return self.entry_low != self.entry_high

    def to_dict(self) -> dict:
        return {
            "symbol":     self.symbol,
            "direction":  self.direction,
            "entry_low":  self.entry_low,
            "entry_high": self.entry_high,
            "sl":         self.sl,
            "tps":        self.tps,
            "created_at": self.created_at,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict())


def parse_signal(text: str) -> Optional[Signal]:
    """
    Parse a Telegram message into a Signal.
    Returns None if the message doesn't look like a valid trade signal.
    """
    original = text
    text = text.strip().lower()

    # ── Header: SYMBOL BUY/SELL @PRICE or @PRICE-PRICE ──────────────────────
    header = re.search(
        r'([a-z]{3,10})\s+(buy|sell)\s+@?([\d.]+)(?:\s*[-–]\s*([\d.]+))?',
        text
    )
    if not header:
        return None

    symbol     = header.group(1).upper()
    direction  = header.group(2)
    entry_low  = float(header.group(3))
    entry_high = float(header.group(4)) if header.group(4) else entry_low

    # ── Stop Loss ────────────────────────────────────────────────────────────
    sl_match = re.search(r'\bsl\s+([\d.]+)', text)
    if not sl_match:
        return None
    sl = float(sl_match.group(1))

    # ── Take Profits (one or many) ───────────────────────────────────────────
    tps = [float(v) for v in re.findall(r'\btp\s+([\d.]+)', text)]
    if not tps:
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
