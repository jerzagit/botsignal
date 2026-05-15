"""
Low-risk full-auto XAUUSD strategy engine.

Runs beside the Telegram signal flow. It scans for M15 breakout-retest setups
aligned with H1/H4 direction and executes through the existing MT5 pipeline.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass
from typing import Iterable

import MetaTrader5 as mt5

from core.config import (
    ENV_MODE, YOUR_CHAT_ID, MT5_SYMBOL_SUFFIX, SL_PIP_SIZE,
    MAX_SPREAD_PIPS, SESSION_FILTER_ENABLED, SESSION_START_HOUR_UTC,
    SESSION_END_HOUR_UTC, STRATEGY_ENABLED, STRATEGY_SYMBOL,
    STRATEGY_TIMEFRAME, STRATEGY_SCAN_INTERVAL, STRATEGY_RISK_PERCENT,
    STRATEGY_DAILY_DRAWDOWN_PERCENT, STRATEGY_LIVE_UNLOCKED,
    STRATEGY_MIN_RR, STRATEGY_BREAKOUT_LOOKBACK,
    STRATEGY_RETEST_TOLERANCE_PIPS, STRATEGY_CONFIRM_BODY_RATIO,
    STRATEGY_SWING_BUFFER_PIPS, STRATEGY_TP_R_MULTIPLE,
)
from core.db import record_guard_event, upsert_signal
from core.mt5 import execute_trade, mt5_connect
from core.signal import Signal
from core.state import get_daily_loss
from core.trend_analyzer import analyze_timeframe

log = logging.getLogger(__name__)

TIMEFRAME_MAP = {
    "M1": mt5.TIMEFRAME_M1,
    "M5": mt5.TIMEFRAME_M5,
    "M15": mt5.TIMEFRAME_M15,
    "M30": mt5.TIMEFRAME_M30,
    "H1": mt5.TIMEFRAME_H1,
}

LAST_DECISION = {
    "time": None,
    "action": "idle",
    "reason": "Strategy has not scanned yet.",
    "symbol": STRATEGY_SYMBOL,
    "direction": None,
    "price": None,
}


@dataclass(frozen=True)
class StrategyDecision:
    action: str
    reason: str
    direction: str | None = None
    entry: float | None = None
    sl: float | None = None
    tp: float | None = None
    level: float | None = None


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


def _body_ratio(candle: dict) -> float:
    rng = candle["high"] - candle["low"]
    if rng <= 0:
        return 0.0
    return abs(candle["close"] - candle["open"]) / rng


def _session_ok() -> bool:
    if not SESSION_FILTER_ENABLED:
        return True
    hour = time.gmtime().tm_hour
    if SESSION_START_HOUR_UTC <= SESSION_END_HOUR_UTC:
        return SESSION_START_HOUR_UTC <= hour < SESSION_END_HOUR_UTC
    return hour >= SESSION_START_HOUR_UTC or hour < SESSION_END_HOUR_UTC


def _trend_allows(direction: str, h1: str, h4: str) -> bool:
    opposing = "BEAR" if direction == "buy" else "BULL"
    return h1 != opposing and h4 != opposing


def evaluate_breakout_retest(
    candles: list[dict],
    h1_direction: str,
    h4_direction: str,
    bid: float,
    ask: float,
) -> StrategyDecision:
    """Pure strategy decision function, testable without MT5."""
    lookback = STRATEGY_BREAKOUT_LOOKBACK
    if len(candles) < lookback + 3:
        return StrategyDecision("wait", "Not enough M15 candles.")

    closed = candles[:-1]
    breakout = closed[-2]
    confirm = closed[-1]
    prior = closed[-(lookback + 2):-2]

    prev_high = max(c["high"] for c in prior)
    prev_low = min(c["low"] for c in prior)
    tolerance = STRATEGY_RETEST_TOLERANCE_PIPS * SL_PIP_SIZE
    buffer = STRATEGY_SWING_BUFFER_PIPS * SL_PIP_SIZE

    bull_break = (
        breakout["close"] > prev_high
        and _body_ratio(breakout) >= STRATEGY_CONFIRM_BODY_RATIO
    )
    bear_break = (
        breakout["close"] < prev_low
        and _body_ratio(breakout) >= STRATEGY_CONFIRM_BODY_RATIO
    )

    if bull_break:
        retested = confirm["low"] <= prev_high + tolerance
        rejected = confirm["close"] > prev_high and confirm["close"] > confirm["open"]
        if retested and rejected:
            if not _trend_allows("buy", h1_direction, h4_direction):
                return StrategyDecision("skip", f"Trend blocks BUY: H1={h1_direction}, H4={h4_direction}.")
            entry = ask
            sl = round(min(confirm["low"], breakout["low"]) - buffer, 2)
            risk = entry - sl
            tp = round(entry + risk * STRATEGY_TP_R_MULTIPLE, 2)
            rr = (tp - entry) / risk if risk > 0 else 0
            if risk <= 0 or rr < STRATEGY_MIN_RR:
                return StrategyDecision("skip", f"Invalid BUY risk math: RR={rr:.2f}.")
            return StrategyDecision("enter", "Bullish breakout-retest confirmed.", "buy", entry, sl, tp, prev_high)

    if bear_break:
        retested = confirm["high"] >= prev_low - tolerance
        rejected = confirm["close"] < prev_low and confirm["close"] < confirm["open"]
        if retested and rejected:
            if not _trend_allows("sell", h1_direction, h4_direction):
                return StrategyDecision("skip", f"Trend blocks SELL: H1={h1_direction}, H4={h4_direction}.")
            entry = bid
            sl = round(max(confirm["high"], breakout["high"]) + buffer, 2)
            risk = sl - entry
            tp = round(entry - risk * STRATEGY_TP_R_MULTIPLE, 2)
            rr = (entry - tp) / risk if risk > 0 else 0
            if risk <= 0 or rr < STRATEGY_MIN_RR:
                return StrategyDecision("skip", f"Invalid SELL risk math: RR={rr:.2f}.")
            return StrategyDecision("enter", "Bearish breakout-retest confirmed.", "sell", entry, sl, tp, prev_low)

    return StrategyDecision("wait", "No breakout-retest confirmation.")


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
    })


def get_strategy_status() -> dict:
    return {
        "enabled": STRATEGY_ENABLED,
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

    if ENV_MODE == "live" and not STRATEGY_LIVE_UNLOCKED:
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

    tf = TIMEFRAME_MAP.get(STRATEGY_TIMEFRAME, mt5.TIMEFRAME_M15)
    rates = mt5.copy_rates_from_pos(symbol_mt5, tf, 0, STRATEGY_BREAKOUT_LOOKBACK + 5)
    h1 = analyze_timeframe(symbol, mt5.TIMEFRAME_H1)
    h4 = analyze_timeframe(symbol, mt5.TIMEFRAME_H4)
    mt5.shutdown()

    if rates is None or h1 is None or h4 is None:
        decision = StrategyDecision("wait", "Not enough candle/trend data.")
        _remember(decision, symbol, mid)
        return decision.reason

    decision = evaluate_breakout_retest(
        _as_candle_dicts(rates),
        h1.get("overall", "NEUTRAL"),
        h4.get("overall", "NEUTRAL"),
        tick.bid,
        tick.ask,
    )
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

    log.info(
        "Strategy mode started: %s %s risk=%.2f%%",
        STRATEGY_SYMBOL, STRATEGY_TIMEFRAME, STRATEGY_RISK_PERCENT * 100,
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
