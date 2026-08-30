"""
Improved historical gap classification + material-impact analysis.

Classifications:
  EXPECTED_WEEKEND_CLOSE
  EXPECTED_BROKER_SESSION_BREAK
  EXPECTED_HOLIDAY_CLOSE
  SUSPICIOUS_DATA_GAP
  UNKNOWN_GAP
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from typing import Any, Iterable

from backtest.interfaces import Candle
from backtest.timeframes import period_seconds

EXPECTED_WEEKEND_CLOSE = "EXPECTED_WEEKEND_CLOSE"
EXPECTED_BROKER_SESSION_BREAK = "EXPECTED_BROKER_SESSION_BREAK"
EXPECTED_HOLIDAY_CLOSE = "EXPECTED_HOLIDAY_CLOSE"
SUSPICIOUS_DATA_GAP = "SUSPICIOUS_DATA_GAP"
UNKNOWN_GAP = "UNKNOWN_GAP"


def _utc(ts: int) -> datetime:
    return datetime.fromtimestamp(int(ts), tz=timezone.utc)


def _is_weekend_span(t0: int, t1: int) -> bool:
    d0, d1 = _utc(t0), _utc(t1)
    if (t1 - t0) >= 36 * 3600 and d0.weekday() >= 4 and d1.weekday() <= 1:
        return True
    if d0.weekday() == 4 and d1.weekday() == 0 and (t1 - t0) >= 24 * 3600:
        return True
    if d0.weekday() >= 5 or d1.weekday() >= 5:
        if (t1 - t0) >= 12 * 3600:
            return True
    return False


def _is_daily_broker_break(t0: int, t1: int, period: int, missing: int) -> bool:
    """
    Recurring short daily break: typically ~1h near 00:00 UTC
    after the prior day's last bar (often open 23:xx → next 01:00).

    Missing-bar cap is timeframe-aware (M15 ~4–8 bars; M1 ~60–180 bars).
    """
    delta = t1 - t0
    # gap length roughly 45m–3.5h (session rollover / daily maintenance)
    if delta < max(period * 2, 45 * 60) or delta > int(3.5 * 3600) + period:
        return False
    max_missing = max(8, int(3.5 * 3600 / period) + 2)
    if missing < 1 or missing > max_missing:
        return False
    end_of_prev = _utc(t0 + period)  # when missing interval starts
    start_next = _utc(t1)
    prev_open = _utc(t0)
    # Classic Eightcap-style: break spanning midnight hour (00:00–01:00 UTC)
    if end_of_prev.hour == 0 and start_next.hour == 1 and start_next.minute == 0:
        return True
    if end_of_prev.hour == 0 and start_next.hour <= 2 and period >= 900 and missing in (3, 4, 5):
        return True
    # Previous bar opened in 21–23h UTC window and next at 01:00
    if prev_open.hour >= 21 and start_next.hour == 1 and start_next.minute == 0:
        return True
    if prev_open.hour == 23 and start_next.hour <= 2:
        return True
    return False


def _is_holidayish(t0: int, t1: int) -> bool:
    d0, d1 = _utc(t0), _utc(t1)
    if _is_weekend_span(t0, t1):
        return False
    # Mid-week closure >= 20h
    if d0.weekday() < 5 and d1.weekday() < 5 and (t1 - t0) >= 20 * 3600:
        return True
    return False


def classify_gap(t0: int, t1: int, timeframe: str) -> str:
    period = period_seconds(timeframe)
    delta = t1 - t0
    if delta <= period:
        return UNKNOWN_GAP
    missing = max(1, (delta // period) - 1)
    if _is_weekend_span(t0, t1):
        return EXPECTED_WEEKEND_CLOSE
    if _is_daily_broker_break(t0, t1, period, missing):
        return EXPECTED_BROKER_SESSION_BREAK
    if _is_holidayish(t0, t1):
        return EXPECTED_HOLIDAY_CLOSE
    # irregular mid-week multi-bar hole
    midweek = _utc(t0).weekday() < 5 and _utc(t1).weekday() < 5
    if midweek and missing >= 2 and delta < 20 * 3600:
        return SUSPICIOUS_DATA_GAP
    if midweek and missing >= 1:
        return SUSPICIOUS_DATA_GAP
    return UNKNOWN_GAP


def analyze_gaps(
    candles: list[Candle],
    timeframe: str,
    *,
    candidate_times: set[int] | None = None,
    trade_windows: list[tuple[int, int]] | None = None,
    session_breaks_from_spec: list[dict] | None = None,
) -> dict[str, Any]:
    """
    trade_windows: list of (entry_unix, exit_unix) for open trades spanning gaps.
    """
    period = period_seconds(timeframe)
    gaps: list[dict[str, Any]] = []
    counts: Counter = Counter()

    for i in range(len(candles) - 1):
        t0 = candles[i].time
        t1 = candles[i + 1].time
        delta = t1 - t0
        if delta <= period:
            continue
        missing = (delta // period) - 1
        if missing <= 0:
            if delta % period != 0:
                missing = 1
            else:
                continue
        kind = classify_gap(t0, t1, timeframe)
        # Optional: if session metadata says closed, upgrade UNKNOWN to session break
        if kind in (UNKNOWN_GAP, SUSPICIOUS_DATA_GAP) and session_breaks_from_spec:
            # lightweight: if gap fully inside known daily break window hours
            pass

        gap_start = t0 + period
        gap_end = t1
        cand_hit = False
        if candidate_times:
            for ct in candidate_times:
                if gap_start < ct < gap_end:
                    cand_hit = True
                    break
        trade_hit = False
        if trade_windows:
            for ent, ex in trade_windows:
                ex = ex or gap_end + 1
                if ent < gap_end and ex > gap_start:
                    trade_hit = True
                    break

        impact = "NO_MATERIAL_IMPACT"
        if kind == SUSPICIOUS_DATA_GAP and (cand_hit or trade_hit):
            impact = "DATA_QUALITY_WARNING"
        elif kind == SUSPICIOUS_DATA_GAP:
            impact = "NO_MATERIAL_IMPACT"

        counts[kind] += 1
        gaps.append(
            {
                "timeframe": timeframe,
                "gap_start": _utc(gap_start).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "gap_end": _utc(gap_end).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "gap_start_unix": gap_start,
                "gap_end_unix": gap_end,
                "expected_bars_missing": missing,
                "classification": kind,
                "kind": kind,  # backward compatible
                "candidate_affected": cand_hit,
                "open_trade_affected": trade_hit,
                "impact": impact,
                "candidate_or_trade_affected": cand_hit or trade_hit,
            }
        )

    suspicious = [g for g in gaps if g["classification"] == SUSPICIOUS_DATA_GAP]
    material = [g for g in suspicious if g["impact"] == "DATA_QUALITY_WARNING"]
    return {
        "timeframe": timeframe,
        "raw_gap_count": len(gaps),
        "gap_count": len(gaps),
        "counts_by_class": dict(counts),
        "expected_weekend": counts[EXPECTED_WEEKEND_CLOSE],
        "expected_broker_breaks": counts[EXPECTED_BROKER_SESSION_BREAK],
        "expected_holidays": counts[EXPECTED_HOLIDAY_CLOSE],
        "suspicious_count": len(suspicious),
        "unknown_count": counts[UNKNOWN_GAP],
        "materially_affected_gaps": len(material),
        "gaps": gaps[:300],
        "gaps_truncated": len(gaps) > 300,
        "suspicious_gaps": suspicious[:100],
        "material_gaps": material[:100],
    }
