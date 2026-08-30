"""Unit tests for isolated backtest scaffolding (no MT5 / Telegram)."""

from datetime import datetime, timezone, timedelta
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from backtest.clock import LiveClock, ReplayClock
from backtest.safety import assert_backtest_safe, assert_not_live_sinks, is_backtest_mode


def test_live_clock_returns_aware_utc_ish():
    c = LiveClock()
    now = c.now()
    assert now.tzinfo is not None
    assert isinstance(c.time(), float)
    assert 0 <= c.utc_hour() <= 23


def test_replay_clock_is_deterministic():
    ts = datetime(2025, 8, 1, 12, 0, tzinfo=timezone.utc)
    c = ReplayClock(cursor=ts)
    assert c.now() == ts
    assert c.time() == ts.timestamp()
    assert c.utc_hour() == 12


def test_replay_clock_advance_forward_only():
    ts = datetime(2025, 8, 1, 12, 0, tzinfo=timezone.utc)
    c = ReplayClock(cursor=ts)
    c.advance_to(ts + timedelta(hours=1))
    assert c.utc_hour() == 13
    with pytest.raises(ValueError):
        c.advance_to(ts)  # backwards


def test_replay_clock_rejects_lookbehind_advance():
    c = ReplayClock(cursor=datetime(2025, 6, 1, tzinfo=timezone.utc))
    with pytest.raises(ValueError):
        c.advance_to(datetime(2025, 5, 1, tzinfo=timezone.utc))


def test_safety_gate_blocks_default_live_mode(monkeypatch):
    monkeypatch.delenv("RUN_MODE", raising=False)
    assert is_backtest_mode() is False
    with pytest.raises(RuntimeError, match="Backtest safety gate"):
        assert_backtest_safe("unit-test")


def test_safety_gate_allows_backtest_mode(monkeypatch):
    monkeypatch.setenv("RUN_MODE", "BACKTEST")
    assert is_backtest_mode() is True
    assert_backtest_safe("unit-test")
    assert_not_live_sinks(mt5_enabled=False, telegram_enabled=False)
    with pytest.raises(RuntimeError, match="live MT5"):
        assert_not_live_sinks(mt5_enabled=True, telegram_enabled=False)


def test_identical_replay_cursors_are_equal():
    a = ReplayClock(cursor=datetime(2025, 1, 1, tzinfo=timezone.utc))
    b = ReplayClock(cursor=datetime(2025, 1, 1, tzinfo=timezone.utc))
    assert a.now() == b.now()
    assert a.time() == b.time()
