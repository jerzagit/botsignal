"""
breakout_retest_v1 — mechanical extraction of evaluate_breakout_retest.

DO NOT change lookback / confirmation / retest / swing / RR / entry / SL / TP
/ trend rules. Algorithm must match the pre-plugin implementation exactly.
"""

from __future__ import annotations

from core.config import (
    SL_PIP_SIZE,
    STRATEGY_BREAKOUT_LOOKBACK,
    STRATEGY_CONFIRM_BODY_RATIO,
    STRATEGY_MIN_RR,
    STRATEGY_RETEST_TOLERANCE_PIPS,
    STRATEGY_SWING_BUFFER_PIPS,
    STRATEGY_TP_R_MULTIPLE,
)
from core.strategies.base import MarketContext, StrategyDecision, StrategyInfo

STRATEGY_NAME = "breakout_retest_v1"
REQUIRED_TIMEFRAMES = ("M15", "H1", "H4")


def _body_ratio(candle: dict) -> float:
    rng = candle["high"] - candle["low"]
    if rng <= 0:
        return 0.0
    return abs(candle["close"] - candle["open"]) / rng


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


class BreakoutRetestV1:
    """Plugin wrapper — same algorithm as evaluate_breakout_retest."""

    name = STRATEGY_NAME
    required_timeframes = REQUIRED_TIMEFRAMES

    def info(self) -> StrategyInfo:
        return StrategyInfo(
            name=STRATEGY_NAME,
            display_name="Breakout Retest V1",
            description="M15 breakout-retest aligned with H1/H4 trend (production baseline).",
            version="1",
            required_timeframes=REQUIRED_TIMEFRAMES,
            status="stable",
        )

    def evaluate(self, context: MarketContext) -> StrategyDecision:
        return evaluate_breakout_retest(
            context.m15_candles,
            context.h1_direction,
            context.h4_direction,
            context.bid,
            context.ask,
        )
