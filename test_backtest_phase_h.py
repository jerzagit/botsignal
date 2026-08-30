"""Phase H confidence / gap / symbol-spec tests."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from backtest.costs import CostModel, apply_costs_to_trade_pnl
from backtest.fills import resolve_fill_price
from backtest.gap_analysis import (
    EXPECTED_BROKER_SESSION_BREAK,
    EXPECTED_WEEKEND_CLOSE,
    SUSPICIOUS_DATA_GAP,
    analyze_gaps,
    classify_gap,
)
from backtest.interfaces import Candle
from backtest.lot import calculate_lot_pure
from backtest.performance import assess_confidence
from backtest.spread_validation import analyze_spreads, spread_points_to_pips
from backtest.symbol_spec import QUALITY_ASSUMPTION, QUALITY_EXACT, load_symbol_spec, resolve_symbol_spec
from test_backtest_replay import _write_mini_dataset
from backtest.runner import run_replay


def test_broker_spec_loader():
    spec = load_symbol_spec(ROOT / "data/backtests/broker_specs/TEST_XAUUSD.json")
    assert spec.quality == QUALITY_EXACT
    assert spec.point == 0.01
    assert spec.tick_value == 1.0
    assert resolve_symbol_spec(None).quality == QUALITY_ASSUMPTION


def test_broker_spec_missing_fields_fails(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text(json.dumps({"symbol": "XAUUSD"}), encoding="utf-8")
    with pytest.raises(ValueError, match="missing required"):
        load_symbol_spec(p)


def test_lot_sizing_with_captured_spec():
    spec = load_symbol_spec(ROOT / "data/backtests/broker_specs/TEST_XAUUSD.json")
    lot, risk, _ = calculate_lot_pure(
        equity=10000, free_margin=10000, entry=2000, sl=1990, risk_percent=0.01, spec=spec
    )
    assert lot > 0
    assert risk == 100.0


def test_weekend_and_daily_break_classification():
    # Fri 21:00 -> Mon 01:00
    fri = int(datetime(2026, 1, 2, 21, 0, tzinfo=timezone.utc).timestamp())  # Friday
    mon = int(datetime(2026, 1, 5, 1, 0, tzinfo=timezone.utc).timestamp())
    assert classify_gap(fri, mon, "M15") == EXPECTED_WEEKEND_CLOSE

    # Daily break: bar open 23:00 -> next open 01:00 (missing 00:00-01:00)
    t0 = int(datetime(2026, 1, 6, 23, 0, tzinfo=timezone.utc).timestamp())  # Tue
    t1 = int(datetime(2026, 1, 7, 1, 0, tzinfo=timezone.utc).timestamp())
    assert classify_gap(t0, t1, "M15") == EXPECTED_BROKER_SESSION_BREAK


def test_suspicious_midweek_gap():
    t0 = int(datetime(2026, 1, 6, 10, 0, tzinfo=timezone.utc).timestamp())
    t1 = int(datetime(2026, 1, 6, 12, 0, tzinfo=timezone.utc).timestamp())  # 2h hole midday
    assert classify_gap(t0, t1, "M15") == SUSPICIOUS_DATA_GAP


def test_analyze_gaps_counts():
    # Build synthetic series with weekend + daily break
    candles = []
    start = int(datetime(2026, 1, 5, 1, 0, tzinfo=timezone.utc).timestamp())  # Mon 01:00
    t = start
    for i in range(20):
        candles.append(Candle(time=t, open=1, high=2, low=0.5, close=1.5, spread=16))
        t += 900
    # inject daily break style jump
    candles.append(
        Candle(
            time=int(datetime(2026, 1, 6, 23, 0, tzinfo=timezone.utc).timestamp()),
            open=1,
            high=2,
            low=0.5,
            close=1.5,
            spread=16,
        )
    )
    candles.append(
        Candle(
            time=int(datetime(2026, 1, 7, 1, 0, tzinfo=timezone.utc).timestamp()),
            open=1,
            high=2,
            low=0.5,
            close=1.5,
            spread=16,
        )
    )
    candles = sorted(candles, key=lambda c: c.time)
    report = analyze_gaps(candles, "M15")
    assert report["raw_gap_count"] >= 1
    assert "counts_by_class" in report


def test_spread_conversion():
    # 16 points * 0.01 / 0.1 = 1.6 pips
    assert abs(spread_points_to_pips(16, 0.01) - 1.6) < 1e-9
    spec = load_symbol_spec(ROOT / "data/backtests/broker_specs/TEST_XAUUSD.json")
    candles = [
        Candle(time=i * 900, open=1, high=2, low=0.5, close=1.5, spread=16) for i in range(10)
    ]
    rep = analyze_spreads(candles, spec, candidate_bar_times={0, 900})
    assert rep["conversion_validated"] is True
    assert rep["all_bars_pips"]["median"] == 1.6


def test_cost_scenario_isolation():
    raw = 100.0
    cost = CostModel(commission_per_lot=7.0, slippage_pips=0.5)
    br = apply_costs_to_trade_pnl(
        direction="buy",
        entry=2000,
        exit_price=2010,
        lot=0.1,
        raw_pnl=raw,
        cost=cost,
        pip_size=0.1,
        tick_size=0.01,
        tick_value=1.0,
    )
    assert br["raw_pnl"] == 100.0
    assert br["adjusted_pnl"] < 100.0
    assert br["cost_model"]["label"] == "COST_SCENARIO"


def test_fill_policy_determinism():
    a, _ = resolve_fill_price(
        "CANDIDATE_PRICE", candidate_entry=10.0, signal_bar_close=11.0, next_bar_open=12.0
    )
    b, _ = resolve_fill_price(
        "SIGNAL_BAR_CLOSE", candidate_entry=10.0, signal_bar_close=11.0, next_bar_open=12.0
    )
    c, _ = resolve_fill_price(
        "NEXT_M15_OPEN", candidate_entry=10.0, signal_bar_close=11.0, next_bar_open=12.0
    )
    assert a == 10.0 and b == 11.0 and c == 12.0


def test_confidence_components_with_exact_spec():
    overall, reasons, comps = assess_confidence(
        validation_status="VALID_WITH_GAPS",
        ambiguous=0,
        spread_policy="historical",
        fill_policy="CANDIDATE_ENTRY_AT_SIGNAL_BAR_CLOSE",
        suspicious_gaps=2,
        material_gaps=0,
        symbol_spec_quality=QUALITY_EXACT,
        spread_conversion_validated=True,
        costs_modelled=False,
    )
    assert comps["strategy_decision"] == "HIGH"
    assert comps["lot_sizing"] == "HIGH"
    assert comps["spread"] == "HIGH"
    assert overall in ("MEDIUM", "HIGH", "LOW")
    assert overall != "HIGH"  # capped without ticks


def test_simulate_with_symbol_spec_keeps_candidates(tmp_path, monkeypatch):
    monkeypatch.setenv("RUN_MODE", "BACKTEST")
    ds = _write_mini_dataset(tmp_path, days=8)
    r = run_replay(
        ds,
        runs_root=tmp_path / "runs",
        run_id="BT_H_SPEC",
        simulate_trades=True,
        initial_balance=10000,
        max_bars=400,
        symbol_spec_path=ROOT / "data/backtests/broker_specs/TEST_XAUUSD.json",
    )
    assert r["candidate_audit_rows"] == r["raw_buy_sell"]
    assert r["meta"]["symbol_spec"]["quality"] == QUALITY_EXACT
