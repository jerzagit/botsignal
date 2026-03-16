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


@dataclass
class CloseAlert:
    symbol:   Optional[str]   # None means "all open positions"
    reason:   str             # "setup_failed" | "early_tp" | "collect_profit"
    raw_text: str


def parse_close_alert(text: str) -> Optional[CloseAlert]:
    """
    Detect Hafiz's early-close / early-profit messages.

    Supported triggers (case insensitive):
        Setup failed:
            "setup failed"
            "xauusd setup failed"

        Early profit / collect:
            "profit Xpips"
            "siapa nak collect"
            "collect dulu"
            "dipersialakan"
            "take profit now"
            "early tp"

    Returns CloseAlert or None.
    """
    lower = text.strip().lower()

    # ── Setup failed ─────────────────────────────────────────────────────────
    if "setup failed" in lower:
        symbol = _extract_symbol(lower)
        return CloseAlert(symbol=symbol, reason="setup_failed", raw_text=text)

    # ── Collect profit (70% close, 30% breakeven) ─────────────────────────────
    collect_triggers = [
        "collect profit",
        "mau collect",
        "siapa mau collect",
        "collect and",
    ]
    for trigger in collect_triggers:
        if trigger in lower:
            symbol = _extract_symbol(lower)
            return CloseAlert(symbol=symbol, reason="collect_profit", raw_text=text)

    # ── Early profit (keep top N at breakeven, close rest) ───────────────────
    profit_triggers = [
        "siapa nak collect",
        "collect dulu",
        "dipersilakan",
        "take profit now",
        "early tp",
        re.compile(r'profit\s+\d+\s*pips?'),
    ]
    for trigger in profit_triggers:
        if isinstance(trigger, str) and trigger in lower:
            symbol = _extract_symbol(lower)
            return CloseAlert(symbol=symbol, reason="early_tp", raw_text=text)
        if isinstance(trigger, re.Pattern) and trigger.search(lower):
            symbol = _extract_symbol(lower)
            return CloseAlert(symbol=symbol, reason="early_tp", raw_text=text)

    return None


def _extract_symbol(lower: str) -> Optional[str]:
    """Extract a trading symbol from a lowercase message string."""
    m = re.search(r'\b(xauusd|eurusd|gbpusd|usdjpy|[a-z]{3}usd|usd[a-z]{3})\b', lower)
    return m.group(1).upper() if m else None


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
