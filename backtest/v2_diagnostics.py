"""
Passive V2 diagnostics — OBSERVE only.

Must never alter StrategyDecision, guards, or execution.
Uses the same helper functions as production for measurement only.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from core.config import MIN_RR_RATIO, SL_PIP_SIZE, STRATEGY_MIN_RR
from core.strategies.v2_structure import (
    atr14,
    confirmed_swing_highs,
    confirmed_swing_lows,
    fib_overlap_for_impulse,
    h1_structure_bias,
)
from core.strategies.v2_zones import (
    BASE_BODY_RATIO_MAX,
    BASE_TR_ATR,
    BASE_WIDTH_ATR,
    Zone,
    _departure_ok,
    _leg_ok,
    _prior_swing_high,
    _prior_swing_low,
    body_size,
    candle_range,
    demand_boundaries,
    is_valid_base,
    supply_boundaries,
    true_range,
    try_build_dbd,
    try_build_rbr,
)


def _pips(price_dist: float) -> float:
    return round(abs(price_dist) / SL_PIP_SIZE, 2) if SL_PIP_SIZE else round(abs(price_dist), 4)


def diagnose_rbr_failure(candles: list[dict], base_end: int) -> str | None:
    """Primary rejection if RBR cannot form. None means a zone would succeed."""
    if try_build_rbr(candles, base_end, "DIAG") is not None:
        return None
    atr = atr14(candles, base_end)
    if atr is None:
        return "INSUFFICIENT_ATR_HISTORY"
    saw_valid_base = False
    last = "BASE_INVALID"
    for length in (1, 2, 3, 4):
        start = base_end - length + 1
        if start < 1:
            continue
        if not is_valid_base(candles, start, base_end, atr):
            base = candles[start : base_end + 1]
            for i, c in enumerate(base):
                abs_i = start + i
                prev = float(candles[abs_i - 1]["close"]) if abs_i > 0 else None
                rng = candle_range(c)
                if rng <= 0:
                    last = "BASE_ZERO_RANGE"
                    break
                if true_range(c, prev) > BASE_TR_ATR * atr:
                    last = "BASE_RANGE_TOO_LARGE"
                    break
                if body_size(c) / rng > BASE_BODY_RATIO_MAX:
                    last = "BASE_BODY_TOO_LARGE"
                    break
            else:
                for i in range(len(base) - 1):
                    a, b = base[i], base[i + 1]
                    if not (
                        min(float(a["high"]), float(b["high"]))
                        > max(float(a["low"]), float(b["low"]))
                    ):
                        last = "BASE_NO_OVERLAP"
                        break
                else:
                    width = max(float(c["high"]) for c in base) - min(float(c["low"]) for c in base)
                    if width > BASE_WIDTH_ATR * atr:
                        last = "BASE_RANGE_TOO_LARGE"
            continue
        saw_valid_base = True
        leg_end = start - 1
        leg_start = max(0, leg_end - 4)
        if not _leg_ok(candles, leg_start, leg_end, direction="rally", atr=atr):
            last = "INCOMING_LEG_TOO_WEAK"
            continue
        _distal, proximal = demand_boundaries(candles[start : base_end + 1])
        ok, dep_end, _, _ = _departure_ok(candles, base_end, proximal, direction="bull", atr=atr)
        if not ok or dep_end is None:
            last = "DEPARTURE_TOO_WEAK"
            continue
        bos = _prior_swing_high(candles, base_end + 1)
        if bos is None:
            last = "BOS_NOT_CONFIRMED"
            continue
        if float(candles[dep_end]["close"]) <= bos:
            last = "BOS_WICK_ONLY" if float(candles[dep_end]["high"]) > bos else "BOS_NOT_CONFIRMED"
            continue
        last = "ZONE_BUILD_FAILED"
    return last if saw_valid_base or last != "BASE_INVALID" else last


def diagnose_dbd_failure(candles: list[dict], base_end: int) -> str | None:
    if try_build_dbd(candles, base_end, "DIAG") is not None:
        return None
    atr = atr14(candles, base_end)
    if atr is None:
        return "INSUFFICIENT_ATR_HISTORY"
    saw_valid_base = False
    last = "BASE_INVALID"
    for length in (1, 2, 3, 4):
        start = base_end - length + 1
        if start < 1:
            continue
        if not is_valid_base(candles, start, base_end, atr):
            base = candles[start : base_end + 1]
            for i, c in enumerate(base):
                abs_i = start + i
                prev = float(candles[abs_i - 1]["close"]) if abs_i > 0 else None
                rng = candle_range(c)
                if rng <= 0:
                    last = "BASE_ZERO_RANGE"
                    break
                if true_range(c, prev) > BASE_TR_ATR * atr:
                    last = "BASE_RANGE_TOO_LARGE"
                    break
                if body_size(c) / rng > BASE_BODY_RATIO_MAX:
                    last = "BASE_BODY_TOO_LARGE"
                    break
            else:
                for i in range(len(base) - 1):
                    a, b = base[i], base[i + 1]
                    if not (
                        min(float(a["high"]), float(b["high"]))
                        > max(float(a["low"]), float(b["low"]))
                    ):
                        last = "BASE_NO_OVERLAP"
                        break
                else:
                    width = max(float(c["high"]) for c in base) - min(float(c["low"]) for c in base)
                    if width > BASE_WIDTH_ATR * atr:
                        last = "BASE_RANGE_TOO_LARGE"
            continue
        saw_valid_base = True
        leg_end = start - 1
        leg_start = max(0, leg_end - 4)
        if not _leg_ok(candles, leg_start, leg_end, direction="drop", atr=atr):
            last = "INCOMING_LEG_TOO_WEAK"
            continue
        _distal, proximal = supply_boundaries(candles[start : base_end + 1])
        ok, dep_end, _, _ = _departure_ok(candles, base_end, proximal, direction="bear", atr=atr)
        if not ok or dep_end is None:
            last = "DEPARTURE_TOO_WEAK"
            continue
        bos = _prior_swing_low(candles, base_end + 1)
        if bos is None:
            last = "BOS_NOT_CONFIRMED"
            continue
        if float(candles[dep_end]["close"]) >= bos:
            last = "BOS_WICK_ONLY" if float(candles[dep_end]["low"]) < bos else "BOS_NOT_CONFIRMED"
            continue
        last = "ZONE_BUILD_FAILED"
    return last


@dataclass
class V2DiagnosticCollector:
    """Passive observer attached by the runner for structure_pullback_v2."""

    evaluations: int = 0
    reason_codes: Counter = field(default_factory=Counter)
    stage_rejects: Counter = field(default_factory=Counter)
    m30_base_attempts_rbr: int = 0
    m30_base_attempts_dbd: int = 0
    valid_bases_rbr: int = 0
    valid_bases_dbd: int = 0
    valid_departures_rbr: int = 0
    valid_departures_dbd: int = 0
    bos_confirmed_rbr: int = 0
    bos_confirmed_dbd: int = 0
    zones: dict[str, dict[str, Any]] = field(default_factory=dict)
    retests: dict[str, dict[str, Any]] = field(default_factory=dict)
    candidates: list[dict[str, Any]] = field(default_factory=list)
    m15_categories: Counter = field(default_factory=Counter)
    direction_filter: dict[str, int] = field(
        default_factory=lambda: {
            "zones_total": 0,
            "after_h4": 0,
            "after_h1": 0,
            "after_both": 0,
            "rbr_before": 0,
            "dbd_before": 0,
            "rbr_both": 0,
            "dbd_both": 0,
        }
    )
    _seen_m30_keys: set[tuple[str, int]] = field(default_factory=set)
    _prev_zone_states: dict[str, str] = field(default_factory=dict)
    _h4_seen_for_zone: dict[str, set[str]] = field(default_factory=dict)
    guard_blocks: list[dict[str, Any]] = field(default_factory=list)

    def after_evaluate(
        self,
        plugin: Any,
        context: Any,
        decision: Any,
        ts_key: str,
        *,
        cursor_unix: int | None = None,
    ) -> None:
        self.evaluations += 1
        md = decision.metadata or {}
        rc = md.get("reason_code") or decision.reason
        self.reason_codes[str(rc)] += 1

        m30 = list(context.m30_candles or [])
        m15 = list(context.m15_candles or [])
        h1 = list(context.h1_candles or [])
        h4 = (context.h4_direction or "NEUTRAL").upper()
        h1s = h1_structure_bias(h1)

        # Diagnose newly seen M30 base ends (same keys as plugin._seen_base_ends)
        seen_plugin = getattr(plugin, "_seen_base_ends", set())
        new_keys = seen_plugin - self._seen_m30_keys
        if new_keys and m30:
            t_to_idx = {int(c["time"]): i for i, c in enumerate(m30)}
            for kind, t in new_keys:
                self._seen_m30_keys.add((kind, t))
                base_end = t_to_idx.get(t)
                if base_end is None or base_end + 1 >= len(m30):
                    continue
                if kind == "RBR":
                    self.m30_base_attempts_rbr += 1
                    z = try_build_rbr(m30, base_end, context.symbol)
                    if z is None:
                        reason = diagnose_rbr_failure(m30, base_end) or "ZONE_BUILD_FAILED"
                        self.stage_rejects[reason] += 1
                        if reason.startswith("BASE_"):
                            pass
                        elif reason in ("INCOMING_LEG_TOO_WEAK",):
                            self.valid_bases_rbr += 1
                        elif reason.startswith("DEPARTURE_"):
                            self.valid_bases_rbr += 1
                        elif reason.startswith("BOS_"):
                            self.valid_bases_rbr += 1
                            self.valid_departures_rbr += 1
                    else:
                        self.valid_bases_rbr += 1
                        self.valid_departures_rbr += 1
                        self.bos_confirmed_rbr += 1
                else:
                    self.m30_base_attempts_dbd += 1
                    z = try_build_dbd(m30, base_end, context.symbol)
                    if z is None:
                        reason = diagnose_dbd_failure(m30, base_end) or "ZONE_BUILD_FAILED"
                        self.stage_rejects[reason] += 1
                        if reason.startswith("BASE_"):
                            pass
                        elif reason in ("INCOMING_LEG_TOO_WEAK",):
                            self.valid_bases_dbd += 1
                        elif reason.startswith("DEPARTURE_"):
                            self.valid_bases_dbd += 1
                        elif reason.startswith("BOS_"):
                            self.valid_bases_dbd += 1
                            self.valid_departures_dbd += 1
                    else:
                        self.valid_bases_dbd += 1
                        self.valid_departures_dbd += 1
                        self.bos_confirmed_dbd += 1

        # Sync zone book (passive — read plugin state only)
        for zid, z in plugin._zones.items():
            st = z.state
            prev = self._prev_zone_states.get(zid)
            if zid not in self.zones:
                rec = self._zone_record(z, h4, h1s)
                self.zones[zid] = rec
                self.direction_filter["zones_total"] += 1
                if z.zone_type == "RBR":
                    self.direction_filter["rbr_before"] += 1
                else:
                    self.direction_filter["dbd_before"] += 1
                # Creation-time direction filter (observational)
                h4_ok = (z.zone_type == "RBR" and h4 == "BULL") or (
                    z.zone_type == "DBD" and h4 == "BEAR"
                )
                h1_ok = (z.zone_type == "RBR" and h1s == "BULLISH") or (
                    z.zone_type == "DBD" and h1s == "BEARISH"
                )
                if h4_ok:
                    self.direction_filter["after_h4"] += 1
                if h1_ok:
                    self.direction_filter["after_h1"] += 1
                if h4_ok and h1_ok:
                    self.direction_filter["after_both"] += 1
                    if z.zone_type == "RBR":
                        self.direction_filter["rbr_both"] += 1
                    else:
                        self.direction_filter["dbd_both"] += 1
            else:
                rec = self.zones[zid]
                rec["final_zone_status"] = st
                rec["touch_count"] = z.touch_count
                if z.invalidated_at:
                    rec["invalidated_at"] = z.invalidated_at
                    rec["primary_rejection_reason"] = "ZONE_INVALIDATED"
                if st == "CONSUMED" and not rec.get("consumed_at"):
                    rec["consumed_at"] = cursor_unix or z.zone_entry_time

            aligned = (z.zone_type == "RBR" and h4 == "BULL" and h1s == "BULLISH") or (
                z.zone_type == "DBD" and h4 == "BEAR" and h1s == "BEARISH"
            )
            if aligned:
                rec["direction_aligned_ever"] = True

            # First-retest audit: zone_entry_time is set on first touch (may already be CONSUMED same bar)
            if zid not in self.retests and z.zone_entry_time and z.touch_count >= 1:
                self._open_retest(z, h4, h1s, m15, h1, ts_key)
            if zid in self.retests and self.retests[zid].get("final_result") is None:
                self._update_retest(z, m15, h1, h4, h1s, ts_key, decision)
                if z.meta.get("left_zone_after_retest"):
                    self.retests[zid]["left_zone_after_retest"] = True
                    self.retests[zid]["left_zone_at"] = z.meta.get("left_zone_at")
                    # V2.1: left zone does not mean rejected yet
                    if not getattr(plugin, "consume_on_leave_zone", True):
                        self.retests[zid]["left_zone_before_confirmation"] = False
                ls = z.meta.get("local_structure")
                if isinstance(ls, dict) and ls:
                    self.retests[zid]["local_structure"] = dict(ls)
                    self.retests[zid]["reaction_pivot_price"] = ls.get("reaction_price")
                    self.retests[zid]["reaction_pivot_timestamp"] = ls.get("reaction_ts")
                    self.retests[zid]["reaction_pivot_confirmed_at"] = ls.get("reaction_confirmed_at")
                    self.retests[zid]["local_trigger_price"] = ls.get("trigger_price")
                    self.retests[zid]["local_trigger_timestamp"] = ls.get("trigger_ts")
                    self.retests[zid]["local_trigger_confirmed_at"] = ls.get("trigger_confirmed_at")
                    if ls.get("structure_shift_at"):
                        self.retests[zid]["m15_confirmation_ts"] = ls.get("structure_shift_at")
                        entry_t = int(z.zone_entry_time or self.retests[zid].get("zone_retest_at") or 0)
                        if entry_t and ls.get("structure_shift_at"):
                            # M15 bars ≈ 900s
                            self.retests[zid]["bars_to_confirmation"] = int(
                                (int(ls["structure_shift_at"]) - entry_t) / 900
                            )

            self._prev_zone_states[zid] = st

        if decision.action == "enter":
            self._record_candidate(decision, md, m15, h1, ts_key)

    def _zone_record(self, z: Zone, h4: str, h1s: str) -> dict[str, Any]:
        return {
            "zone_id": z.zone_id,
            "symbol": z.symbol,
            "zone_type": z.zone_type,
            "direction": "BUY" if z.zone_type == "RBR" else "SELL",
            "base_start": z.base_start,
            "base_end": z.base_end,
            "zone_created_at": z.created_at,
            "proximal": z.proximal,
            "distal": z.distal,
            "zone_width": z.width,
            "m30_atr": z.atr,
            "departure_end": z.departure_end,
            "departure_strength_atr": z.meta.get("departure_strength_atr"),
            "departure_max_body_ratio": z.meta.get("departure_body_ratio"),
            "bos_confirmed": True,
            "bos_level": z.bos_level,
            "h4_bias_at_creation": h4,
            "h1_structure_at_creation": h1s,
            "direction_aligned_at_creation": (
                (z.zone_type == "RBR" and h4 == "BULL" and h1s == "BULLISH")
                or (z.zone_type == "DBD" and h4 == "BEAR" and h1s == "BEARISH")
            ),
            "fresh_at_creation": True,
            "touch_count": 0,
            "first_retest_at": None,
            "invalidated_at": None,
            "consumed_at": None,
            "final_zone_status": z.state,
            "primary_rejection_reason": None,
            "direction_aligned_ever": False,
            "fib_overlap": z.fib_overlap,
            "fib_level_nearest": z.fib_level_nearest,
            "fib_retracement_pct": z.fib_retracement_pct,
        }

    def _open_retest(
        self, z: Zone, h4: str, h1s: str, m15: list[dict], h1: list[dict], ts_key: str
    ) -> None:
        side = "buy" if z.zone_type == "RBR" else "sell"
        entry_t = int(z.zone_entry_time or 0)
        # fib observational
        fib_overlap = False
        fib_lvl = None
        fib_pct = None
        if len(h1) >= 10:
            if side == "buy":
                impulse_start = float(min(c["low"] for c in h1[-20:]))
                impulse_end = float(max(c["high"] for c in h1[-20:]))
            else:
                impulse_start = float(max(c["high"] for c in h1[-20:]))
                impulse_end = float(min(c["low"] for c in h1[-20:]))
            fib_overlap, fib_lvl, fib_pct = fib_overlap_for_impulse(
                impulse_start, impulse_end, z.proximal, z.distal
            )
        self.retests[z.zone_id] = {
            "zone_id": z.zone_id,
            "zone_type": z.zone_type,
            "proximal": z.proximal,
            "distal": z.distal,
            "zone_width": z.width,
            "zone_created_at": z.created_at,
            "zone_retest_at": entry_t,
            "h4_bias_at_retest": h4,
            "h1_structure_at_retest": h1s,
            "aligned": (
                (side == "buy" and h4 == "BULL" and h1s == "BULLISH")
                or (side == "sell" and h4 == "BEAR" and h1s == "BEARISH")
            ),
            "aligned_while_waiting": (
                (side == "buy" and h4 == "BULL" and h1s == "BULLISH")
                or (side == "sell" and h4 == "BEAR" and h1s == "BEARISH")
            ),
            "entry_side": side.upper(),
            "m15_bars_observed": 0,
            "trigger_swing_available": False,
            "trigger_pivot_price": None,
            "trigger_pivot_candle_ts": None,
            "trigger_pivot_confirmed_at": None,
            "wick_broke_trigger": False,
            "close_broke_trigger": False,
            "m15_confirmation_ts": None,
            "bars_to_confirmation": None,
            "left_zone_before_confirmation": False,
            "invalidated_before_confirmation": False,
            "consumed_before_confirmation": False,
            "final_state": z.state,
            "final_result": None,
            "primary_reason": None,
            "mfe_price": 0.0,
            "mae_price": 0.0,
            "mfe_pips": 0.0,
            "mae_pips": 0.0,
            "near_miss_price": None,
            "near_miss_pips": None,
            "fib_overlap": fib_overlap,
            "fib_level_nearest": fib_lvl,
            "fib_retracement_pct": fib_pct,
            "m15_category": None,
            "counterfactual_rr_proximal": None,
            "counterfactual_rr_mid": None,
            "DIAGNOSTIC_ONLY": True,
        }
        if z.zone_id in self.zones:
            self.zones[z.zone_id]["first_retest_at"] = entry_t
            self.zones[z.zone_id]["touch_count"] = 1

    def _update_retest(
        self,
        z: Zone,
        m15: list[dict],
        h1: list[dict],
        h4: str,
        h1s: str,
        ts_key: str,
        decision: Any,
    ) -> None:
        r = self.retests[z.zone_id]
        if r.get("final_result") is not None:
            return
        entry_t = int(z.zone_entry_time or r["zone_retest_at"])
        side = r["entry_side"].lower()
        after = [c for c in m15 if int(c["time"]) >= entry_t]
        r["m15_bars_observed"] = len(after)
        r["final_state"] = z.state

        # MFE/MAE from retest
        if after:
            if side == "buy":
                mfe = max(float(c["high"]) for c in after) - float(after[0]["close"])
                mae = float(after[0]["close"]) - min(float(c["low"]) for c in after)
            else:
                mfe = float(after[0]["close"]) - min(float(c["low"]) for c in after)
                mae = max(float(c["high"]) for c in after) - float(after[0]["close"])
            r["mfe_price"] = round(max(0.0, mfe), 4)
            r["mae_price"] = round(max(0.0, mae), 4)
            r["mfe_pips"] = _pips(r["mfe_price"])
            r["mae_pips"] = _pips(r["mae_price"])

        # Trigger pivot analysis (same rules as strategy — observe only)
        m15_times = [int(c["time"]) for c in m15]
        try:
            entry_i = next(i for i, t in enumerate(m15_times) if t >= entry_t)
        except StopIteration:
            return
        scope_start = max(0, entry_i - 40)
        m15_scope = m15[scope_start:]
        swing_idx = None
        swing_px = None
        pivot_candle_ts = None
        pivot_confirmed_at = None
        if side == "buy":
            piv = confirmed_swing_highs(m15_scope, left=2, right=2)
            eligible = []
            for i, px in piv:
                abs_i = scope_start + i
                if abs_i + 2 >= len(m15):
                    continue
                eligible.append((abs_i, px))
            pre = [(i, px) for i, px in eligible if int(m15[i]["time"]) <= entry_t]
            pick = pre[-1] if pre else (eligible[-1] if eligible else None)
            if pick:
                swing_idx, swing_px = pick
                pivot_candle_ts = int(m15[swing_idx]["time"])
                pivot_confirmed_at = int(m15[swing_idx + 2]["time"]) if swing_idx + 2 < len(m15) else None
        else:
            piv = confirmed_swing_lows(m15_scope, left=2, right=2)
            eligible = []
            for i, px in piv:
                abs_i = scope_start + i
                if abs_i + 2 >= len(m15):
                    continue
                eligible.append((abs_i, px))
            pre = [(i, px) for i, px in eligible if int(m15[i]["time"]) <= entry_t]
            pick = pre[-1] if pre else (eligible[-1] if eligible else None)
            if pick:
                swing_idx, swing_px = pick
                pivot_candle_ts = int(m15[swing_idx]["time"])
                pivot_confirmed_at = int(m15[swing_idx + 2]["time"]) if swing_idx + 2 < len(m15) else None

        if swing_px is None:
            r["trigger_swing_available"] = False
            r["m15_category"] = "C_NO_TRIGGER_PIVOT"
        else:
            r["trigger_swing_available"] = True
            r["trigger_pivot_price"] = swing_px
            r["required_break_price"] = swing_px
            r["trigger_pivot_candle_ts"] = pivot_candle_ts
            r["trigger_pivot_confirmed_at"] = pivot_confirmed_at
            r["pivot_lookahead_ok"] = bool(
                pivot_confirmed_at is None
                or (pivot_candle_ts is not None and pivot_confirmed_at > pivot_candle_ts)
            )
            if pivot_candle_ts is not None and pivot_candle_ts <= entry_t:
                r["m15_category"] = r.get("m15_category") or "A_PIVOT_AVAILABLE_AT_RETEST"
            else:
                r["m15_category"] = r.get("m15_category") or "B_PIVOT_CONFIRMED_AFTER_RETEST"

            # wick / close after entry
            scan_from = max((swing_idx or 0) + 2, entry_i)
            best_close = None
            best_wick = None
            for j in range(scan_from, len(m15)):
                c = m15[j]
                if int(c["time"]) < entry_t:
                    continue
                if side == "buy":
                    best_close = float(c["close"]) if best_close is None else max(best_close, float(c["close"]))
                    best_wick = float(c["high"]) if best_wick is None else max(best_wick, float(c["high"]))
                    if float(c["high"]) > swing_px:
                        r["wick_broke_trigger"] = True
                    if float(c["close"]) > swing_px:
                        r["close_broke_trigger"] = True
                        r["m15_confirmation_ts"] = int(c["time"])
                        r["bars_to_confirmation"] = j - entry_i
                else:
                    best_close = float(c["close"]) if best_close is None else min(best_close, float(c["close"]))
                    best_wick = float(c["low"]) if best_wick is None else min(best_wick, float(c["low"]))
                    if float(c["low"]) < swing_px:
                        r["wick_broke_trigger"] = True
                    if float(c["close"]) < swing_px:
                        r["close_broke_trigger"] = True
                        r["m15_confirmation_ts"] = int(c["time"])
                        r["bars_to_confirmation"] = j - entry_i
            if best_close is not None and not r["close_broke_trigger"]:
                if side == "buy":
                    dist = swing_px - best_close
                else:
                    dist = best_close - swing_px
                r["near_miss_price"] = round(dist, 4)
                r["near_miss_pips"] = _pips(dist)
            if r["wick_broke_trigger"] and not r["close_broke_trigger"]:
                r["m15_category"] = "D_WICK_ONLY_BREAK"
            elif r["close_broke_trigger"]:
                r["m15_category"] = "E_CLOSE_THROUGH_PIVOT"

            # counterfactual RR at proximal / mid (diagnostic only)
            mid = (z.proximal + z.distal) / 2.0
            buffer = 0.10 * z.atr
            if side == "buy":
                sl = z.distal - buffer
                highs = confirmed_swing_highs(h1, left=2, right=2)
                tps = [p for _, p in highs if p > mid]
                if tps:
                    tp = tps[-1]
                    risk = mid - sl
                    r["counterfactual_rr_mid"] = round((tp - mid) / risk, 4) if risk > 0 else None
                    risk_p = z.proximal - sl
                    r["counterfactual_rr_proximal"] = (
                        round((tp - z.proximal) / risk_p, 4) if risk_p > 0 else None
                    )
            else:
                sl = z.distal + buffer
                lows = confirmed_swing_lows(h1, left=2, right=2)
                tps = [p for _, p in lows if p < mid]
                if tps:
                    tp = tps[-1]
                    risk = sl - mid
                    r["counterfactual_rr_mid"] = round((mid - tp) / risk, 4) if risk > 0 else None
                    risk_p = sl - z.proximal
                    r["counterfactual_rr_proximal"] = (
                        round((z.proximal - tp) / risk_p, 4) if risk_p > 0 else None
                    )

        # Track whether direction was aligned while waiting (strategy only tries M15 when aligned)
        aligned_now = (z.zone_type == "RBR" and h4 == "BULL" and h1s == "BULLISH") or (
            z.zone_type == "DBD" and h4 == "BEAR" and h1s == "BEARISH"
        )
        if aligned_now:
            r["aligned_while_waiting"] = True
        r["h4_bias_at_retest_latest"] = h4
        r["h1_structure_at_retest_latest"] = h1s

        # Terminal outcomes (category counted once in finalize)
        if z.state == "INVALIDATED":
            r["invalidated_before_confirmation"] = not r["close_broke_trigger"]
            r["final_result"] = "REJECTED"
            r["primary_reason"] = "ZONE_INVALIDATED"
            r["m15_category"] = "G_INVALIDATED_BEFORE_TRIGGER"
        elif z.state == "CONSUMED" and (decision.metadata or {}).get("reason_code") != "candidate_ready":
            r["consumed_before_confirmation"] = True
            if not r["close_broke_trigger"]:
                r["left_zone_before_confirmation"] = True
                r["final_result"] = "REJECTED"
                r["primary_reason"] = "LEFT_ZONE_WITHOUT_CONFIRMATION"
                r["m15_category"] = "F_LEFT_ZONE_BEFORE_TRIGGER"
            elif r.get("close_broke_trigger") and not (
                (decision.metadata or {}).get("reason_code") == "candidate_ready"
            ):
                # Close broke historically but not on the last bar → strategy consumes late break
                r["final_result"] = "REJECTED"
                r["primary_reason"] = "M15_CONFIRMATION_TIMEOUT"
                r["m15_category"] = "E_CLOSE_THROUGH_PIVOT"
        elif (decision.metadata or {}).get("reason_code") == "candidate_ready" and (
            decision.metadata or {}
        ).get("zone_id") == z.zone_id:
            r["final_result"] = "CONFIRMED"
            r["primary_reason"] = "CONFIRMED"
            r["m15_category"] = "E_CLOSE_THROUGH_PIVOT"
        elif decision.action == "enter" and (decision.metadata or {}).get("zone_id") == z.zone_id:
            r["final_result"] = "CONFIRMED"
            r["primary_reason"] = "CONFIRMED"
            r["m15_category"] = "E_CLOSE_THROUGH_PIVOT"

    def _record_candidate(
        self, decision: Any, md: dict, m15: list[dict], h1: list[dict], ts_key: str
    ) -> None:
        entry = decision.entry
        sl = decision.sl
        tp = decision.tp
        risk = abs((entry or 0) - (sl or 0))
        reward = abs((tp or 0) - (entry or 0))
        rr = (reward / risk) if risk > 0 else None
        required = float(MIN_RR_RATIO)
        # structural targets audit
        side = (decision.direction or "").lower()
        targets = []
        selected = None
        if side == "buy":
            for i, px in confirmed_swing_highs(h1, left=2, right=2):
                if entry is not None and px > entry:
                    targets.append(
                        {
                            "index": i,
                            "price": px,
                            "timestamp": int(h1[i]["time"]),
                            "distance": round(px - entry, 4),
                        }
                    )
            if targets:
                selected = targets[-1]
        else:
            for i, px in confirmed_swing_lows(h1, left=2, right=2):
                if entry is not None and px < entry:
                    targets.append(
                        {
                            "index": i,
                            "price": px,
                            "timestamp": int(h1[i]["time"]),
                            "distance": round(entry - px, 4),
                        }
                    )
            if targets:
                selected = targets[-1]
        self.candidates.append(
            {
                "timestamp": ts_key,
                "zone_id": md.get("zone_id"),
                "direction": (decision.direction or "").upper(),
                "zone_proximal": md.get("proximal"),
                "zone_distal": md.get("distal"),
                "m15_trigger_swing": md.get("m15_trigger_swing"),
                "m15_confirmation_close": md.get("m15_confirmation_close"),
                "entry": entry,
                "sl": sl,
                "tp": tp,
                "risk": round(risk, 4) if risk else None,
                "reward": round(reward, 4) if reward else None,
                "rr": round(rr, 4) if rr is not None else md.get("rr"),
                "required_rr": required,
                "rr_shortfall": round(required - (rr or 0), 4) if rr is not None else None,
                "eligible_h1_targets": targets,
                "selected_h1_target": selected,
                "NOTE": "DIAGNOSTIC_ONLY geometry at strategy emit time",
            }
        )

    def note_guard(self, candidate_id: str, first_block: str | None, would_execute: bool, rr: float | None) -> None:
        self.guard_blocks.append(
            {
                "candidate_id": candidate_id,
                "first_block": first_block,
                "would_execute": would_execute,
                "rr": rr,
            }
        )

    def finalize(self) -> None:
        # Close open retests still waiting; assign primary reason once
        for _zid, r in self.retests.items():
            if r.get("final_result") is None:
                r["final_result"] = "REJECTED"
                if not r.get("aligned_while_waiting") and not r.get("aligned"):
                    r["primary_reason"] = "H4_H1_NOT_ALIGNED_DURING_WAIT"
                    r["m15_category"] = r.get("m15_category") or "F_LEFT_ZONE_BEFORE_TRIGGER"
                elif not r.get("trigger_swing_available"):
                    r["primary_reason"] = "NO_M15_TRIGGER_SWING"
                    r["m15_category"] = r.get("m15_category") or "C_NO_TRIGGER_PIVOT"
                elif r.get("wick_broke_trigger") and not r.get("close_broke_trigger"):
                    r["primary_reason"] = "M15_WICK_BREAK_ONLY"
                    r["m15_category"] = "D_WICK_ONLY_BREAK"
                elif not r.get("close_broke_trigger"):
                    r["primary_reason"] = "M15_SHIFT_NOT_CONFIRMED"
                    r["m15_category"] = r.get("m15_category") or "C_NO_TRIGGER_PIVOT"
                    if r.get("trigger_swing_available"):
                        r["m15_category"] = "B_PIVOT_CONFIRMED_AFTER_RETEST"
                else:
                    r["primary_reason"] = r.get("primary_reason") or "OTHER"
            # Ensure category present
            if not r.get("m15_category"):
                r["m15_category"] = "OTHER"

    def build_funnel(self, plugin_stats: dict, guard_funnel: dict | None) -> dict[str, Any]:
        gf = guard_funnel or {}
        valid_rbr = int(plugin_stats.get("valid_rbr", len([z for z in self.zones.values() if z["zone_type"] == "RBR"])))
        valid_dbd = int(plugin_stats.get("valid_dbd", len([z for z in self.zones.values() if z["zone_type"] == "DBD"])))
        first_retests = int(plugin_stats.get("first_retests", len(self.retests)))
        m15_conf = int(
            plugin_stats.get(
                "m15_confirmations",
                sum(1 for r in self.retests.values() if r.get("final_result") == "CONFIRMED"),
            )
        )
        stages = []

        def add(name, inp, passed, extra=None):
            fail = max(0, int(inp) - int(passed))
            stages.append(
                {
                    "stage": name,
                    "input": int(inp),
                    "pass": int(passed),
                    "fail": fail,
                    "pass_pct": round(100.0 * passed / inp, 2) if inp else None,
                    "fail_pct": round(100.0 * fail / inp, 2) if inp else None,
                    **(extra or {}),
                }
            )

        attempts = self.m30_base_attempts_rbr + self.m30_base_attempts_dbd
        vb = self.valid_bases_rbr + self.valid_bases_dbd
        vd = self.valid_departures_rbr + self.valid_departures_dbd
        bos = self.bos_confirmed_rbr + self.bos_confirmed_dbd
        zones_total = valid_rbr + valid_dbd
        aligned = int(self.direction_filter.get("after_both") or 0)
        waiting = len(self.retests)
        trig = sum(1 for r in self.retests.values() if r.get("trigger_swing_available"))
        raw = int(gf.get("raw_candidates", len(self.candidates)))
        we = int(gf.get("would_execute", 0))
        buy = sum(1 for c in self.candidates if c.get("direction") == "BUY")
        sell = sum(1 for c in self.candidates if c.get("direction") == "SELL")

        add("ALL_EVALUATIONS", self.evaluations, self.evaluations)
        add("BASE_CANDIDATES", attempts, attempts, {"rbr": self.m30_base_attempts_rbr, "dbd": self.m30_base_attempts_dbd})
        add("VALID_BASES", attempts, vb)
        add("VALID_DEPARTURES", vb, vd)
        add("BOS_CONFIRMED", vd, bos)
        add("VALID_RBR_DBD_ZONES", bos, zones_total, {"rbr": valid_rbr, "dbd": valid_dbd})
        add("DIRECTION_ALIGNED_AT_CREATION", zones_total, aligned)
        add("FRESH_ZONES", zones_total, zones_total)  # first-touch policy; all start fresh
        add("FIRST_RETEST", zones_total, first_retests)
        add("WAITING_M15_CONFIRMATION", first_retests, waiting)
        add("M15_TRIGGER_SWING_AVAILABLE", waiting, trig)
        add("M15_STRUCTURE_SHIFT", waiting, m15_conf)
        add("RAW_STRATEGY_CANDIDATE", m15_conf, raw, {"buy": buy, "sell": sell})
        add("PRE_PRODUCTION_GUARDS", raw, we)
        add("WOULD_EXECUTE", we, we)
        return {
            "stages": stages,
            "valid_rbr": valid_rbr,
            "valid_dbd": valid_dbd,
            "first_retests": first_retests,
            "m15_confirmations": m15_conf,
            "raw_candidates": raw,
            "raw_buy": buy,
            "raw_sell": sell,
            "would_execute": we,
            "blocked": gf.get("blocked"),
            "first_blocking_reasons": gf.get("first_blocking_reasons"),
            "direction_filter": dict(self.direction_filter),
            "stage_rejects": dict(self.stage_rejects),
        }

    def write_artifacts(self, out_dir: Path, plugin_stats: dict, guard_funnel: dict | None) -> dict[str, Any]:
        self.finalize()
        out_dir = Path(out_dir)
        funnel = self.build_funnel(plugin_stats, guard_funnel)

        def wjson(name, obj):
            (out_dir / name).write_text(json.dumps(obj, indent=2, sort_keys=True) + "\n", encoding="utf-8")

        def wjsonl(name, rows):
            with (out_dir / name).open("w", encoding="utf-8") as f:
                for row in rows:
                    f.write(json.dumps(row, sort_keys=True) + "\n")

        wjson("v2_funnel.json", funnel)
        lines = ["V2 DIAGNOSTIC FUNNEL", ""]
        for s in funnel["stages"]:
            lines.append(
                f"{s['stage']:28} in={s['input']:<6} pass={s['pass']:<6} fail={s['fail']:<6} "
                f"pass%={s['pass_pct']}"
            )
        (out_dir / "v2_funnel.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")

        wjsonl("v2_zone_audit.jsonl", [self.zones[k] for k in sorted(self.zones)])
        wjsonl("v2_retest_audit.jsonl", [self.retests[k] for k in sorted(self.retests)])

        # M15 trigger diagnostics
        cats = dict(self.m15_categories)
        # ensure all retests counted
        for r in self.retests.values():
            cats.setdefault(r.get("m15_category") or "OTHER", 0)
        wjson(
            "v2_m15_trigger_diagnostics.json",
            {
                "categories": dict(Counter(r.get("m15_category") or "OTHER" for r in self.retests.values())),
                "first_retest_count": len(self.retests),
                "confirmed": sum(1 for r in self.retests.values() if r.get("final_result") == "CONFIRMED"),
                "NOTE": "Passive classification of first-retest outcomes",
            },
        )

        wjson("v2_rejection_reasons.json", {"stage_rejects": dict(self.stage_rejects), "evaluate_reason_codes": dict(self.reason_codes)})
        wjson("v2_candidate_geometry.json", {"candidates": self.candidates, "guards": self.guard_blocks})
        wjson(
            "v2_structural_target_audit.json",
            {
                "candidates": [
                    {
                        "timestamp": c["timestamp"],
                        "zone_id": c["zone_id"],
                        "eligible_h1_targets": c["eligible_h1_targets"],
                        "selected_h1_target": c["selected_h1_target"],
                    }
                    for c in self.candidates
                ]
            },
        )

        # Reaction / near-miss
        near_bins = Counter()
        reaction = {"meaningful_mfe": 0, "weak_mfe": 0, "wick_only": 0, "rows": []}
        for r in self.retests.values():
            if r.get("near_miss_pips") is not None:
                p = float(r["near_miss_pips"])
                if p <= 5:
                    near_bins["0-5"] += 1
                elif p <= 10:
                    near_bins["5-10"] += 1
                elif p <= 20:
                    near_bins["10-20"] += 1
                elif p <= 50:
                    near_bins["20-50"] += 1
                else:
                    near_bins[">50"] += 1
            if r.get("final_result") != "CONFIRMED":
                if float(r.get("mfe_pips") or 0) >= 20:
                    reaction["meaningful_mfe"] += 1
                else:
                    reaction["weak_mfe"] += 1
                if r.get("m15_category") == "D_WICK_ONLY_BREAK":
                    reaction["wick_only"] += 1
            reaction["rows"].append(
                {
                    "zone_id": r["zone_id"],
                    "mfe_pips": r.get("mfe_pips"),
                    "mae_pips": r.get("mae_pips"),
                    "primary_reason": r.get("primary_reason"),
                    "m15_category": r.get("m15_category"),
                }
            )
        wjson("v2_reaction_analysis.json", reaction)
        wjson("v2_near_miss_analysis.json", {"bins_pips": dict(near_bins), "NOTE": "distance of best close to trigger when no close break"})

        # Fib observation
        fib_yes = [r for r in self.retests.values() if r.get("fib_overlap")]
        fib_no = [r for r in self.retests.values() if not r.get("fib_overlap")]
        def avg_mfe(rows):
            if not rows:
                return None
            return round(sum(float(r.get("mfe_pips") or 0) for r in rows) / len(rows), 2)

        fib_obs = {
            "first_retests_fib_overlap": len(fib_yes),
            "first_retests_non_fib": len(fib_no),
            "avg_mfe_pips_fib": avg_mfe(fib_yes),
            "avg_mfe_pips_non_fib": avg_mfe(fib_no),
            "NOTE": "Observational only; Fib does not filter trades",
        }

        # time to confirmation distribution
        conf_dist = Counter()
        for r in self.retests.values():
            b = r.get("bars_to_confirmation")
            if b is None:
                continue
            if b <= 0:
                conf_dist["same_bar"] += 1
            elif b <= 2:
                conf_dist["1-2"] += 1
            elif b <= 4:
                conf_dist["3-4"] += 1
            elif b <= 8:
                conf_dist["5-8"] += 1
            else:
                conf_dist[">8"] += 1

        retest_outcomes = Counter(r.get("primary_reason") or "OTHER" for r in self.retests.values())
        summary = {
            "evaluations": self.evaluations,
            "funnel": funnel,
            "direction_filter": self.direction_filter,
            "first_retest_outcomes": dict(retest_outcomes),
            "m15_categories": dict(Counter(r.get("m15_category") or "OTHER" for r in self.retests.values())),
            "confirmation_bar_distribution": dict(conf_dist),
            "fib_observation": fib_obs,
            "candidates": len(self.candidates),
            "top_bottlenecks": self._rank_bottlenecks(funnel, retest_outcomes),
            "plugin_stats": plugin_stats,
        }
        wjson("v2_diagnostic_summary.json", summary)

        txt = [
            "V2 DIAGNOSTIC SUMMARY",
            "",
            f"Evaluations: {self.evaluations}",
            f"Valid RBR: {funnel['valid_rbr']}",
            f"Valid DBD: {funnel['valid_dbd']}",
            f"Direction aligned (ever): {self.direction_filter['after_both']}",
            f"First retests: {funnel['first_retests']}",
            f"M15 structure shifts: {funnel['m15_confirmations']}",
            f"Raw candidates: {funnel['raw_candidates']}",
            f"Would execute: {funnel['would_execute']}",
            "",
            "FIRST RETEST OUTCOMES",
            *[f"  {k}: {v}" for k, v in sorted(retest_outcomes.items(), key=lambda x: -x[1])],
            "",
            "M15 CATEGORIES",
            *[
                f"  {k}: {v}"
                for k, v in sorted(
                    Counter(r.get("m15_category") or "OTHER" for r in self.retests.values()).items(),
                    key=lambda x: -x[1],
                )
            ],
            "",
            "TOP BOTTLENECKS",
            *[f"  {i+1}. {b}" for i, b in enumerate(summary["top_bottlenecks"])],
            "",
            "NO RULE CHANGES. DIAGNOSTIC ONLY.",
            "",
        ]
        (out_dir / "v2_diagnostic_summary.txt").write_text("\n".join(txt), encoding="utf-8")
        # semantic hash payload
        semantic = {
            "funnel_stages": [(s["stage"], s["pass"], s["fail"]) for s in funnel["stages"]],
            "zone_ids": sorted(self.zones),
            "retest_ids": sorted(self.retests),
            "candidate_ts": [c["timestamp"] for c in self.candidates],
            "outcomes": sorted((k, retest_outcomes[k]) for k in retest_outcomes),
        }
        import hashlib

        h = hashlib.sha256(json.dumps(semantic, sort_keys=True).encode()).hexdigest()
        wjson("v2_diagnostic_semantic_hash.json", {"semantic_hash": h, "semantic": semantic})
        return summary

    def _rank_bottlenecks(self, funnel: dict, retest_outcomes: Counter) -> list[str]:
        drops = []
        stages = funnel.get("stages") or []
        for s in stages:
            if s["input"] and s["fail"] and s["stage"] not in ("ALL_EVALUATIONS", "VALID_RBR", "VALID_DBD"):
                drops.append((s["fail"], s["stage"], s["fail_pct"]))
        drops.sort(reverse=True)
        ranked = [f"{name} (fail={n}, {pct}%)" for n, name, pct in drops[:3]]
        # also mention top retest reasons
        for k, v in retest_outcomes.most_common(2):
            if k != "CONFIRMED":
                ranked.append(f"retest:{k}={v}")
        return ranked[:3]
