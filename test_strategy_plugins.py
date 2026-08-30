"""Strategy plugin architecture tests (zero-behaviour refactor)."""

from __future__ import annotations

import sys
from dataclasses import fields
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from core.strategies.base import FORBIDDEN_CONTEXT_FIELDS, MarketContext, StrategyDecision
from core.strategies.breakout_retest_v1 import BreakoutRetestV1, evaluate_breakout_retest
from core.strategies.registry import (
    DEFAULT_STRATEGY,
    get_strategy,
    list_strategies,
    resolve_strategy_name,
)
from core.strategy import evaluate_breakout_retest as compat_evaluate


def test_registry_contains_v1():
    assert "breakout_retest_v1" in list_strategies()
    assert get_strategy("breakout_retest_v1").name == "breakout_retest_v1"


def test_unknown_strategy_fails():
    with pytest.raises(ValueError, match="Unknown strategy"):
        get_strategy("not_a_registered_strategy_xyz")


def test_default_strategy_is_v1():
    assert DEFAULT_STRATEGY == "breakout_retest_v1"
    assert resolve_strategy_name(None) == "breakout_retest_v1"
    assert resolve_strategy_name("") == "breakout_retest_v1"


def test_compatibility_alias():
    assert resolve_strategy_name("breakout_retest") == "breakout_retest_v1"
    assert get_strategy("breakout_retest").name == "breakout_retest_v1"


def test_v1_direct_equals_registry():
    candles = [
        {"time": i, "open": 2000 + i * 0.1, "high": 2001 + i * 0.1, "low": 1999 + i * 0.1, "close": 2000.5 + i * 0.1}
        for i in range(30)
    ]
    d1 = evaluate_breakout_retest(candles, "BULL", "BULL", 2000.0, 2000.2)
    from core.strategies.base import build_market_context

    d2 = get_strategy("breakout_retest_v1").evaluate(
        build_market_context(
            symbol="XAUUSD",
            m15_candles=candles,
            h1_direction="BULL",
            h4_direction="BULL",
            bid=2000.0,
            ask=2000.2,
        )
    )
    assert d1 == d2


def test_compat_reexport_same_implementation():
    assert compat_evaluate is evaluate_breakout_retest


def test_strategy_decision_schema():
    names = {f.name for f in fields(StrategyDecision)}
    assert {"action", "reason", "direction", "entry", "sl", "tp", "level"}.issubset(names)
    d = StrategyDecision("wait", "No breakout-retest confirmation.")
    assert d.action == "wait"
    assert d.direction is None
    assert d.metadata is None


def test_market_context_forbids_execution_metadata():
    from core.strategies.base import build_market_context

    with pytest.raises(ValueError, match="must not include"):
        build_market_context(
            symbol="XAUUSD",
            m15_candles=[],
            h1_direction="NEUTRAL",
            h4_direction="NEUTRAL",
            bid=1.0,
            ask=1.1,
            metadata={"mt5": object()},
        )
    ctx_fields = {f.name for f in fields(MarketContext)}
    assert not (FORBIDDEN_CONTEXT_FIELDS & ctx_fields)


def test_plugin_has_no_order_send_attr():
    p = BreakoutRetestV1()
    assert not hasattr(p, "order_send")
    assert not hasattr(p, "execute_trade")
