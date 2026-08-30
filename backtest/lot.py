"""Pure lot sizing mirroring core/risk.py::calculate_lot without MT5 I/O."""

from __future__ import annotations

from core.config import MAX_LOT, MIN_LOT, SL_PIP_SIZE
from backtest.symbol_spec import SymbolSpec


def calculate_lot_pure(
    *,
    equity: float,
    free_margin: float,
    entry: float,
    sl: float,
    risk_percent: float,
    spec: SymbolSpec,
) -> tuple[float, float, str]:
    """
    Returns (lot, risk_usd, explanation).
    lot == 0.0 means failure (do not trade).
    Formula matches core/risk.py (equity * risk_percent / risk_per_lot).
    """
    if free_margin <= 0 or equity <= 0:
        return 0.0, 0.0, "No free margin / equity"
    sl_distance = abs(entry - sl)
    if sl_distance <= 0:
        return 0.0, 0.0, "SL distance is zero"
    if spec.tick_size <= 0 or spec.tick_value <= 0:
        return 0.0, 0.0, "Invalid tick_size/tick_value"
    sl_in_ticks = sl_distance / spec.tick_size
    risk_per_lot = sl_in_ticks * spec.tick_value
    if risk_per_lot <= 0:
        return 0.0, 0.0, "Risk per lot is zero"
    risk_percent = max(0.0, float(risk_percent))
    risk_amount = equity * risk_percent
    raw_lot = risk_amount / risk_per_lot
    vol_step = spec.volume_step or 0.01
    lot = max(MIN_LOT, min(MAX_LOT, raw_lot))
    lot = round(round(lot / vol_step) * vol_step, 2)
    if lot > 0 and lot * risk_per_lot > free_margin:
        return 0.0, 0.0, "Not enough free margin for risk-sized lot"
    sl_pips = sl_distance / SL_PIP_SIZE
    expl = (
        f"equity={equity:.2f} risk%={risk_percent*100:.2f} risk_usd={risk_amount:.2f} "
        f"sl_pips={sl_pips:.0f} risk/lot={risk_per_lot:.2f} raw={raw_lot:.4f} lot={lot}"
    )
    return lot, risk_amount, expl
