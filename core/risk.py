"""
core/risk.py
Lot size calculator — risks a % of free margin based on the SL distance.

Formula:
    risk_amount  = free_margin × risk_percent
    pip_value    = contract_size × tick_value / tick_size
    lot_size     = risk_amount / (sl_distance × pip_value)
    lot_size     = clamp(lot_size, MIN_LOT, MAX_LOT)
    lot_size     = round to nearest 0.01

This means every trade risks the same % of your current margin,
automatically scaling down when your account shrinks and up when it grows.
"""

import logging
import MetaTrader5 as mt5

from core.config import RISK_PERCENT, MIN_LOT, MAX_LOT, MT5_SYMBOL_SUFFIX, \
                       SL_PIP_SIZE, SL_WARN_MIN_PIPS, SL_WARN_MAX_PIPS
from core.signal import Signal

log = logging.getLogger(__name__)


def calculate_lot(signal: Signal, risk_override: float = None) -> tuple[float, str]:
    """
    Calculate lot size for a signal based on current free margin.

    Args:
        risk_override: use this risk % instead of RISK_PERCENT (e.g. for manual trades)

    Returns:
        (lot_size, explanation_string)
        lot_size = 0.0 means calculation failed — do NOT trade.
    """
    # ── Get account info ─────────────────────────────────────────────────────
    account = mt5.account_info()
    if account is None:
        return 0.0, "❌ Could not read MT5 account info."

    free_margin = account.margin_free
    if free_margin <= 0:
        return 0.0, "❌ No free margin available."

    # ── Get symbol info ───────────────────────────────────────────────────────
    sym_info = mt5.symbol_info(signal.symbol + MT5_SYMBOL_SUFFIX)
    if sym_info is None:
        return 0.0, f"❌ Symbol {signal.symbol + MT5_SYMBOL_SUFFIX} not found."

    tick_size  = sym_info.trade_tick_size    # e.g. 0.01 for XAUUSD
    tick_value = sym_info.trade_tick_value   # USD value of 1 tick on 1 lot
    contract   = sym_info.trade_contract_size  # usually 100 for gold, 100000 for FX

    if tick_size == 0 or tick_value == 0:
        return 0.0, f"❌ Could not get tick info for {signal.symbol}."

    # ── Calculate risk amount ─────────────────────────────────────────────────
    risk_pct    = risk_override if risk_override is not None else RISK_PERCENT
    risk_amount = free_margin * risk_pct

    # ── SL distance in price units and pips ──────────────────────────────────
    entry_ref   = signal.entry_mid
    sl_distance = abs(entry_ref - signal.sl)
    if sl_distance == 0:
        return 0.0, "❌ SL distance is zero — cannot calculate lot."

    sl_pips = sl_distance / SL_PIP_SIZE

    # ── Convert SL distance to monetary risk per lot ──────────────────────────
    sl_in_ticks  = sl_distance / tick_size
    risk_per_lot = sl_in_ticks * tick_value

    if risk_per_lot == 0:
        return 0.0, "❌ Risk per lot is zero — check symbol tick values."

    # ── Raw lot size ──────────────────────────────────────────────────────────
    raw_lot  = risk_amount / risk_per_lot
    vol_step = sym_info.volume_step
    lot      = max(MIN_LOT, min(MAX_LOT, raw_lot))
    lot      = round(round(lot / vol_step) * vol_step, 2)

    # ── Warnings ──────────────────────────────────────────────────────────────
    warnings = []

    if sl_pips < SL_WARN_MIN_PIPS:
        warnings.append(
            f"⚠️ *SL unusually tight* — `{sl_pips:.0f} pips` "
            f"(normal: {SL_WARN_MIN_PIPS}–{SL_WARN_MAX_PIPS} pips)"
        )
    elif sl_pips > SL_WARN_MAX_PIPS:
        warnings.append(
            f"⚠️ *SL unusually wide* — `{sl_pips:.0f} pips` "
            f"(normal: {SL_WARN_MIN_PIPS}–{SL_WARN_MAX_PIPS} pips)"
        )

    if raw_lot < MIN_LOT:
        warnings.append(
            f"⚠️ *Margin tight* — calculated `{raw_lot:.4f}` lots, "
            f"using minimum `{MIN_LOT}`"
        )

    warning_str = "\n".join(warnings) + "\n" if warnings else ""

    explanation = (
        f"{warning_str}"
        f"💰 Free margin: `${free_margin:,.2f}`\n"
        f"📊 Risk: `{risk_pct*100:.0f}%` -> `${risk_amount:,.2f}`\n"
        f"📏 SL: `{sl_pips:.0f} pips` ({sl_distance:.2f} pts)\n"
        f"📦 Lot: `{lot}`"
    )

    log.info(
        f"Lot calc | margin={free_margin:.2f} risk={risk_amount:.2f} "
        f"sl={sl_pips:.0f}pips risk/lot={risk_per_lot:.2f} raw={raw_lot:.4f} → lot={lot}"
    )

    return lot, explanation
