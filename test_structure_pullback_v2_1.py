"""Tests for structure_pullback_v2_1 lifecycle experiment (leave-zone only)."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from core.strategies.registry import DEFAULT_STRATEGY, get_strategy, list_strategies, list_strategy_info
from core.strategies.structure_pullback_v2 import LEAVE_WITHOUT_CONFIRM_ATR, StructurePullbackV2
from core.strategies.structure_pullback_v2_1 import StructurePullbackV21, STRATEGY_NAME as V21
from core.strategies.v2_zones import Zone


def _zone(**kw):
    defaults = dict(
        zone_id="Z1",
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
    defaults.update(kw)
    return Zone(**defaults)


def test_registry_v2_1_and_default():
    assert DEFAULT_STRATEGY == "breakout_retest_v1"
    assert "structure_pullback_v2" in list_strategies()
    assert V21 in list_strategies()
    infos = {i.name: i for i in list_strategy_info()}
    assert infos[V21].status == "experimental"
    assert "leaves the M30 zone" in infos[V21].description.lower() or "leaves the M30" in infos[V21].description
    p = get_strategy(V21)
    assert isinstance(p, StructurePullbackV21)
    assert p.consume_on_leave_zone is False
    assert get_strategy("structure_pullback_v2").consume_on_leave_zone is True


def test_v2_consumes_on_leave_v21_does_not():
    last = {"time": 100, "open": 120, "high": 121, "low": 119, "close": 120}  # well above demand
    z2 = _zone()
    z2.state = "WAITING_CONFIRMATION"
    z2.zone_entry_time = 50
    z2.touch_count = 1
    v2 = StructurePullbackV2(consume_on_leave_zone=True)
    v2._zones[z2.zone_id] = z2
    v2._update_zone_lifecycle([last], [])
    assert z2.state == "CONSUMED"
    assert v2.stats["left_zone_consumed"] == 1

    z21 = _zone(zone_id="Z21")
    z21.state = "WAITING_CONFIRMATION"
    z21.zone_entry_time = 50
    z21.touch_count = 1
    v21 = StructurePullbackV21()
    v21._zones[z21.zone_id] = z21
    v21._update_zone_lifecycle([last], [])
    assert z21.state == "WAITING_CONFIRMATION"
    assert z21.meta.get("left_zone_after_retest") is True
    assert v21.stats["left_zone_continued_waiting"] == 1


def test_v21_invalidates_on_m30_distal_close_same_as_v2():
    # RBR demand invalid when close < distal
    last = {"time": 200, "open": 89, "high": 89.5, "low": 88, "close": 89.0}
    for cls in (StructurePullbackV2, StructurePullbackV21):
        z = _zone(zone_id=f"inv-{cls.__name__}")
        z.state = "WAITING_CONFIRMATION"
        z.zone_entry_time = 50
        z.touch_count = 1
        p = cls() if cls is StructurePullbackV21 else StructurePullbackV2()
        p._zones[z.zone_id] = z
        p._update_zone_lifecycle([last], [])
        assert z.state == "INVALIDATED"
        assert z.invalidation_reason == "m30_close_below_distal"


def test_v21_no_confirm_after_invalidation():
    v21 = StructurePullbackV21()
    z = _zone()
    z.state = "INVALIDATED"
    z.zone_entry_time = 50
    z.touch_count = 1
    d = v21._try_trigger(z, [], [], "buy", {"strategy_name": V21})
    assert d is None


def test_v21_reentry_does_not_new_setup():
    v21 = StructurePullbackV21()
    z = _zone()
    z.state = "WAITING_CONFIRMATION"
    z.zone_entry_time = 50
    z.touch_count = 1
    z.meta["left_zone_after_retest"] = True
    z.meta["left_zone_at"] = 80
    # re-enter: bar overlaps zone
    inside = {"time": 120, "open": 95, "high": 101, "low": 94, "close": 96}
    v21._zones[z.zone_id] = z
    v21._update_zone_lifecycle([inside], [])
    assert z.touch_count == 1
    assert z.state == "WAITING_CONFIRMATION"
    assert v21.stats["first_retests"] == 0  # no new first retest
    assert z.meta.get("reentry_count") == 1


def test_v2_v21_same_leave_detection_threshold():
    v2 = StructurePullbackV2()
    v21 = StructurePullbackV21()
    z = _zone()
    # just barely left
    atr = z.atr
    low = max(z.proximal, z.distal) + LEAVE_WITHOUT_CONFIRM_ATR * atr + 0.01
    last = {"time": 1, "open": low, "high": low + 1, "low": low, "close": low + 0.5}
    assert v2._zone_has_left(z, last) is True
    assert v21._zone_has_left(z, last) is True


def test_backtest_safety_unchanged():
    from backtest.safety import assert_backtest_safe, assert_not_live_sinks

    assert_backtest_safe("v21")
    assert_not_live_sinks(mt5_enabled=False, telegram_enabled=False)
    assert not hasattr(StructurePullbackV21(), "order_send")
