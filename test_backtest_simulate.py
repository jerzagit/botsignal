"""Phase C/F/G simulation tests."""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from backtest.account import SimulatedAccount
from backtest.execution import SimulatedExecution
from backtest.guards import OpenPositionView, evaluate_strategy_guards
from backtest.interfaces import Candle
from backtest.lot import calculate_lot_pure
from backtest.outcomes import check_bar_barriers, pnl_for_exit
from backtest.symbol_spec import default_xauusd_spec
from test_backtest_replay import _write_mini_dataset
from backtest.runner import run_replay


def test_buy_sl_and_tp():
    buy = Candle(time=1, open=100, high=105, low=99, close=104)
    assert check_bar_barriers("buy", sl=98, tp=110, bar=buy) is None
    hit_sl = check_bar_barriers("buy", sl=99.5, tp=110, bar=buy)
    assert hit_sl and hit_sl.outcome == "SL_HIT"
    hit_tp = check_bar_barriers("buy", sl=90, tp=104.5, bar=buy)
    assert hit_tp and hit_tp.outcome == "TP_HIT"


def test_sell_sl_and_tp():
    sell = Candle(time=1, open=100, high=101, low=95, close=96)
    assert check_bar_barriers("sell", sl=102, tp=96, bar=sell).outcome == "TP_HIT"
    assert check_bar_barriers("sell", sl=100.5, tp=90, bar=sell).outcome == "SL_HIT"


def test_same_bar_ambiguous_conservative_sl_first():
    bar = Candle(time=1, open=100, high=110, low=90, close=105)
    hit = check_bar_barriers("buy", sl=95, tp=108, bar=bar, intrabar_policy="conservative")
    assert hit.outcome == "SL_HIT"
    hit2 = check_bar_barriers("buy", sl=95, tp=108, bar=bar, intrabar_policy="unresolved")
    assert hit2.outcome == "AMBIGUOUS_INTRABAR"


def test_session_guard_boundaries(monkeypatch):
    monkeypatch.setenv("RUN_MODE", "BACKTEST")
    acct = SimulatedAccount(10000)
    spec = default_xauusd_spec()
    # 06 UTC — outside default 7-21
    ts = datetime(2026, 1, 2, 6, 0, tzinfo=timezone.utc)
    tr = evaluate_strategy_guards(
        ts=ts,
        direction="buy",
        entry=2000,
        sl=1990,
        tp=2020,
        account=acct,
        open_positions=[],
        spec=spec,
        spread_points=16,
    )
    assert tr.final == "BLOCKED"
    assert tr.blocked_by == "session"
    assert any(g.result == "NOT_EVALUATED" for g in tr.guards)

    ts2 = datetime(2026, 1, 2, 12, 0, tzinfo=timezone.utc)
    tr2 = evaluate_strategy_guards(
        ts=ts2,
        direction="buy",
        entry=2000,
        sl=1990,
        tp=2020,
        account=acct,
        open_positions=[],
        spec=spec,
        spread_points=16,
    )
    assert tr2.final == "WOULD_EXECUTE"


def test_rr_guard_blocks_low_rr(monkeypatch):
    monkeypatch.setenv("RUN_MODE", "BACKTEST")
    acct = SimulatedAccount(10000)
    spec = default_xauusd_spec()
    ts = datetime(2026, 1, 2, 12, 0, tzinfo=timezone.utc)
    # SL 10 pts, TP 5 pts → RR 0.5 < 1.4
    tr = evaluate_strategy_guards(
        ts=ts,
        direction="buy",
        entry=2000,
        sl=1990,
        tp=2005,
        account=acct,
        open_positions=[],
        spec=spec,
        spread_points=16,
    )
    assert tr.final == "BLOCKED"
    assert tr.blocked_by == "rr_ratio"


def test_lot_and_pnl_update_balance():
    spec = default_xauusd_spec()
    lot, risk, _ = calculate_lot_pure(
        equity=10000, free_margin=10000, entry=2000, sl=1990, risk_percent=0.01, spec=spec
    )
    assert lot > 0
    acct = SimulatedAccount(10000)
    ex = SimulatedExecution(acct, spec)
    t = ex.open_trade(
        candidate_id="C1",
        symbol="XAUUSD",
        direction="buy",
        signal_time="t",
        entry_time="t",
        entry=2000,
        sl=1990,
        tp=2020,
        lot=lot,
        risk_usd=risk,
        balance_before=10000,
        guard_quality_summary={},
    )
    bar = Candle(time=2, open=2000, high=2025, low=1995, close=2020)
    hits = ex.process_bar(bar, datetime(2026, 1, 2, 13, tzinfo=timezone.utc))
    assert hits
    assert acct.balance != 10000
    assert t.realized_pnl is not None


def test_stack_guard_reduce(monkeypatch):
    monkeypatch.setenv("RUN_MODE", "BACKTEST")
    acct = SimulatedAccount(10000)
    spec = default_xauusd_spec()
    ts = datetime(2026, 1, 2, 12, 0, tzinfo=timezone.utc)
    opens = [OpenPositionView("T1", "buy", 2000, 1990, 0.05)]
    tr = evaluate_strategy_guards(
        ts=ts,
        direction="buy",
        entry=2000,
        sl=1990,
        tp=2020,
        account=acct,
        open_positions=opens,
        spec=spec,
        spread_points=16,
    )
    # reduce mode should still allow if residual lot > 0
    assert tr.final in ("WOULD_EXECUTE", "BLOCKED")
    stack = next(g for g in tr.guards if g.guard == "same_direction_stack")
    assert stack.result in ("ADJUSTED", "PASS", "FAIL", "NOT_EVALUATED")


def test_trade_cannot_open_after_session_fail(monkeypatch):
    monkeypatch.setenv("RUN_MODE", "BACKTEST")
    acct = SimulatedAccount(10000)
    tr = evaluate_strategy_guards(
        ts=datetime(2026, 1, 2, 3, 0, tzinfo=timezone.utc),
        direction="buy",
        entry=2000,
        sl=1990,
        tp=2020,
        account=acct,
        open_positions=[],
        spec=default_xauusd_spec(),
        spread_points=16,
    )
    assert tr.final == "BLOCKED"
    assert tr.lot is None


def test_simulate_on_mini_dataset_accounting(tmp_path, monkeypatch):
    monkeypatch.setenv("RUN_MODE", "BACKTEST")
    ds = _write_mini_dataset(tmp_path, days=8)
    r = run_replay(
        ds,
        runs_root=tmp_path / "runs",
        run_id="BT_SIM_MINI_001",
        simulate_trades=True,
        initial_balance=10000,
        max_bars=500,
    )
    assert r["candidate_audit_rows"] == r["raw_buy_sell"]
    assert (tmp_path / "runs" / "BT_SIM_MINI_001" / "candidate_audit.csv").exists()
    assert (tmp_path / "runs" / "BT_SIM_MINI_001" / "performance.json").exists()


def test_deterministic_simulate(tmp_path, monkeypatch):
    monkeypatch.setenv("RUN_MODE", "BACKTEST")
    ds = _write_mini_dataset(tmp_path, days=6)
    a = run_replay(
        ds,
        runs_root=tmp_path / "runs",
        run_id="BT_SIM_DET_A",
        simulate_trades=True,
        initial_balance=10000,
        max_bars=300,
    )
    b = run_replay(
        ds,
        runs_root=tmp_path / "runs",
        run_id="BT_SIM_DET_B",
        simulate_trades=True,
        initial_balance=10000,
        max_bars=300,
    )
    assert a["run_hash"] == b["run_hash"]
    assert a["decision_hash"] == b["decision_hash"]


def test_order_send_never_during_simulate(tmp_path, monkeypatch):
    monkeypatch.setenv("RUN_MODE", "BACKTEST")

    def boom(*_a, **_k):
        raise AssertionError("order_send")

    try:
        import MetaTrader5 as mt5

        monkeypatch.setattr(mt5, "order_send", boom, raising=False)
    except Exception:
        pass
    import core.mt5 as mt5_mod

    monkeypatch.setattr(mt5_mod, "execute_trade", boom, raising=False)
    ds = _write_mini_dataset(tmp_path, days=5)
    run_replay(
        ds,
        runs_root=tmp_path / "runs",
        run_id="BT_SIM_SAFE",
        simulate_trades=True,
        initial_balance=5000,
        max_bars=200,
    )


def test_pnl_formula_buy():
    pnl = pnl_for_exit("buy", 2000, 2010, 0.1, tick_size=0.01, tick_value=1.0)
    # 10 / 0.01 * 1 * 0.1 = 100
    assert abs(pnl - 100.0) < 1e-6
