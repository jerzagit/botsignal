"""
Authoritative direction-aware trade geometry math for diagnostics.

No abs() on risk/reward — invalid directional geometry returns INVALID_GEOMETRY.
"""

from __future__ import annotations

from typing import Any

from core.config import MIN_RR_RATIO, SL_PIP_SIZE

INVALID_GEOMETRY = "INVALID_GEOMETRY"


def pips_from_price(distance: float) -> float:
    """Display-only absolute distance in pips."""
    return round(abs(distance) / SL_PIP_SIZE, 2) if SL_PIP_SIZE else round(abs(distance), 4)


def compute_geometry(
    direction: str,
    entry: float,
    sl: float,
    tp: float,
    min_rr: float = MIN_RR_RATIO,
) -> dict[str, Any]:
    side = (direction or "").lower()
    if side not in ("buy", "sell"):
        return _invalid("unknown_direction", direction=direction)

    if side == "buy":
        risk = entry - sl
        reward = tp - entry
        risk_formula = "entry - sl"
        reward_formula = "tp - entry"
        required_tp = entry + risk * min_rr
        shortfall = required_tp - tp  # positive when TP too low
    else:
        risk = sl - entry
        reward = entry - tp
        risk_formula = "sl - entry"
        reward_formula = "entry - tp"
        required_tp = entry - risk * min_rr
        shortfall = tp - required_tp  # positive when TP too high (too near for SELL)

    risk_r = round(risk, 4)
    reward_r = round(reward, 4)
    tp_r = round(tp, 4)
    entry_r = round(entry, 4)
    sl_r = round(sl, 4)
    req_tp_r = round(required_tp, 4)

    invalid_reason = None
    if risk <= 0:
        invalid_reason = "risk_not_positive"
    elif reward <= 0:
        invalid_reason = "reward_not_positive"

    if invalid_reason:
        return {
            "geometry_valid": False,
            "invalid_reason": invalid_reason,
            "status": INVALID_GEOMETRY,
            "direction": direction.upper(),
            "entry": entry_r,
            "sl": sl_r,
            "tp": tp_r,
            "risk": risk_r,
            "reward": reward_r,
            "rr": None,
            "risk_formula": risk_formula,
            "reward_formula": reward_formula,
            "rr_formula": None,
            "MIN_RR": min_rr,
            "required_tp_at_min_rr": req_tp_r,
            "target_shortfall_price": None,
            "target_shortfall_pips": None,
            "target_shortfall_r": None,
            "target_exceeds_min_rr": False,
            "passes_min_rr": False,
        }

    rr = round(reward / risk, 4)
    if shortfall <= 0:
        shortfall_price = 0.0
        exceeds = True
    else:
        shortfall_price = round(shortfall, 4)
        exceeds = False

    shortfall_pips = pips_from_price(shortfall_price) if shortfall_price else 0.0
    shortfall_r = round(shortfall_price / risk, 4) if risk > 0 else None

    return {
        "geometry_valid": True,
        "invalid_reason": None,
        "status": "OK",
        "direction": direction.upper(),
        "entry": entry_r,
        "sl": sl_r,
        "tp": tp_r,
        "risk": risk_r,
        "reward": reward_r,
        "rr": rr,
        "risk_formula": risk_formula,
        "reward_formula": reward_formula,
        "rr_formula": f"{reward_r} / {risk_r}",
        "MIN_RR": min_rr,
        "required_tp_at_min_rr": req_tp_r,
        "target_shortfall_price": shortfall_price,
        "target_shortfall_pips": shortfall_pips,
        "target_shortfall_r": shortfall_r,
        "target_exceeds_min_rr": exceeds,
        "passes_min_rr": rr >= min_rr,
    }


def _invalid(reason: str, **extra: Any) -> dict[str, Any]:
    out = {
        "geometry_valid": False,
        "invalid_reason": reason,
        "status": INVALID_GEOMETRY,
        "rr": None,
        "passes_min_rr": False,
        "target_exceeds_min_rr": False,
    }
    out.update(extra)
    return out


def sl_distance_from_entry(direction: str, entry: float, sl: float) -> float | None:
    g = compute_geometry(direction, entry, sl, entry + 1 if direction.lower() == "buy" else entry - 1)
    if not g.get("geometry_valid"):
        # still compute raw directional distance for invalid SL reporting
        side = direction.lower()
        if side == "buy":
            return round(entry - sl, 4)
        if side == "sell":
            return round(sl - entry, 4)
        return None
    return g["risk"]


def sl_tighter_than_current(direction: str, entry: float, current_sl: float, alt_sl: float) -> bool:
    """Tighter = smaller risk distance from entry."""
    side = direction.lower()
    if side == "buy":
        return alt_sl > current_sl  # higher SL below entry = closer
    if side == "sell":
        return alt_sl < current_sl  # lower SL above entry = closer
    return False


def tp_further_than_current(direction: str, entry: float, current_tp: float, alt_tp: float) -> bool:
    side = direction.lower()
    if side == "buy":
        return alt_tp > current_tp
    if side == "sell":
        return alt_tp < current_tp
    return False


def literal_formula_block(direction: str, entry: float, sl: float, tp: float, min_rr: float = MIN_RR_RATIO) -> dict[str, Any]:
    g = compute_geometry(direction, entry, sl, tp, min_rr=min_rr)
    side = direction.lower()
    lines: list[str] = []
    if side == "sell":
        lines = [
            f"Risk: SL - Entry = {sl} - {entry} = {g.get('risk')}",
            f"Reward: Entry - TP = {entry} - {tp} = {g.get('reward')}",
            f"RR: {g.get('reward')} / {g.get('risk')} = {g.get('rr')}",
            f"Required TP @ {min_rr}R: {entry} - ({g.get('risk')} * {min_rr}) = {g.get('required_tp_at_min_rr')}",
        ]
    elif side == "buy":
        lines = [
            f"Risk: Entry - SL = {entry} - {sl} = {g.get('risk')}",
            f"Reward: TP - Entry = {tp} - {entry} = {g.get('reward')}",
            f"RR: {g.get('reward')} / {g.get('risk')} = {g.get('rr')}",
            f"Required TP @ {min_rr}R: {entry} + ({g.get('risk')} * {min_rr}) = {g.get('required_tp_at_min_rr')}",
        ]
    return {"geometry": g, "lines": lines}
