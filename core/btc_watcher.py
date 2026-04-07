"""
core/btc_watcher.py
BTC/USD weekend breakout + Fibonacci retrace bot.

Strategy:
  1. Every BTC_INTERVAL_SECS, fetch last 10 H1 candles
  2. Detect if current price breaks the previous H1 candle's high or low
     → Bullish breakout: ask > prev_H1_high
     → Bearish breakout: bid < prev_H1_low
  3. After breakout, wait for price to RETRACE into the 0–25% Fibonacci zone
     → Bullish retrace zone: [prev_H1_high - 0.25×range, prev_H1_high]
     → Bearish retrace zone: [prev_H1_low,  prev_H1_low  + 0.25×range]
  4. Room check: TP distance must be ≥ average H1 candle range (enough room to float)
  5. Margin check: account margin level must be ≥ MIN_MARGIN_LEVEL
  6. Place 1 single trade, monitor floating P&L every BTC_PROFIT_CHECK_SECS
  7. Close automatically when profit ≥ BTC_PROFIT_TARGET_USD (default $10)
  8. Cooldown BTC_COOLDOWN_MINS after each trade
"""

import asyncio
import functools
import logging
import os
import time
import uuid
from datetime import datetime

import MetaTrader5 as mt5

from core.config import MT5_SYMBOL_SUFFIX, YOUR_CHAT_ID, MIN_MARGIN_LEVEL
from core.mt5 import mt5_connect, execute_trade, close_position
from core.risk import calculate_lot
from core.signal import Signal
from core.db import upsert_signal, upsert_candles

log = logging.getLogger(__name__)

# ── BTC-specific config (from .env) ───────────────────────────────────────────
BTC_SYMBOL            = os.getenv("BTC_SYMBOL",            "BTCUSD")
BTC_SYMBOL_SUFFIX     = os.getenv("BTC_SYMBOL_SUFFIX",     MT5_SYMBOL_SUFFIX)
BTC_PIP_SIZE          = float(os.getenv("BTC_PIP_SIZE",          "1.0"))
BTC_SL_BUFFER_PIPS    = float(os.getenv("BTC_SL_BUFFER_PIPS",   "200"))
BTC_RR_RATIO          = float(os.getenv("BTC_RR_RATIO",          "1.5"))
BTC_RISK_PERCENT      = float(os.getenv("BTC_RISK_PERCENT",      "0.01"))
BTC_FIB_MAX           = float(os.getenv("BTC_FIB_MAX",           "0.25"))
BTC_INTERVAL_SECS     = int(os.getenv("BTC_INTERVAL_SECS",       "60"))
BTC_COOLDOWN_MINS     = int(os.getenv("BTC_COOLDOWN_MINS",        "60"))   # 1h cooldown (1 per H1 candle)
BTC_WEEKEND_ONLY      = os.getenv("BTC_WEEKEND_ONLY", "true").lower() == "true"
BTC_PROFIT_TARGET_USD = float(os.getenv("BTC_PROFIT_TARGET_USD", "10.0"))
BTC_PROFIT_CHECK_SECS = int(os.getenv("BTC_PROFIT_CHECK_SECS",   "10"))


# ── Breakout state ─────────────────────────────────────────────────────────────
class _BreakoutState:
    """Tracks an active breakout waiting for Fibonacci retrace entry."""
    def __init__(self, direction: str, h4_high: float, h4_low: float, candle_time: int):
        self.direction   = direction
        self.h4_high     = h4_high
        self.h4_low      = h4_low
        self.h4_range    = h4_high - h4_low
        self.candle_time = candle_time

        if direction == "buy":
            self.zone_high = h4_high
            self.zone_low  = h4_high - self.h4_range * BTC_FIB_MAX
        else:
            self.zone_low  = h4_low
            self.zone_high = h4_low + self.h4_range * BTC_FIB_MAX

    def in_retrace_zone(self, ask: float, bid: float) -> bool:
        price = ask if self.direction == "buy" else bid
        return self.zone_low <= price <= self.zone_high

    def entry_price(self, ask: float, bid: float) -> float:
        return ask if self.direction == "buy" else bid

    def sl_price(self) -> float:
        buf = BTC_SL_BUFFER_PIPS * BTC_PIP_SIZE
        return round(self.h4_low - buf, 2) if self.direction == "buy" else round(self.h4_high + buf, 2)

    def tp_price(self, entry: float) -> float:
        sl_dist = abs(entry - self.sl_price())
        return (
            round(entry + sl_dist * BTC_RR_RATIO, 2)
            if self.direction == "buy"
            else round(entry - sl_dist * BTC_RR_RATIO, 2)
        )

    def summary(self) -> str:
        return (
            f"{self.direction.upper()} | H1 {self.h4_low:.2f}–{self.h4_high:.2f} | "
            f"retrace zone {self.zone_low:.2f}–{self.zone_high:.2f}"
        )


# ── MT5 helpers ────────────────────────────────────────────────────────────────

def _ensure_symbol(symbol_mt5: str) -> bool:
    """Make sure the symbol is visible in MT5 Market Watch. Returns True on success."""
    info = mt5.symbol_info(symbol_mt5)
    if info is None:
        log.error(f"BTC: symbol '{symbol_mt5}' not found in MT5 — check Market Watch")
        return False
    if not info.visible:
        if not mt5.symbol_select(symbol_mt5, True):
            log.error(f"BTC: could not enable '{symbol_mt5}' in Market Watch: {mt5.last_error()}")
            return False
        log.info(f"BTC: enabled '{symbol_mt5}' in Market Watch")
    return True


def _fetch_h4_candles(count: int = 10) -> list | None:
    """Fetch the last `count` completed H1 candles. Returns list of dicts or None."""
    symbol_mt5 = BTC_SYMBOL + BTC_SYMBOL_SUFFIX
    if not mt5_connect():
        log.error("BTC: MT5 connect failed (candle fetch)")
        return None
    try:
        if not _ensure_symbol(symbol_mt5):
            mt5.shutdown()
            return None
        rates = mt5.copy_rates_from_pos(symbol_mt5, mt5.TIMEFRAME_H1, 0, count + 1)
        mt5.shutdown()
        if rates is None or len(rates) < count + 1:
            log.warning(f"BTC: insufficient H1 data for {symbol_mt5} — got {len(rates) if rates is not None else 0}, need {count + 1}")
            return None
        return [
            {"time": int(r[0]), "high": float(r[2]), "low": float(r[3])}
            for r in rates[:-1]   # exclude current incomplete candle
        ]
    except Exception as e:
        log.error(f"BTC: H1 candle fetch error: {e}")
        mt5.shutdown()
        return None


def _fetch_price() -> tuple[float, float] | tuple[None, None]:
    """Fetch current BTC ask/bid. Returns (ask, bid) or (None, None)."""
    symbol_mt5 = BTC_SYMBOL + BTC_SYMBOL_SUFFIX
    if not mt5_connect():
        return None, None
    try:
        if not _ensure_symbol(symbol_mt5):
            mt5.shutdown()
            return None, None
        tick = mt5.symbol_info_tick(symbol_mt5)
        mt5.shutdown()
        if tick is None:
            log.warning(f"BTC: no tick for {symbol_mt5} — {mt5.last_error()}")
            return None, None
        return float(tick.ask), float(tick.bid)
    except Exception as e:
        log.error(f"BTC: price fetch error: {e}")
        mt5.shutdown()
        return None, None


def _avg_h4_range(candles: list) -> float:
    """Calculate average H1 candle range (high − low) across all candles."""
    if not candles:
        return 0.0
    return sum(c["high"] - c["low"] for c in candles) / len(candles)


def _check_margin() -> tuple[bool, str]:
    """
    Check account margin level. Returns (ok, message).
    ok=True  → safe to trade.
    ok=False → margin too low, block the trade.
    """
    if not mt5_connect():
        return False, "❌ BTC: Could not connect to MT5 for margin check."
    try:
        account = mt5.account_info()
        mt5.shutdown()
        if account is None:
            return False, "❌ BTC: Could not retrieve account info."

        free_margin  = account.free_margin
        equity       = account.equity
        margin_level = account.margin_level if account.margin > 0 else 9999.0

        if margin_level < MIN_MARGIN_LEVEL:
            return False, (
                f"❌ *BTC trade blocked — margin too low*\n"
                f"Margin level: `{margin_level:.1f}%` | Required: `≥ {MIN_MARGIN_LEVEL:.0f}%`\n"
                f"Free margin: `${free_margin:,.2f}` | Equity: `${equity:,.2f}`\n"
                f"_Close some open positions to free up margin._"
            )
        return True, (
            f"Margin level: {margin_level:.1f}% | "
            f"Free margin: ${free_margin:,.2f} | Equity: ${equity:,.2f}"
        )
    except Exception as e:
        mt5.shutdown()
        return False, f"❌ BTC: margin check error: {e}"


def _get_btc_tickets() -> list[int]:
    """Return ticket IDs for all currently open BTCUSD positions."""
    symbol_mt5 = BTC_SYMBOL + BTC_SYMBOL_SUFFIX
    if not mt5_connect():
        return []
    try:
        positions = mt5.positions_get(symbol=symbol_mt5)
        mt5.shutdown()
        if positions is None:
            return []
        return [int(p.ticket) for p in positions]
    except Exception as e:
        log.error(f"BTC: get positions error: {e}")
        mt5.shutdown()
        return []


def _get_total_profit(tickets: list[int]) -> float:
    """Get total floating P&L (USD) for the given ticket IDs."""
    if not tickets:
        return 0.0
    if not mt5_connect():
        return 0.0
    try:
        positions = mt5.positions_get()
        mt5.shutdown()
        if positions is None:
            return 0.0
        return sum(float(p.profit) for p in positions if int(p.ticket) in tickets)
    except Exception as e:
        log.error(f"BTC: profit check error: {e}")
        mt5.shutdown()
        return 0.0


def _build_signal(state: _BreakoutState, entry: float) -> Signal:
    sl = state.sl_price()
    tp = state.tp_price(entry)
    return Signal(
        symbol=BTC_SYMBOL,
        direction=state.direction,
        entry_low=entry,
        entry_high=entry,
        sl=sl,
        tps=[tp],
        raw_text=(
            f"[BTC RETRACE] {state.direction.upper()} @ {entry:.2f} | "
            f"H1 {state.h4_low:.2f}–{state.h4_high:.2f} | "
            f"Fib zone {state.zone_low:.2f}–{state.zone_high:.2f} | "
            f"SL {sl:.2f} TP {tp:.2f}"
        ),
    )


# ── Profit monitor ─────────────────────────────────────────────────────────────

async def _monitor_and_close(tickets: list[int], target_usd: float, entry_info: str, bot):
    """
    Background task: polls floating P&L every BTC_PROFIT_CHECK_SECS.
    Closes all tickets the moment profit >= target_usd.
    Also detects if the position was stopped out externally.
    """
    loop = asyncio.get_event_loop()
    log.info(f"BTC: profit monitor started — {len(tickets)} ticket(s), target=${target_usd:.0f}")

    while True:
        await asyncio.sleep(BTC_PROFIT_CHECK_SECS)
        try:
            profit = await loop.run_in_executor(None, _get_total_profit, tickets)
            log.debug(f"BTC: floating P&L = ${profit:.2f} / target ${target_usd:.0f}")

            # ── Target hit — close all tickets ───────────────────────────────
            if profit >= target_usd:
                close_results = []
                for ticket in tickets:
                    result = await loop.run_in_executor(
                        None, functools.partial(close_position, ticket)
                    )
                    close_results.append(result)

                msg = (
                    f"💰 *BTC Trade Closed — ${profit:.2f} profit*\n"
                    f"{entry_info}\n"
                    f"Target: `${target_usd:.0f}` | Actual: `${profit:.2f}`\n"
                    + "\n".join(close_results)
                )
                await bot.send_message(YOUR_CHAT_ID, msg, parse_mode="Markdown")
                log.info(f"BTC: closed at ${profit:.2f} (target ${target_usd:.0f})")
                break

            # ── Check if position already closed (SL hit / manual close) ─────
            open_tickets = await loop.run_in_executor(None, _get_btc_tickets)
            still_open = [t for t in tickets if t in open_tickets]
            if not still_open:
                await bot.send_message(
                    YOUR_CHAT_ID,
                    f"⚠️ *BTC position closed externally* (SL hit or manual)\n"
                    f"Final P&L: `${profit:.2f}`",
                    parse_mode="Markdown",
                )
                log.info(f"BTC: position closed externally — P&L ${profit:.2f}")
                break

        except Exception as e:
            log.error(f"BTC: profit monitor error: {e}", exc_info=True)


# ── Candle saver ──────────────────────────────────────────────────────────────

def _fetch_candles_for_save(symbol_mt5: str, tf_const: int, count: int = 200) -> list | None:
    """Fetch completed candles (all fields) for DB storage. Returns list of dicts or None."""
    if not mt5_connect():
        return None
    try:
        if not _ensure_symbol(symbol_mt5):
            mt5.shutdown()
            return None
        rates = mt5.copy_rates_from_pos(symbol_mt5, tf_const, 0, count + 1)
        mt5.shutdown()
        if rates is None or len(rates) < 2:
            return None
        return [
            {
                "time":   int(r[0]),
                "open":   float(r[1]),
                "high":   float(r[2]),
                "low":    float(r[3]),
                "close":  float(r[4]),
                "volume": int(r[5]),
            }
            for r in rates[:-1]   # exclude current incomplete candle
        ]
    except Exception as e:
        log.error(f"Candle saver fetch error ({symbol_mt5}): {e}")
        mt5.shutdown()
        return None


async def start_candle_saver():
    """
    Background task: saves H1 and D1 candles for BTCUSD and XAUUSD every hour.
    Stores last 200 H1 candles + last 365 D1 candles per symbol.
    """
    import MetaTrader5 as _mt5

    # Symbol + suffix mappings: (mt5_symbol, plain_symbol, timeframe_const, tf_label, candle_count)
    JOBS = [
        (BTC_SYMBOL + BTC_SYMBOL_SUFFIX, BTC_SYMBOL, _mt5.TIMEFRAME_H1,  "H1",  200),
        (BTC_SYMBOL + BTC_SYMBOL_SUFFIX, BTC_SYMBOL, _mt5.TIMEFRAME_D1,  "D1",  365),
        ("XAUUSD" + MT5_SYMBOL_SUFFIX,   "XAUUSD",   _mt5.TIMEFRAME_H1,  "H1",  200),
        ("XAUUSD" + MT5_SYMBOL_SUFFIX,   "XAUUSD",   _mt5.TIMEFRAME_D1,  "D1",  365),
    ]

    log.info("Candle saver started — saving H1 + D1 for BTCUSD and XAUUSD every hour")

    while True:
        loop = asyncio.get_event_loop()
        for symbol_mt5, symbol, tf_const, tf_label, count in JOBS:
            try:
                candles = await loop.run_in_executor(
                    None, _fetch_candles_for_save, symbol_mt5, tf_const, count
                )
                if candles:
                    saved = await loop.run_in_executor(
                        None, upsert_candles, symbol, tf_label, candles
                    )
                    log.info(f"Candle saver: {symbol} {tf_label} — {saved} rows saved")
                else:
                    log.warning(f"Candle saver: no data for {symbol_mt5} {tf_label}")
            except Exception as e:
                log.error(f"Candle saver error ({symbol} {tf_label}): {e}")

        await asyncio.sleep(3600)   # run every hour


# ── Manual / forced entry ─────────────────────────────────────────────────────

async def force_entry(direction: str, bot, skip_guards: bool = False):
    """
    Force an immediate BTC trade in the given direction.
    Uses current H1 candles for SL/TP — bypasses breakout/retrace detection.
    skip_guards=True skips room check, margin check, and lot=0 block.
    Called by the /btcbuy command.
    """
    loop = asyncio.get_event_loop()
    direction = direction.lower()

    await bot.send_message(
        YOUR_CHAT_ID,
        f"⚡ *BTC manual {direction.upper()} entry requested*"
        + (" _(guards skipped)_" if skip_guards else " — checking conditions..."),
        parse_mode="Markdown",
    )

    # Fetch candles + price
    candles = await loop.run_in_executor(None, _fetch_h4_candles, 10)
    if not candles:
        await bot.send_message(
            YOUR_CHAT_ID,
            f"❌ *BTC: Could not fetch H1 candles*\n"
            f"Symbol: `{BTC_SYMBOL + BTC_SYMBOL_SUFFIX}`\n"
            f"Fix: Right-click `{BTC_SYMBOL}` in MT5 Market Watch → Charts → load history, then retry.",
            parse_mode="Markdown",
        )
        return

    ask, bid = await loop.run_in_executor(None, _fetch_price)
    if ask is None:
        await bot.send_message(
            YOUR_CHAT_ID,
            f"❌ *BTC: Could not fetch price*\n"
            f"Symbol: `{BTC_SYMBOL + BTC_SYMBOL_SUFFIX}`\n"
            f"Fix: Make sure `{BTC_SYMBOL}` is visible in MT5 Market Watch.",
            parse_mode="Markdown",
        )
        return

    avg_range = _avg_h4_range(candles)
    prev      = candles[-1]

    state  = _BreakoutState(direction, prev["high"], prev["low"], prev["time"])
    entry  = ask if direction == "buy" else bid
    signal = _build_signal(state, entry)
    tp_dist = abs(signal.tps[0] - entry)

    if not skip_guards:
        # Room check
        if tp_dist < avg_range:
            await bot.send_message(
                YOUR_CHAT_ID,
                f"⚠️ *BTC manual entry skipped — not enough room*\n"
                f"TP distance: `${tp_dist:.2f}` | Avg H1 range: `${avg_range:.2f}`",
                parse_mode="Markdown",
            )
            return

        # Margin check
        margin_ok, margin_msg = await loop.run_in_executor(None, _check_margin)
        if not margin_ok:
            await bot.send_message(YOUR_CHAT_ID, margin_msg, parse_mode="Markdown")
            return

    # Lot sizing
    signal_id = "btc_" + uuid.uuid4().hex[:8]
    lot, _ = await loop.run_in_executor(
        None, functools.partial(calculate_lot, signal, risk_override=BTC_RISK_PERCENT)
    )

    if lot <= 0:
        if skip_guards:
            lot = 0.01  # fallback to minimum lot when guards are skipped
            log.warning("BTC: lot=0 with guards skipped — using MIN_LOT=0.01")
        else:
            await bot.send_message(YOUR_CHAT_ID, "⚠️ BTC manual entry: lot=0 — margin too thin.")
            return

    # Capture tickets before trade
    tickets_before = await loop.run_in_executor(None, _get_btc_tickets)

    # Execute — skip all internal guards too when skip_guards=True
    upsert_signal(signal_id, signal, status="pending")
    trade_fn = functools.partial(
        execute_trade, signal, signal_id,
        lot,         # lot_override
        None,        # own_tickets
        None,        # tp_override
        True,        # skip_proximity
        "breakout",  # entry_mode
        None,        # layer_num
        skip_guards, # skip_rr_check
    )
    result = await loop.run_in_executor(None, trade_fn)
    upsert_signal(signal_id, signal, status="executed")

    # Find new tickets
    tickets_after = await loop.run_in_executor(None, _get_btc_tickets)
    new_tickets   = [t for t in tickets_after if t not in tickets_before]

    entry_info = (
        f"Entry: `{entry:.2f}` | H1 `{prev['low']:.2f}–{prev['high']:.2f}` | "
        f"Manual {direction.upper()}"
    )

    await bot.send_message(
        YOUR_CHAT_ID,
        f"✅ *BTC Manual {direction.upper()} Entered*\n"
        f"Entry: `{entry:.2f}` | SL: `{signal.sl:.2f}` | TP: `{signal.tps[0]:.2f}`\n"
        f"Lot: `{lot}` | Avg H1: `${avg_range:.2f}` | Profit target: `${BTC_PROFIT_TARGET_USD:.0f}`\n"
        f"```\n{result}\n```",
        parse_mode="Markdown",
    )

    if new_tickets:
        asyncio.create_task(
            _monitor_and_close(new_tickets, BTC_PROFIT_TARGET_USD, entry_info, bot)
        )
    else:
        log.warning("BTC: /btcbuy — no new tickets found after trade")


# ── Main watcher loop ──────────────────────────────────────────────────────────

async def start_btc_watcher(bot):
    """
    BTC H1 breakout + Fibonacci retrace watcher.
    Runs forever until the process is stopped.
    """
    breakout: _BreakoutState | None = None
    last_fire_time = 0.0
    seen_candle_time: int = 0

    symbol_display = BTC_SYMBOL + BTC_SYMBOL_SUFFIX
    log.info(
        f"BTC watcher started — {symbol_display} | "
        f"H1 breakout + 0–{int(BTC_FIB_MAX*100)}% Fib retrace | "
        f"profit target ${BTC_PROFIT_TARGET_USD:.0f} | "
        f"weekend_only={BTC_WEEKEND_ONLY}"
    )

    while True:
        try:
            # ── Weekend gate ──────────────────────────────────────────────────
            if BTC_WEEKEND_ONLY and datetime.now().weekday() < 5:
                await asyncio.sleep(BTC_INTERVAL_SECS)
                continue

            # ── Cooldown gate ─────────────────────────────────────────────────
            if time.time() - last_fire_time < BTC_COOLDOWN_MINS * 60:
                await asyncio.sleep(BTC_INTERVAL_SECS)
                continue

            loop = asyncio.get_event_loop()

            # ── Fetch H1 candles (10 for avg range) + current price ───────────
            candles = await loop.run_in_executor(None, _fetch_h4_candles, 10)
            ask, bid = await loop.run_in_executor(None, _fetch_price)

            if not candles:
                log.warning(f"BTC: H1 candles unavailable for {BTC_SYMBOL + BTC_SYMBOL_SUFFIX}")
                await asyncio.sleep(BTC_INTERVAL_SECS)
                continue
            if ask is None:
                log.warning(f"BTC: price unavailable for {BTC_SYMBOL + BTC_SYMBOL_SUFFIX}")
                await asyncio.sleep(BTC_INTERVAL_SECS)
                continue

            avg_range = _avg_h4_range(candles)
            prev      = candles[-1]   # most recent completed H1 candle
            prev_high = prev["high"]
            prev_low  = prev["low"]
            prev_time = prev["time"]

            # ── Phase 1: Detect H1 breakout ───────────────────────────────────
            if breakout is None and prev_time != seen_candle_time:
                direction = None
                if ask > prev_high:
                    direction = "buy"
                elif bid < prev_low:
                    direction = "sell"

                if direction:
                    breakout = _BreakoutState(direction, prev_high, prev_low, prev_time)
                    seen_candle_time = prev_time
                    log.info(f"BTC H1 breakout: {breakout.summary()}")

                    await bot.send_message(
                        YOUR_CHAT_ID,
                        f"📊 *BTC H1 Breakout Detected*\n"
                        f"Direction: *{direction.upper()}*\n"
                        f"H1 range: `{prev_low:.2f} – {prev_high:.2f}`\n"
                        f"Avg H1 range: `${avg_range:.2f}`\n"
                        f"Waiting for retrace → `{breakout.zone_low:.2f} – {breakout.zone_high:.2f}` "
                        f"(0–{int(BTC_FIB_MAX*100)}% Fib)",
                        parse_mode="Markdown",
                    )

            # ── Phase 2: Monitor retrace + entry ──────────────────────────────
            if breakout is not None:

                # Invalidation: price moved through the opposite side of H1 range
                if breakout.direction == "buy" and bid < breakout.h4_low:
                    log.info("BTC: bullish breakout invalidated — resetting")
                    await bot.send_message(YOUR_CHAT_ID, "⚠️ BTC breakout invalidated — price below H1 low. Resetting.")
                    breakout = None

                elif breakout.direction == "sell" and ask > breakout.h4_high:
                    log.info("BTC: bearish breakout invalidated — resetting")
                    await bot.send_message(YOUR_CHAT_ID, "⚠️ BTC breakout invalidated — price above H1 high. Resetting.")
                    breakout = None

                elif breakout.in_retrace_zone(ask, bid):
                    entry  = breakout.entry_price(ask, bid)
                    signal = _build_signal(breakout, entry)
                    tp     = signal.tps[0]
                    tp_dist = abs(tp - entry)

                    # ── Room check: TP must be ≥ avg H1 range ────────────────
                    if tp_dist < avg_range:
                        msg = (
                            f"⚠️ *BTC entry skipped — not enough room*\n"
                            f"TP distance: `${tp_dist:.2f}` | "
                            f"Avg H1 range needed: `${avg_range:.2f}`\n"
                            f"_Trade would be too tight to float properly._"
                        )
                        log.warning(f"BTC: room check failed — TP dist ${tp_dist:.2f} < avg H1 ${avg_range:.2f}")
                        await bot.send_message(YOUR_CHAT_ID, msg, parse_mode="Markdown")
                        breakout = None
                        await asyncio.sleep(BTC_INTERVAL_SECS)
                        continue

                    # ── Margin check ──────────────────────────────────────────
                    margin_ok, margin_msg = await loop.run_in_executor(None, _check_margin)
                    if not margin_ok:
                        log.warning(f"BTC: margin check failed")
                        await bot.send_message(YOUR_CHAT_ID, margin_msg, parse_mode="Markdown")
                        breakout = None
                        await asyncio.sleep(BTC_INTERVAL_SECS)
                        continue

                    log.info(f"BTC: margin OK — {margin_msg}")

                    # ── Lot sizing ────────────────────────────────────────────
                    signal_id = "btc_" + uuid.uuid4().hex[:8]
                    lot, _ = await loop.run_in_executor(
                        None,
                        functools.partial(calculate_lot, signal, risk_override=BTC_RISK_PERCENT)
                    )

                    if lot <= 0:
                        msg = (
                            f"⚠️ BTC retrace zone reached ({breakout.direction.upper()} @ {entry:.2f}) "
                            f"but lot=0 — margin too thin."
                        )
                        log.warning(msg)
                        await bot.send_message(YOUR_CHAT_ID, msg)
                        breakout = None
                        await asyncio.sleep(BTC_INTERVAL_SECS)
                        continue

                    # ── Capture tickets before trade ──────────────────────────
                    tickets_before = await loop.run_in_executor(None, _get_btc_tickets)

                    # ── Execute trade ─────────────────────────────────────────
                    upsert_signal(signal_id, signal, status="pending")
                    trade_fn = functools.partial(
                        execute_trade,
                        signal, signal_id,
                        lot,        # lot_override
                        None,       # own_tickets
                        None,       # tp_override
                        True,       # skip_proximity
                        "breakout", # entry_mode
                    )
                    result = await loop.run_in_executor(None, trade_fn)
                    upsert_signal(signal_id, signal, status="executed")

                    # ── Find new tickets ──────────────────────────────────────
                    tickets_after = await loop.run_in_executor(None, _get_btc_tickets)
                    new_tickets   = [t for t in tickets_after if t not in tickets_before]

                    entry_info = (
                        f"Entry: `{entry:.2f}` | H1 `{breakout.h4_low:.2f}–{breakout.h4_high:.2f}` | "
                        f"Fib zone `{breakout.zone_low:.2f}–{breakout.zone_high:.2f}`"
                    )

                    msg = (
                        f"✅ *BTC Trade Entered — {breakout.direction.upper()}*\n"
                        f"{entry_info}\n"
                        f"SL: `{signal.sl:.2f}` | TP: `{signal.tps[0]:.2f}`\n"
                        f"Lot: `{lot}` | Avg H1 range: `${avg_range:.2f}`\n"
                        f"Profit target: `${BTC_PROFIT_TARGET_USD:.0f}`\n"
                        f"```\n{result}\n```"
                    )
                    await bot.send_message(YOUR_CHAT_ID, msg, parse_mode="Markdown")

                    # ── Launch profit monitor as background task ──────────────
                    if new_tickets:
                        asyncio.create_task(
                            _monitor_and_close(new_tickets, BTC_PROFIT_TARGET_USD, entry_info, bot)
                        )
                    else:
                        log.warning("BTC: no new tickets found after trade — profit monitor not started")

                    last_fire_time = time.time()
                    breakout = None

        except Exception as e:
            log.error(f"BTC watcher error: {e}", exc_info=True)

        await asyncio.sleep(BTC_INTERVAL_SECS)
