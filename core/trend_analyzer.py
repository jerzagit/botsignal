"""
core/trend_analyzer.py
Market direction analyzer — EMA crossover + RSI + candle structure.
Provides /trend command output and auto-alerts on H1/H4 direction changes.
"""

import asyncio
import logging

import MetaTrader5 as mt5

from core.config import (
    YOUR_CHAT_ID, MT5_SYMBOL_SUFFIX, SL_PIP_SIZE,
    TREND_ENABLED, TREND_INTERVAL,
    TREND_EMA_SHORT, TREND_EMA_LONG, TREND_RSI_PERIOD,
    MANUAL_SYMBOL,
)
from core.mt5 import mt5_connect

log = logging.getLogger(__name__)

# Map friendly names to MT5 timeframe constants
TIMEFRAMES = [
    ("M5",  mt5.TIMEFRAME_M5),
    ("M15", mt5.TIMEFRAME_M15),
    ("H1",  mt5.TIMEFRAME_H1),
    ("H4",  mt5.TIMEFRAME_H4),
]

# Last known direction per timeframe — used for change detection
_last_direction: dict[str, str] = {}


# ── Indicators ────────────────────────────────────────────────────────────────

def calculate_ema(closes: list[float], period: int) -> list[float]:
    """Compute Exponential Moving Average from close prices."""
    if len(closes) < period:
        return closes[:]
    k = 2 / (period + 1)
    ema = [sum(closes[:period]) / period]  # SMA seed
    for price in closes[period:]:
        ema.append(price * k + ema[-1] * (1 - k))
    return ema


def calculate_rsi(closes: list[float], period: int = 14) -> float:
    """Compute RSI from close prices. Returns latest RSI value."""
    if len(closes) < period + 1:
        return 50.0  # neutral fallback

    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gains = [d if d > 0 else 0 for d in deltas]
    losses = [-d if d < 0 else 0 for d in deltas]

    # Wilder's smoothed average
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period

    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def detect_structure(highs: list[float], lows: list[float], lookback: int = 3) -> str:
    """
    Detect market structure from recent swing highs/lows.
    Returns 'BULL', 'BEAR', or 'NEUTRAL'.
    """
    if len(highs) < lookback + 1 or len(lows) < lookback + 1:
        return "NEUTRAL"

    recent_highs = highs[-(lookback + 1):]
    recent_lows = lows[-(lookback + 1):]

    higher_highs = sum(1 for i in range(1, len(recent_highs))
                       if recent_highs[i] > recent_highs[i - 1])
    higher_lows = sum(1 for i in range(1, len(recent_lows))
                      if recent_lows[i] > recent_lows[i - 1])
    lower_highs = sum(1 for i in range(1, len(recent_highs))
                      if recent_highs[i] < recent_highs[i - 1])
    lower_lows = sum(1 for i in range(1, len(recent_lows))
                     if recent_lows[i] < recent_lows[i - 1])

    bull_score = higher_highs + higher_lows
    bear_score = lower_highs + lower_lows

    if bull_score > bear_score:
        return "BULL"
    elif bear_score > bull_score:
        return "BEAR"
    return "NEUTRAL"


# ── Per-timeframe analysis ────────────────────────────────────────────────────

def analyze_timeframe(symbol: str, tf_const: int) -> dict | None:
    """
    Fetch candles and run all 3 indicators for one timeframe.
    Returns dict with ema/rsi/structure/overall directions, or None on error.
    MT5 must be connected before calling this.
    """
    symbol_mt5 = symbol + MT5_SYMBOL_SUFFIX
    rates = mt5.copy_rates_from_pos(symbol_mt5, tf_const, 0, 100)
    if rates is None or len(rates) < TREND_EMA_LONG + 5:
        return None

    closes = [r[4] for r in rates]  # close price
    highs = [r[2] for r in rates]   # high
    lows = [r[3] for r in rates]    # low

    # EMA crossover
    ema_short = calculate_ema(closes, TREND_EMA_SHORT)
    ema_long = calculate_ema(closes, TREND_EMA_LONG)
    if ema_short and ema_long:
        ema_dir = "BULL" if ema_short[-1] > ema_long[-1] else "BEAR"
    else:
        ema_dir = "NEUTRAL"

    # RSI
    rsi_val = calculate_rsi(closes, TREND_RSI_PERIOD)
    if rsi_val > 55:
        rsi_dir = "BULL"
    elif rsi_val < 45:
        rsi_dir = "BEAR"
    else:
        rsi_dir = "NEUTRAL"

    # Structure
    struct_dir = detect_structure(highs, lows, lookback=3)

    # Overall — 2/3 agree
    votes = [ema_dir, rsi_dir, struct_dir]
    bull_count = votes.count("BULL")
    bear_count = votes.count("BEAR")
    if bull_count >= 2:
        overall = "BULL"
    elif bear_count >= 2:
        overall = "BEAR"
    else:
        overall = "NEUTRAL"

    return {
        "ema": ema_dir,
        "rsi": rsi_dir,
        "rsi_val": round(rsi_val, 1),
        "structure": struct_dir,
        "overall": overall,
    }


def analyze_all_timeframes(symbol: str = None) -> dict[str, dict] | None:
    """
    Connect to MT5, analyze all 4 timeframes, return results dict.
    Returns None on MT5 connection failure.
    """
    symbol = symbol or MANUAL_SYMBOL
    if not mt5_connect():
        return None

    results = {}
    for tf_name, tf_const in TIMEFRAMES:
        result = analyze_timeframe(symbol, tf_const)
        if result:
            results[tf_name] = result

    mt5.shutdown()
    return results


def format_trend_table(results: dict[str, dict], symbol: str = None) -> str:
    """Format analysis results into a Telegram-friendly table."""
    symbol = symbol or MANUAL_SYMBOL
    lines = [f"*{symbol} Market Direction*\n"]
    lines.append("`TF   | EMA  | RSI  | Struct | Overall`")
    lines.append("`-----|------|------|--------|--------`")

    for tf_name in ["M5", "M15", "H1", "H4"]:
        r = results.get(tf_name)
        if not r:
            lines.append(f"`{tf_name:<5}| ---  | ---  | ---    | ---`")
            continue
        lines.append(
            f"`{tf_name:<5}| {r['ema']:<5}| {r['rsi']:<5}| {r['structure']:<7}| {r['overall']}`"
        )

    # Suggestion based on H1
    h1 = results.get("H1", {})
    h4 = results.get("H4", {})
    suggestions = []
    if h1.get("overall") == "BEAR":
        suggestions.append("H1 bearish - caution on buys")
    elif h1.get("overall") == "BULL":
        suggestions.append("H1 bullish - caution on sells")
    if h4.get("overall") == "BEAR":
        suggestions.append("H4 bearish - bigger picture favors sells")
    elif h4.get("overall") == "BULL":
        suggestions.append("H4 bullish - bigger picture favors buys")

    if suggestions:
        lines.append(f"\n_Suggestion: {'; '.join(suggestions)}_")

    return "\n".join(lines)


# ── Background trend watcher ─────────────────────────────────────────────────

async def start_trend_watcher(bot):
    """Background loop — checks direction every TREND_INTERVAL seconds.
    Alerts on H1 or H4 direction changes only."""
    if not TREND_ENABLED:
        return

    log.info(f"Trend watcher started (interval={TREND_INTERVAL}s)")

    while True:
        try:
            await asyncio.sleep(TREND_INTERVAL)
            results = await asyncio.get_event_loop().run_in_executor(
                None, analyze_all_timeframes, None
            )
            if not results:
                continue

            # Check H1 and H4 for direction changes
            for tf_name in ("H1", "H4"):
                r = results.get(tf_name)
                if not r:
                    continue
                current = r["overall"]
                key = tf_name
                prev = _last_direction.get(key)

                if prev is not None and prev != current:
                    msg = (
                        f"*Direction Change:* {MANUAL_SYMBOL} {tf_name} "
                        f"`{prev}` -> `{current}`"
                    )
                    try:
                        await bot.send_message(
                            chat_id=YOUR_CHAT_ID,
                            text=msg,
                            parse_mode="Markdown",
                        )
                        log.info(f"Trend alert: {MANUAL_SYMBOL} {tf_name} {prev} -> {current}")
                    except Exception as e:
                        log.error(f"Failed to send trend alert: {e}")

                _last_direction[key] = current

        except Exception as e:
            log.error(f"Trend watcher error: {e}")
            await asyncio.sleep(10)
