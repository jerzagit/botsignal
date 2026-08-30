"""Phase B/E historical replay pipeline tests (no live MT5 / Telegram)."""

from __future__ import annotations

import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from backtest.clock import ReplayClock
from backtest.dataset import (
    dataset_content_hash,
    validate_dataset,
    validate_timeframe_candles,
    write_candles_csv,
    write_dataset_meta,
)
from backtest.interfaces import Candle
from backtest.provider import HistoricalReplayProvider, LookAheadError
from backtest.runner import run_replay
from backtest.safety import assert_backtest_safe
from backtest.timeframes import is_bar_closed, period_seconds
from core.trend_analyzer import analyze_candles


def _ts(y, m, d, hh, mm=0) -> int:
    return int(datetime(y, m, d, hh, mm, tzinfo=timezone.utc).timestamp())


def _bar(t: int, o: float, h: float, l: float, c: float) -> dict:
    return {
        "time": t,
        "open": o,
        "high": h,
        "low": l,
        "close": c,
        "tick_volume": 1,
        "spread": 0,
        "real_volume": 0,
    }


def _write_mini_dataset(tmp: Path, *, days: int = 3, with_dup: bool = False, bad_ohlc: bool = False) -> Path:
    """Build a tiny XAUUSD dataset with M15/H1/H4 for unit tests."""
    ds = tmp / "XAUUSD_test_20260101_20260103"
    ds.mkdir(parents=True, exist_ok=True)
    start = _ts(2026, 1, 2, 0, 0)  # Friday-ish synthetic continuum

    m15_rows = []
    price = 2600.0
    n_m15 = days * 24 * 4
    for i in range(n_m15):
        t = start + i * 900
        o = price
        c = price + (0.5 if i % 7 else -0.3)
        h = max(o, c) + 0.8
        l = min(o, c) - 0.8
        if bad_ohlc and i == 10:
            h = l - 1  # invalid
        m15_rows.append(_bar(t, o, h, l, c))
        price = c
    if with_dup and m15_rows:
        m15_rows.insert(5, dict(m15_rows[5]))

    h1_rows = []
    for i in range(days * 24):
        t = start + i * 3600
        o = 2600 + i * 0.1
        c = o + 0.2
        h1_rows.append(_bar(t, o, o + 0.5, o - 0.5, c))

    h4_rows = []
    for i in range(days * 6):
        t = start + i * 14400
        o = 2600 + i * 0.4
        c = o + 0.3
        h4_rows.append(_bar(t, o, o + 1, o - 1, c))

    write_candles_csv(ds / "M15.csv", m15_rows)
    write_candles_csv(ds / "H1.csv", h1_rows)
    write_candles_csv(ds / "H4.csv", h4_rows)
    write_dataset_meta(
        ds,
        {
            "symbol": "XAUUSD",
            "broker_server": "test",
            "date_from": "2026-01-02",
            "date_to": "2026-01-05",
            "timeframes": ["M15", "H1", "H4"],
            "row_counts": {"M15": len(m15_rows), "H1": len(h1_rows), "H4": len(h4_rows)},
            "data_checksum_sha256": dataset_content_hash(ds, ["M15", "H1", "H4"]),
            "dataset_id": ds.name,
            "timezone": {"canonical_storage": "UTC"},
        },
    )
    return ds


# ── 1. loader ─────────────────────────────────────────────────────────────────

def test_historical_dataset_loader(tmp_path):
    ds = _write_mini_dataset(tmp_path)
    clock = ReplayClock(cursor=datetime(2026, 1, 3, 12, 0, tzinfo=timezone.utc))
    p = HistoricalReplayProvider(ds, clock)
    m15 = p.closed_candles("M15")
    assert len(m15) > 0
    assert m15[0].time < m15[-1].time
    assert all(is_bar_closed(c.time, "M15", clock.time()) for c in m15)


# ── 2. chronological iteration ───────────────────────────────────────────────

def test_chronological_m15_iteration(tmp_path):
    ds = _write_mini_dataset(tmp_path)
    p = HistoricalReplayProvider(ds, ReplayClock(cursor=datetime(1970, 1, 1, tzinfo=timezone.utc)))
    events = p.m15_close_events()
    times = [e[0] for e in events]
    assert times == sorted(times)
    assert len(times) == len(set(times))


# ── 3. no future candle access ───────────────────────────────────────────────

def test_no_future_candle_access(tmp_path):
    ds = _write_mini_dataset(tmp_path)
    cursor = datetime(2026, 1, 2, 10, 0, tzinfo=timezone.utc)
    p = HistoricalReplayProvider(ds, ReplayClock(cursor=cursor))
    closed = p.closed_candles("M15")
    assert closed
    assert all(c.time + 900 <= int(cursor.timestamp()) for c in closed)
    # Next open after last closed must not be visible as closed
    last = closed[-1]
    assert not is_bar_closed(last.time + 900, "M15", cursor.timestamp())
    p.assert_no_future_access()


# ── 4. M15 visibility ────────────────────────────────────────────────────────

def test_m15_visibility_at_close(tmp_path):
    ds = _write_mini_dataset(tmp_path)
    open_t = _ts(2026, 1, 2, 9, 45)
    close_t = open_t + 900  # 10:00
    clock = ReplayClock(cursor=datetime.fromtimestamp(close_t, tz=timezone.utc))
    p = HistoricalReplayProvider(ds, clock)
    times = {c.time for c in p.closed_candles("M15")}
    assert open_t in times
    assert (open_t + 900) not in times  # next bar not closed yet


# ── 5–6. H1 / H4 closed-bar sync ─────────────────────────────────────────────

@pytest.mark.parametrize(
    "hh,mm,expect_h1_0900,expect_h1_1000",
    [
        (9, 45, False, False),   # 09:00 H1 still forming
        (10, 0, True, False),    # 09:00 H1 just closed; 10:00 not closed
        (11, 45, True, True),    # 10:00 H1 closed at 11:00
        (12, 0, True, True),
    ],
)
def test_h1_closed_bar_synchronization(tmp_path, hh, mm, expect_h1_0900, expect_h1_1000):
    ds = _write_mini_dataset(tmp_path)
    cursor = datetime(2026, 1, 2, hh, mm, tzinfo=timezone.utc)
    p = HistoricalReplayProvider(ds, ReplayClock(cursor=cursor))
    times = {c.time for c in p.closed_candles("H1")}
    t0900 = _ts(2026, 1, 2, 9, 0)
    t1000 = _ts(2026, 1, 2, 10, 0)
    assert (t0900 in times) is expect_h1_0900
    assert (t1000 in times) is expect_h1_1000


@pytest.mark.parametrize(
    "hh,mm,expect_h4_0800,expect_h4_1200",
    [
        (9, 45, False, False),
        (10, 0, False, False),
        (11, 45, False, False),
        (12, 0, True, False),  # 08:00 H4 closes at 12:00
    ],
)
def test_h4_closed_bar_synchronization(tmp_path, hh, mm, expect_h4_0800, expect_h4_1200):
    ds = _write_mini_dataset(tmp_path)
    # Ensure 08:00 and 12:00 H4 bars exist (start at 00:00)
    cursor = datetime(2026, 1, 2, hh, mm, tzinfo=timezone.utc)
    p = HistoricalReplayProvider(ds, ReplayClock(cursor=cursor))
    times = {c.time for c in p.closed_candles("H4")}
    t0800 = _ts(2026, 1, 2, 8, 0)
    t1200 = _ts(2026, 1, 2, 12, 0)
    assert (t0800 in times) is expect_h4_0800
    assert (t1200 in times) is expect_h4_1200


# ── 7. ReplayClock integration ───────────────────────────────────────────────

def test_replay_clock_integration(tmp_path):
    ds = _write_mini_dataset(tmp_path)
    clock = ReplayClock(cursor=datetime(2026, 1, 2, 8, 0, tzinfo=timezone.utc))
    p = HistoricalReplayProvider(ds, clock)
    n1 = len(p.closed_candles("M15"))
    clock.advance_to(datetime(2026, 1, 2, 12, 0, tzinfo=timezone.utc))
    n2 = len(p.closed_candles("M15"))
    assert n2 > n1


# ── 8–9. invalid / duplicate detection ───────────────────────────────────────

def test_invalid_dataset_detection(tmp_path):
    ds = _write_mini_dataset(tmp_path, bad_ohlc=True)
    v = validate_dataset(ds, ["M15", "H1", "H4"])
    assert v.status == "INVALID"
    assert v.timeframes["M15"].ohlc_violations >= 1


def test_duplicate_candle_detection(tmp_path):
    ds = _write_mini_dataset(tmp_path, with_dup=True)
    v = validate_timeframe_candles(
        [
            Candle(time=1, open=1, high=2, low=0.5, close=1.5),
            Candle(time=1, open=1, high=2, low=0.5, close=1.5),
        ],
        "M15",
    )
    assert v.duplicate_timestamps == 1
    assert not v.status_ok
    full = validate_dataset(ds, ["M15", "H1", "H4"])
    assert full.status == "INVALID"


# ── 10. deterministic replay ─────────────────────────────────────────────────

def test_deterministic_replay(tmp_path, monkeypatch):
    monkeypatch.setenv("RUN_MODE", "BACKTEST")
    ds = _write_mini_dataset(tmp_path, days=5)
    runs = tmp_path / "runs"
    a = run_replay(ds, runs_root=runs, run_id="BT_DET_A", max_bars=200)
    b = run_replay(ds, runs_root=runs, run_id="BT_DET_B", max_bars=200)
    assert a["decision_hash"] == b["decision_hash"]
    assert a["run_hash"] == b["run_hash"]
    assert a["funnel"] == b["funnel"]
    assert a["m15_processed"] == b["m15_processed"]


# ── 11–12. MT5 order_send / Telegram never called ────────────────────────────

def test_order_send_and_telegram_never_called(tmp_path, monkeypatch):
    monkeypatch.setenv("RUN_MODE", "BACKTEST")

    def boom_order(*_a, **_k):
        raise AssertionError("mt5.order_send must never be called during replay")

    def boom_tg(*_a, **_k):
        raise AssertionError("Telegram send must never be called during replay")

    # Patch common live sinks if modules are importable
    import core.mt5 as mt5_mod

    monkeypatch.setattr(mt5_mod, "execute_trade", boom_order, raising=False)
    if hasattr(mt5_mod, "mt5"):
        monkeypatch.setattr(mt5_mod.mt5, "order_send", boom_order, raising=False)

    try:
        import MetaTrader5 as mt5_pkg

        monkeypatch.setattr(mt5_pkg, "order_send", boom_order, raising=False)
    except Exception:
        pass

    try:
        import core.notifier as notifier

        for name in ("send_message", "notify", "send_telegram", "tg_send"):
            if hasattr(notifier, name):
                monkeypatch.setattr(notifier, name, boom_tg)
    except Exception:
        pass

    ds = _write_mini_dataset(tmp_path, days=4)
    result = run_replay(ds, runs_root=tmp_path / "runs", run_id="BT_SAFE_001", max_bars=80)
    assert result["m15_processed"] > 0
    assert_backtest_safe("post-replay")


def test_runner_fails_closed_without_backtest_mode(tmp_path, monkeypatch):
    monkeypatch.delenv("RUN_MODE", raising=False)
    ds = _write_mini_dataset(tmp_path)
    with pytest.raises(RuntimeError, match="Backtest safety gate"):
        run_replay(ds, runs_root=tmp_path / "runs", run_id="BT_BLOCK")


def test_analyze_candles_pure_voting():
    """Smoke: pure analyzer returns overall without MT5."""
    closes = [float(100 + i * 0.1) for i in range(40)]
    highs = [c + 0.5 for c in closes]
    lows = [c - 0.5 for c in closes]
    out = analyze_candles(closes, highs, lows)
    assert out is not None
    assert out["overall"] in {"BULL", "BEAR", "NEUTRAL"}
    assert math.isfinite(out["rsi_val"])


def test_forming_stub_no_lookahead(tmp_path):
    ds = _write_mini_dataset(tmp_path)
    close_t = _ts(2026, 1, 2, 10, 0)
    clock = ReplayClock(cursor=datetime.fromtimestamp(close_t, tz=timezone.utc))
    p = HistoricalReplayProvider(ds, clock)
    window = p.m15_strategy_window(30)
    assert window[-1]["time"] == close_t
    assert window[-2]["time"] == close_t - 900
    # Mid-bar: forming stub is the in-progress bar open (<= cursor), flat OHLC — no future OHLC
    mid = close_t - 60
    clock2 = ReplayClock(cursor=datetime.fromtimestamp(mid, tz=timezone.utc))
    p2 = HistoricalReplayProvider(ds, clock2)
    window2 = p2.m15_strategy_window(30)
    assert window2[-1]["time"] <= mid
    last_closed = window2[-2]
    forming = window2[-1]
    assert forming["open"] == forming["high"] == forming["low"] == forming["close"] == last_closed["close"]
    closed_times = {c.time for c in p2.closed_candles("M15")}
    assert (close_t - 900) not in closed_times  # 09:45 bar not closed until 10:00
