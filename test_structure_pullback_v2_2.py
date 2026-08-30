"""Tests for structure_pullback_v2_2 post-retest local M15 structure."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from core.strategies.registry import DEFAULT_STRATEGY, get_strategy, list_strategies, list_strategy_info
from core.strategies.structure_pullback_v2 import (
    M15_TRIGGER_EXISTING,
    M15_TRIGGER_POST_RETEST_LOCAL,
    StructurePullbackV2,
)
from core.strategies.structure_pullback_v2_1 import StructurePullbackV21
from core.strategies.structure_pullback_v2_2 import StructurePullbackV22, STRATEGY_NAME as V22
from core.strategies.v2_zones import Zone


def _c(t, o, h, l, c):
    return {"time": t, "open": o, "high": h, "low": l, "close": c}


def _zone(**kw):
    d = dict(
        zone_id="Z",
        zone_type="RBR",
        symbol="XAUUSD",
        created_at=1,
        base_start=1,
        base_end=2,
        proximal=100.0,
        distal=90.0,
        atr=2.0,
        departure_end=10,
        bos_level=110.0,
    )
    d.update(kw)
    return Zone(**d)


def test_registry_v22_default_unchanged():
    assert DEFAULT_STRATEGY == "breakout_retest_v1"
    assert V22 in list_strategies()
    infos = {i.name: i for i in list_strategy_info()}
    assert "local M15" in infos[V22].description.lower() or "Post-retest" in infos[V22].description
    p = get_strategy(V22)
    assert isinstance(p, StructurePullbackV22)
    assert p.consume_on_leave_zone is False
    assert p.m15_trigger_mode == M15_TRIGGER_POST_RETEST_LOCAL
    assert get_strategy("structure_pullback_v2").m15_trigger_mode == M15_TRIGGER_EXISTING
    assert get_strategy("structure_pullback_v2_1").m15_trigger_mode == M15_TRIGGER_EXISTING


def test_v22_uses_v21_lifecycle_no_consume_on_leave():
    last = {"time": 100, "open": 120, "high": 121, "low": 119, "close": 120}
    z = _zone()
    z.state = "WAITING_CONFIRMATION"
    z.zone_entry_time = 50
    z.touch_count = 1
    p = StructurePullbackV22()
    p._zones[z.zone_id] = z
    p._update_zone_lifecycle([last], [])
    assert z.state == "WAITING_CONFIRMATION"
    assert z.meta.get("left_zone_after_retest") is True


def test_v22_ignores_pre_retest_pivots():
    """Pre-retest swing low must not become reaction pivot."""
    t0 = 1000
    # Build series: clear swing low BEFORE retest, then flat after
    m15 = []
    for i in range(20):
        # low pivot at i=5 with price 95 — before retest at 10000
        low = 95 if i == 5 else 100
        high = 101
        m15.append(_c(t0 + i * 900, 100, high, low, 100))
    # extend enough RIGHT bars after pivot 5
    retest_at = t0 + 12 * 900  # after pivot 5
    z = _zone()
    z.state = "WAITING_CONFIRMATION"
    z.zone_entry_time = retest_at
    z.touch_count = 1
    p = StructurePullbackV22()
    meta = {"strategy_name": V22}
    d = p._try_trigger_local(z, m15, m15, "buy", meta)
    assert d is not None
    assert d.action == "wait"
    assert (d.metadata or {}).get("reason_code") == "NO_LOCAL_REACTION_PIVOT"
    assert z.meta.get("local_structure", {}).get("reaction_ts") is None


def _buy_local_sequence():
    """
    Build M15 where after retest_at:
    - swing low at bar L
    - swing high at bar H > L
    - later close above that high
    """
    t0 = 10_000
    retest_at = t0
    bars = []
    # bars 0..4 flat after retest
    for i in range(5):
        bars.append(_c(retest_at + i * 900, 100, 101, 99, 100))
    # bar 5: swing low 95 (need left 2 already, right 2 after)
    bars.append(_c(retest_at + 5 * 900, 100, 100.5, 95, 96))
    # right bars for low confirmation (6,7) — higher lows
    bars.append(_c(retest_at + 6 * 900, 96, 98, 96, 97))
    bars.append(_c(retest_at + 7 * 900, 97, 99, 96.5, 98))
    # bars 8,9 build toward high
    bars.append(_c(retest_at + 8 * 900, 98, 102, 97, 101))
    bars.append(_c(retest_at + 9 * 900, 101, 103, 100, 102))
    # bar 10: swing high 108
    bars.append(_c(retest_at + 10 * 900, 102, 108, 101, 106))
    # right for high (11,12)
    bars.append(_c(retest_at + 11 * 900, 106, 107, 104, 105))
    bars.append(_c(retest_at + 12 * 900, 105, 106, 103, 104))
    # bar 13: wick only above 108
    bars.append(_c(retest_at + 13 * 900, 104, 109, 103, 105))
    # bar 14: close above 108
    bars.append(_c(retest_at + 14 * 900, 105, 110, 104, 109))
    return retest_at, bars


def test_buy_requires_post_retest_low_then_high_then_close():
    retest_at, bars = _buy_local_sequence()
    # H1 with confirmed swing high above entry for TP
    h1 = []
    for i in range(20):
        h = 200 if i == 10 else 150
        h1.append(_c(retest_at + i * 3600, 140, h, 130, 145))
    z = _zone()
    z.state = "WAITING_CONFIRMATION"
    z.zone_entry_time = retest_at
    z.touch_count = 1
    p = StructurePullbackV22()
    meta = {"strategy_name": V22}
    d = p._try_trigger_local(z, bars[:13], h1, "buy", meta)
    assert d.action == "wait"
    ls = z.meta["local_structure"]
    assert ls["reaction_price"] == 95
    assert ls["trigger_price"] == 108
    d2 = p._try_trigger_local(z, bars[:14], h1, "buy", meta)
    assert d2.action == "wait"
    assert (d2.metadata or {}).get("reason_code") == "WAITING_LOCAL_STRUCTURE_SHIFT"
    d3 = p._try_trigger_local(z, bars, h1, "buy", meta)
    assert d3.action == "enter"
    assert d3.direction == "buy"
    assert d3.entry == 109


def test_historical_close_before_trigger_confirm_ignored():
    """Close above trigger high before RIGHT confirmation must not fire."""
    retest_at, bars = _buy_local_sequence()
    early = bars[:11]
    z = _zone()
    z.state = "WAITING_CONFIRMATION"
    z.zone_entry_time = retest_at
    z.touch_count = 1
    p = StructurePullbackV22()
    early.append(_c(retest_at + 11 * 900 + 1, 106, 110, 105, 109))
    d = p._try_trigger_local(z, early, early, "buy", {"strategy_name": V22})
    assert d.action == "wait"
    assert z.meta.get("local_structure", {}).get("trigger_ts") is None


def test_first_local_sequence_locked():
    retest_at, bars = _buy_local_sequence()
    # Add another higher high later — must not replace locked 108
    bars.append(_c(retest_at + 15 * 900, 109, 120, 108, 110))
    bars.append(_c(retest_at + 16 * 900, 110, 111, 109, 110))
    bars.append(_c(retest_at + 17 * 900, 110, 111, 109, 110))
    z = _zone()
    z.state = "WAITING_CONFIRMATION"
    z.zone_entry_time = retest_at
    z.touch_count = 1
    p = StructurePullbackV22()
    p._try_trigger_local(z, bars[:13], bars[:13], "buy", {"strategy_name": V22})
    locked = z.meta["local_structure"]["trigger_price"]
    p._try_trigger_local(z, bars, bars, "buy", {"strategy_name": V22})
    assert z.meta["local_structure"]["trigger_price"] == locked == 108


def test_reentry_does_not_reset_local_structure():
    p = StructurePullbackV22()
    z = _zone()
    z.state = "WAITING_CONFIRMATION"
    z.zone_entry_time = 50
    z.touch_count = 1
    z.meta["left_zone_after_retest"] = True
    z.meta["left_zone_at"] = 80
    z.meta["local_structure"] = {"reaction_ts": 90, "reaction_price": 95, "trigger_ts": 100, "trigger_price": 108}
    inside = {"time": 120, "open": 95, "high": 101, "low": 94, "close": 96}
    p._zones[z.zone_id] = z
    p._update_zone_lifecycle([inside], [])
    assert z.meta["local_structure"]["trigger_price"] == 108
    assert z.touch_count == 1


def test_v22_invalidation_blocks_trigger():
    p = StructurePullbackV22()
    z = _zone()
    z.state = "INVALIDATED"
    z.zone_entry_time = 50
    assert p._try_trigger(z, [], [], "buy", {}) is None


def test_sell_local_close_break():
    t0 = 20_000
    retest_at = t0
    bars = []
    for i in range(5):
        bars.append(_c(retest_at + i * 900, 200, 201, 199, 200))
    bars.append(_c(retest_at + 5 * 900, 200, 210, 199, 205))
    bars.append(_c(retest_at + 6 * 900, 205, 208, 203, 204))
    bars.append(_c(retest_at + 7 * 900, 204, 206, 202, 203))
    bars.append(_c(retest_at + 8 * 900, 203, 204, 198, 199))
    bars.append(_c(retest_at + 9 * 900, 199, 200, 197, 198))
    bars.append(_c(retest_at + 10 * 900, 198, 199, 190, 192))
    bars.append(_c(retest_at + 11 * 900, 192, 194, 191, 193))
    bars.append(_c(retest_at + 12 * 900, 193, 195, 192, 194))
    bars.append(_c(retest_at + 13 * 900, 194, 195, 189, 193))
    bars.append(_c(retest_at + 14 * 900, 193, 194, 188, 189))
    h1 = []
    # clear confirmed swing low at 150 (below entry 189) with LEFT/RIGHT=2
    for i in range(15):
        if i == 7:
            h1.append(_c(retest_at + i * 3600, 180, 185, 150, 160))
        elif i in (5, 6, 8, 9):
            h1.append(_c(retest_at + i * 3600, 175, 190, 170, 180))
        else:
            h1.append(_c(retest_at + i * 3600, 185, 195, 180, 188))
    z = _zone(zone_type="DBD", proximal=210.0, distal=220.0)
    z.state = "WAITING_CONFIRMATION"
    z.zone_entry_time = retest_at
    z.touch_count = 1
    p = StructurePullbackV22()
    d = p._try_trigger_local(z, bars[:14], h1, "sell", {"strategy_name": V22})
    assert d.action == "wait"
    d2 = p._try_trigger_local(z, bars, h1, "sell", {"strategy_name": V22})
    assert d2.action == "enter"
    assert d2.direction == "sell"
    assert d2.entry == 189


def test_v2_v21_modes_unchanged_flags():
    assert StructurePullbackV2().consume_on_leave_zone is True
    assert StructurePullbackV2().m15_trigger_mode == M15_TRIGGER_EXISTING
    assert StructurePullbackV21().consume_on_leave_zone is False
    assert StructurePullbackV21().m15_trigger_mode == M15_TRIGGER_EXISTING
