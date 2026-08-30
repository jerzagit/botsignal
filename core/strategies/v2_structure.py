"""
Shared structure helpers for structure_pullback_v2 (pivots, ATR, legs).

CLOSED candles only — callers must pass already-closed series.
"""

from __future__ import annotations

from typing import Sequence


def body_size(c: dict) -> float:
    return abs(float(c["close"]) - float(c["open"]))


def candle_range(c: dict) -> float:
    return float(c["high"]) - float(c["low"])


def body_top(c: dict) -> float:
    return max(float(c["open"]), float(c["close"]))


def body_bottom(c: dict) -> float:
    return min(float(c["open"]), float(c["close"]))


def true_range(c: dict, prev_close: float | None) -> float:
    h, l = float(c["high"]), float(c["low"])
    if prev_close is None:
        return h - l
    return max(h - l, abs(h - prev_close), abs(l - prev_close))


def atr14(candles: Sequence[dict], end_idx: int) -> float | None:
    """ATR(14) using candles[: end_idx+1] (end_idx inclusive). None if insufficient."""
    if end_idx < 13:
        return None
    start = end_idx - 13
    trs = []
    for i in range(start, end_idx + 1):
        prev = float(candles[i - 1]["close"]) if i > 0 else None
        trs.append(true_range(candles[i], prev))
    return sum(trs) / 14.0


def confirmed_swing_highs(
    candles: Sequence[dict], *, left: int = 2, right: int = 2
) -> list[tuple[int, float]]:
    """Return (index, price) for confirmed pivot highs (no lookahead beyond right)."""
    out = []
    n = len(candles)
    for i in range(left, n - right):
        h = float(candles[i]["high"])
        if all(h >= float(candles[j]["high"]) for j in range(i - left, i)) and all(
            h > float(candles[j]["high"]) for j in range(i + 1, i + right + 1)
        ):
            out.append((i, h))
    return out


def confirmed_swing_lows(
    candles: Sequence[dict], *, left: int = 2, right: int = 2
) -> list[tuple[int, float]]:
    out = []
    n = len(candles)
    for i in range(left, n - right):
        l = float(candles[i]["low"])
        if all(l <= float(candles[j]["low"]) for j in range(i - left, i)) and all(
            l < float(candles[j]["low"]) for j in range(i + 1, i + right + 1)
        ):
            out.append((i, l))
    return out


def h1_structure_bias(h1: Sequence[dict], *, left: int = 2, right: int = 2) -> str:
    """
    BULLISH / BEARISH / NEUTRAL from latest confirmed swings + last BOS-like break.
    """
    if len(h1) < left + right + 5:
        return "NEUTRAL"
    highs = confirmed_swing_highs(h1, left=left, right=right)
    lows = confirmed_swing_lows(h1, left=left, right=right)
    if len(highs) < 2 or len(lows) < 2:
        return "NEUTRAL"
    # HH/HL → bullish; LH/LL → bearish
    hh = highs[-1][1] > highs[-2][1]
    hl = lows[-1][1] > lows[-2][1]
    lh = highs[-1][1] < highs[-2][1]
    ll = lows[-1][1] < lows[-2][1]
    last_close = float(h1[-1]["close"])
    # Structural break vs prior opposite swing
    bull_bos = last_close > highs[-1][1]
    bear_bos = last_close < lows[-1][1]
    if bull_bos or (hh and hl):
        if bear_bos and (lh and ll) and not (hh and hl):
            return "BEARISH"
        return "BULLISH"
    if bear_bos or (lh and ll):
        return "BEARISH"
    return "NEUTRAL"


def fib_overlap_for_impulse(
    impulse_start: float,
    impulse_end: float,
    zone_proximal: float,
    zone_distal: float,
) -> tuple[bool, float | None, float | None]:
    """
    Retracement of zone mid vs H1 impulse. Returns (overlap_38_61, nearest_level, pct).
    """
    move = impulse_end - impulse_start
    if abs(move) < 1e-9:
        return False, None, None
    mid = (zone_proximal + zone_distal) / 2.0
    # retracement from impulse_end toward impulse_start
    retr = (impulse_end - mid) / move
    # normalize so 0 = start, 1 = end of impulse; retracement after impulse is from end
    # For bullish impulse start<end, pullback retracement pct = (end - price) / move
    if move > 0:
        pct = (impulse_end - mid) / move
    else:
        pct = (mid - impulse_end) / abs(move)
    levels = (0.382, 0.5, 0.618)
    nearest = min(levels, key=lambda x: abs(x - pct))
    overlap = 0.382 <= pct <= 0.618
    return overlap, nearest, round(pct * 100.0, 2)
