"""SL/TP outcome resolution on historical OHLC (no look-ahead past cursor)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from backtest.interfaces import Candle

Outcome = Literal[
    "SL_HIT",
    "TP_HIT",
    "AMBIGUOUS_INTRABAR",
    "OPEN",
    "DATA_GAP",
]


@dataclass
class BarrierHit:
    outcome: Outcome
    exit_price: float | None
    bar_open_time: int | None
    note: str = ""


def check_bar_barriers(
    direction: str,
    sl: float,
    tp: float,
    bar: Candle,
    *,
    intrabar_policy: str = "conservative",
) -> BarrierHit | None:
    """
    Inspect one bar for SL/TP. Returns None if neither touched.
    conservative: same-bar both → SL first.
    unresolved: same-bar both → AMBIGUOUS_INTRABAR.
    """
    d = direction.lower()
    if d == "buy":
        hit_sl = bar.low <= sl
        hit_tp = bar.high >= tp
    else:
        hit_sl = bar.high >= sl
        hit_tp = bar.low <= tp

    if hit_sl and hit_tp:
        if intrabar_policy == "unresolved":
            return BarrierHit("AMBIGUOUS_INTRABAR", None, bar.time, "same bar SL+TP")
        # conservative baseline: SL first
        return BarrierHit("SL_HIT", sl, bar.time, "conservative_sl_first")
    if hit_sl:
        return BarrierHit("SL_HIT", sl, bar.time)
    if hit_tp:
        return BarrierHit("TP_HIT", tp, bar.time)
    return None


def pnl_for_exit(
    direction: str,
    entry: float,
    exit_price: float,
    lot: float,
    *,
    tick_size: float,
    tick_value: float,
) -> float:
    if tick_size <= 0:
        return 0.0
    if direction.lower() == "buy":
        move = exit_price - entry
    else:
        move = entry - exit_price
    ticks = move / tick_size
    return ticks * tick_value * lot
