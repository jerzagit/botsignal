"""Optional execution cost model — never silently applied to RAW baseline."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class CostModel:
    commission_per_lot: float = 0.0  # USD round-turn or per side? document as per-lot round trip
    slippage_pips: float = 0.0
    swap_policy: str = "none"  # none | zero (explicit)
    label: str = "NOT MODELLED"

    @property
    def is_raw(self) -> bool:
        return self.commission_per_lot == 0 and self.slippage_pips == 0 and self.swap_policy in (
            "none",
            "zero",
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "commission_per_lot": self.commission_per_lot,
            "slippage_pips": self.slippage_pips,
            "swap_policy": self.swap_policy,
            "label": "NOT MODELLED" if self.is_raw else "COST_SCENARIO",
            "assumption": not self.is_raw,
        }


def apply_costs_to_trade_pnl(
    *,
    direction: str,
    entry: float,
    exit_price: float,
    lot: float,
    raw_pnl: float,
    cost: CostModel,
    pip_size: float,
    tick_size: float,
    tick_value: float,
) -> dict[str, Any]:
    """Return cost-adjusted PnL breakdown. Does not mutate raw."""
    slip = 0.0
    if cost.slippage_pips and tick_size > 0:
        slip_price = cost.slippage_pips * pip_size
        # adverse slippage
        if direction.lower() == "buy":
            # worse entry higher / worse exit lower — approximate as 2 * slip on one side of PnL
            slip = -((slip_price / tick_size) * tick_value * lot)
        else:
            slip = -((slip_price / tick_size) * tick_value * lot)
    commission = -abs(cost.commission_per_lot) * lot
    swap = 0.0  # not modelled beyond explicit zero
    adjusted = raw_pnl + slip + commission + swap
    return {
        "raw_pnl": raw_pnl,
        "slippage_pnl": round(slip, 4),
        "commission_pnl": round(commission, 4),
        "swap_pnl": swap,
        "adjusted_pnl": round(adjusted, 4),
        "cost_model": cost.to_dict(),
    }
