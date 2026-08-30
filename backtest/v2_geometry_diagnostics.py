"""
Passive V2 geometry diagnostics — decompose RR shortfall without altering strategy.

OBSERVE only: never creates StrategyDecision, guards, trades, or P/L effects.
"""

from __future__ import annotations

import hashlib
import json
import statistics
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from backtest.dataset import candles_to_dicts, load_candles_csv, load_dataset_meta
from backtest.geometry_math import (
    compute_geometry,
    literal_formula_block,
    pips_from_price,
    sl_tighter_than_current,
    tp_further_than_current,
)
from core.config import MIN_RR_RATIO, SL_PIP_SIZE, STRATEGY_MIN_RR
from core.strategies.v2_structure import confirmed_swing_highs, confirmed_swing_lows

SL_BUFFER_ATR = 0.10
PIVOT_LEFT = 2
PIVOT_RIGHT = 2
DIAGNOSTIC_ONLY = "DIAGNOSTIC_ONLY"
NO_SIGNAL = "NO_SIGNAL"
GEOMETRY_OBSERVATION_ONLY = "GEOMETRY_OBSERVATION_ONLY"


def _pips(price_dist: float) -> float:
    return pips_from_price(price_dist)


def _geom(direction: str, entry: float, sl: float, tp: float, min_rr: float = MIN_RR_RATIO) -> dict[str, Any]:
    return compute_geometry(direction, entry, sl, tp, min_rr=min_rr)


def _load_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def _write_jsonl(path: Path, rows: Sequence[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, sort_keys=True) + "\n")


def slice_closed(candles: Sequence[dict], as_of_unix: int) -> list[dict]:
    return [c for c in candles if int(c["time"]) <= as_of_unix]


def m15_bar_at_open(m15: Sequence[dict], open_time: int) -> dict | None:
    for c in m15:
        if int(c["time"]) == open_time:
            return c
    return None


def first_m15_after(m15: Sequence[dict], unix: int) -> dict | None:
    for c in m15:
        if int(c["time"]) > unix:
            return c
    return None


def m15_bars_from_to(m15: Sequence[dict], start_unix: int, end_unix: int) -> list[dict]:
    return [c for c in m15 if int(c["time"]) > start_unix and int(c["time"]) <= end_unix]


def pivot_confirmed_at(candles: Sequence[dict], pivot_idx: int, right: int = PIVOT_RIGHT) -> int | None:
    conf_i = pivot_idx + right
    if conf_i >= len(candles):
        return None
    return int(candles[conf_i]["time"])


def pivot_confirmed_before(
    candles: Sequence[dict], pivot_idx: int, as_of_unix: int, right: int = PIVOT_RIGHT
) -> bool:
    conf = pivot_confirmed_at(candles, pivot_idx, right)
    return conf is not None and conf <= as_of_unix


def sl_valid(direction: str, entry: float, sl: float) -> bool:
    if direction.lower() == "buy":
        return sl < entry
    return sl > entry


def production_sl(distal: float, zone_atr: float, direction: str) -> float:
    buffer = SL_BUFFER_ATR * zone_atr
    if direction.lower() == "buy":
        return round(distal - buffer, 2)
    return round(distal + buffer, 2)


def eligible_h1_targets(
    h1_slice: Sequence[dict],
    entry: float,
    direction: str,
    as_of_unix: int,
) -> list[dict[str, Any]]:
    side = direction.lower()
    out: list[dict[str, Any]] = []
    if side == "buy":
        for i, px in confirmed_swing_highs(h1_slice, left=PIVOT_LEFT, right=PIVOT_RIGHT):
            if px <= entry:
                continue
            if not pivot_confirmed_before(h1_slice, i, as_of_unix):
                continue
            conf_ts = pivot_confirmed_at(h1_slice, i)
            out.append(
                {
                    "index": i,
                    "price": round(px, 2),
                    "pivot_timestamp": int(h1_slice[i]["time"]),
                    "pivot_confirmed_timestamp": conf_ts,
                    "distance": round(px - entry, 4),
                }
            )
    else:
        for i, px in confirmed_swing_lows(h1_slice, left=PIVOT_LEFT, right=PIVOT_RIGHT):
            if px >= entry:
                continue
            if not pivot_confirmed_before(h1_slice, i, as_of_unix):
                continue
            conf_ts = pivot_confirmed_at(h1_slice, i)
            out.append(
                {
                    "index": i,
                    "price": round(px, 2),
                    "pivot_timestamp": int(h1_slice[i]["time"]),
                    "pivot_confirmed_timestamp": conf_ts,
                    "distance": round(entry - px, 4),
                }
            )
    out.sort(key=lambda t: t["distance"])
    return out


def production_tp(h1_slice: Sequence[dict], entry: float, direction: str, as_of_unix: int) -> dict | None:
    """Match structure_pullback_v2: last chronological eligible pivot."""
    side = direction.lower()
    selected = None
    if side == "buy":
        for i, px in confirmed_swing_highs(h1_slice, left=PIVOT_LEFT, right=PIVOT_RIGHT):
            if px > entry and pivot_confirmed_before(h1_slice, i, as_of_unix):
                selected = {
                    "index": i,
                    "price": round(px, 2),
                    "pivot_timestamp": int(h1_slice[i]["time"]),
                    "pivot_confirmed_timestamp": pivot_confirmed_at(h1_slice, i),
                    "distance": round(px - entry, 4),
                }
    else:
        for i, px in confirmed_swing_lows(h1_slice, left=PIVOT_LEFT, right=PIVOT_RIGHT):
            if px < entry and pivot_confirmed_before(h1_slice, i, as_of_unix):
                selected = {
                    "index": i,
                    "price": round(px, 2),
                    "pivot_timestamp": int(h1_slice[i]["time"]),
                    "pivot_confirmed_timestamp": pivot_confirmed_at(h1_slice, i),
                    "distance": round(entry - px, 4),
                }
    return selected


def next_further_h1_target(
    targets_sorted: list[dict[str, Any]], current_tp: float, direction: str
) -> dict[str, Any] | None:
    side = direction.lower()
    further: list[dict[str, Any]] = []
    for t in targets_sorted:
        px = float(t["price"])
        if side == "buy" and px > current_tp:
            further.append(t)
        elif side == "sell" and px < current_tp:
            further.append(t)
    if not further:
        return None
  # nearest among further (smallest additional distance)
    if side == "buy":
        further.sort(key=lambda x: x["distance"])
    else:
        further.sort(key=lambda x: x["distance"])
    return further[0]


def rr_reference_levels(direction: str, entry: float, risk: float) -> dict[str, float]:
    side = direction.lower()
    mults = {"price_1R": 1.0, "price_1_4R": 1.4, "price_1_5R": 1.5, "price_2R": 2.0}
    out: dict[str, float] = {}
    for key, m in mults.items():
        if side == "buy":
            out[key] = round(entry + m * risk, 2)
        else:
            out[key] = round(entry - m * risk, 2)
    return out


def zone_valid_at(z: dict[str, Any], as_of_unix: int) -> bool:
    created = int(z.get("zone_created_at") or 0)
    if created > as_of_unix:
        return False
    inv = z.get("invalidated_at")
    if inv is not None and int(inv) <= as_of_unix:
        return False
    return True


def nearest_opposing_m30_zones(
    zones: Sequence[dict[str, Any]],
    entry: float,
    direction: str,
    as_of_unix: int,
    limit: int = 3,
) -> list[dict[str, Any]]:
    side = direction.lower()
    candidates: list[dict[str, Any]] = []
    for z in zones:
        if not zone_valid_at(z, as_of_unix):
            continue
        zt = z.get("zone_type")
        prox = float(z.get("proximal") or 0)
        if side == "buy" and zt == "DBD" and prox > entry:
            dist = prox - entry
            candidates.append(
                {
                    "zone_id": z.get("zone_id"),
                    "created_at": z.get("zone_created_at"),
                    "type": zt,
                    "proximal": prox,
                    "distal": z.get("distal"),
                    "fresh_status": "used" if z.get("consumed_at") else "fresh",
                    "distance_from_entry": round(dist, 4),
                    "target_price": round(prox, 2),
                }
            )
        elif side == "sell" and zt == "RBR" and prox < entry:
            dist = entry - prox
            candidates.append(
                {
                    "zone_id": z.get("zone_id"),
                    "created_at": z.get("zone_created_at"),
                    "type": zt,
                    "proximal": prox,
                    "distal": z.get("distal"),
                    "fresh_status": "used" if z.get("consumed_at") else "fresh",
                    "distance_from_entry": round(dist, 4),
                    "target_price": round(prox, 2),
                }
            )
    candidates.sort(key=lambda x: x["distance_from_entry"])
    return candidates[:limit]


def classify_root_cause(
    current_rr: float,
    delta_entry: float,
    delta_sl: float,
    delta_tp: float,
    min_rr: float = MIN_RR_RATIO,
) -> str:
    if current_rr >= min_rr:
        return "CURRENT_GEOMETRY_ALREADY_REASONABLE"
    effects = {
        "ENTRY_DELAY_DOMINANT": delta_entry,
        "SL_DISTANCE_DOMINANT": delta_sl,
        "TP_DISTANCE_DOMINANT": delta_tp,
    }
    positive = {k: v for k, v in effects.items() if v > 0}
    if not positive:
        return "NO_CLEAR_SINGLE_DOMINANT"
    ranked = sorted(positive.items(), key=lambda x: x[1], reverse=True)
    top_label, top_val = ranked[0]
    second_val = ranked[1][1] if len(ranked) > 1 else 0.0
    if second_val > 0 and second_val >= 0.8 * top_val:
        return "COMBINED_GEOMETRY"
    return top_label


def semantic_hash(rows: Sequence[dict[str, Any]]) -> str:
    payload = json.dumps(rows, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


def _entry_variants(
    retest: dict[str, Any],
    m15: Sequence[dict],
    actual_entry: float,
    confirmation_unix: int,
) -> dict[str, dict[str, Any]]:
    variants: dict[str, dict[str, Any]] = {}
    retest_at = retest.get("zone_retest_at")
    left_at = retest.get("left_zone_at")

    e0_bar = m15_bar_at_open(m15, confirmation_unix) if confirmation_unix else None
    variants["E0"] = {
        "label": "actual_confirmation_close",
        "entry": actual_entry,
        "entry_source": "actual_confirmation_close",
        "timestamp": confirmation_unix,
        "available": True,
    }

    if retest_at:
        b1 = first_m15_after(m15, int(retest_at))
        if b1:
            variants["E1"] = {
                "label": "first_m15_close_after_retest",
                "entry": float(b1["close"]),
                "entry_source": "first_m15_close_after_retest",
                "timestamp": int(b1["time"]),
                "available": True,
            }

    if left_at:
        b2 = first_m15_after(m15, int(left_at))
        if b2:
            variants["E2"] = {
                "label": "first_m15_close_after_leave_zone",
                "entry": float(b2["close"]),
                "entry_source": "first_m15_close_after_leave_zone",
                "timestamp": int(b2["time"]),
                "available": True,
            }

    ls = retest.get("local_structure") or {}
    react_conf = retest.get("reaction_pivot_confirmed_at") or ls.get("reaction_confirmed_at")
    if react_conf:
        bar = m15_bar_at_open(m15, int(react_conf))
        if bar:
            variants["E3"] = {
                "label": "m15_close_at_reaction_pivot_confirmation",
                "entry": float(bar["close"]),
                "entry_source": "reaction_pivot_confirmation_close",
                "timestamp": int(bar["time"]),
                "available": True,
            }

    trig_conf = retest.get("local_trigger_confirmed_at") or ls.get("trigger_confirmed_at")
    if trig_conf:
        bar = m15_bar_at_open(m15, int(trig_conf))
        if bar:
            variants["E4"] = {
                "label": "m15_close_at_trigger_pivot_confirmation",
                "entry": float(bar["close"]),
                "entry_source": "trigger_pivot_confirmation_close",
                "timestamp": int(bar["time"]),
                "available": True,
            }

    return variants


def _alt_sl_variants(
    retest: dict[str, Any],
    zone_atr: float,
    direction: str,
    entry_unix: int,
    m15_slice: Sequence[dict],
) -> dict[str, dict[str, Any]]:
    buffer = SL_BUFFER_ATR * zone_atr
    ls = retest.get("local_structure") or {}
    react_px = retest.get("reaction_pivot_price") or ls.get("reaction_price")
    react_conf = retest.get("reaction_pivot_confirmed_at") or ls.get("reaction_confirmed_at")
    trig_px = retest.get("local_trigger_price") or ls.get("trigger_price")
    trig_conf = retest.get("local_trigger_confirmed_at") or ls.get("trigger_confirmed_at")

    out: dict[str, dict[str, Any]] = {}

    if react_px is not None and react_conf is not None:
        if direction.lower() == "buy":
            sl = round(float(react_px) - buffer, 2)
        else:
            sl = round(float(react_px) + buffer, 2)
        conf_before = int(react_conf) <= entry_unix
        out["ALT_SL_M15_REACTION"] = {
            "sl": sl,
            "pivot_price": float(react_px),
            "pivot_confirmed_at": int(react_conf),
            "available_at_entry": conf_before,
            "confirmed_before_entry": conf_before,
            "uses_future_information": not conf_before,
        }

    if trig_px is not None and trig_conf is not None:
        out["ALT_SL_M15_TRIGGER_PIVOT_HINDSIGHT"] = {
            "sl": round(float(trig_px) + buffer, 2) if direction.lower() == "sell" else round(float(trig_px) - buffer, 2),
            "pivot_price": float(trig_px),
            "pivot_confirmed_at": int(trig_conf),
            "available_at_entry": int(trig_conf) <= entry_unix,
            "confirmed_before_entry": int(trig_conf) <= entry_unix,
            "uses_future_information": int(trig_conf) > entry_unix,
            "hindsight_only": True,
            "note": "Trigger pivot is opposite-side; not valid structure anchor SL",
        }

    # Structure anchor = post-retest reaction pivot (same side as structure leg)
    if react_px is not None and react_conf is not None:
        if direction.lower() == "buy":
            sl_st = round(float(react_px) - buffer, 2)
        else:
            sl_st = round(float(react_px) + buffer, 2)
        conf_before = int(react_conf) <= entry_unix
        out["ALT_SL_M15_STRUCTURE"] = {
            "sl": sl_st,
            "pivot_price": float(react_px),
            "pivot_confirmed_at": int(react_conf),
            "available_at_entry": conf_before,
            "confirmed_before_entry": conf_before,
            "uses_future_information": not conf_before,
        }
        if "ALT_SL_M15_REACTION" in out and out["ALT_SL_M15_REACTION"]["sl"] == sl_st:
            out["ALT_SL_M15_STRUCTURE"]["identical_to_reaction"] = True

    return out


def analyze_candidate(
    cand: dict[str, Any],
    retest: dict[str, Any],
    zone_row: dict[str, Any],
    m15: list[dict],
    h1: list[dict],
    m30_zones: list[dict[str, Any]],
    min_rr: float = MIN_RR_RATIO,
) -> dict[str, Any]:
    direction = cand.get("direction") or "SELL"
    entry = float(cand["entry"])
    sl = float(cand["sl"])
    tp = float(cand["tp"])
    zone_distal = float(cand.get("zone_distal") or zone_row.get("distal") or 0)
    zone_prox = float(cand.get("zone_proximal") or zone_row.get("proximal") or 0)
    zone_atr = float(
        zone_row.get("m30_atr") or cand.get("zone_atr") or retest.get("zone_atr") or 0
    )
    zone_id = cand.get("zone_id")
    strategy = cand.get("strategy") or "unknown"

    conf_unix = int(retest.get("m15_confirmation_ts") or retest.get("local_structure", {}).get("structure_shift_at") or 0)
    if not conf_unix:
        # fallback: find m15 bar matching entry close
        for c in m15:
            if abs(float(c["close"]) - entry) < 0.02:
                conf_unix = int(c["time"])
                break

    h1_at = slice_closed(h1, conf_unix)
    m15_at = slice_closed(m15, conf_unix)

    geom = _geom(direction, entry, sl, tp, min_rr=min_rr)
    buffer = SL_BUFFER_ATR * zone_atr
    if direction.lower() == "buy":
        zone_structure_risk = entry - zone_distal
        buffer_risk = buffer
    else:
        zone_structure_risk = zone_distal - entry
        buffer_risk = buffer
    total_risk = geom.get("risk") or 0
    buffer_pct = round((buffer_risk / total_risk) * 100, 2) if total_risk > 0 else None

    targets = eligible_h1_targets(h1_at, entry, direction, conf_unix)
    prod_tp = production_tp(h1_at, entry, direction, conf_unix)
    selected_match = prod_tp and abs(float(prod_tp["price"]) - tp) < 0.02

    alt_sl = _alt_sl_variants(retest, zone_atr, direction, conf_unix, m15_at)
    opposing = nearest_opposing_m30_zones(m30_zones, entry, direction, conf_unix, limit=3)
    alt_tp_zone = opposing[0] if opposing else None

    next_h1 = next_further_h1_target(targets, tp, direction) if targets else None

    literal = literal_formula_block(direction, entry, sl, tp, min_rr=min_rr)
    rr_ref = rr_reference_levels(direction, entry, geom.get("risk") or 0) if geom.get("risk") else {}

    entry_vars = _entry_variants(retest, m15, entry, conf_unix)
    entry_timing_rows: list[dict[str, Any]] = []
    for key, ev in entry_vars.items():
        if not ev.get("available"):
            continue
        e = float(ev["entry"])
        g = _geom(direction, e, sl, tp, min_rr=min_rr)
        chron_ok = key == "E0" or (ev.get("timestamp") and int(ev["timestamp"]) <= conf_unix)
        entry_timing_rows.append(
            {
                "variant": key,
                "label": ev["label"],
                "entry": e,
                "entry_source": ev["entry_source"],
                "timestamp": ev.get("timestamp"),
                "sl": sl,
                "tp": tp,
                "risk": g.get("risk"),
                "reward": g.get("reward"),
                "rr": g.get("rr"),
                "geometry_valid": g.get("geometry_valid"),
                "passes_min_rr": g.get("passes_min_rr"),
                "diagnostic_only": DIAGNOSTIC_ONLY,
                "observational_only": key != "E0",
                "not_valid_strategy_entry": key != "E0",
                "chronologically_valid": chron_ok and key != "E0",
                "uses_future_information": not chron_ok and key != "E0",
            }
        )

    retest_at = int(retest.get("zone_retest_at") or 0)
    e0_rr = geom.get("rr") or 0.0
    best_entry_rr = e0_rr
    best_entry_key = "E0"
    for row in entry_timing_rows:
        if row["variant"] == "E0":
            continue
        if row.get("chronologically_valid") and row.get("geometry_valid") and row.get("rr") is not None:
            if row["rr"] > best_entry_rr:
                best_entry_rr = row["rr"]
                best_entry_key = row["variant"]
    delta_entry = round(best_entry_rr - e0_rr, 4)

    best_sl_rr = e0_rr
    best_sl_label = "S0"
    sl_rows: list[dict[str, Any]] = []
    for label, alt in alt_sl.items():
        if label.endswith("HINDSIGHT") or "HINDSIGHT" in label:
            continue
        sl_px = alt["sl"]
        valid_sl = sl_valid(direction, entry, sl_px)
        g = _geom(direction, entry, sl_px, tp, min_rr=min_rr) if valid_sl else {"geometry_valid": False, "risk": None, "reward": None, "rr": None, "passes_min_rr": False}
        tighter = sl_tighter_than_current(direction, entry, sl, sl_px) if valid_sl else False
        row = {
            "label": label,
            "entry": entry,
            "sl": sl_px,
            "tp": tp,
            "valid": valid_sl,
            "risk": g.get("risk"),
            "reward": g.get("reward"),
            "rr": g.get("rr"),
            "geometry_valid": g.get("geometry_valid"),
            "passes_min_rr": g.get("passes_min_rr"),
            "sl_distance_from_entry": g.get("risk"),
            "tighter_than_current": tighter,
            "available_at_entry": alt.get("available_at_entry"),
            "confirmed_before_entry": alt.get("confirmed_before_entry"),
            "uses_future_information": alt.get("uses_future_information"),
            "diagnostic_only": DIAGNOSTIC_ONLY,
        }
        sl_rows.append(row)
        chron_ok_sl = alt.get("available_at_entry") and not alt.get("uses_future_information")
        if valid_sl and chron_ok_sl and g.get("geometry_valid") and g.get("rr") is not None:
            if g["rr"] > best_sl_rr:
                best_sl_rr = g["rr"]
                best_sl_label = label
    delta_sl = round(best_sl_rr - e0_rr, 4)

    best_tp_rr = e0_rr
    best_tp_label = "T0"
    tp_rows: list[dict[str, Any]] = []
    if next_h1:
        g = _geom(direction, entry, sl, float(next_h1["price"]), min_rr=min_rr)
        tp_rows.append(
            {
                "label": "ALT_TP_NEXT_H1",
                "entry": entry,
                "sl": sl,
                "tp": next_h1["price"],
                "reward": g.get("reward"),
                "rr": g.get("rr"),
                "geometry_valid": g.get("geometry_valid"),
                "passes_min_rr": g.get("passes_min_rr"),
                "further_than_current": tp_further_than_current(direction, entry, tp, float(next_h1["price"])),
                "diagnostic_only": DIAGNOSTIC_ONLY,
                "available_at_entry": True,
                "uses_future_information": False,
            }
        )
        if g.get("geometry_valid") and g.get("rr") is not None and g["rr"] > best_tp_rr:
            best_tp_rr = g["rr"]
            best_tp_label = "ALT_TP_NEXT_H1"
    else:
        tp_rows.append({"label": "ALT_TP_NEXT_H1", "tp": None, "rr": None, "diagnostic_only": DIAGNOSTIC_ONLY})

    if alt_tp_zone:
        g = _geom(direction, entry, sl, float(alt_tp_zone["target_price"]), min_rr=min_rr)
        tp_rows.append(
            {
                "label": "ALT_TP_OPPOSING_M30_ZONE",
                "entry": entry,
                "sl": sl,
                "tp": alt_tp_zone["target_price"],
                "zone_id": alt_tp_zone["zone_id"],
                "reward": g.get("reward"),
                "rr": g.get("rr"),
                "geometry_valid": g.get("geometry_valid"),
                "passes_min_rr": g.get("passes_min_rr"),
                "further_than_current": tp_further_than_current(direction, entry, tp, float(alt_tp_zone["target_price"])),
                "diagnostic_only": DIAGNOSTIC_ONLY,
                "available_at_entry": True,
                "uses_future_information": False,
            }
        )
        if g.get("geometry_valid") and g.get("rr") is not None and g["rr"] > best_tp_rr:
            best_tp_rr = g["rr"]
            best_tp_label = "ALT_TP_OPPOSING_M30_ZONE"
    else:
        tp_rows.append(
            {"label": "ALT_TP_OPPOSING_M30_ZONE", "tp": None, "rr": None, "diagnostic_only": DIAGNOSTIC_ONLY}
        )

    delta_tp = round(best_tp_rr - e0_rr, 4)
    entry_only_pass = bool(best_entry_rr >= min_rr and best_entry_key != "E0")
    sl_only_pass = bool(best_sl_rr >= min_rr and best_sl_label != "S0")
    tp_only_pass = bool(best_tp_rr >= min_rr and best_tp_label != "T0")
    root = classify_root_cause(e0_rr, delta_entry, delta_sl, delta_tp)

    matrix: list[dict[str, Any]] = []
    sl_variants = {"S0": sl}
    for label, alt in alt_sl.items():
        if label.endswith("HINDSIGHT") or "HINDSIGHT" in label:
            continue
        if alt.get("available_at_entry") and not alt.get("uses_future_information"):
            sl_variants[label] = alt["sl"]
    tp_variants = {"T0": tp}
    if next_h1:
        tp_variants["ALT_TP_NEXT_H1"] = float(next_h1["price"])
    if alt_tp_zone:
        tp_variants["ALT_TP_OPPOSING_M30_ZONE"] = float(alt_tp_zone["target_price"])

    best_combo_rr = e0_rr
    best_combo: dict[str, Any] = {"entry": "E0", "sl": "S0", "tp": "T0", "rr": e0_rr}
    for ek, ev in entry_vars.items():
        if ek != "E0" and not (ev.get("timestamp") and int(ev["timestamp"]) <= conf_unix):
            continue
        e_px = float(ev["entry"])
        for sk, s_px in sl_variants.items():
            if not sl_valid(direction, e_px, s_px):
                continue
            for tk, t_px in tp_variants.items():
                g = _geom(direction, e_px, s_px, t_px, min_rr=min_rr)
                fut = ek != "E0" and int(ev.get("timestamp") or 0) > conf_unix
                chron = not fut
                matrix.append(
                    {
                        "entry_variant": ek,
                        "sl_variant": sk,
                        "tp_variant": tk,
                        "entry": e_px,
                        "sl": s_px,
                        "tp": t_px,
                        "risk": g.get("risk"),
                        "reward": g.get("reward"),
                        "rr": g.get("rr"),
                        "geometry_valid": g.get("geometry_valid"),
                        "passes_min_rr": g.get("passes_min_rr"),
                        "invalid_reason": g.get("invalid_reason"),
                        "entry_valid_signal": NO_SIGNAL,
                        "diagnostic_only": DIAGNOSTIC_ONLY,
                        "chronologically_valid": chron,
                        "future_information": fut,
                    }
                )
                if chron and g["rr"] is not None and g["rr"] > best_combo_rr:
                    best_combo_rr = g["rr"]
                    best_combo = {
                        "entry": ek,
                        "sl": sk,
                        "tp": tk,
                        "entry_price": e_px,
                        "sl_price": s_px,
                        "tp_price": t_px,
                        "rr": g["rr"],
                    }

    single_var_sufficient = entry_only_pass or sl_only_pass or tp_only_pass

    best_single_entry = {"variant": best_entry_key, "rr": best_entry_rr, "passes_min_rr": entry_only_pass}
    best_single_sl = {"variant": best_sl_label, "rr": best_sl_rr, "passes_min_rr": sl_only_pass}
    best_single_tp = {"variant": best_tp_label, "rr": best_tp_rr, "passes_min_rr": tp_only_pass}

    # H1 target age
    h1_target_age_hours = None
    h1_target_age_bars = None
    target_timing = None
    if prod_tp:
        zone_created = int(zone_row.get("zone_created_at") or 0)
        pivot_ts = int(prod_tp.get("pivot_timestamp") or 0)
        if conf_unix and pivot_ts:
            h1_target_age_hours = round((conf_unix - pivot_ts) / 3600.0, 2)
        if prod_tp.get("index") is not None:
            h1_target_age_bars = prod_tp["index"]
        if pivot_ts < zone_created:
            target_timing = "before_zone"
        elif pivot_ts < retest_at:
            target_timing = "after_zone_before_retest"
        else:
            target_timing = "after_retest"

    # RR decay
    decay_rows: list[dict[str, Any]] = []
    rr_at_retest = None
    rr_best_before = None
    rr_at_conf = e0_rr
    if retest_at and conf_unix > retest_at:
        bars = m15_bars_from_to(m15, retest_at, conf_unix)
        for bar in bars:
            e_obs = float(bar["close"])
            sl_obs = production_sl(zone_distal, zone_atr, direction)
            h1_slice = slice_closed(h1, int(bar["time"]))
            tp_obs = production_tp(h1_slice, e_obs, direction, int(bar["time"]))
            if not tp_obs:
                continue
            g = _geom(direction, e_obs, sl_obs, float(tp_obs["price"]), min_rr=min_rr)
            decay_rows.append(
                {
                    "m15_time": int(bar["time"]),
                    "entry_observed": e_obs,
                    "sl": sl_obs,
                    "tp": tp_obs["price"],
                    "risk": g.get("risk"),
                    "reward": g.get("reward"),
                    "rr": g.get("rr"),
                    "geometry_valid": g.get("geometry_valid"),
                    "diagnostic_only": DIAGNOSTIC_ONLY,
                }
            )
            if g["rr"] is not None:
                if rr_at_retest is None:
                    rr_at_retest = g["rr"]
                if rr_best_before is None or g["rr"] > rr_best_before:
                    rr_best_before = g["rr"]

    rr_decay_abs = None
    rr_decay_pct = None
    if rr_at_retest is not None and rr_at_conf is not None:
        rr_decay_abs = round(rr_at_conf - rr_at_retest, 4)
        if rr_at_retest != 0:
            rr_decay_pct = round((rr_decay_abs / rr_at_retest) * 100, 2)

    early_entry = entry_vars.get(best_entry_key) or entry_vars.get("E1")
    entry_delay_price = None
    entry_delay_pips = None
    if early_entry and best_entry_key != "E0":
        ep = float(early_entry["entry"])
        if direction.lower() == "buy":
            entry_delay_price = round(entry - ep, 4)
        else:
            entry_delay_price = round(ep - entry, 4)
        entry_delay_pips = _pips(entry_delay_price)

    decomposition = {
        "strategy": strategy,
        "zone_id": zone_id,
        "direction": direction,
        "first_retest_at": retest_at,
        "confirmation_at": conf_unix,
        "CURRENT_GEOMETRY": {
            "entry": entry,
            "entry_source": "actual_confirmation_close",
            "zone_proximal": zone_prox,
            "zone_distal": zone_distal,
            "zone_atr": zone_atr,
            "current_sl": sl,
            "current_sl_buffer": round(buffer, 4),
            "current_sl_distance": geom.get("risk"),
            "current_tp": tp,
            "current_tp_source": "production_h1_last_chronological",
            "current_tp_pivot_timestamp": prod_tp.get("pivot_timestamp") if prod_tp else None,
            "current_tp_distance": geom.get("reward"),
            "current_risk": geom.get("risk"),
            "current_reward": geom.get("reward"),
            "current_rr": geom.get("rr"),
            "geometry_valid": geom.get("geometry_valid"),
            "passes_min_rr": geom.get("passes_min_rr"),
            "MIN_RR": min_rr,
            "rr_shortfall": round(min_rr - (geom.get("rr") or 0), 4) if geom.get("rr") is not None else None,
            "required_tp_at_min_rr": geom.get("required_tp_at_min_rr"),
            "target_shortfall_price": geom.get("target_shortfall_price"),
            "target_shortfall_pips": geom.get("target_shortfall_pips"),
            "target_shortfall_r": geom.get("target_shortfall_r"),
            "target_exceeds_min_rr": geom.get("target_exceeds_min_rr"),
            "literal_formulas": literal,
            "sl_decomposition": {
                "zone_structure_risk": round(zone_structure_risk, 4),
                "buffer_risk": round(buffer_risk, 4),
                "buffer_percent_of_total_risk": buffer_pct,
            },
            "production_tp_matches_rule": selected_match,
            "diagnostic_only": DIAGNOSTIC_ONLY,
        },
        "entry_delay": {
            "entry_delay_price": entry_delay_price,
            "entry_delay_pips": entry_delay_pips,
            "m15_bars_retest_to_confirmation": retest.get("bars_to_confirmation"),
            "best_earlier_entry_variant": best_entry_key,
            "rr_at_best_earlier_entry": best_entry_rr,
            "rr_delta": delta_entry,
        },
        "ROOT_CAUSE": root,
        "ROOT_CAUSE_EVIDENCE": {
            "delta_RR_entry": delta_entry,
            "delta_RR_sl": delta_sl,
            "delta_RR_tp": delta_tp,
            "best_entry_variant": best_entry_key,
            "best_sl_variant": best_sl_label,
            "best_tp_variant": best_tp_label,
        },
        "MIN_RR_CROSSING": {
            "current_rr": e0_rr,
            "entry_only_rr": best_entry_rr,
            "entry_only_pass": entry_only_pass,
            "sl_only_rr": best_sl_rr,
            "sl_only_pass": sl_only_pass,
            "tp_only_rr": best_tp_rr,
            "tp_only_pass": tp_only_pass,
        },
        "BEST_SINGLE_VARIABLE": {
            "entry": best_single_entry,
            "sl": best_single_sl,
            "tp": best_single_tp,
            "combined": best_combo,
        },
        "RR_REFERENCE_LEVELS": {**rr_ref, "diagnostic_only": DIAGNOSTIC_ONLY},
        "TP_SHORTFALL_1_4R": {
            "required_1_4R_price": geom.get("required_tp_at_min_rr"),
            "current_tp": tp,
            "price_shortfall": geom.get("target_shortfall_price"),
            "pip_shortfall": geom.get("target_shortfall_pips"),
            "r_shortfall": geom.get("target_shortfall_r"),
            "target_exceeds_min_rr": geom.get("target_exceeds_min_rr"),
        },
        "COMBINED_MATRIX_BEST": {
            **best_combo,
            "rr": best_combo_rr,
            "passes_min_rr": best_combo_rr >= min_rr,
            "single_variable_sufficient": single_var_sufficient,
            "combination_required": best_combo_rr >= min_rr and not single_var_sufficient,
        },
        "RECONCILIATION_AUDIT": literal,
        "H1_TARGET_AGE": {
            "hours": h1_target_age_hours,
            "h1_bars": h1_target_age_bars,
            "target_timing_vs_zone": target_timing,
        },
        "sl_alternatives": sl_rows,
        "tp_alternatives": tp_rows,
    }

    return {
        "decomposition": decomposition,
        "entry_timing": entry_timing_rows,
        "sl_rows": sl_rows,
        "tp_rows": tp_rows,
        "matrix": matrix,
        "h1_audit": {
            "zone_id": zone_id,
            "strategy": strategy,
            "entry_unix": conf_unix,
            "eligible_targets_sorted_nearest_first": targets,
            "CURRENT_SELECTED_TARGET": prod_tp,
            "production_rule_match": selected_match,
            "diagnostic_only": DIAGNOSTIC_ONLY,
        },
        "opposing_zones": {
            "zone_id": zone_id,
            "strategy": strategy,
            "nearest_opposing_m30_zones": opposing,
            "diagnostic_only": DIAGNOSTIC_ONLY,
        },
        "rr_decay": {
            "zone_id": zone_id,
            "strategy": strategy,
            "RR_at_retest": rr_at_retest,
            "RR_best_before_confirmation": rr_best_before,
            "RR_at_confirmation": rr_at_conf,
            "RR_decay_absolute": rr_decay_abs,
            "RR_decay_percent": rr_decay_pct,
            "series": decay_rows,
            "diagnostic_only": DIAGNOSTIC_ONLY,
        },
    }


def analyze_retest_geometry(
    retest: dict[str, Any],
    zone_row: dict[str, Any],
    m15: list[dict],
    h1: list[dict],
    min_rr: float = MIN_RR_RATIO,
) -> dict[str, Any]:
    retest_at = int(retest.get("zone_retest_at") or 0)
    if not retest_at:
        return {"zone_id": retest.get("zone_id"), "status": "NO_RETEST"}

    direction = "BUY" if retest.get("zone_type") == "RBR" else "SELL"
    distal = float(retest.get("distal") or zone_row.get("distal") or 0)
    prox = float(retest.get("proximal") or zone_row.get("proximal") or 0)
    zone_atr = float(zone_row.get("m30_atr") or 0)
    width = float(retest.get("zone_width") or abs(prox - distal))

    h1_slice = slice_closed(h1, retest_at)
    sl = production_sl(distal, zone_atr, direction)

    observations: list[dict[str, Any]] = []
    for label, entry_px in [
        ("zone_proximal", prox),
        ("first_m15_close_after_retest", None),
    ]:
        slot_status = "valid"
        if label.endswith("after_retest"):
            bar = first_m15_after(m15, retest_at)
            if not bar:
                observations.append(
                    {
                        "hypothetical_entry_label": label,
                        "status": "N/A",
                        "reason": "no_m15_bar_after_retest",
                        "diagnostic_only": GEOMETRY_OBSERVATION_ONLY,
                    }
                )
                continue
            entry_px = float(bar["close"])
        tp_sel = production_tp(h1_slice, float(entry_px), direction, retest_at)
        if not tp_sel:
            observations.append(
                {
                    "hypothetical_entry_label": label,
                    "entry": entry_px,
                    "status": "no_eligible_tp",
                    "tp": None,
                    "rr": None,
                    "geometry_valid": False,
                    "diagnostic_only": GEOMETRY_OBSERVATION_ONLY,
                }
            )
            continue
        g = _geom(direction, float(entry_px), sl, float(tp_sel["price"]), min_rr=min_rr)
        if not g.get("geometry_valid"):
            slot_status = "invalid_geometry"
        observations.append(
            {
                "hypothetical_entry_label": label,
                "entry": entry_px,
                "sl": sl,
                "tp": tp_sel["price"],
                "nearest_h1_target": tp_sel,
                "risk": g.get("risk"),
                "reward": g.get("reward"),
                "rr": g.get("rr"),
                "geometry_valid": g.get("geometry_valid"),
                "rr_ge_min": g.get("passes_min_rr"),
                "status": slot_status,
                "diagnostic_only": GEOMETRY_OBSERVATION_ONLY,
            }
        )

    nearest = production_tp(h1_slice, prox, direction, retest_at)

    return {
        "zone_id": retest.get("zone_id"),
        "zone_type": retest.get("zone_type"),
        "zone_retest_at": retest_at,
        "zone_proximal": prox,
        "zone_distal": distal,
        "zone_width": width,
        "nearest_eligible_h1_at_retest": nearest,
        "structural_sl_m30_distal": sl,
        "observations": observations,
        "entry_valid_signal": NO_SIGNAL,
        "label": GEOMETRY_OBSERVATION_ONLY,
        "diagnostic_only": DIAGNOSTIC_ONLY,
    }


def compare_geometry_diagnostics(old_dir: Path, new_dir: Path) -> dict[str, Any]:
    old_rows = {r.get("zone_id"): r for r in _load_jsonl(old_dir / "v2_candidate_geometry_decomposition.jsonl")}
    new_rows = {r.get("zone_id"): r for r in _load_jsonl(new_dir / "v2_candidate_geometry_decomposition.jsonl")}
    rows = []
    for zid in sorted(set(old_rows) | set(new_rows)):
        o, n = old_rows.get(zid), new_rows.get(zid)
        if not o or not n:
            continue
        oc, nc = o.get("CURRENT_GEOMETRY") or {}, n.get("CURRENT_GEOMETRY") or {}
        om, nm = o.get("MIN_RR_CROSSING") or {}, n.get("MIN_RR_CROSSING") or {}
        rows.append(
            {
                "zone_id": zid,
                "current_rr": {"old": oc.get("current_rr"), "new": nc.get("current_rr")},
                "required_tp_1_4": {
                    "old": (o.get("TP_SHORTFALL_1_4R") or {}).get("required_1_4R_price"),
                    "new": (n.get("TP_SHORTFALL_1_4R") or {}).get("required_1_4R_price"),
                },
                "entry_only_rr": {"old": om.get("entry_only_rr"), "new": nm.get("entry_only_rr")},
                "sl_only_rr": {"old": om.get("sl_only_rr"), "new": nm.get("sl_only_rr")},
                "tp_only_rr": {"old": om.get("tp_only_rr"), "new": nm.get("tp_only_rr")},
                "entry_only_pass": {"old": om.get("entry_only_pass"), "new": nm.get("entry_only_pass")},
                "sl_only_pass": {"old": om.get("sl_only_pass"), "new": nm.get("sl_only_pass")},
                "tp_only_pass": {"old": om.get("tp_only_pass"), "new": nm.get("tp_only_pass")},
                "root_cause": {"old": o.get("ROOT_CAUSE"), "new": n.get("ROOT_CAUSE")},
            }
        )
    out_doc = {"old_run": old_dir.name, "new_run": new_dir.name, "candidates": rows}
    with (new_dir / "geometry_diagnostic_reconciliation.json").open("w", encoding="utf-8") as f:
        json.dump(out_doc, f, indent=2, sort_keys=True)
        f.write("\n")
    lines = ["GEOMETRY DIAGNOSTIC RECONCILIATION", f"Old: {old_dir.name}", f"New: {new_dir.name}", ""]
    for r in rows:
        lines.append(json.dumps(r, indent=2))
    (new_dir / "geometry_diagnostic_reconciliation.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out_doc


def collect_candidates_from_sources(source_dirs: list[tuple[Path, str]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for run_dir, strategy in source_dirs:
        run_dir = Path(run_dir)
        doc = _load_json(run_dir / "v2_candidate_geometry.json")
        for c in doc.get("candidates") or []:
            zid = c.get("zone_id") or ""
            key = f"{strategy}:{zid}:{c.get('entry')}"
            if key in seen:
                continue
            seen.add(key)
            row = dict(c)
            row["strategy"] = strategy
            row["source_run"] = run_dir.name
            out.append(row)
    return out


def run_geometry_diagnostic(
    dataset_dir: Path,
    source_dirs: list[tuple[Path, str]],
    out_dir: Path,
    retest_source: Path | None = None,
    min_rr: float | None = None,
    compare_old_dir: Path | None = None,
) -> dict[str, Any]:
    """Run passive geometry diagnostic; writes artifacts to out_dir."""
    dataset_dir = Path(dataset_dir)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    min_rr = float(min_rr or MIN_RR_RATIO)

    meta_ds = load_dataset_meta(dataset_dir)
    m15 = candles_to_dicts(load_candles_csv(dataset_dir / "M15.csv", "M15"))
    h1 = candles_to_dicts(load_candles_csv(dataset_dir / "H1.csv", "H1"))

    candidates = collect_candidates_from_sources(source_dirs)

    # Retest/zone audits from retest source run
    rs_path = Path(retest_source) if retest_source else Path(source_dirs[-1][0])
    if not rs_path.is_absolute() and not (rs_path / "v2_retest_audit.jsonl").is_file():
        # caller may pass run id only
        alt = Path(source_dirs[-1][0]).parent / rs_path.name
        if (alt / "v2_retest_audit.jsonl").is_file():
            rs_path = alt
    retests = {r["zone_id"]: r for r in _load_jsonl(rs_path / "v2_retest_audit.jsonl")}
    zones = {z["zone_id"]: z for z in _load_jsonl(rs_path / "v2_zone_audit.jsonl")}
    m30_zone_list = list(zones.values())

    decomp_rows: list[dict[str, Any]] = []
    entry_rows: list[dict[str, Any]] = []
    sl_rows: list[dict[str, Any]] = []
    tp_rows: list[dict[str, Any]] = []
    matrix_rows: list[dict[str, Any]] = []
    h1_rows: list[dict[str, Any]] = []
    opposing_rows: list[dict[str, Any]] = []
    decay_rows: list[dict[str, Any]] = []
    retest_rows: list[dict[str, Any]] = []

    for cand in candidates:
        zid = cand.get("zone_id")
        retest = retests.get(zid) or {}
        zone_row = zones.get(zid) or {}
        result = analyze_candidate(cand, retest, zone_row, m15, h1, m30_zone_list, min_rr=min_rr)
        decomp_rows.append(result["decomposition"])
        for er in result["entry_timing"]:
            er["zone_id"] = zid
            er["strategy"] = cand.get("strategy")
            entry_rows.append(er)
        for sr in result["sl_rows"]:
            sr["zone_id"] = zid
            sr["strategy"] = cand.get("strategy")
            sl_rows.append(sr)
        for tr in result["tp_rows"]:
            tr["zone_id"] = zid
            tr["strategy"] = cand.get("strategy")
            tp_rows.append(tr)
        for mr in result["matrix"]:
            mr["zone_id"] = zid
            mr["strategy"] = cand.get("strategy")
            matrix_rows.append(mr)
        h1_rows.append(result["h1_audit"])
        opposing_rows.append(result["opposing_zones"])
        dr = result["rr_decay"]
        dr["zone_id"] = zid
        dr["strategy"] = cand.get("strategy")
        decay_rows.append(dr)

    for zid, retest in retests.items():
        if not retest.get("zone_retest_at"):
            continue
        zone_row = zones.get(zid) or {}
        retest_rows.append(analyze_retest_geometry(retest, zone_row, m15, h1, min_rr=min_rr))

    retests_ge_14 = sum(
        1
        for r in retest_rows
        for o in r.get("observations") or []
        if o.get("rr_ge_min")
    )
    retests_below = sum(
        1
        for r in retest_rows
        for o in r.get("observations") or []
        if o.get("rr") is not None and not o.get("rr_ge_min")
    )
    obs_generated = sum(len(r.get("observations") or []) for r in retest_rows)
    obs_valid = sum(
        1 for r in retest_rows for o in (r.get("observations") or []) if o.get("status") == "valid"
    )
    obs_na = sum(
        1 for r in retest_rows for o in (r.get("observations") or []) if o.get("status") == "N/A"
    )
    obs_invalid = sum(
        1 for r in retest_rows for o in (r.get("observations") or []) if o.get("status") == "invalid_geometry"
    )
    obs_no_tp = sum(
        1 for r in retest_rows for o in (r.get("observations") or []) if o.get("status") == "no_eligible_tp"
    )
    total_retests = len(retest_rows)
    expected_slots = total_retests * 2
    retest_reconciliation = {
        "total_retests": total_retests,
        "expected_observation_slots": expected_slots,
        "actual_generated_observations": obs_generated,
        "valid_observations": obs_valid,
        "na_observations": obs_na,
        "invalid_geometry_observations": obs_invalid,
        "no_eligible_tp_observations": obs_no_tp,
        "rr_ge_min_rr": retests_ge_14,
        "rr_below_min_rr": retests_below,
        "counts_reconcile": obs_generated == obs_valid + obs_na + obs_invalid + obs_no_tp,
    }

    current_rrs = [d["CURRENT_GEOMETRY"]["current_rr"] for d in decomp_rows if d["CURRENT_GEOMETRY"].get("current_rr")]
    delta_entries = [d["ROOT_CAUSE_EVIDENCE"]["delta_RR_entry"] for d in decomp_rows]
    delta_sls = [d["ROOT_CAUSE_EVIDENCE"]["delta_RR_sl"] for d in decomp_rows]
    delta_tps = [d["ROOT_CAUSE_EVIDENCE"]["delta_RR_tp"] for d in decomp_rows]

    def _med(vals: list[float]) -> float | None:
        return round(statistics.median(vals), 4) if vals else None

    aggregate = {
        "sample_size_candidates": len(decomp_rows),
        "sample_size_retests": len(retest_rows),
        "current_rr": {
            "min": min(current_rrs) if current_rrs else None,
            "median": _med(current_rrs),
            "mean": round(statistics.mean(current_rrs), 4) if current_rrs else None,
            "max": max(current_rrs) if current_rrs else None,
        },
        "rr_improvement_entry_only": {"median_delta": _med(delta_entries), "max_delta": max(delta_entries) if delta_entries else None},
        "rr_improvement_sl_only": {"median_delta": _med(delta_sls), "max_delta": max(delta_sls) if delta_sls else None},
        "rr_improvement_tp_only": {"median_delta": _med(delta_tps), "max_delta": max(delta_tps) if delta_tps else None},
        "count_entry_only_ge_min_rr": sum(1 for d in decomp_rows if d["MIN_RR_CROSSING"]["entry_only_pass"]),
        "count_sl_only_ge_min_rr": sum(1 for d in decomp_rows if d["MIN_RR_CROSSING"]["sl_only_pass"]),
        "count_tp_only_ge_min_rr": sum(1 for d in decomp_rows if d["MIN_RR_CROSSING"]["tp_only_pass"]),
        "count_combination_required": sum(
            1 for d in decomp_rows if d["COMBINED_MATRIX_BEST"].get("combination_required")
        ),
        "retest_observations_ge_1_4": retests_ge_14,
        "retest_observations_below_1_4": retests_below,
        "retest_observation_reconciliation": retest_reconciliation,
        "no_future_target_usage": True,
        "diagnostic_only": DIAGNOSTIC_ONLY,
        "note": "Small sample — not statistically significant.",
    }

    semantic_payload = {
        "decomposition": decomp_rows,
        "entry_timing": entry_rows,
        "sl": sl_rows,
        "tp": tp_rows,
        "matrix": matrix_rows,
        "retest": retest_rows,
        "decay": decay_rows,
        "h1": h1_rows,
        "opposing": opposing_rows,
    }
    semantic_hash_val = semantic_hash(
        [semantic_payload[k] for k in sorted(semantic_payload.keys())]
    )

    summary = {
        "run_type": "v2_geometry_diagnostic",
        "dataset_id": meta_ds.get("dataset_id"),
        "symbol": meta_ds.get("symbol"),
        "MIN_RR": min_rr,
        "STRATEGY_MIN_RR": STRATEGY_MIN_RR,
        "candidates_analyzed": len(decomp_rows),
        "first_retests_observed": len(retest_rows),
        "aggregate": aggregate,
        "semantic_hash": semantic_hash_val,
        "sources": [{"run_id": str(p.name), "strategy": s} for p, s in source_dirs],
        "finish_time": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "diagnostic_only": DIAGNOSTIC_ONLY,
        "candidates": decomp_rows,
    }

    _write_jsonl(out_dir / "v2_candidate_geometry_decomposition.jsonl", decomp_rows)
    _write_jsonl(out_dir / "v2_entry_timing_audit.jsonl", entry_rows)
    _write_jsonl(out_dir / "v2_sl_alternatives.jsonl", sl_rows)
    _write_jsonl(out_dir / "v2_tp_alternatives.jsonl", tp_rows)
    _write_jsonl(out_dir / "v2_rr_matrix.jsonl", matrix_rows)
    _write_jsonl(out_dir / "v2_retest_geometry.jsonl", retest_rows)
    _write_jsonl(out_dir / "v2_rr_decay.jsonl", decay_rows)
    _write_jsonl(out_dir / "v2_h1_target_audit.jsonl", h1_rows)
    _write_jsonl(out_dir / "v2_opposing_zone_audit.jsonl", opposing_rows)

    summary_path = out_dir / "v2_geometry_summary.json"
    with summary_path.open("w", encoding="utf-8") as f:
        json.dump({k: v for k, v in summary.items() if k != "candidates"}, f, indent=2, sort_keys=True)
        f.write("\n")

    txt_lines = [
        "V2 GEOMETRY DIAGNOSTIC SUMMARY",
        f"Candidates: {len(decomp_rows)}",
        f"Retests observed: {len(retest_rows)}",
        f"Semantic hash: {semantic_hash_val}",
        "",
        "AGGREGATE (diagnostic only)",
        json.dumps(aggregate, indent=2),
    ]
    (out_dir / "v2_geometry_summary.txt").write_text("\n".join(txt_lines) + "\n", encoding="utf-8")

    run_meta = {
        "run_id": out_dir.name,
        "run_type": "v2_geometry_diagnostic",
        "dataset_id": meta_ds.get("dataset_id"),
        "symbol": meta_ds.get("symbol"),
        "geometry_diagnostic": True,
        "semantic_hash": semantic_hash_val,
        "candidates_analyzed": len(decomp_rows),
        "sources": [p.name for p, _ in source_dirs],
    }
    with (out_dir / "meta.json").open("w", encoding="utf-8") as f:
        json.dump(run_meta, f, indent=2, sort_keys=True)
        f.write("\n")

    reconciliation = None
    if compare_old_dir and Path(compare_old_dir).is_dir():
        reconciliation = compare_geometry_diagnostics(Path(compare_old_dir), out_dir)

    return {
        "out_dir": str(out_dir),
        "semantic_hash": semantic_hash_val,
        "summary": summary,
        "aggregate": aggregate,
        "reconciliation": reconciliation,
    }
