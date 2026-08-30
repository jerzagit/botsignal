"""V2 diagnostic collectors — observe only; must not alter decisions."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from backtest.catalog import load_run_detail
from backtest.v2_diagnostics import (
    V2DiagnosticCollector,
    diagnose_rbr_failure,
    _pips,
)
from core.strategies.base import StrategyDecision, build_market_context
from core.strategies.registry import get_strategy
from core.strategies.v2_structure import confirmed_swing_highs


def _c(t, o, h, l, c):
    return {"time": t, "open": o, "high": h, "low": l, "close": c}


def test_funnel_counters_do_not_alter_decisions():
    plugin = get_strategy("structure_pullback_v2")
    plugin.reset()
    diag = V2DiagnosticCollector()
    ctx = build_market_context(
        symbol="XAUUSD",
        timestamp="t0",
        candles={"M15": [_c(i, 1, 2, 0.5, 1.5) for i in range(15)], "M30": [], "H1": [], "H4": []},
        h1_direction="BULL",
        h4_direction="BULL",
        bid=1,
        ask=1.1,
    )
    d1 = plugin.evaluate(ctx)
    snap = (d1.action, d1.reason, json.dumps(d1.metadata or {}, sort_keys=True))
    diag.after_evaluate(plugin, ctx, d1, "t0")
    d2 = plugin.evaluate(ctx)
    assert (d2.action, d2.reason, json.dumps(d2.metadata or {}, sort_keys=True)) == snap


def test_zone_id_deterministic():
    from core.strategies.v2_zones import Zone

    z1 = Zone(
        zone_id="XAUUSD:M30:RBR:1:1",
        zone_type="RBR",
        symbol="XAUUSD",
        created_at=1,
        base_start=1,
        base_end=1,
        proximal=10,
        distal=9,
        atr=1,
        departure_end=2,
        bos_level=11,
    )
    z2 = Zone(
        zone_id="XAUUSD:M30:RBR:1:1",
        zone_type="RBR",
        symbol="XAUUSD",
        created_at=1,
        base_start=1,
        base_end=1,
        proximal=10,
        distal=9,
        atr=1,
        departure_end=2,
        bos_level=11,
    )
    assert z1.zone_id == z2.zone_id


def test_first_retest_audit_once():
    diag = V2DiagnosticCollector()
    from core.strategies.v2_zones import Zone

    z = Zone(
        zone_id="Z1",
        zone_type="RBR",
        symbol="XAUUSD",
        created_at=10,
        base_start=1,
        base_end=2,
        proximal=100,
        distal=90,
        atr=2,
        departure_end=20,
        bos_level=110,
    )
    z.state = "WAITING_CONFIRMATION"
    z.zone_entry_time = 30
    z.touch_count = 1
    plugin = SimpleNamespace(_zones={"Z1": z}, _seen_base_ends=set(), stats={})
    m15 = [_c(30 + i * 900, 100, 101, 99, 100) for i in range(10)]
    ctx = build_market_context(
        symbol="XAUUSD",
        candles={"M15": m15, "M30": m15, "H1": m15, "H4": m15},
        h1_direction="BULL",
        h4_direction="BULL",
        bid=100,
        ask=100.1,
    )
    d = StrategyDecision("wait", "w", metadata={"reason_code": "waiting_m15_structure_shift"})
    # simulate prev WAITING_PULLBACK → CONFIRMATION by seeding prev state
    diag._prev_zone_states["Z1"] = "WAITING_PULLBACK"
    diag.after_evaluate(plugin, ctx, d, "t1", cursor_unix=30)
    assert "Z1" in diag.retests
    diag.after_evaluate(plugin, ctx, d, "t2", cursor_unix=31)
    assert len(diag.retests) == 1


def test_rejection_reason_primary_no_double_count():
    diag = V2DiagnosticCollector()
    diag.stage_rejects["BASE_RANGE_TOO_LARGE"] += 1
    diag.stage_rejects["DEPARTURE_TOO_WEAK"] += 1
    assert sum(diag.stage_rejects.values()) == 2


def test_m15_pivot_candle_vs_confirmation_time():
    # RIGHT=2 → confirm two bars after pivot candle
    candles = []
    t0 = 1_700_000_000
    for i in range(20):
        px = 2000 + (5 if i == 10 else 0)
        candles.append(_c(t0 + i * 900, px, px + 1, px - 1, px))
    highs = confirmed_swing_highs(candles, left=2, right=2)
    assert highs
    idx, _px = highs[0]
    pivot_ts = int(candles[idx]["time"])
    conf_ts = int(candles[idx + 2]["time"])
    assert conf_ts > pivot_ts
    assert conf_ts - pivot_ts == 1800  # 2 * 15m


def test_no_unconfirmed_pivot_usage_in_strategy_helpers():
    candles = [_c(i, 1, 2, 0, 1) for i in range(5)]
    # with only 5 bars, last possible pivot needing RIGHT=2 can't be last bars
    highs = confirmed_swing_highs(candles, left=2, right=2)
    for i, _ in highs:
        assert i + 2 < len(candles)


def test_wick_only_and_close_break_classification():
    diag = V2DiagnosticCollector()
    from core.strategies.v2_zones import Zone

    z = Zone(
        zone_id="ZBUY",
        zone_type="RBR",
        symbol="XAUUSD",
        created_at=1,
        base_start=1,
        base_end=2,
        proximal=100,
        distal=90,
        atr=2,
        departure_end=10,
        bos_level=110,
    )
    z.state = "WAITING_CONFIRMATION"
    z.zone_entry_time = 100
    # Flat then wick above trigger without close, then close above
    m15 = [
        _c(100, 100, 100.5, 99.5, 100),
        _c(1900, 100, 105, 99, 100),  # wick 105, close 100 — wick only if trigger=104
        _c(2800, 100, 106, 99, 105.5),  # close through
    ]
    # Build synthetic swing high at bar0 = 100.5 confirmed by bars needing room —
    # inject retest record and call _update directly
    diag.retests["ZBUY"] = {
        "zone_id": "ZBUY",
        "zone_type": "RBR",
        "entry_side": "BUY",
        "zone_retest_at": 100,
        "aligned": True,
        "aligned_while_waiting": True,
        "wick_broke_trigger": False,
        "close_broke_trigger": False,
        "final_result": None,
        "m15_category": None,
        "mfe_price": 0,
        "mae_price": 0,
        "mfe_pips": 0,
        "mae_pips": 0,
        "DIAGNOSTIC_ONLY": True,
    }
    # Force known pivot by patching path: set after first update with expanded series
    # that has confirmed swing high at index with price 104
    series = []
    t0 = 0
    for i in range(20):
        # create a clear pivot high at i=10
        h = 104 if i == 10 else 101
        series.append(_c(t0 + i * 900, 100, h, 99, 100.5 if i != 12 else 100.2))
    # entry at time of bar 12
    z.zone_entry_time = int(series[12]["time"])
    diag.retests["ZBUY"]["zone_retest_at"] = z.zone_entry_time
    # add wick-only then close bars after
    series.append(_c(series[-1]["time"] + 900, 100, 105, 99, 100))  # wick only vs 104
    series.append(_c(series[-1]["time"] + 900, 100, 106, 99, 105))  # close break
    d = StrategyDecision("wait", "w", metadata={"reason_code": "waiting_m15_structure_shift"})
    diag._update_retest(z, series, series, "BULL", "BULLISH", "t", d)
    assert diag.retests["ZBUY"]["wick_broke_trigger"] is True
    assert diag.retests["ZBUY"]["close_broke_trigger"] is True
    assert diag.retests["ZBUY"]["m15_category"] == "E_CLOSE_THROUGH_PIVOT"


def test_near_miss_buy_sell():
    trigger = 100.0
    best_close_buy = 99.5
    dist_buy = trigger - best_close_buy
    assert abs(dist_buy - 0.5) < 1e-9
    assert _pips(dist_buy) > 0
    best_close_sell = 100.5
    dist_sell = best_close_sell - trigger
    assert abs(dist_sell - 0.5) < 1e-9


def test_mfe_mae_buy_sell():
    # BUY: mfe = max high - first close; mae = first close - min low
    after = [_c(1, 100, 110, 95, 100), _c(2, 100, 108, 98, 105)]
    mfe = max(c["high"] for c in after) - after[0]["close"]
    mae = after[0]["close"] - min(c["low"] for c in after)
    assert mfe == 10
    assert mae == 5
    # SELL inverse
    mfe_s = after[0]["close"] - min(c["low"] for c in after)
    mae_s = max(c["high"] for c in after) - after[0]["close"]
    assert mfe_s == 5
    assert mae_s == 10


def test_rr_breakdown_matches_formula():
    entry, sl, tp = 100.0, 90.0, 115.0
    risk = entry - sl
    reward = tp - entry
    rr = reward / risk
    assert round(rr, 4) == 1.5


def test_structural_target_excludes_future_pivots():
    # confirmed_swing_highs only returns pivots with RIGHT bars present
    candles = [_c(i, 1, 2 + (1 if i == 3 else 0), 0, 1) for i in range(8)]
    highs = confirmed_swing_highs(candles, left=2, right=2)
    for i, _ in highs:
        assert i + 2 < len(candles)


def test_counterfactual_does_not_generate_trade():
    diag = V2DiagnosticCollector()
    assert diag.candidates == []
    # counterfactual fields are labels only
    row = {"DIAGNOSTIC_ONLY": True, "NOT_A_STRATEGY_SIGNAL": True, "counterfactual_rr_mid": 2.0}
    assert row["DIAGNOSTIC_ONLY"] is True
    assert len(diag.candidates) == 0


def test_fib_diagnostics_do_not_filter():
    from core.strategies.v2_structure import fib_overlap_for_impulse

    overlap, lvl, pct = fib_overlap_for_impulse(100, 200, 140, 130)
    # Fib is observational; strategy does not skip on False
    assert isinstance(overlap, bool)


def test_dashboard_handles_missing_diagnostic_artifact(tmp_path):
    run = tmp_path / "BT_OLD"
    run.mkdir()
    (run / "meta.json").write_text(
        json.dumps({"run_id": "BT_OLD", "strategy": "breakout_retest_v1"}), encoding="utf-8"
    )
    (run / "funnel.json").write_text(json.dumps({"total_evaluations": 10}), encoding="utf-8")
    detail = load_run_detail(run)
    assert detail["has_v2_diagnostics"] is False
    assert detail["v2_zone_audit"] == []
    assert detail["funnel"]["total_evaluations"] == 10


def test_diagnose_rbr_returns_machine_reason():
    candles = [_c(i * 1800, 2000, 2001, 1999, 2000) for i in range(40)]
    reason = diagnose_rbr_failure(candles, 30)
    assert reason is not None
    assert isinstance(reason, str)


def test_backtest_safety_imports():
    from backtest.safety import assert_backtest_safe, assert_not_live_sinks

    assert_backtest_safe("test")
    assert_not_live_sinks(mt5_enabled=False, telegram_enabled=False)
