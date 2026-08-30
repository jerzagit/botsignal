"""
M30 RBR/DBD zone detection for structure_pullback_v2 — locked rules.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Sequence

from core.strategies.v2_structure import (
    atr14,
    body_bottom,
    body_size,
    body_top,
    candle_range,
    confirmed_swing_highs,
    confirmed_swing_lows,
    true_range,
)

PIVOT_LEFT = 2
PIVOT_RIGHT = 2
BASE_TR_ATR = 0.80
BASE_BODY_RATIO_MAX = 0.60
BASE_WIDTH_ATR = 1.20
LEG_ATR = 1.0
DEPARTURE_ATR = 1.0
DEPARTURE_BODY_MIN = 0.60
DEPARTURE_MAX_BARS = 3


@dataclass
class Zone:
    zone_id: str
    zone_type: str  # RBR | DBD
    symbol: str
    created_at: int
    base_start: int
    base_end: int
    proximal: float
    distal: float
    atr: float
    departure_end: int
    bos_level: float
    touch_count: int = 0
    state: str = "WAITING_PULLBACK"
    zone_entry_time: int | None = None
    trigger_swing: float | None = None
    invalidated_at: int | None = None
    invalidation_price: float | None = None
    invalidation_reason: str | None = None
    fib_overlap: bool = False
    fib_level_nearest: float | None = None
    fib_retracement_pct: float | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def width(self) -> float:
        return abs(self.proximal - self.distal)

    def to_dict(self) -> dict[str, Any]:
        return {
            "zone_id": self.zone_id,
            "zone_type": self.zone_type,
            "created_at": self.created_at,
            "base_start": self.base_start,
            "base_end": self.base_end,
            "proximal": self.proximal,
            "distal": self.distal,
            "atr": self.atr,
            "departure_end": self.departure_end,
            "bos_level": self.bos_level,
            "touch_count": self.touch_count,
            "state": self.state,
            "zone_entry_time": self.zone_entry_time,
            "trigger_swing": self.trigger_swing,
            "invalidated_at": self.invalidated_at,
            "invalidation_price": self.invalidation_price,
            "invalidation_reason": self.invalidation_reason,
            "fib_overlap": self.fib_overlap,
            "fib_level_nearest": self.fib_level_nearest,
            "fib_retracement_pct": self.fib_retracement_pct,
            "zone_width": self.width,
        }


def _overlap(a: dict, b: dict) -> bool:
    return min(float(a["high"]), float(b["high"])) > max(float(a["low"]), float(b["low"]))


def is_valid_base(candles: Sequence[dict], start: int, end: int, atr: float) -> bool:
    """Base candles indices inclusive [start, end], length 1..4."""
    if atr <= 0:
        return False
    n = end - start + 1
    if n < 1 or n > 4:
        return False
    base = candles[start : end + 1]
    for i, c in enumerate(base):
        abs_i = start + i
        prev_close = float(candles[abs_i - 1]["close"]) if abs_i > 0 else None
        if true_range(c, prev_close) > BASE_TR_ATR * atr:
            return False
        rng = candle_range(c)
        if rng <= 0:
            return False
        if body_size(c) / rng > BASE_BODY_RATIO_MAX:
            return False
    for i in range(len(base) - 1):
        if not _overlap(base[i], base[i + 1]):
            return False
    width = max(float(c["high"]) for c in base) - min(float(c["low"]) for c in base)
    if width > BASE_WIDTH_ATR * atr:
        return False
    return True


def demand_boundaries(base: Sequence[dict]) -> tuple[float, float]:
    """RBR: distal=min low, proximal=max body top."""
    distal = min(float(c["low"]) for c in base)
    proximal = max(body_top(c) for c in base)
    return distal, proximal


def supply_boundaries(base: Sequence[dict]) -> tuple[float, float]:
    """DBD: distal=max high, proximal=min body bottom. Zone [proximal, distal]."""
    distal = max(float(c["high"]) for c in base)
    proximal = min(body_bottom(c) for c in base)
    return distal, proximal


def _leg_ok(
    candles: Sequence[dict],
    start: int,
    end: int,
    *,
    direction: str,
    atr: float,
) -> bool:
    """Incoming rally/drop into base. end is last candle before base."""
    if end < start or atr <= 0:
        return False
    a = float(candles[start]["open"])
    b = float(candles[end]["close"])
    net = b - a
    if direction == "rally":
        return net >= LEG_ATR * atr and b > a
    return (-net) >= LEG_ATR * atr and b < a


def _departure_ok(
    candles: Sequence[dict],
    base_end: int,
    proximal: float,
    *,
    direction: str,
    atr: float,
) -> tuple[bool, int | None, float, float]:
    """
    Returns (ok, departure_end_idx, strength_atr, best_body_ratio).
    Departure within next 1..3 candles after base_end.
    """
    best_body = 0.0
    strength = 0.0
    end_idx = None
    for k in range(1, DEPARTURE_MAX_BARS + 1):
        i = base_end + k
        if i >= len(candles):
            break
        c = candles[i]
        rng = candle_range(c)
        br = (body_size(c) / rng) if rng > 0 else 0.0
        best_body = max(best_body, br)
        if direction == "bull":
            move = float(c["close"]) - proximal
            strength = max(strength, move / atr if atr else 0.0)
            if move >= DEPARTURE_ATR * atr and br >= DEPARTURE_BODY_MIN:
                end_idx = i
                return True, end_idx, strength, best_body
        else:
            move = proximal - float(c["close"])
            strength = max(strength, move / atr if atr else 0.0)
            if move >= DEPARTURE_ATR * atr and br >= DEPARTURE_BODY_MIN:
                end_idx = i
                return True, end_idx, strength, best_body
    return False, end_idx, strength, best_body


def _prior_swing_high(candles: Sequence[dict], before_idx: int) -> float | None:
    piv = confirmed_swing_highs(candles[:before_idx], left=PIVOT_LEFT, right=PIVOT_RIGHT)
    return piv[-1][1] if piv else None


def _prior_swing_low(candles: Sequence[dict], before_idx: int) -> float | None:
    piv = confirmed_swing_lows(candles[:before_idx], left=PIVOT_LEFT, right=PIVOT_RIGHT)
    return piv[-1][1] if piv else None


def try_build_rbr(
    candles: Sequence[dict],
    base_end: int,
    symbol: str,
) -> Zone | None:
    atr = atr14(candles, base_end)
    if atr is None:
        return None
    # Prefer smallest base length
    for length in (1, 2, 3, 4):
        start = base_end - length + 1
        if start < 1:
            continue
        if not is_valid_base(candles, start, base_end, atr):
            continue
        # incoming rally: look back up to 5 bars before base
        leg_end = start - 1
        leg_start = max(0, leg_end - 4)
        if not _leg_ok(candles, leg_start, leg_end, direction="rally", atr=atr):
            continue
        distal, proximal = demand_boundaries(candles[start : base_end + 1])
        ok, dep_end, strength, body_r = _departure_ok(
            candles, base_end, proximal, direction="bull", atr=atr
        )
        if not ok or dep_end is None:
            continue
        bos_level = _prior_swing_high(candles, base_end + 1)
        if bos_level is None:
            continue
        # BOS: departure close above prior swing high (close only)
        if float(candles[dep_end]["close"]) <= bos_level:
            continue
        zid = f"{symbol}:M30:RBR:{candles[start]['time']}:{candles[base_end]['time']}"
        return Zone(
            zone_id=zid,
            zone_type="RBR",
            symbol=symbol,
            created_at=int(candles[dep_end]["time"]),
            base_start=int(candles[start]["time"]),
            base_end=int(candles[base_end]["time"]),
            proximal=proximal,
            distal=distal,
            atr=atr,
            departure_end=int(candles[dep_end]["time"]),
            bos_level=bos_level,
            state="WAITING_PULLBACK",
            meta={
                "departure_strength_atr": round(strength, 4),
                "departure_body_ratio": round(body_r, 4),
                "bos_confirmed": True,
            },
        )
    return None


def try_build_dbd(
    candles: Sequence[dict],
    base_end: int,
    symbol: str,
) -> Zone | None:
    atr = atr14(candles, base_end)
    if atr is None:
        return None
    for length in (1, 2, 3, 4):
        start = base_end - length + 1
        if start < 1:
            continue
        if not is_valid_base(candles, start, base_end, atr):
            continue
        leg_end = start - 1
        leg_start = max(0, leg_end - 4)
        if not _leg_ok(candles, leg_start, leg_end, direction="drop", atr=atr):
            continue
        distal, proximal = supply_boundaries(candles[start : base_end + 1])
        ok, dep_end, strength, body_r = _departure_ok(
            candles, base_end, proximal, direction="bear", atr=atr
        )
        if not ok or dep_end is None:
            continue
        bos_level = _prior_swing_low(candles, base_end + 1)
        if bos_level is None:
            continue
        if float(candles[dep_end]["close"]) >= bos_level:
            continue
        zid = f"{symbol}:M30:DBD:{candles[start]['time']}:{candles[base_end]['time']}"
        return Zone(
            zone_id=zid,
            zone_type="DBD",
            symbol=symbol,
            created_at=int(candles[dep_end]["time"]),
            base_start=int(candles[start]["time"]),
            base_end=int(candles[base_end]["time"]),
            proximal=proximal,
            distal=distal,
            atr=atr,
            departure_end=int(candles[dep_end]["time"]),
            bos_level=bos_level,
            state="WAITING_PULLBACK",
            meta={
                "departure_strength_atr": round(strength, 4),
                "departure_body_ratio": round(body_r, 4),
                "bos_confirmed": True,
            },
        )
    return None


def price_in_zone(price: float, zone: Zone) -> bool:
    lo, hi = sorted((zone.proximal, zone.distal))
    return lo <= price <= hi


def bar_overlaps_zone(c: dict, zone: Zone) -> bool:
    lo, hi = sorted((zone.proximal, zone.distal))
    return min(float(c["high"]), hi) > max(float(c["low"]), lo)
