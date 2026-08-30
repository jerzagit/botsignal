"""
Fail-closed safety for historical replay.

Backtest code MUST call assert_backtest_safe() before touching any adapter
that could reach MT5 or Telegram in a misconfigured environment.
"""

from __future__ import annotations

import os


ALLOWED_BACKTEST_MODES = {"BACKTEST", "REPLAY", "SIM"}


def run_mode() -> str:
    return (os.getenv("RUN_MODE") or "LIVE").strip().upper()


def is_backtest_mode() -> bool:
    return run_mode() in ALLOWED_BACKTEST_MODES


def assert_backtest_safe(context: str = "") -> None:
    """
    Raise if we are not explicitly in backtest mode.

    Live bot does not set RUN_MODE (defaults to LIVE), so accidental import
    of replay runners into production tasks will fail closed when this is called.
    """
    mode = run_mode()
    if mode not in ALLOWED_BACKTEST_MODES:
        raise RuntimeError(
            "Backtest safety gate blocked execution. "
            f"RUN_MODE={mode!r} (need one of {sorted(ALLOWED_BACKTEST_MODES)}). "
            f"Context: {context or 'n/a'}"
        )


def assert_not_live_sinks(mt5_enabled: bool, telegram_enabled: bool) -> None:
    assert_backtest_safe("assert_not_live_sinks")
    if mt5_enabled:
        raise RuntimeError("BACKTEST must not enable live MT5 order sinks.")
    if telegram_enabled:
        raise RuntimeError("BACKTEST must not enable live Telegram sinks.")
