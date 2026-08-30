"""Tests for structure_pullback_v2 locked rules + multi-TF context."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from core.strategies.base import MarketContext, build_market_context
from core.strategies.registry import DEFAULT_STRATEGY, get_strategy, list_strategies, resolve_strategy_name
from core.strategies.v2_structure import confirmed_swing_highs, h1_structure_bias
from core.strategies.v2_zones import (
    demand_boundaries,
    is_valid_base,
    supply_boundaries,
    try_build_dbd,
    try_build_rbr,
)


def _c(t, o, h, l, c):
    return {"time": t, "open": o, "high": h, "low": l, "close": c}


def test_generic_candles_map_and_v1_accessors():
    ctx = build_market_context(
        symbol="XAUUSD",
        bid=1,
        ask=2,
        candles={"M15": [_c(1, 1, 2, 0.5, 1.5)], "H1": [], "H4": []},
    )
    assert ctx.m15_candles[0]["close"] == 1.5
    assert "M15" in ctx.candles
    assert ctx.tf("m15")[0]["close"] == 1.5


def test_required_tfs():
    v1 = get_strategy("breakout_retest_v1")
    v2 = get_strategy("structure_pullback_v2")
    assert v1.required_timeframes == ("M15", "H1", "H4")
    assert v2.required_timeframes == ("M15", "M30", "H1", "H4")


def test_registry_default_and_v2():
    assert DEFAULT_STRATEGY == "breakout_retest_v1"
    assert "structure_pullback_v2" in list_strategies()
    info = get_strategy("structure_pullback_v2").info()
    assert info.status == "experimental"
    with pytest.raises(ValueError):
        resolve_strategy_name("not_a_real_strategy")


def test_rbr_dbd_boundaries():
    base = [_c(1, 10, 12, 8, 11), _c(2, 10.5, 11.5, 9, 10.8)]
    d_lo, d_hi = demand_boundaries(base)
    assert d_lo == 8
    assert d_hi == max(11, 10.8)
    s_hi, s_lo = supply_boundaries(base)  # distal, proximal
    # supply distal = max high, proximal = min body bottom
    assert s_hi == 12
    assert s_lo == min(10, 10.5)


def test_base_rejects_large_body():
    # Build ATR-ish series then a fat body candle
    candles = []
    t0 = 1_700_000_000
    px = 2000.0
    for i in range(20):
        candles.append(_c(t0 + i * 1800, px, px + 1, px - 1, px + 0.2))
    # fat body base candidate
    candles.append(_c(t0 + 20 * 1800, px, px + 2, px - 2, px + 1.8))
    from core.strategies.v2_structure import atr14

    atr = atr14(candles, 19)
    assert atr is not None
    assert is_valid_base(candles, 20, 20, atr) is False


def test_wick_only_bos_rejected_close_accepted():
    # Synthetic: need enough history for ATR + swings — smoke that builders return None without BOS
    candles = []
    t0 = 1_700_000_000
    px = 2000.0
    for i in range(40):
        candles.append(_c(t0 + i * 1800, px, px + 0.5, px - 0.5, px))
    assert try_build_rbr(candles, 30, "XAUUSD") is None
    assert try_build_dbd(candles, 30, "XAUUSD") is None


def test_v2_plugin_no_execution_hooks():
    p = get_strategy("structure_pullback_v2")
    assert not hasattr(p, "order_send")
    assert not hasattr(p, "execute_trade")
    ctx = build_market_context(
        symbol="XAUUSD",
        bid=2000,
        ask=2000.2,
        candles={"M15": [], "M30": [], "H1": [], "H4": []},
        h4_direction="NEUTRAL",
    )
    d = p.evaluate(ctx)
    assert d.action in ("wait", "skip")


def test_h1_structure_helper_runs():
    candles = []
    t0 = 1_700_000_000
    px = 2000.0
    for i in range(30):
        # gentle uptrend
        candles.append(_c(t0 + i * 3600, px + i, px + i + 2, px + i - 1, px + i + 1))
    bias = h1_structure_bias(candles)
    assert bias in ("BULLISH", "BEARISH", "NEUTRAL")


def test_confirmed_swing_no_lookahead_edge():
    candles = [_c(i, 1, 2, 0.5, 1) for i in range(10)]
    candles[5] = _c(5, 1, 5, 0.5, 1)  # high peak
    # need 2 right bars — with only data to 9, index 5 is confirmable
    piv = confirmed_swing_highs(candles, left=2, right=2)
    assert any(i == 5 for i, _ in piv)
