"""Historical spread field validation (MT5 rates spread = points)."""

from __future__ import annotations

import statistics
from typing import Any, Iterable

from backtest.interfaces import Candle
from backtest.symbol_spec import SymbolSpec
from core.config import SL_PIP_SIZE


def spread_points_to_pips(spread_points: int | float, point: float) -> float:
    """MT5 rate['spread'] is in points; pip = SL_PIP_SIZE price units."""
    return (float(spread_points) * float(point)) / float(SL_PIP_SIZE)


def analyze_spreads(
    candles: Iterable[Candle],
    spec: SymbolSpec,
    *,
    candidate_bar_times: set[int] | None = None,
) -> dict[str, Any]:
    all_pts: list[int] = []
    cand_pips: list[float] = []
    invalid = 0
    for c in candles:
        if c.spread is None:
            continue
        if c.spread < 0 or c.spread > 10_000:
            invalid += 1
            continue
        all_pts.append(int(c.spread))
        if candidate_bar_times is not None and c.time in candidate_bar_times:
            cand_pips.append(spread_points_to_pips(c.spread, spec.point))

    def stats_pts(vals: list[int]) -> dict[str, float | None]:
        if not vals:
            return {"min": None, "median": None, "p95": None, "max": None, "n": 0}
        s = sorted(vals)
        p95 = s[int(0.95 * (len(s) - 1))]
        return {
            "min": float(min(s)),
            "median": float(statistics.median(s)),
            "p95": float(p95),
            "max": float(max(s)),
            "n": len(s),
        }

    def stats_pips(vals: list[float]) -> dict[str, float | None]:
        if not vals:
            return {"min": None, "median": None, "p95": None, "max": None, "n": 0}
        s = sorted(vals)
        p95 = s[int(0.95 * (len(s) - 1))]
        return {
            "min": round(min(s), 4),
            "median": round(statistics.median(s), 4),
            "p95": round(p95, 4),
            "max": round(max(s), 4),
            "n": len(s),
        }

    all_pips = [spread_points_to_pips(p, spec.point) for p in all_pts]
    return {
        "unit": "MT5 copy_rates spread field = points; pips = points * point / SL_PIP_SIZE",
        "point": spec.point,
        "sl_pip_size": SL_PIP_SIZE,
        "conversion_validated": spec.quality == "EXACT_BROKER_METADATA" and spec.point > 0,
        "invalid_spread_rows": invalid,
        "all_bars_points": stats_pts(all_pts),
        "all_bars_pips": stats_pips(all_pips),
        "candidate_bars_pips": stats_pips(cand_pips),
        "symbol_spec_quality": spec.quality,
    }
