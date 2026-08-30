"""Fill price policies for simulated entry (strategy unchanged)."""

from __future__ import annotations

from typing import Literal

FillPolicy = Literal[
    "CANDIDATE_ENTRY_AT_SIGNAL_BAR_CLOSE",
    "SIGNAL_BAR_CLOSE",
    "NEXT_M15_OPEN",
    "CANDIDATE_PRICE",
]

# Production: execute_trade sends market order at live ask/bid at order_send time.
# Strategy candidate entry was ask/bid at decision build; proximity skipped.
# Closest OHLC-only proxy among these is CANDIDATE_PRICE / SIGNAL_BAR_CLOSE
# (replay historically used bid=ask=close for candidate entry).


def resolve_fill_price(
    policy: str,
    *,
    candidate_entry: float,
    signal_bar_close: float,
    next_bar_open: float | None,
) -> tuple[float, str]:
    p = policy.upper()
    if p in ("CANDIDATE_ENTRY_AT_SIGNAL_BAR_CLOSE", "CANDIDATE_PRICE"):
        return float(candidate_entry), "APPROXIMATED"
    if p == "SIGNAL_BAR_CLOSE":
        return float(signal_bar_close), "APPROXIMATED"
    if p == "NEXT_M15_OPEN":
        if next_bar_open is None:
            return float(candidate_entry), "APPROXIMATED_FALLBACK_CANDIDATE"
        return float(next_bar_open), "APPROXIMATED"
    raise ValueError(f"Unknown fill policy: {policy}")
