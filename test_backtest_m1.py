"""Phase I M1 resolver unit tests."""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from backtest.interfaces import Candle
from backtest.m1_resolver import M1OutcomeResolver, classify_outcome_change
from backtest.account import SimulatedAccount
from backtest.execution import SimulatedExecution
from backtest.symbol_spec import default_xauusd_spec


def _m1(start: int, n: int, pattern: str) -> list[Candle]:
    """pattern chars: '.' flat, 'S' hit SL path for buy (low), 'T' hit TP (high)."""
    out = []
    px = 2000.0
    for i in range(n):
        t = start + i * 60
        o = px
        h, l, c = px + 0.2, px - 0.2, px
        ch = pattern[i] if i < len(pattern) else "."
        if ch == "S":
            l = px - 20
        if ch == "T":
            h = px + 20
        if ch == "B":  # both
            l = px - 20
            h = px + 20
        out.append(Candle(time=t, open=o, high=h, low=l, close=c, spread=16))
    return out


def test_buy_sl_first_m1():
    start = int(datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc).timestamp())
    r = M1OutcomeResolver(_m1(start, 10, "....S...."))
    res = r.walk_outcome(
        direction="buy",
        entry_unix=start,
        entry_price=2000,
        sl=1990,
        tp=2020,
        until_unix=start + 600,
        intrabar_policy="unresolved",
    )
    assert res.outcome == "SL_HIT"


def test_buy_tp_first_m1():
    start = int(datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc).timestamp())
    r = M1OutcomeResolver(_m1(start, 10, "....T...."))
    res = r.walk_outcome(
        direction="buy",
        entry_unix=start,
        entry_price=2000,
        sl=1990,
        tp=2010,
        until_unix=start + 600,
        intrabar_policy="unresolved",
    )
    assert res.outcome == "TP_HIT"


def test_sell_sl_and_tp():
    start = int(datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc).timestamp())
    r = M1OutcomeResolver(_m1(start, 5, ".S..."))
    # for sell SL is above: high >= sl
    candles = _m1(start, 5, ".....")
    candles[1] = Candle(time=start + 60, open=2000, high=2015, low=1995, close=2000, spread=16)
    r = M1OutcomeResolver(candles)
    assert (
        r.walk_outcome(
            direction="sell",
            entry_unix=start,
            entry_price=2000,
            sl=2010,
            tp=1980,
            until_unix=start + 300,
            intrabar_policy="unresolved",
        ).outcome
        == "SL_HIT"
    )
    candles2 = _m1(start, 5, ".....")
    candles2[2] = Candle(time=start + 120, open=2000, high=2005, low=1975, close=2000, spread=16)
    r2 = M1OutcomeResolver(candles2)
    assert (
        r2.walk_outcome(
            direction="sell",
            entry_unix=start,
            entry_price=2000,
            sl=2010,
            tp=1980,
            until_unix=start + 300,
            intrabar_policy="unresolved",
        ).outcome
        == "TP_HIT"
    )


def test_same_m1_bar_ambiguous():
    start = int(datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc).timestamp())
    r = M1OutcomeResolver(_m1(start, 5, ".B..."))
    res = r.walk_outcome(
        direction="buy",
        entry_unix=start,
        entry_price=2000,
        sl=1990,
        tp=2010,
        until_unix=start + 300,
        intrabar_policy="unresolved",
    )
    assert res.outcome == "AMBIGUOUS_M1_INTRABAR"


def test_no_future_m1_access():
    start = int(datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc).timestamp())
    r = M1OutcomeResolver(_m1(start, 10, ".........."))
    with pytest.raises(RuntimeError):
        r.resolve_entry(
            signal_close_unix=start,
            direction="buy",
            candidate_entry=2000,
            spread_points=16,
            cursor_unix=start - 60,
        )


def test_m1_closes_update_balance_before_next():
    start = int(datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc).timestamp())
    candles = _m1(start, 30, "." * 5 + "S" + "." * 24)
    resolver = M1OutcomeResolver(candles)
    acct = SimulatedAccount(10000)
    ex = SimulatedExecution(acct, default_xauusd_spec())
    t = ex.open_trade(
        candidate_id="C1",
        symbol="XAUUSD",
        direction="buy",
        signal_time="t",
        entry_time="t",
        entry=2000,
        sl=1990,
        tp=2020,
        lot=0.1,
        risk_usd=100,
        balance_before=10000,
        guard_quality_summary={},
        meta={"outcome_engine": "m1", "m1_entry_unix": start},
    )
    bal0 = acct.balance
    hits = ex.process_m1_until(
        resolver, start + 20 * 60, datetime.fromtimestamp(start + 20 * 60, tz=timezone.utc)
    )
    assert hits
    assert acct.balance != bal0
    assert t.status == "CLOSED"


def test_change_classification():
    assert classify_outcome_change("WIN", "LOSS") == "LEGACY_WIN_TO_M1_LOSS"
    assert classify_outcome_change("LOSS", "WIN") == "LEGACY_LOSS_TO_M1_WIN"
    assert classify_outcome_change("WIN", "WIN") == "NO_CHANGE"


def test_m1_gap_daily_break_not_suspicious():
    from backtest.gap_analysis import EXPECTED_BROKER_SESSION_BREAK, classify_gap

    t0 = int(datetime(2026, 6, 2, 23, 59, tzinfo=timezone.utc).timestamp())
    t1 = int(datetime(2026, 6, 3, 1, 0, tzinfo=timezone.utc).timestamp())
    assert classify_gap(t0, t1, "M1") == EXPECTED_BROKER_SESSION_BREAK


def test_open_crosses_many_m15_via_m1_minutes():
    start = int(datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc).timestamp())
    # 40 minutes flat then SL
    r = M1OutcomeResolver(_m1(start, 50, "." * 40 + "S" + "." * 9))
    res = r.walk_outcome(
        direction="buy",
        entry_unix=start,
        entry_price=2000,
        sl=1990,
        tp=2100,
        until_unix=start + 50 * 60,
        intrabar_policy="unresolved",
    )
    assert res.outcome == "SL_HIT"
    assert res.minutes_held is not None and res.minutes_held >= 40


def test_entry_policy_next_m1():
    start = int(datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc).timestamp())
    r = M1OutcomeResolver(_m1(start, 5, "....."))
    px, unix, pol = r.resolve_entry(
        signal_close_unix=start,
        direction="buy",
        candidate_entry=2000.0,
        spread_points=16,
        cursor_unix=start + 60,
    )
    assert pol == "NEXT_M1_AVAILABLE"
    assert unix == start
    assert px == pytest.approx(2000.0 + 0.08)  # half of 16 points * 0.01
