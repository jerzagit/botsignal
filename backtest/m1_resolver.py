"""
M1-assisted execution / outcome resolution (strategy stays on M15).

Quality label: M1_APPROXIMATED — not exact tick tape.
Entry policy (production approx): NEXT_M1_AVAILABLE after M15 signal close.
Live execute_trade uses market ask/bid at order_send; we approximate with
M1 open ± half historical spread (BUY→ask, SELL→bid).
"""

from __future__ import annotations

import bisect
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from backtest.gap_analysis import SUSPICIOUS_DATA_GAP, classify_gap
from backtest.interfaces import Candle
from backtest.outcomes import BarrierHit, check_bar_barriers
from backtest.timeframes import period_seconds


ENTRY_POLICY_NEXT_M1 = "NEXT_M1_AVAILABLE"
QUALITY = "M1_APPROXIMATED"


@dataclass
class M1Resolution:
    entry_time_unix: int | None
    entry_price: float | None
    exit_time_unix: int | None
    exit_price: float | None
    outcome: str  # SL_HIT | TP_HIT | AMBIGUOUS_M1_INTRABAR | OPEN | UNRESOLVED_DATA_GAP
    minutes_held: int | None
    resolution_quality: str
    data_quality_warning: bool
    ambiguity_reason: str = ""
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "entry_time_unix": self.entry_time_unix,
            "entry_price": self.entry_price,
            "exit_time_unix": self.exit_time_unix,
            "exit_price": self.exit_price,
            "outcome": self.outcome,
            "minutes_held": self.minutes_held,
            "resolution_quality": self.resolution_quality,
            "data_quality_warning": self.data_quality_warning,
            "ambiguity_reason": self.ambiguity_reason,
            "note": self.note,
        }


class M1OutcomeResolver:
    def __init__(self, candles: list[Candle], *, point: float = 0.01) -> None:
        self.candles = candles
        self.times = [c.time for c in candles]
        self.point = point
        self._cursor_idx = 0  # last processed exclusive end index

    def _idx_ge(self, unix_ts: int) -> int:
        return bisect.bisect_left(self.times, int(unix_ts))

    def _idx_gt(self, unix_ts: int) -> int:
        return bisect.bisect_right(self.times, int(unix_ts))

    def assert_no_future(self, cursor_unix: int, accessed_open: int) -> None:
        if accessed_open > cursor_unix:
            raise RuntimeError(f"Future M1 access: bar={accessed_open} cursor={cursor_unix}")

    def bars_until(self, cursor_unix: int) -> list[Candle]:
        """M1 bars fully closed by cursor (open + 60 <= cursor)."""
        end = bisect.bisect_right(self.times, int(cursor_unix) - period_seconds("M1"))
        return self.candles[:end]

    def resolve_entry(
        self,
        *,
        signal_close_unix: int,
        direction: str,
        candidate_entry: float,
        spread_points: int | None,
        cursor_unix: int,
    ) -> tuple[float, int, str]:
        """
        NEXT_M1_AVAILABLE: first M1 bar with open_time >= signal_close.
        Fill at open ± half spread (BUY ask / SELL bid). Bar must be visible
        (open <= cursor). Using open only — no H/L/C look-ahead for fill.
        """
        i = self._idx_ge(signal_close_unix)
        if i >= len(self.candles):
            # no M1 yet — fall back candidate
            return float(candidate_entry), signal_close_unix, "FALLBACK_CANDIDATE_NO_M1"
        bar = self.candles[i]
        if bar.time > cursor_unix:
            raise RuntimeError("M1 entry bar not yet available at cursor")
        self.assert_no_future(cursor_unix, bar.time)
        half = 0.0
        sp = spread_points if spread_points is not None else bar.spread
        if sp is not None:
            half = (float(sp) * self.point) / 2.0
        if direction.lower() == "buy":
            px = bar.open + half
        else:
            px = bar.open - half
        return float(px), int(bar.time), ENTRY_POLICY_NEXT_M1

    def walk_outcome(
        self,
        *,
        direction: str,
        entry_unix: int,
        entry_price: float,
        sl: float,
        tp: float,
        until_unix: int,
        from_after_unix: int | None = None,
        intrabar_policy: str = "unresolved",
    ) -> M1Resolution:
        """
        Walk M1 bars closed by until_unix.
        from_after_unix: continue after this open time (exclusive) for incremental scans.
        """
        start = self._idx_ge(entry_unix)
        if from_after_unix is not None:
            start = max(start, self._idx_gt(from_after_unix))
        end = bisect.bisect_right(self.times, int(until_unix) - period_seconds("M1"))
        warning = False
        prev_t = from_after_unix
        for i in range(start, end):
            bar = self.candles[i]
            self.assert_no_future(until_unix, bar.time)
            if prev_t is not None:
                kind = classify_gap(prev_t, bar.time, "M1")
                if kind == SUSPICIOUS_DATA_GAP:
                    warning = True
                    return M1Resolution(
                        entry_time_unix=entry_unix,
                        entry_price=entry_price,
                        exit_time_unix=None,
                        exit_price=None,
                        outcome="UNRESOLVED_DATA_GAP",
                        minutes_held=None,
                        resolution_quality=QUALITY,
                        data_quality_warning=True,
                        ambiguity_reason="suspicious M1 gap on path",
                        note=f"gap after {prev_t}",
                    )
            prev_t = bar.time
            hit = check_bar_barriers(
                direction, sl, tp, bar, intrabar_policy=intrabar_policy
            )
            if hit is None:
                continue
            if hit.outcome == "AMBIGUOUS_INTRABAR":
                return M1Resolution(
                    entry_time_unix=entry_unix,
                    entry_price=entry_price,
                    exit_time_unix=bar.time + period_seconds("M1"),
                    exit_price=None,
                    outcome="AMBIGUOUS_M1_INTRABAR",
                    minutes_held=max(1, (bar.time - entry_unix) // 60 + 1),
                    resolution_quality=QUALITY,
                    data_quality_warning=warning,
                    ambiguity_reason="same M1 bar touches SL and TP",
                    note=hit.note,
                )
            exit_unix = bar.time + period_seconds("M1")
            return M1Resolution(
                entry_time_unix=entry_unix,
                entry_price=entry_price,
                exit_time_unix=exit_unix,
                exit_price=hit.exit_price,
                outcome=hit.outcome,
                minutes_held=max(1, (bar.time - entry_unix) // 60 + 1),
                resolution_quality=QUALITY,
                data_quality_warning=warning,
                note=hit.note,
            )
        return M1Resolution(
            entry_time_unix=entry_unix,
            entry_price=entry_price,
            exit_time_unix=None,
            exit_price=None,
            outcome="OPEN",
            minutes_held=None,
            resolution_quality=QUALITY,
            data_quality_warning=warning,
            note="still open at cursor",
        )


def classify_outcome_change(legacy: str | None, m1: str | None) -> str:
    def norm(o: str | None) -> str:
        o = (o or "").upper()
        if o in ("WIN",):
            return "WIN"
        if o in ("LOSS",):
            return "LOSS"
        if o in ("AMBIGUOUS", "AMBIGUOUS_M1_INTRABAR"):
            return "AMBIGUOUS"
        if o in ("OPEN_AT_END", "OPEN"):
            return "OPEN"
        if o in ("UNRESOLVED_DATA_GAP",):
            return "UNRESOLVED"
        return o or "UNKNOWN"

    a, b = norm(legacy), norm(m1)
    if a == b:
        return "NO_CHANGE"
    if a == "WIN" and b == "LOSS":
        return "LEGACY_WIN_TO_M1_LOSS"
    if a == "LOSS" and b == "WIN":
        return "LEGACY_LOSS_TO_M1_WIN"
    if a == "WIN" and b == "AMBIGUOUS":
        return "LEGACY_WIN_TO_AMBIGUOUS"
    if a == "LOSS" and b == "AMBIGUOUS":
        return "LEGACY_LOSS_TO_AMBIGUOUS"
    if a == "OPEN" and b in ("WIN", "LOSS", "AMBIGUOUS"):
        return "OPEN_TO_RESOLVED"
    if a in ("WIN", "LOSS") and b == "OPEN":
        return "RESOLVED_TO_OPEN"
    return f"{a}_TO_{b}"
