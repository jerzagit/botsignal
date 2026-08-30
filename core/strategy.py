"""
Low-risk full-auto XAUUSD strategy orchestrator.

Loads market data, builds MarketContext, calls the active strategy plugin,
then applies the SAME pre-guards / execute_trade path as before.

Decision algorithms live under core/strategies/ — not in this file.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import Iterable

import MetaTrader5 as mt5

from core.config import (
    ACTIVE_STRATEGY,
    IS_LIVE_MODE,
    YOUR_CHAT_ID,
    MT5_SYMBOL_SUFFIX,
    SL_PIP_SIZE,
    MAX_SPREAD_PIPS,
    SESSION_FILTER_ENABLED,
    SESSION_START_HOUR_UTC,
    SESSION_END_HOUR_UTC,
    STRATEGY_ENABLED,
    STRATEGY_SYMBOL,
    STRATEGY_TIMEFRAME,
    STRATEGY_SCAN_INTERVAL,
    STRATEGY_RISK_PERCENT,
    STRATEGY_DAILY_DRAWDOWN_PERCENT,
    STRATEGY_LIVE_UNLOCKED,
    STRATEGY_BREAKOUT_LOOKBACK,
)
from core.db import record_guard_event, upsert_signal
from core.mt5 import execute_trade, mt5_connect
from core.signal import Signal
from core.state import get_daily_loss
from core.strategies.base import MarketContext, StrategyDecision, build_market_context
from core.strategies.breakout_retest_v1 import evaluate_breakout_retest  # noqa: F401 — compat re-export
from core.strategies.registry import get_strategy, resolve_strategy_name
from core.trend_analyzer import analyze_timeframe

log = logging.getLogger(__name__)

# Backward-compatible re-exports (single V1 implementation lives in strategies/).
__all__ = [
    "StrategyDecision",
    "MarketContext",
    "evaluate_breakout_retest",
    "scan_once",
    "start_strategy",
    "get_strategy_status",
    "LAST_DECISION",
]

TIMEFRAME_MAP = {
    "M1": mt5.TIMEFRAME_M1,
    "M5": mt5.TIMEFRAME_M5,
    "M15": mt5.TIMEFRAME_M15,
    "M30": mt5.TIMEFRAME_M30,
    "H1": mt5.TIMEFRAME_H1,
    "H4": mt5.TIMEFRAME_H4,
    "D1": mt5.TIMEFRAME_D1,
}

LAST_DECISION = {
    "time": None,
    "action": "idle",
    "reason": "Strategy has not scanned yet.",
    "symbol": STRATEGY_SYMBOL,
    "direction": None,
    "price": None,
}


def _symbol_mt5(symbol: str) -> str:
    return symbol if symbol.endswith(MT5_SYMBOL_SUFFIX) else symbol + MT5_SYMBOL_SUFFIX


def _as_candle_dicts(rates: Iterable) -> list[dict]:
    candles = []
    for r in rates:
        candles.append({
            "time": int(r[0]),
            "open": float(r[1]),
            "high": float(r[2]),
            "low": float(r[3]),
            "close": float(r[4]),
        })
    return candles


def _session_ok() -> bool:
    if not SESSION_FILTER_ENABLED:
        return True
    hour = time.gmtime().tm_hour
    if SESSION_START_HOUR_UTC <= SESSION_END_HOUR_UTC:
        return SESSION_START_HOUR_UTC <= hour < SESSION_END_HOUR_UTC
    return hour >= SESSION_START_HOUR_UTC or hour < SESSION_END_HOUR_UTC


def _remember(decision: StrategyDecision, symbol: str, price: float | None = None):
    LAST_DECISION.update({
        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
        "action": decision.action,
        "reason": decision.reason,
        "symbol": symbol,
        "direction": decision.direction,
        "price": round(price, 2) if price is not None else None,
        "entry": round(decision.entry, 2) if decision.entry else None,
        "sl": decision.sl,
        "tp": decision.tp,
        "level": decision.level,
        "strategy": resolve_strategy_name(ACTIVE_STRATEGY),
    })


def get_strategy_status() -> dict:
    return {
        "enabled": STRATEGY_ENABLED,
        "active_strategy": resolve_strategy_name(ACTIVE_STRATEGY),
        "symbol": STRATEGY_SYMBOL,
        "timeframe": STRATEGY_TIMEFRAME,
        "risk_percent": STRATEGY_RISK_PERCENT,
        "daily_drawdown_percent": STRATEGY_DAILY_DRAWDOWN_PERCENT,
        "live_unlocked": STRATEGY_LIVE_UNLOCKED,
        "last_decision": dict(LAST_DECISION),
    }


def scan_once() -> str:
    """Run one strategy scan and maybe execute one trade."""
    symbol = STRATEGY_SYMBOL
    symbol_mt5 = _symbol_mt5(symbol)
    strategy_name = resolve_strategy_name(ACTIVE_STRATEGY)
    plugin = get_strategy(strategy_name)

    if IS_LIVE_MODE and not STRATEGY_LIVE_UNLOCKED:
        decision = StrategyDecision("skip", "Live mode is locked for strategy auto-trading.")
        _remember(decision, symbol)
        return decision.reason

    if not _session_ok():
        decision = StrategyDecision("skip", "Outside configured trading session.")
        _remember(decision, symbol)
        return decision.reason

    if not mt5_connect():
        decision = StrategyDecision("skip", "Could not connect to MT5.")
        _remember(decision, symbol)
        return decision.reason

    account = mt5.account_info()
    if account is None:
        mt5.shutdown()
        decision = StrategyDecision("skip", "Could not read MT5 account.")
        _remember(decision, symbol)
        return decision.reason

    max_daily_loss = account.equity * (STRATEGY_DAILY_DRAWDOWN_PERCENT / 100)
    if get_daily_loss() >= max_daily_loss:
        mt5.shutdown()
        decision = StrategyDecision("skip", "Strategy daily drawdown limit reached.")
        _remember(decision, symbol)
        return decision.reason

    existing = mt5.positions_get(symbol=symbol_mt5) or []
    if existing:
        mt5.shutdown()
        decision = StrategyDecision("skip", f"Open {symbol} position exists; no stacking.")
        _remember(decision, symbol)
        return decision.reason

    tick = mt5.symbol_info_tick(symbol_mt5)
    if tick is None:
        mt5.shutdown()
        decision = StrategyDecision("skip", f"No tick data for {symbol_mt5}.")
        _remember(decision, symbol)
        return decision.reason

    spread_pips = (tick.ask - tick.bid) / SL_PIP_SIZE
    mid = (tick.ask + tick.bid) / 2
    if spread_pips > MAX_SPREAD_PIPS:
        mt5.shutdown()
        decision = StrategyDecision("skip", f"Spread too wide: {spread_pips:.1f} pips.")
        _remember(decision, symbol, mid)
        return decision.reason

    required = tuple(getattr(plugin, "required_timeframes", ("M15", "H1", "H4")))
    candle_map: dict = {}
    lookback = max(STRATEGY_BREAKOUT_LOOKBACK + 5, 80)
    for tf_name in required:
        tf_const = TIMEFRAME_MAP.get(tf_name)
        if tf_const is None:
            continue
        rates_tf = mt5.copy_rates_from_pos(symbol_mt5, tf_const, 0, lookback)
        if rates_tf is not None:
            candle_map[tf_name] = _as_candle_dicts(rates_tf)

    h1 = analyze_timeframe(symbol, mt5.TIMEFRAME_H1)
    h4 = analyze_timeframe(symbol, mt5.TIMEFRAME_H4)
    mt5.shutdown()

    if "M15" not in candle_map or h1 is None or h4 is None:
        decision = StrategyDecision("wait", "Not enough candle/trend data.")
        _remember(decision, symbol, mid)
        return decision.reason

    context = build_market_context(
        symbol=symbol,
        timestamp=None,
        candles=candle_map,
        h1_direction=h1.get("overall", "NEUTRAL"),
        h4_direction=h4.get("overall", "NEUTRAL"),
        bid=tick.bid,
        ask=tick.ask,
        spread_pips=spread_pips,
    )
    decision = plugin.evaluate(context)
    _remember(decision, symbol, mid)

    if decision.action != "enter":
        if decision.action == "skip":
            record_guard_event(
                "strategy", "", symbol, decision.direction or "",
                decision.reason, "", "trend/retest/risk"
            )
        return decision.reason

    signal_id = "strat_" + uuid.uuid4().hex[:8]
    signal = Signal(
        symbol=symbol,
        direction=decision.direction,
        entry_low=round(decision.entry, 2),
        entry_high=round(decision.entry, 2),
        sl=decision.sl,
        tps=[decision.tp],
        raw_text=f"[STRATEGY] {symbol} {decision.direction.upper()} breakout-retest",
        created_at=time.time(),
    )
    upsert_signal(signal_id, signal, status="pending")
    result = execute_trade(
        signal,
        signal_id=signal_id,
        entry_mode="strategy",
        skip_proximity=True,
        risk_percent=STRATEGY_RISK_PERCENT,
    )
    status = "executed" if "Trade Executed" in result else "blocked"
    upsert_signal(signal_id, signal, status=status)
    return result


async def start_strategy(bot):
    if not STRATEGY_ENABLED:
        log.info("Strategy mode disabled (STRATEGY_ENABLED=false)")
        return

    strategy_name = resolve_strategy_name(ACTIVE_STRATEGY)
    log.info(
        "Strategy mode started: plugin=%s %s %s risk=%.2f%%",
        strategy_name, STRATEGY_SYMBOL, STRATEGY_TIMEFRAME, STRATEGY_RISK_PERCENT * 100,
    )

    while True:
        try:
            result = await asyncio.get_event_loop().run_in_executor(None, scan_once)
            if "Trade Executed" in result and bot is not None:
                await bot.send_message(
                    chat_id=YOUR_CHAT_ID,
                    text=f"*Strategy trade executed*\n\n{result}",
                    parse_mode="Markdown",
                )
        except Exception as exc:
            log.error("Strategy loop error: %s", exc, exc_info=True)
        await asyncio.sleep(STRATEGY_SCAN_INTERVAL)
