"""
core/risk.py
Lot size calculator — 10% of free margin = RISK_PIPS pips of risk.

Formula:
    risk_amount   = free_margin × 0.10
    per_pip_risk  = risk_per_lot / sl_pips
    lot_size      = risk_amount / (RISK_PIPS × per_pip_risk)
    lot_size      = clamp(lot_size, MIN_LOT, MAX_LOT)
    lot_size      = round to nearest volume step

The 10% budget is scaled by actual SL distance vs RISK_PIPS benchmark.
"""

import logging
import MetaTrader5 as mt5

from core.config import (
    MIN_LOT, MAX_LOT, MT5_SYMBOL_SUFFIX, SL_PIP_SIZE, RISK_PERCENT,
    RISK_PIPS_XAUUSD, RISK_PIPS_DEFAULT
)
from core.signal import Signal

def _get_risk_pips_for_symbol(symbol: str) -> int:
    """Return the risk benchmark (in pips) for the given symbol."""
    sym_upper = symbol.upper()
    if "XAU" in sym_upper or "GOLD" in sym_upper:
        return RISK_PIPS_XAUUSD
    else:
        return RISK_PIPS_DEFAULT

log = logging.getLogger(__name__)


def calculate_lot(signal: Signal, risk_override: float = None) -> tuple[float, str]:
    """
    Calculate lot size for a signal based on current free margin.

    10% of free margin = 1000 pips of risk.
    Lot scales proportionally: sl_pips < 1000 → bigger lot, sl_pips > 1000 → smaller lot.

    Args:
        risk_override: deprecated — kept for compatibility, ignored.

    Returns:
        (lot_size, explanation_string)
        lot_size = 0.0 means calculation failed — do NOT trade.
    """
    account = mt5.account_info()
    if account is None:
        return 0.0, "❌ Could not read MT5 account info."

    free_margin = account.margin_free
    equity = getattr(account, "equity", 0) or free_margin
    if free_margin <= 0 or equity <= 0:
        return 0.0, "❌ No free margin available."

    sym_mt5 = signal.symbol + MT5_SYMBOL_SUFFIX
    sym_info = mt5.symbol_info(sym_mt5)
    if sym_info is None:
        return 0.0, f"❌ Symbol {sym_mt5} not found."

    tick_size  = sym_info.trade_tick_size
    tick_value = sym_info.trade_tick_value
    if tick_size == 0 or tick_value == 0:
        return 0.0, f"❌ Could not get tick info for {signal.symbol}."

    sl_distance = abs(signal.entry_mid - signal.sl)
    if sl_distance == 0:
        return 0.0, "❌ SL distance is zero — cannot calculate lot."

    sl_pips      = sl_distance / SL_PIP_SIZE
    sl_in_ticks = sl_distance / tick_size
    risk_per_lot = sl_in_ticks * tick_value
    if risk_per_lot == 0:
        return 0.0, "❌ Risk per lot is zero — check symbol tick values."

    risk_percent = RISK_PERCENT if risk_override is None else risk_override
    risk_percent = max(0.0, risk_percent)
    risk_amount  = equity * risk_percent
    raw_lot      = risk_amount / risk_per_lot

    vol_step = sym_info.volume_step
    lot      = max(MIN_LOT, min(MAX_LOT, raw_lot))
    lot      = round(round(lot / vol_step) * vol_step, 2)

    warnings = []
    if raw_lot < MIN_LOT:
        warnings.append(
            f"⚠️ *Margin tight* — calculated `{raw_lot:.4f}` lots, "
            f"using minimum `{MIN_LOT}`"
        )
    if lot > 0 and lot * risk_per_lot > free_margin:
        return 0.0, "❌ Not enough free margin for minimum risk-sized lot."

    warning_str = "\n".join(warnings) + "\n" if warnings else ""

    explanation = (
        f"{warning_str}"
        f"💰 Equity: `${equity:,.2f}` | Free margin: `${free_margin:,.2f}`\n"
        f"📊 Risk: `{risk_percent*100:.2f}%` -> `${risk_amount:,.2f}` ({signal.symbol})\n"
        f"📏 SL: `{sl_pips:.0f} pips` ({sl_distance:.2f} pts)\n"
        f"📦 Lot: `{lot}`"
    )

    log.info(
        f"Lot calc | equity={equity:.2f} free_margin={free_margin:.2f} risk={risk_amount:.2f} "
        f"sl={sl_pips:.0f}pips risk/lot={risk_per_lot:.2f} raw={raw_lot:.4f} -> lot={lot} "
        f"(risk_percent={risk_percent:.4f})"
    )

    return lot, explanation
