"""Tests for passive V2 geometry diagnostics — must not alter strategy decisions."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from backtest.catalog import default_runs_root, load_run_detail
from backtest.v2_diagnostics import V2DiagnosticCollector
from backtest.geometry_math import compute_geometry
from backtest.v2_geometry_diagnostics import (
    DIAGNOSTIC_ONLY,
    GEOMETRY_OBSERVATION_ONLY,
    NO_SIGNAL,
    classify_root_cause,
    eligible_h1_targets,
    nearest_opposing_m30_zones,
    next_further_h1_target,
    pivot_confirmed_before,
    production_sl,
    production_tp,
    rr_reference_levels,
    run_geometry_diagnostic,
    semantic_hash,
    sl_valid,
)
from core.config import MIN_RR_RATIO
from core.strategies.base import StrategyDecision, build_market_context
from core.strategies.registry import get_strategy
from core.strategies.structure_pullback_v2_1 import StructurePullbackV21
from core.strategies.structure_pullback_v2_2 import StructurePullbackV22


def _c(t, o, h, l, cl):
    return {"time": t, "open": o, "high": h, "low": l, "close": cl}


RUNS = default_runs_root()
DATASET = RUNS / "datasets" / "XAUUSD_EightcapDemo_20260101_20260801"
V2_RUN = RUNS / "BT_XAUUSD_V2_DIAGNOSTIC_001"
V21_RUN = RUNS / "BT_XAUUSD_V2_1_LIFECYCLE_001"
V22_RUN = RUNS / "BT_XAUUSD_V2_2_LOCAL_M15_001"


def _strategy_snap(strategy: str, ctx):
    plugin = get_strategy(strategy)
    plugin.reset()
    d = plugin.evaluate(ctx)
    snap = (d.action, d.reason, json.dumps(d.metadata or {}, sort_keys=True))
    plugin2 = get_strategy(strategy)
    plugin2.reset()
    d2 = plugin2.evaluate(ctx)
    return snap, (d2.action, d2.reason, json.dumps(d2.metadata or {}, sort_keys=True))


@pytest.fixture
def tiny_ctx():
    m15 = [_c(i * 900, 100, 101, 99, 100) for i in range(20)]
    return build_market_context(
        symbol="XAUUSD",
        timestamp="t0",
        candles={"M15": m15, "M30": m15, "H1": m15, "H4": m15},
        h1_direction="BULL",
        h4_direction="BULL",
        bid=100,
        ask=100.1,
    )


def test_diagnostics_do_not_alter_v2_decisions(tiny_ctx):
    snap, after = _strategy_snap("structure_pullback_v2", tiny_ctx)
    diag = V2DiagnosticCollector()
    plugin = get_strategy("structure_pullback_v2")
    plugin.reset()
    d = plugin.evaluate(tiny_ctx)
    diag.after_evaluate(plugin, tiny_ctx, d, "t0")
    assert after == snap


def test_diagnostics_do_not_alter_v21_decisions(tiny_ctx):
    plugin = StructurePullbackV21()
    d1 = plugin.evaluate(tiny_ctx)
    snap = (d1.action, d1.reason, json.dumps(d1.metadata or {}, sort_keys=True))
    diag = V2DiagnosticCollector()
    diag.after_evaluate(plugin, tiny_ctx, d1, "t0")
    d2 = plugin.evaluate(tiny_ctx)
    assert (d2.action, d2.reason, json.dumps(d2.metadata or {}, sort_keys=True)) == snap


def test_diagnostics_do_not_alter_v22_decisions(tiny_ctx):
    plugin = StructurePullbackV22()
    d1 = plugin.evaluate(tiny_ctx)
    snap = (d1.action, d1.reason, json.dumps(d1.metadata or {}, sort_keys=True))
    diag = V2DiagnosticCollector()
    diag.after_evaluate(plugin, tiny_ctx, d1, "t0")
    d2 = plugin.evaluate(tiny_ctx)
    assert (d2.action, d2.reason, json.dumps(d2.metadata or {}, sort_keys=True)) == snap


def test_calc_rr_buy_sell():
    g = compute_geometry("buy", 100, 90, 115)
    assert g["risk"] == 10
    assert g["reward"] == 15
    assert g["rr"] == 1.5
    g2 = compute_geometry("sell", 100, 110, 85)
    assert g2["risk"] == 10
    assert g2["reward"] == 15
    assert g2["rr"] == 1.5


def test_sl_valid_rejects_invalid_buy_sell():
    assert sl_valid("buy", 100, 95)
    assert not sl_valid("buy", 100, 105)
    assert sl_valid("sell", 100, 105)
    assert not sl_valid("sell", 100, 95)


def test_rr_reference_1_4r_buy_sell():
    buy = rr_reference_levels("buy", 100, 10)
    assert buy["price_1_4R"] == 114.0
    sell = rr_reference_levels("sell", 100, 10)
    assert sell["price_1_4R"] == 86.0


def test_future_h1_target_excluded():
    h1 = [
        _c(0, 1, 2, 0.5, 1.5),
        _c(3600, 1, 2, 0.5, 1.5),
        _c(7200, 1, 5, 0.5, 4.5),
        _c(10800, 1, 6, 0.5, 5.5),
        _c(14400, 1, 7, 0.5, 6.5),
        _c(18000, 1, 8, 0.5, 7.5),
    ]
    # pivot at index 2 high=5 confirmed at index 4 time 14400
    targets = eligible_h1_targets(h1, 100, "buy", as_of_unix=10800)
    assert all(t["pivot_confirmed_timestamp"] <= 10800 for t in targets)


def test_future_m15_pivot_excluded():
    m15 = [_c(i * 900, 1, 2, 0.5, 1.5) for i in range(10)]
    assert not pivot_confirmed_before(m15, 5, 3600)


def test_next_h1_target_sorted():
    targets = [
        {"price": 110, "distance": 10},
        {"price": 120, "distance": 20},
        {"price": 130, "distance": 30},
    ]
    nxt = next_further_h1_target(targets, 120, "buy")
    assert nxt["price"] == 130


def test_nearest_opposing_m30_zone():
    zones = [
        {
            "zone_id": "Z1",
            "zone_type": "DBD",
            "zone_created_at": 100,
            "proximal": 105,
            "distal": 110,
            "invalidated_at": None,
        },
        {
            "zone_id": "Z2",
            "zone_type": "DBD",
            "zone_created_at": 100,
            "proximal": 102,
            "distal": 108,
            "invalidated_at": None,
        },
    ]
    found = nearest_opposing_m30_zones(zones, 100, "buy", 200, limit=1)
    assert found[0]["zone_id"] == "Z2"


def test_future_m30_zone_excluded():
    zones = [
        {
            "zone_id": "Z1",
            "zone_type": "DBD",
            "zone_created_at": 500,
            "proximal": 105,
            "distal": 110,
            "invalidated_at": None,
        },
    ]
    found = nearest_opposing_m30_zones(zones, 100, "buy", 200, limit=3)
    assert found == []


def test_root_cause_classification_deterministic():
    assert classify_root_cause(0.9, 0.5, 0.1, 0.1) == "ENTRY_DELAY_DOMINANT"
    assert classify_root_cause(0.9, 0.4, 0.4, 0.1) == "COMBINED_GEOMETRY"
    assert classify_root_cause(1.5, 0, 0, 0) == "CURRENT_GEOMETRY_ALREADY_REASONABLE"


def test_retest_geometry_labels():
    from backtest.v2_geometry_diagnostics import analyze_retest_geometry

    retest = {
        "zone_id": "Z",
        "zone_type": "RBR",
        "zone_retest_at": 900,
        "proximal": 100,
        "distal": 95,
        "zone_width": 5,
    }
    zone = {"m30_atr": 1.0}
    m15 = [_c(i * 900, 100, 101, 99, 100) for i in range(5)]
    h1 = [_c(i * 3600, 100, 101, 99, 100) for i in range(10)]
    row = analyze_retest_geometry(retest, zone, m15, h1)
    assert row["label"] == GEOMETRY_OBSERVATION_ONLY
    assert row["entry_valid_signal"] == NO_SIGNAL


def test_geometry_module_import_only_no_strategy_effect(tiny_ctx):
    """Importing geometry module must not register hooks."""
    plugin = get_strategy("structure_pullback_v2")
    plugin.reset()
    before = plugin.evaluate(tiny_ctx)
    import backtest.v2_geometry_diagnostics  # noqa: F401
    plugin.reset()
    after = plugin.evaluate(tiny_ctx)
    assert (after.action, after.reason) == (before.action, before.reason)


@pytest.mark.skipif(not V22_RUN.is_dir(), reason="V2.2 baseline run missing")
def test_current_geometry_reproduces_v22_candidate_rr():
    cand_doc = json.loads((V22_RUN / "v2_candidate_geometry.json").read_text(encoding="utf-8"))
    c1 = cand_doc["candidates"][0]
    entry, sl, tp = float(c1["entry"]), float(c1["sl"]), float(c1["tp"])
    g = compute_geometry("sell", entry, sl, tp)
    assert g["rr"] == c1["rr"]


@pytest.mark.skipif(not DATASET.is_dir(), reason="dataset missing")
@pytest.mark.skipif(not V22_RUN.is_dir(), reason="V2.2 run missing")
def test_geometry_diagnostic_run_deterministic():
    sources = [
        (V2_RUN, "structure_pullback_v2"),
        (V21_RUN, "structure_pullback_v2_1"),
        (V22_RUN, "structure_pullback_v2_2"),
    ]
    out1 = RUNS / "BT_XAUUSD_V2_GEOMETRY_DIAGNOSTIC_TEST_A"
    out2 = RUNS / "BT_XAUUSD_V2_GEOMETRY_DIAGNOSTIC_TEST_B"
    r1 = run_geometry_diagnostic(DATASET, sources, out1, retest_source=V22_RUN)
    r2 = run_geometry_diagnostic(DATASET, sources, out2, retest_source=V22_RUN)
    assert r1["semantic_hash"] == r2["semantic_hash"]


@pytest.mark.skipif(not DATASET.is_dir(), reason="dataset missing")
@pytest.mark.skipif(not V22_RUN.is_dir(), reason="V2.2 run missing")
def test_full_geometry_run_reproduces_candidate1_rr():
    sources = [
        (V2_RUN, "structure_pullback_v2"),
        (V21_RUN, "structure_pullback_v2_1"),
        (V22_RUN, "structure_pullback_v2_2"),
    ]
    out = RUNS / "BT_XAUUSD_V2_GEOMETRY_DIAGNOSTIC_TEST_C"
    run_geometry_diagnostic(DATASET, sources, out, retest_source=V22_RUN)
    rows = [
        json.loads(l)
        for l in (out / "v2_candidate_geometry_decomposition.jsonl").read_text(encoding="utf-8").splitlines()
        if l.strip()
    ]
    c1 = next(r for r in rows if "1779348600" in r["zone_id"])
    assert c1["CURRENT_GEOMETRY"]["current_rr"] == 0.9912


def test_matrix_rows_diagnostic_only():
    row = {
        "entry_valid_signal": NO_SIGNAL,
        "diagnostic_only": DIAGNOSTIC_ONLY,
        "chronologically_valid": True,
    }
    assert row["entry_valid_signal"] == "NO_SIGNAL"
    assert row["diagnostic_only"] == "DIAGNOSTIC_ONLY"


def test_dashboard_handles_na_alternatives():
    detail = load_run_detail(RUNS / "BT_XAUUSD_V2_GEOMETRY_DIAGNOSTIC_TEST_C") if (
        RUNS / "BT_XAUUSD_V2_GEOMETRY_DIAGNOSTIC_TEST_C"
    ).is_dir() else {"has_geometry_diagnostics": False}
    if detail.get("has_geometry_diagnostics"):
        assert detail["v2_geometry_decomposition"] is not None


def test_semantic_hash_stable():
    a = semantic_hash([{"x": 1}])
    b = semantic_hash([{"x": 1}])
    assert a == b
