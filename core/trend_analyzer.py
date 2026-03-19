"""
core/trend_analyzer.py
Market direction analyzer — EMA crossover + RSI + candle structure.
Provides /trend command output and auto-alerts on H1/H4 direction changes.
"""

import asyncio
import logging

import MetaTrader5 as mt5

import time
import uuid

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from core.config import (
    YOUR_CHAT_ID, MT5_SYMBOL_SUFFIX, SL_PIP_SIZE,
    TREND_ENABLED, TREND_INTERVAL,
    TREND_EMA_SHORT, TREND_EMA_LONG, TREND_RSI_PERIOD,
    MANUAL_SYMBOL, FIB_MAX_RETRACEMENT,
    FIB_SCANNER_INTERVAL,
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


def check_trend_alignment(direction: str, symbol: str = None) -> dict:
    """
    Check if direction aligns with H1 + H4 trend.
    Returns: {aligned: bool, h1: str, h4: str, warning: str|None}
    Only fetches H1 + H4 (faster than all 4 TFs).
    """
    symbol = symbol or MANUAL_SYMBOL
    result = {"aligned": True, "h1": "NEUTRAL", "h4": "NEUTRAL", "warning": None}

    if not mt5_connect():
        return result  # fail-open: can't check → assume aligned

    h1 = analyze_timeframe(symbol, mt5.TIMEFRAME_H1)
    h4 = analyze_timeframe(symbol, mt5.TIMEFRAME_H4)
    mt5.shutdown()

    h1_dir = h1["overall"] if h1 else "NEUTRAL"
    h4_dir = h4["overall"] if h4 else "NEUTRAL"
    result["h1"] = h1_dir
    result["h4"] = h4_dir

    # Check opposition: BUY vs BEAR, SELL vs BULL
    cmd_bull = direction.lower() == "buy"
    opposing = []
    if cmd_bull and h1_dir == "BEAR":
        opposing.append("H1: BEAR")
    elif not cmd_bull and h1_dir == "BULL":
        opposing.append("H1: BULL")
    if cmd_bull and h4_dir == "BEAR":
        opposing.append("H4: BEAR")
    elif not cmd_bull and h4_dir == "BULL":
        opposing.append("H4: BULL")

    if opposing:
        result["aligned"] = False
        result["warning"] = " | ".join(opposing) + f" — opposing your {direction.upper()}"

    return result


def check_fib_entry(direction: str, current_price: float, symbol: str = None) -> dict:
    """
    Check if price is in 0-38.2% Fib retracement of last opposite H1 candle.

    For BUY: find last bearish H1 candle, good zone = near the low (0-38.2%)
    For SELL: find last bullish H1 candle, good zone = near the high (0-38.2%)

    Returns: {in_zone, fib_pct, fib_382, candle_high, candle_low, warning}
    """
    symbol = symbol or MANUAL_SYMBOL
    result = {
        "in_zone": True, "fib_pct": 0.0, "fib_382": 0.0,
        "candle_high": 0.0, "candle_low": 0.0, "warning": None,
    }

    if not mt5_connect():
        return result  # fail-open

    symbol_mt5 = symbol + MT5_SYMBOL_SUFFIX
    rates = mt5.copy_rates_from_pos(symbol_mt5, mt5.TIMEFRAME_H1, 0, 20)
    mt5.shutdown()

    if rates is None or len(rates) < 3:
        return result  # not enough data → assume OK

    # Skip current incomplete candle (index -1), scan from -2 backwards
    is_buy = direction.lower() == "buy"
    candle = None
    for i in range(len(rates) - 2, -1, -1):
        r = rates[i]
        o, h, l, c = r[1], r[2], r[3], r[4]  # open, high, low, close
        if is_buy and c < o:    # bearish candle (drop) → good for buy pullback
            candle = {"open": o, "high": h, "low": l, "close": c}
            break
        elif not is_buy and c > o:  # bullish candle (rally) → good for sell pullback
            candle = {"open": o, "high": h, "low": l, "close": c}
            break

    if candle is None:
        return result  # no opposite candle found → skip check

    ch = candle["high"]
    cl = candle["low"]
    rng = ch - cl

    if rng <= 0:
        return result  # doji/no range → skip

    result["candle_high"] = round(ch, 2)
    result["candle_low"] = round(cl, 2)

    if is_buy:
        # BUY: 0% = low (best), 100% = high (worst)
        # Fib 38.2% price = low + range × 0.382
        fib_level = cl + rng * FIB_MAX_RETRACEMENT
        fib_pct = (current_price - cl) / rng  # 0 = at low, 1 = at high
    else:
        # SELL: 0% = high (best), 100% = low (worst)
        # Fib 38.2% price = high - range × 0.382
        fib_level = ch - rng * FIB_MAX_RETRACEMENT
        fib_pct = (ch - current_price) / rng  # 0 = at high, 1 = at low

    result["fib_382"] = round(fib_level, 2)
    result["fib_pct"] = round(max(0, fib_pct) * 100, 1)  # as percentage

    if fib_pct > FIB_MAX_RETRACEMENT:
        result["in_zone"] = False
        result["warning"] = (
            f"Price at {result['fib_pct']:.0f}% retracement "
            f"(above {FIB_MAX_RETRACEMENT*100:.0f}%)"
        )

    return result


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


# ── Background Fib entry scanner ─────────────────────────────────────────────

# Cooldown tracking: {("buy"|"sell", candle_open_time): True}
_fib_alerted: dict[tuple, bool] = {}

# Pending Fib alerts: {alert_id: {"direction": "buy"|"sell", "price": float, ...}}
fib_pending: dict[str, dict] = {}


def _scan_fib_zones(symbol: str = None) -> list[dict]:
    """
    Connect MT5, check both BUY and SELL Fib zones.
    Returns list of zone dicts that are in-zone (0 to 2 results).
    Each dict: {direction, price, fib_pct, fib_382, candle_high, candle_low, candle_time}
    """
    symbol = symbol or MANUAL_SYMBOL
    if not mt5_connect():
        return []

    symbol_mt5 = symbol + MT5_SYMBOL_SUFFIX

    # Get current price
    tick = mt5.symbol_info_tick(symbol_mt5)
    if tick is None:
        mt5.shutdown()
        return []

    price = (tick.bid + tick.ask) / 2

    # Get H1 candles
    rates = mt5.copy_rates_from_pos(symbol_mt5, mt5.TIMEFRAME_H1, 0, 20)
    mt5.shutdown()

    if rates is None or len(rates) < 3:
        return []

    zones = []
    for direction in ("buy", "sell"):
        is_buy = direction == "buy"
        candle = None
        candle_time = None
        for i in range(len(rates) - 2, -1, -1):
            r = rates[i]
            o, h, l, c = r[1], r[2], r[3], r[4]
            if is_buy and c < o:     # bearish candle → buy zone
                candle = {"high": h, "low": l}
                candle_time = int(r[0])
                break
            elif not is_buy and c > o:  # bullish candle → sell zone
                candle = {"high": h, "low": l}
                candle_time = int(r[0])
                break

        if candle is None:
            continue

        ch, cl = candle["high"], candle["low"]
        rng = ch - cl
        if rng <= 0:
            continue

        if is_buy:
            fib_level = cl + rng * FIB_MAX_RETRACEMENT
            fib_pct = (price - cl) / rng
        else:
            fib_level = ch - rng * FIB_MAX_RETRACEMENT
            fib_pct = (ch - price) / rng

        if fib_pct < 0:
            continue  # price beyond the candle range (wrong side)

        if fib_pct <= FIB_MAX_RETRACEMENT:
            zones.append({
                "direction": direction,
                "price": round(price, 2),
                "fib_pct": round(fib_pct * 100, 1),
                "fib_382": round(fib_level, 2),
                "candle_high": round(ch, 2),
                "candle_low": round(cl, 2),
                "candle_time": candle_time,
            })

    return zones


async def start_fib_scanner(bot):
    """Background loop — scans for Fib entry zones every FIB_SCANNER_INTERVAL seconds.
    Alerts once per direction per H1 candle (cooldown by candle open time)."""
    log.info(f"Fib entry scanner started (interval={FIB_SCANNER_INTERVAL}s)")

    while True:
        try:
            await asyncio.sleep(FIB_SCANNER_INTERVAL)

            zones = await asyncio.get_event_loop().run_in_executor(
                None, _scan_fib_zones, None
            )
            if not zones:
                continue

            for zone in zones:
                direction = zone["direction"]
                candle_time = zone["candle_time"]
                cooldown_key = (direction, candle_time)

                if cooldown_key in _fib_alerted:
                    continue  # already alerted for this candle

                # Mark as alerted
                _fib_alerted[cooldown_key] = True

                # Get trend context
                trend = await asyncio.get_event_loop().run_in_executor(
                    None, check_trend_alignment, direction, MANUAL_SYMBOL
                )

                # Build trend line
                opp_dir = "BEAR" if direction == "buy" else "BULL"
                h1_mark = "\u2705" if trend["h1"] != opp_dir else "\u26a0\ufe0f"
                h4_mark = "\u2705" if trend["h4"] != opp_dir else "\u26a0\ufe0f"
                trend_line = f"H1: `{trend['h1']}` {h1_mark} | H4: `{trend['h4']}` {h4_mark}"
                if not trend["aligned"]:
                    trend_line += f" \u2014 opposing {direction.upper()}"

                # Candle description
                if direction == "buy":
                    candle_desc = f"Last bearish H1: `{zone['candle_high']}`\u2192`{zone['candle_low']}`"
                else:
                    candle_desc = f"Last bullish H1: `{zone['candle_low']}`\u2192`{zone['candle_high']}`"

                dir_emoji = "\U0001f7e2 BUY" if direction == "buy" else "\U0001f534 SELL"
                alert_id = uuid.uuid4().hex[:8]

                # Store pending alert data for callback
                fib_pending[alert_id] = {
                    "direction": direction,
                    "price": zone["price"],
                    "created_at": time.time(),
                }

                # Build message
                msg = (
                    f"\U0001f4ca *Fib Entry Alert* \u2014 {dir_emoji} zone\n\n"
                    f"`{MANUAL_SYMBOL}` @ `{zone['price']}`\n"
                    f"Fib: `{zone['fib_pct']:.0f}%` \u2705 (0\u201338.2% zone)\n"
                    f"{candle_desc} | 38.2% = `{zone['fib_382']}`\n\n"
                    f"Trend: {trend_line}"
                )

                btn_label = "\U0001f7e2 BUY NOW" if direction == "buy" else "\U0001f534 SELL NOW"
                action_key = f"fibalert_{direction}_{alert_id}"
                dismiss_key = f"fibalert_dismiss_{alert_id}"

                keyboard = InlineKeyboardMarkup([[
                    InlineKeyboardButton(btn_label, callback_data=action_key),
                    InlineKeyboardButton("\u274c DISMISS", callback_data=dismiss_key),
                ]])

                try:
                    await bot.send_message(
                        chat_id=YOUR_CHAT_ID,
                        text=msg,
                        parse_mode="Markdown",
                        reply_markup=keyboard,
                    )
                    log.info(
                        f"Fib alert: {MANUAL_SYMBOL} {direction.upper()} zone "
                        f"@ {zone['price']} (fib {zone['fib_pct']:.0f}%) [{alert_id}]"
                    )
                except Exception as e:
                    log.error(f"Failed to send Fib alert: {e}")

            # Clean old cooldown entries (> 24h)
            cutoff = time.time() - 86400
            stale = [k for k in _fib_alerted if k[1] < cutoff]
            for k in stale:
                _fib_alerted.pop(k, None)

            # Clean old pending alerts (> 30min)
            stale_alerts = [k for k, v in fib_pending.items()
                           if time.time() - v["created_at"] > 1800]
            for k in stale_alerts:
                fib_pending.pop(k, None)

        except Exception as e:
            log.error(f"Fib scanner error: {e}")
            await asyncio.sleep(10)
