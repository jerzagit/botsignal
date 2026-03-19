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
)
from core.mt5   import mt5_connect, execute_trade
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
