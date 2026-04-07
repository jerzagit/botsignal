"""
core/watcher.py
Price watcher — monitors price after a signal arrives and auto-executes
when price enters Hafiz's entry zone.

Flow:
  1. Signal detected -> watcher task starts
  2. Every WATCH_INTERVAL_SECS: check current price vs entry zone
  3. Price within ENTRY_MAX_DISTANCE_PIPS -> run all guards -> execute
  4. Spread too wide -> retry next cycle (spread normalises)
  5. Any other guard blocks -> notify user, stop watching
  6. SIGNAL_EXPIRY reached -> notify expired, stop watching
"""

import asyncio
import logging
import time

import MetaTrader5 as mt5

from core.config import (
    YOUR_CHAT_ID, SIGNAL_EXPIRY, WATCH_INTERVAL_SECS,
    MT5_SYMBOL_SUFFIX, SL_PIP_SIZE, ENTRY_MAX_DISTANCE_PIPS,
    PROFIT_LOCK_ENABLED, PROFIT_LOCK_PIPS, PROFIT_LOCK_TP_PIPS,
    TRAIL_ENABLED, TRAIL_PIPS,
)
from core.mt5   import mt5_connect, execute_trade, modify_sl_tp
from core.state import pending
from core.db    import upsert_signal

log = logging.getLogger(__name__)


def _get_price(symbol: str, direction: str):
    """Lightweight price fetch. Returns (ask or bid) or None on failure."""
    if not mt5_connect():
        return None
    tick = mt5.symbol_info_tick(symbol)
    mt5.shutdown()
    if tick is None:
        return None
    return tick.ask if direction == "buy" else tick.bid


async def watch_and_execute(signal, signal_id: str, bot):
    """
    Watch price every WATCH_INTERVAL_SECS seconds.
    Auto-executes when price enters the entry zone within SIGNAL_EXPIRY.
    """
    symbol   = signal.symbol + MT5_SYMBOL_SUFFIX
    deadline = signal.created_at + SIGNAL_EXPIRY
    zone_str = (
        f"{signal.entry_low}"
        if signal.entry_low == signal.entry_high
        else f"{signal.entry_low}–{signal.entry_high}"
    )
    mins = SIGNAL_EXPIRY // 60

    log.info(
        f"Watcher started: {signal.symbol} {signal.direction.upper()} "
        f"[{signal_id}] zone={zone_str}"
    )

    while time.time() < deadline:

        # Signal was cancelled externally (e.g. close alert closed it)
        if signal_id not in pending:
            log.info(f"Watcher [{signal_id}]: signal removed — stopping")
            return

        # Lightweight price check
        price = await asyncio.get_event_loop().run_in_executor(
            None, _get_price, symbol, signal.direction
        )

        if price is not None:
            distance_pts  = max(0.0, max(signal.entry_low - price, price - signal.entry_high))
            distance_pips = distance_pts / SL_PIP_SIZE

            log.debug(
                f"Watcher [{signal_id}]: price={price} "
                f"distance={distance_pips:.0f} pips from zone"
            )

            if distance_pips <= ENTRY_MAX_DISTANCE_PIPS:
                # ── Price is in zone — attempt execution ──────────────────────
                log.info(
                    f"Watcher [{signal_id}]: price {price} entered zone "
                    f"({distance_pips:.0f} pips) — executing"
                )
                pending.pop(signal_id, None)

                result = await asyncio.get_event_loop().run_in_executor(
                    None, execute_trade, signal, signal_id
                )

                if "Trade Executed" in result:
                    upsert_signal(signal_id, signal, status="executed")
                    await bot.send_message(
                        chat_id=YOUR_CHAT_ID,
                        text=f"🤖 *Auto-Executed!*\n\n{result}",
                        parse_mode="Markdown"
                    )
                    log.info(f"Watcher [{signal_id}]: auto-executed ✅")
                    await _monitor_profit_lock(signal, signal_id, bot, symbol)
                    return

                elif "spread too wide" in result:
                    # Spread can normalise in minutes — keep watching
                    pending[signal_id] = signal
                    log.info(f"Watcher [{signal_id}]: spread too wide — retrying")
                    await bot.send_message(
                        chat_id=YOUR_CHAT_ID,
                        text=(
                            f"⏳ *Price in zone — spread too wide, retrying...*\n"
                            f"`{signal.symbol} {signal.direction.upper()}` | zone: `{zone_str}`\n"
                            f"_Checking again in {WATCH_INTERVAL_SECS}s_"
                        ),
                        parse_mode="Markdown"
                    )

                else:
                    # Guard blocked (margin, stack, RR, etc.) — not retryable
                    upsert_signal(signal_id, signal, status="blocked")
                    await bot.send_message(
                        chat_id=YOUR_CHAT_ID,
                        text=f"🤖 *Auto-Execute Blocked*\n\n{result}",
                        parse_mode="Markdown"
                    )
                    log.info(
                        f"Watcher [{signal_id}]: blocked — "
                        f"{result.splitlines()[0]}"
                    )
                    return

        await asyncio.sleep(WATCH_INTERVAL_SECS)

    # ── Signal expired — price never reached the zone ─────────────────────────
    pending.pop(signal_id, None)
    upsert_signal(signal_id, signal, status="expired")
    await bot.send_message(
        chat_id=YOUR_CHAT_ID,
        text=(
            f"⏰ *Signal expired — price never reached entry zone*\n"
            f"`{signal.symbol} {signal.direction.upper()}` | Zone: `{zone_str}`\n"
            f"_Watched for {mins} min — no entry made._"
        ),
        parse_mode="Markdown"
    )
    log.info(f"Watcher [{signal_id}]: expired after {mins} min")


async def _monitor_profit_lock(signal, signal_id: str, bot, symbol: str):
    """
    After execution, monitor open positions for this signal.
    - Profit lock: at +PROFIT_LOCK_PIPS → SL to breakeven, TP tightened
    - Trailing stop: after lock, trail SL every TRAIL_PIPS of further movement
    Exits when all positions are closed.
    """
    locked_tickets = set()
    trail_prices   = {}   # ticket → best price seen
    trail_pts      = TRAIL_PIPS * SL_PIP_SIZE
    log.info(f"Monitor [{signal_id}]: started (profit_lock={PROFIT_LOCK_ENABLED}, trail={TRAIL_ENABLED})")

    while True:
        await asyncio.sleep(WATCH_INTERVAL_SECS)

        if not mt5_connect():
            continue

        all_positions = mt5.positions_get(symbol=symbol)
        mt5.shutdown()

        if not all_positions:
            log.info(f"Monitor [{signal_id}]: all positions closed — done")
            return

        pos_map = {p.ticket: p for p in all_positions}

        # ── Profit lock ───────────────────────────────────────────────────────
        if PROFIT_LOCK_ENABLED:
            locked_this_cycle = []
            for pos in all_positions:
                if pos.ticket in locked_tickets:
                    continue
                profit_pips = (
                    (pos.price_open - pos.price_current) / SL_PIP_SIZE
                    if signal.direction == "sell"
                    else (pos.price_current - pos.price_open) / SL_PIP_SIZE
                )
                if profit_pips < PROFIT_LOCK_PIPS:
                    continue
                new_sl = pos.price_open
                new_tp = round(
                    pos.price_open - PROFIT_LOCK_TP_PIPS * SL_PIP_SIZE
                    if signal.direction == "sell"
                    else pos.price_open + PROFIT_LOCK_TP_PIPS * SL_PIP_SIZE,
                    2
                ) if PROFIT_LOCK_TP_PIPS > 0 else pos.tp
                result = modify_sl_tp(pos.ticket, new_sl=new_sl, new_tp=new_tp)
                if "❌" not in result:
                    locked_tickets.add(pos.ticket)
                    trail_prices[pos.ticket] = pos.price_current
                    locked_this_cycle.append((pos.ticket, profit_pips, new_tp))
                    log.info(f"ProfitLock [{signal_id}]: #{pos.ticket} {profit_pips:.0f}p → SL=BE TP={new_tp}")

            if locked_this_cycle:
                lines = "\n".join(f"  `#{t}` — `{p:.0f}p` profit → TP `{tp}`" for t, p, tp in locked_this_cycle)
                await bot.send_message(
                    chat_id=YOUR_CHAT_ID,
                    text=(
                        f"🔒 *Profit Lock — {len(locked_this_cycle)} position(s) secured!*\n"
                        f"`{signal.symbol} {signal.direction.upper()}`\n"
                        f"{lines}\n"
                        f"_SL moved to breakeven | TP tightened to {PROFIT_LOCK_TP_PIPS}p_"
                    ),
                    parse_mode="Markdown"
                )

        # ── Trailing stop ─────────────────────────────────────────────────────
        if TRAIL_ENABLED and trail_prices:
            trailed = []
            for ticket, best_price in list(trail_prices.items()):
                pos = pos_map.get(ticket)
                if pos is None:
                    trail_prices.pop(ticket, None)
                    continue
                current = pos.price_current
                if signal.direction == "sell":
                    if current < best_price - trail_pts:
                        new_sl = round(current + trail_pts, 2)
                        if new_sl >= pos.sl:
                            continue
                        result = modify_sl_tp(ticket, new_sl=new_sl)
                        if "❌" not in result:
                            trail_prices[ticket] = current
                            trailed.append((ticket, new_sl))
                            log.info(f"Trail [{signal_id}]: #{ticket} price={current} → SL={new_sl}")
                else:
                    if current > best_price + trail_pts:
                        new_sl = round(current - trail_pts, 2)
                        if new_sl <= pos.sl:
                            continue
                        result = modify_sl_tp(ticket, new_sl=new_sl)
                        if "❌" not in result:
                            trail_prices[ticket] = current
                            trailed.append((ticket, new_sl))
                            log.info(f"Trail [{signal_id}]: #{ticket} price={current} → SL={new_sl}")

            if trailed:
                lines = "\n".join(f"  `#{t}` → SL `{sl}`" for t, sl in trailed)
                await bot.send_message(
                    chat_id=YOUR_CHAT_ID,
                    text=(
                        f"📈 *Trailing Stop updated — {len(trailed)} position(s)*\n"
                        f"`{signal.symbol} {signal.direction.upper()}`\n"
                        f"{lines}\n"
                        f"_Trailing {TRAIL_PIPS}p behind price_"
                    ),
                    parse_mode="Markdown"
                )
