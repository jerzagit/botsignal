"""
structure_pullback_v2 — H4 direction → H1 structure → M30 S&D → M15 shift.

LOCKED rules: see docs/STRUCTURE_PULLBACK_V2.md
Does NOT call MT5 / Telegram / DB. Stateful zone book is instance-local.

Lifecycle policy:
  consume_on_leave_zone=True  (V2 default): leave zone after first retest → CONSUMED
  consume_on_leave_zone=False (V2.1): keep WAITING_CONFIRMATION while zone valid
"""

from __future__ import annotations

from typing import Any

from core.strategies.base import MarketContext, StrategyDecision, StrategyInfo
from core.strategies.v2_structure import (
    atr14,
    confirmed_swing_highs,
    confirmed_swing_lows,
    fib_overlap_for_impulse,
    h1_structure_bias,
)
from core.strategies.v2_zones import (
    Zone,
    bar_overlaps_zone,
    price_in_zone,
    try_build_dbd,
    try_build_rbr,
)

STRATEGY_NAME = "structure_pullback_v2"
REQUIRED_TIMEFRAMES = ("M15", "M30", "H1", "H4")
SL_BUFFER_ATR = 0.10
# After touch, if price leaves zone by >= this ATR and no confirmation → CONSUMED (V2 only)
LEAVE_WITHOUT_CONFIRM_ATR = 0.50

# M15 trigger reference modes
M15_TRIGGER_EXISTING = "EXISTING"
M15_TRIGGER_POST_RETEST_LOCAL = "POST_RETEST_LOCAL_STRUCTURE"
# Timestamp convention: candle["time"] is bar open unix; closed bars are those with
# RIGHT confirmation bars present in the series (no lookahead). Local pivots require
# pivot candle time >= first_retest_at (zone_entry_time).


class StructurePullbackV2:
    name = STRATEGY_NAME
    required_timeframes = REQUIRED_TIMEFRAMES
    version = "2"
    display_name = "Structure Pullback V2"
    description = (
        "H4 bias + H1 structure + M30 RBR/DBD first retest + M15 structure-shift entry."
    )

    def __init__(
        self,
        *,
        consume_on_leave_zone: bool = True,
        m15_trigger_mode: str = M15_TRIGGER_EXISTING,
    ) -> None:
        self.consume_on_leave_zone = bool(consume_on_leave_zone)
        mode = (m15_trigger_mode or M15_TRIGGER_EXISTING).strip().upper()
        if mode not in (M15_TRIGGER_EXISTING, M15_TRIGGER_POST_RETEST_LOCAL):
            raise ValueError(f"Unknown m15_trigger_mode: {m15_trigger_mode!r}")
        self.m15_trigger_mode = mode
        self._zones: dict[str, Zone] = {}
        self._seen_base_ends: set[tuple[str, int]] = set()
        self._last_m30_len: int = 0
        self.stats: dict[str, int] = {
            "valid_rbr": 0,
            "valid_dbd": 0,
            "rejected_base": 0,
            "rejected_departure": 0,
            "rejected_bos": 0,
            "invalidated": 0,
            "first_retests": 0,
            "m15_confirmations": 0,
            "left_zone_consumed": 0,
            "left_zone_continued_waiting": 0,
            "reentry_while_waiting": 0,
            "local_reaction_pivots": 0,
            "local_trigger_pivots": 0,
            "local_wick_only_breaks": 0,
        }

    def info(self) -> StrategyInfo:
        return StrategyInfo(
            name=self.name,
            display_name=self.display_name,
            description=self.description,
            version=self.version,
            required_timeframes=REQUIRED_TIMEFRAMES,
            status="experimental",
        )

    def reset(self) -> None:
        self.__init__(
            consume_on_leave_zone=self.consume_on_leave_zone,
            m15_trigger_mode=self.m15_trigger_mode,
        )

    def evaluate(self, context: MarketContext) -> StrategyDecision:
        symbol = context.symbol
        m15 = context.m15_candles
        m30 = context.m30_candles
        h1 = context.h1_candles
        h4_dir = (context.h4_direction or "NEUTRAL").upper()
        h1_struct = h1_structure_bias(h1)

        meta_base: dict[str, Any] = {
            "strategy_name": self.name,
            "h4_bias": h4_dir,
            "h1_structure": h1_struct,
            "reason_code": "waiting",
            "consume_on_leave_zone": self.consume_on_leave_zone,
            "m15_trigger_mode": self.m15_trigger_mode,
        }

        if len(m15) < 10 or len(m30) < 30:
            return StrategyDecision(
                "wait",
                "Not enough multi-TF candles for V2.",
                metadata={**meta_base, "reason_code": "insufficient_candles"},
            )

        self._ingest_new_m30(m30, symbol)
        self._update_zone_lifecycle(m30, m15)

        # Direction gate (unchanged — does not terminate zone state)
        if h4_dir == "BULL" and h1_struct == "BULLISH":
            side = "buy"
        elif h4_dir == "BEAR" and h1_struct == "BEARISH":
            side = "sell"
        else:
            code = "h4_not_aligned" if h4_dir not in ("BULL", "BEAR") else "h1_not_aligned"
            if h4_dir == "BULL" and h1_struct != "BULLISH":
                code = "h1_not_aligned"
            if h4_dir == "BEAR" and h1_struct != "BEARISH":
                code = "h1_not_aligned"
            if h4_dir == "NEUTRAL":
                code = "h4_not_aligned"
            return StrategyDecision(
                "wait",
                f"Direction filter: H4={h4_dir} H1={h1_struct}.",
                metadata={**meta_base, "reason_code": code},
            )

        wanted = "RBR" if side == "buy" else "DBD"
        candidates = [
            z
            for z in self._zones.values()
            if z.zone_type == wanted and z.state in ("WAITING_PULLBACK", "TOUCHED", "WAITING_CONFIRMATION")
        ]
        if not candidates:
            return StrategyDecision(
                "wait",
                "No valid fresh M30 zone for direction.",
                metadata={**meta_base, "reason_code": "no_valid_m30_zone"},
            )

        candidates.sort(key=lambda z: z.created_at, reverse=True)
        for zone in candidates:
            decision = self._try_trigger(zone, m15, h1, side, meta_base)
            if decision is not None:
                return decision

        z = candidates[0]
        return StrategyDecision(
            "wait",
            f"Waiting pullback/confirmation on {z.zone_id}.",
            metadata={
                **meta_base,
                **z.to_dict(),
                "reason_code": (
                    "waiting_m15_structure_shift"
                    if z.state == "WAITING_CONFIRMATION"
                    else "waiting_pullback"
                ),
                "left_zone_after_retest": bool(z.meta.get("left_zone_after_retest")),
            },
        )

    def _ingest_new_m30(self, m30: list[dict], symbol: str) -> None:
        n = len(m30)
        start_scan = max(20, self._last_m30_len - 5)
        for base_end in range(start_scan, n):
            key = ("RBR", int(m30[base_end]["time"]))
            if key not in self._seen_base_ends:
                if base_end + 1 < n:
                    z = try_build_rbr(m30, base_end, symbol)
                    self._seen_base_ends.add(key)
                    if z and z.zone_id not in self._zones:
                        if any(int(c["time"]) == z.departure_end for c in m30):
                            self._zones[z.zone_id] = z
                            self.stats["valid_rbr"] += 1
            key2 = ("DBD", int(m30[base_end]["time"]))
            if key2 not in self._seen_base_ends:
                if base_end + 1 < n:
                    z = try_build_dbd(m30, base_end, symbol)
                    self._seen_base_ends.add(key2)
                    if z and z.zone_id not in self._zones:
                        if any(int(c["time"]) == z.departure_end for c in m30):
                            self._zones[z.zone_id] = z
                            self.stats["valid_dbd"] += 1
        self._last_m30_len = n

    def _zone_has_left(self, z: Zone, last: dict) -> bool:
        atr = z.atr or 1.0
        if z.zone_type == "RBR":
            left = float(last["low"]) > max(z.proximal, z.distal) + LEAVE_WITHOUT_CONFIRM_ATR * atr
        else:
            left = float(last["high"]) < min(z.proximal, z.distal) - LEAVE_WITHOUT_CONFIRM_ATR * atr
        return bool(left and not bar_overlaps_zone(last, z))

    def _update_zone_lifecycle(self, m30: list[dict], m15: list[dict]) -> None:
        if not m30:
            return
        last = m30[-1]
        last_t = int(last["time"])
        last_close = float(last["close"])
        for z in list(self._zones.values()):
            if z.state in ("CONSUMED", "TRIGGERED", "INVALIDATED"):
                continue
            # Invalidation by M30 close beyond distal (identical for V2 / V2.1)
            if z.zone_type == "RBR" and last_close < z.distal:
                z.state = "INVALIDATED"
                z.invalidated_at = last_t
                z.invalidation_price = last_close
                z.invalidation_reason = "m30_close_below_distal"
                self.stats["invalidated"] += 1
                continue
            if z.zone_type == "DBD" and last_close > z.distal:
                z.state = "INVALIDATED"
                z.invalidated_at = last_t
                z.invalidation_price = last_close
                z.invalidation_reason = "m30_close_above_distal"
                self.stats["invalidated"] += 1
                continue

            if z.state == "WAITING_PULLBACK":
                for c in m30:
                    ct = int(c["time"])
                    if ct <= z.departure_end:
                        continue
                    if bar_overlaps_zone(c, z):
                        z.touch_count = 1
                        z.state = "WAITING_CONFIRMATION"
                        z.zone_entry_time = ct
                        self.stats["first_retests"] += 1
                        break
            elif z.state == "WAITING_CONFIRMATION":
                left = self._zone_has_left(z, last)
                if left:
                    first_leave = z.meta.get("left_zone_at") is None
                    z.meta["left_zone_after_retest"] = True
                    z.meta["_inside_after_leave"] = False
                    if first_leave:
                        z.meta["left_zone_at"] = last_t
                    if self.consume_on_leave_zone:
                        z.state = "CONSUMED"
                        if first_leave:
                            self.stats["left_zone_consumed"] += 1
                    elif first_leave:
                        # V2.1: keep waiting for same M15 confirmation
                        self.stats["left_zone_continued_waiting"] += 1
                elif z.meta.get("left_zone_after_retest") and bar_overlaps_zone(last, z):
                    # Re-entry into same first-retest lifecycle — observational only
                    if not z.meta.get("_inside_after_leave"):
                        z.meta["reentry_count"] = int(z.meta.get("reentry_count") or 0) + 1
                        self.stats["reentry_while_waiting"] += 1
                        z.meta["_inside_after_leave"] = True

    def _try_trigger(
        self,
        zone: Zone,
        m15: list[dict],
        h1: list[dict],
        side: str,
        meta_base: dict[str, Any],
    ) -> StrategyDecision | None:
        if zone.state not in ("WAITING_CONFIRMATION", "TOUCHED"):
            return None
        if zone.zone_entry_time is None:
            return None
        if self.m15_trigger_mode == M15_TRIGGER_POST_RETEST_LOCAL:
            return self._try_trigger_local(zone, m15, h1, side, meta_base)
        return self._try_trigger_existing(zone, m15, h1, side, meta_base)

    def _try_trigger_existing(
        self,
        zone: Zone,
        m15: list[dict],
        h1: list[dict],
        side: str,
        meta_base: dict[str, Any],
    ) -> StrategyDecision | None:
        # Pivot selection (V2/V2.1 locked): prefer last confirmed swing at/before zone_entry_time;
        # else latest eligible confirmed swing.
        entry_t = int(zone.zone_entry_time)
        m15_times = [int(c["time"]) for c in m15]
        try:
            entry_i = next(i for i, t in enumerate(m15_times) if t >= entry_t)
        except StopIteration:
            return None
        scope_start = max(0, entry_i - 40)
        m15_scope = m15[scope_start:]
        if len(m15_scope) < 5:
            return None
        last = m15[-1]
        last_t = int(last["time"])

        if side == "buy":
            piv = confirmed_swing_highs(m15_scope, left=2, right=2)
            eligible = []
            for i, px in piv:
                abs_i = scope_start + i
                if abs_i + 2 >= len(m15):
                    continue
                eligible.append((abs_i, px))
            if not eligible:
                return StrategyDecision(
                    "wait",
                    "No confirmed M15 swing high for BUY trigger.",
                    metadata={
                        **meta_base,
                        **zone.to_dict(),
                        "reason_code": "no_m15_trigger_swing",
                        "m15_trigger_status": "no_swing",
                        "left_zone_after_retest": bool(zone.meta.get("left_zone_after_retest")),
                    },
                )
            swing_idx, swing_px = eligible[-1]
            pre = [(i, px) for i, px in eligible if int(m15[i]["time"]) <= entry_t]
            if pre:
                swing_idx, swing_px = pre[-1]
            for j in range(max(swing_idx + 2, entry_i), len(m15)):
                c = m15[j]
                if int(c["time"]) < entry_t:
                    continue
                if float(c["close"]) > swing_px:
                    if int(c["time"]) != last_t:
                        zone.state = "CONSUMED"
                        return None
                    return self._emit_enter(zone, c, h1, side, swing_px, meta_base)
            return StrategyDecision(
                "wait",
                "Waiting M15 close above trigger swing high.",
                metadata={
                    **meta_base,
                    **zone.to_dict(),
                    "reason_code": "waiting_m15_structure_shift",
                    "m15_trigger_swing": swing_px,
                    "m15_trigger_status": "waiting_close",
                    "left_zone_after_retest": bool(zone.meta.get("left_zone_after_retest")),
                },
            )
        else:
            piv = confirmed_swing_lows(m15_scope, left=2, right=2)
            eligible = []
            for i, px in piv:
                abs_i = scope_start + i
                if abs_i + 2 >= len(m15):
                    continue
                eligible.append((abs_i, px))
            if not eligible:
                return StrategyDecision(
                    "wait",
                    "No confirmed M15 swing low for SELL trigger.",
                    metadata={
                        **meta_base,
                        **zone.to_dict(),
                        "reason_code": "no_m15_trigger_swing",
                        "m15_trigger_status": "no_swing",
                        "left_zone_after_retest": bool(zone.meta.get("left_zone_after_retest")),
                    },
                )
            swing_idx, swing_px = eligible[-1]
            pre = [(i, px) for i, px in eligible if int(m15[i]["time"]) <= entry_t]
            if pre:
                swing_idx, swing_px = pre[-1]
            for j in range(max(swing_idx + 2, entry_i), len(m15)):
                c = m15[j]
                if int(c["time"]) < entry_t:
                    continue
                if float(c["close"]) < swing_px:
                    if int(c["time"]) != last_t:
                        zone.state = "CONSUMED"
                        return None
                    return self._emit_enter(zone, c, h1, side, swing_px, meta_base)
            return StrategyDecision(
                "wait",
                "Waiting M15 close below trigger swing low.",
                metadata={
                    **meta_base,
                    **zone.to_dict(),
                    "reason_code": "waiting_m15_structure_shift",
                    "m15_trigger_swing": swing_px,
                    "m15_trigger_status": "waiting_close",
                    "left_zone_after_retest": bool(zone.meta.get("left_zone_after_retest")),
                },
            )

    def _local_meta_extra(self, zone: Zone) -> dict[str, Any]:
        ls = zone.meta.get("local_structure") or {}
        return {
            "left_zone_after_retest": bool(zone.meta.get("left_zone_after_retest")),
            "local_structure": ls or None,
            "reaction_pivot_type": ls.get("reaction_type"),
            "reaction_pivot_timestamp": ls.get("reaction_ts"),
            "reaction_pivot_price": ls.get("reaction_price"),
            "reaction_pivot_confirmed_at": ls.get("reaction_confirmed_at"),
            "local_trigger_type": ls.get("trigger_type"),
            "local_trigger_timestamp": ls.get("trigger_ts"),
            "local_trigger_price": ls.get("trigger_price"),
            "local_trigger_confirmed_at": ls.get("trigger_confirmed_at"),
        }

    def _try_trigger_local(
        self,
        zone: Zone,
        m15: list[dict],
        h1: list[dict],
        side: str,
        meta_base: dict[str, Any],
    ) -> StrategyDecision | None:
        """
        V2.2: first confirmed post-retest reaction pivot, then first opposite trigger
        pivot after it, then live close-break only after trigger confirmation.
        No pre-retest pivots. No retroactive historical close-through scan.
        """
        entry_t = int(zone.zone_entry_time)
        if len(m15) < 5:
            return None
        last = m15[-1]
        last_t = int(last["time"])
        ls = zone.meta.setdefault("local_structure", {})
        extra = lambda: {**meta_base, **zone.to_dict(), **self._local_meta_extra(zone)}

        # --- lock / discover reaction pivot (first post-retest) ---
        if ls.get("reaction_ts") is None:
            if side == "buy":
                pivs = confirmed_swing_lows(m15, left=2, right=2)
                for i, px in pivs:
                    if i + 2 >= len(m15):
                        continue
                    pts = int(m15[i]["time"])
                    if pts < entry_t:
                        continue
                    ls.update(
                        {
                            "reaction_type": "SWING_LOW",
                            "reaction_idx": i,
                            "reaction_price": px,
                            "reaction_ts": pts,
                            "reaction_confirmed_at": int(m15[i + 2]["time"]),
                        }
                    )
                    self.stats["local_reaction_pivots"] += 1
                    break
            else:
                pivs = confirmed_swing_highs(m15, left=2, right=2)
                for i, px in pivs:
                    if i + 2 >= len(m15):
                        continue
                    pts = int(m15[i]["time"])
                    if pts < entry_t:
                        continue
                    ls.update(
                        {
                            "reaction_type": "SWING_HIGH",
                            "reaction_idx": i,
                            "reaction_price": px,
                            "reaction_ts": pts,
                            "reaction_confirmed_at": int(m15[i + 2]["time"]),
                        }
                    )
                    self.stats["local_reaction_pivots"] += 1
                    break
            if ls.get("reaction_ts") is None:
                return StrategyDecision(
                    "wait",
                    "Waiting post-retest local reaction pivot.",
                    metadata={**extra(), "reason_code": "NO_LOCAL_REACTION_PIVOT", "m15_trigger_status": "no_reaction"},
                )

        reaction_ts = int(ls["reaction_ts"])
        reaction_idx = int(ls["reaction_idx"])

        # --- lock / discover local trigger (first opposite after reaction) ---
        if ls.get("trigger_ts") is None:
            if side == "buy":
                pivs = confirmed_swing_highs(m15, left=2, right=2)
                for i, px in pivs:
                    if i + 2 >= len(m15):
                        continue
                    pts = int(m15[i]["time"])
                    if pts <= reaction_ts:
                        continue
                    if i <= reaction_idx:
                        continue
                    ls.update(
                        {
                            "trigger_type": "SWING_HIGH",
                            "trigger_idx": i,
                            "trigger_price": px,
                            "trigger_ts": pts,
                            "trigger_confirmed_at": int(m15[i + 2]["time"]),
                        }
                    )
                    self.stats["local_trigger_pivots"] += 1
                    break
            else:
                pivs = confirmed_swing_lows(m15, left=2, right=2)
                for i, px in pivs:
                    if i + 2 >= len(m15):
                        continue
                    pts = int(m15[i]["time"])
                    if pts <= reaction_ts:
                        continue
                    if i <= reaction_idx:
                        continue
                    ls.update(
                        {
                            "trigger_type": "SWING_LOW",
                            "trigger_idx": i,
                            "trigger_price": px,
                            "trigger_ts": pts,
                            "trigger_confirmed_at": int(m15[i + 2]["time"]),
                        }
                    )
                    self.stats["local_trigger_pivots"] += 1
                    break
            if ls.get("trigger_ts") is None:
                return StrategyDecision(
                    "wait",
                    "Waiting post-retest local trigger pivot.",
                    metadata={
                        **extra(),
                        "reason_code": "WAITING_LOCAL_TRIGGER_PIVOT",
                        "m15_trigger_status": "waiting_trigger",
                    },
                )

        trigger_px = float(ls["trigger_price"])
        trigger_conf = int(ls["trigger_confirmed_at"])
        # Trigger must be fully confirmed; structure shift only on candles AFTER confirmation
        if last_t <= trigger_conf:
            return StrategyDecision(
                "wait",
                "Waiting local trigger pivot confirmation.",
                metadata={
                    **extra(),
                    "reason_code": "WAITING_LOCAL_TRIGGER_PIVOT",
                    "m15_trigger_status": "trigger_confirming",
                    "m15_trigger_swing": trigger_px,
                },
            )

        # Live candle only — no backward scan for historical close-through
        if side == "buy":
            wick = float(last["high"]) > trigger_px
            close_ok = float(last["close"]) > trigger_px
        else:
            wick = float(last["low"]) < trigger_px
            close_ok = float(last["close"]) < trigger_px

        if wick and not close_ok:
            if not ls.get("wick_only_noted"):
                ls["wick_only_noted"] = True
                self.stats["local_wick_only_breaks"] += 1

        if close_ok:
            ls["structure_shift_at"] = last_t
            ls["structure_shift_close"] = float(last["close"])
            return self._emit_enter(zone, last, h1, side, trigger_px, meta_base)

        return StrategyDecision(
            "wait",
            "Waiting local M15 structure-shift close.",
            metadata={
                **extra(),
                "reason_code": "WAITING_LOCAL_STRUCTURE_SHIFT",
                "m15_trigger_status": "waiting_close",
                "m15_trigger_swing": trigger_px,
            },
        )

    def _emit_enter(
        self,
        zone: Zone,
        confirm: dict,
        h1: list[dict],
        side: str,
        swing_px: float,
        meta_base: dict[str, Any],
    ) -> StrategyDecision:
        entry = float(confirm["close"])
        buffer = SL_BUFFER_ATR * zone.atr
        if side == "buy":
            sl = round(zone.distal - buffer, 2)
            highs = confirmed_swing_highs(h1, left=2, right=2)
            tp_candidates = [p for _, p in highs if p > entry]
            if not tp_candidates:
                return StrategyDecision(
                    "skip",
                    "no_structural_target",
                    metadata={
                        **meta_base,
                        **zone.to_dict(),
                        "reason_code": "no_structural_target",
                        "entry": entry,
                        "sl": sl,
                    },
                )
            tp = round(tp_candidates[-1], 2)
            risk = entry - sl
            rr = (tp - entry) / risk if risk > 0 else 0.0
        else:
            sl = round(zone.distal + buffer, 2)
            lows = confirmed_swing_lows(h1, left=2, right=2)
            tp_candidates = [p for _, p in lows if p < entry]
            if not tp_candidates:
                return StrategyDecision(
                    "skip",
                    "no_structural_target",
                    metadata={
                        **meta_base,
                        **zone.to_dict(),
                        "reason_code": "no_structural_target",
                        "entry": entry,
                        "sl": sl,
                    },
                )
            tp = round(tp_candidates[-1], 2)
            risk = sl - entry
            rr = (entry - tp) / risk if risk > 0 else 0.0

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
                impulse_start, impulse_end, zone.proximal, zone.distal
            )
            zone.fib_overlap = fib_overlap
            zone.fib_level_nearest = fib_lvl
            zone.fib_retracement_pct = fib_pct

        zone.state = "TRIGGERED"
        zone.trigger_swing = swing_px
        zone.state = "CONSUMED"
        self.stats["m15_confirmations"] += 1

        md = {
            **meta_base,
            **zone.to_dict(),
            "reason_code": "candidate_ready",
            "m15_trigger_status": "confirmed",
            "m15_trigger_swing": swing_px,
            "m15_confirmation_close": entry,
            "entry": entry,
            "sl": sl,
            "tp": tp,
            "rr": round(rr, 4),
            "fib_overlap": fib_overlap,
            "fib_level_nearest": fib_lvl,
            "fib_retracement_pct": fib_pct,
            "bos_confirmed": True,
            "bos_level": zone.bos_level,
            "departure_strength_atr": zone.meta.get("departure_strength_atr"),
            "departure_body_ratio": zone.meta.get("departure_body_ratio"),
            "zone_timeframe": "M30",
            "zone_atr": zone.atr,
            "zone_touch_count": zone.touch_count,
            "left_zone_after_retest": bool(zone.meta.get("left_zone_after_retest")),
            "left_zone_at": zone.meta.get("left_zone_at"),
            **(
                self._local_meta_extra(zone)
                if self.m15_trigger_mode == M15_TRIGGER_POST_RETEST_LOCAL
                else {}
            ),
        }
        return StrategyDecision(
            "enter",
            f"V2 {zone.zone_type} pullback M15 structure-shift confirmed.",
            side,
            entry,
            sl,
            tp,
            zone.proximal,
            md,
        )
