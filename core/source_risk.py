"""
Source-level risk buckets for multi-channel Telegram signals.

Each configured Telegram source can consume only its own risk allocation while
the account still has a global open-risk cap.
"""

import logging
import math
from dataclasses import dataclass

import MetaTrader5 as mt5

from core.config import (
    MT5_SYMBOL_SUFFIX, SOURCE_RISK_DEFAULT, SOURCE_RISK_MODE,
    MAX_TOTAL_OPEN_RISK, MIN_LOT,
)

log = logging.getLogger(__name__)


@dataclass
class SourceRiskResult:
    allowed: bool
    lot: float
    reason: str = ""
    note: str = ""


def _risk_per_lot(symbol: str, entry_mid: float, sl: float) -> float:
    sym_mt5 = symbol + MT5_SYMBOL_SUFFIX
    info = mt5.symbol_info(sym_mt5)
    if info is None:
        return 0.0
    tick_size = info.trade_tick_size
    tick_value = info.trade_tick_value
    if tick_size == 0 or tick_value == 0:
        return 0.0
    sl_distance = abs(entry_mid - sl)
    if sl_distance == 0:
        return 0.0
    return (sl_distance / tick_size) * tick_value


def _open_risk_by_source() -> tuple[dict[str, float], float]:
    """
    Return (source_used, total_used) in account currency.

    Uses DB rows where outcome is still NULL and the signal has a real source_id.
    Manual/personal cover trades stay outside Telegram source buckets.
    """
    source_used: dict[str, float] = {}
    total_used = 0.0

    try:
        from core.db import get_conn
        conn = get_conn()
        with conn.cursor() as cur:
            cur.execute("""
                SELECT
                    COALESCE(s.source_id, '') AS source_id,
                    s.symbol,
                    s.entry_low,
                    s.entry_high,
                    s.sl,
                    t.lot
                FROM trades t
                JOIN signals s ON t.signal_id = s.signal_id
                WHERE t.outcome IS NULL
                  AND t.lot IS NOT NULL
                  AND COALESCE(s.source_id, '') <> ''
            """)
            rows = cur.fetchall()
        conn.close()
    except Exception as e:
        log.warning("source risk DB read failed: %s", e)
        return {}, 0.0

    for row in rows:
        try:
            symbol = row["symbol"]
            entry_mid = (float(row["entry_low"]) + float(row["entry_high"])) / 2
            sl = float(row["sl"])
            lot = float(row["lot"])
            risk = _risk_per_lot(symbol, entry_mid, sl) * lot
        except Exception:
            continue
        if risk <= 0:
            continue
        total_used += risk
        source_id = row["source_id"] or ""
        if source_id:
            source_used[source_id] = source_used.get(source_id, 0.0) + risk

    return source_used, total_used


def apply_source_risk_bucket(signal, account, proposed_lot: float) -> SourceRiskResult:
    """
    Apply the source risk bucket to an already calculated/proposed lot.

    Returns the same lot when the bucket has room, a reduced lot when configured
    to reduce, or a block result if remaining risk cannot fund MIN_LOT.
    """
    source_id = getattr(signal, "source_id", "") or ""
    if not source_id or proposed_lot <= 0:
        return SourceRiskResult(True, proposed_lot)

    risk_percent = getattr(signal, "source_risk_percent", 0.0) or SOURCE_RISK_DEFAULT
    equity = getattr(account, "equity", 0.0) or getattr(account, "margin_free", 0.0)
    if risk_percent <= 0 or equity <= 0:
        return SourceRiskResult(True, proposed_lot)

    risk_per_lot = _risk_per_lot(signal.symbol, signal.entry_mid, signal.sl)
    if risk_per_lot <= 0:
        return SourceRiskResult(True, proposed_lot)

    source_used, total_used = _open_risk_by_source()
    source_budget = equity * risk_percent
    source_remaining = source_budget - source_used.get(source_id, 0.0)
    total_remaining = float("inf")
    if MAX_TOTAL_OPEN_RISK > 0:
        total_budget = equity * MAX_TOTAL_OPEN_RISK
        total_remaining = total_budget - total_used

    remaining = min(source_remaining, total_remaining)
    proposed_risk = proposed_lot * risk_per_lot

    if proposed_risk <= remaining + 0.01:
        return SourceRiskResult(True, proposed_lot)

    if SOURCE_RISK_MODE == "block":
        reason = (
            f"Source `{source_id}` risk bucket full. "
            f"Remaining `${max(0.0, remaining):.2f}` | Needed `${proposed_risk:.2f}`"
        )
        return SourceRiskResult(False, proposed_lot, reason=reason)

    if remaining <= 0:
        reason = (
            f"Source `{source_id}` has no remaining risk budget. "
            f"Used `${source_used.get(source_id, 0.0):.2f}` / `${source_budget:.2f}`"
        )
        return SourceRiskResult(False, proposed_lot, reason=reason)

    info = mt5.symbol_info(signal.symbol + MT5_SYMBOL_SUFFIX)
    step = getattr(info, "volume_step", 0.01) or 0.01
    reduced_lot = math.floor((remaining / risk_per_lot) / step) * step
    reduced_lot = round(reduced_lot, 2)

    if reduced_lot < MIN_LOT:
        reason = (
            f"Source `{source_id}` remaining risk only allows `{reduced_lot:.2f}` lot, "
            f"below MIN_LOT `{MIN_LOT}`."
        )
        return SourceRiskResult(False, proposed_lot, reason=reason)

    note = (
        f"📉 Source risk bucket reduced lot `{proposed_lot}` -> `{reduced_lot}` "
        f"({source_id}: `${max(0.0, remaining):.2f}` risk remaining)"
    )
    log.info(
        "Source risk reduce | source=%s proposed_lot=%.2f reduced_lot=%.2f "
        "remaining=%.2f risk_per_lot=%.2f",
        source_id, proposed_lot, reduced_lot, remaining, risk_per_lot
    )
    return SourceRiskResult(True, reduced_lot, note=note)
