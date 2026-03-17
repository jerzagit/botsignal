"""
core/map_watcher.py
AutoZone — background price monitor for mapped zones.

Every WATCH_INTERVAL_SECS checks if current price is inside any unfired zone.
When triggered: builds a Signal, fires execute_trade / watch_layered_entry,
marks zone as fired (one-shot).

Zones auto-expire at midnight Malaysia time (valid_date filter).
"""

import asyncio
import logging
import time
import uuid
from datetime import datetime, timedelta, timezone

import MetaTrader5 as mt5

from core.config import (
    YOUR_CHAT_ID, WATCH_INTERVAL_SECS, MT5_SYMBOL_SUFFIX,
    LAYER_MODE, MAP_ENABLED,
)
from core.signal import Signal
from core.mt5 import mt5_connect, execute_trade
from core.db import (
    get_today_zones, mark_zone_fired, upsert_signal,
)

log = logging.getLogger(__name__)

_MY_TZ = timezone(timedelta(hours=8))


def _today_my() -> str:
    return datetime.now(_MY_TZ).strftime("%Y-%m-%d")


def _get_price(symbol: str, direction: str):
    """Fetch current ask (buy) or bid (sell) for a symbol."""
    if not mt5_connect():
        return None
    tick = mt5.symbol_info_tick(symbol)
    mt5.shutdown()
    if tick is None:
        return None
    return tick.ask if direction == "buy" else tick.bid


async def _notify(bot, text: str):
    try:
        await bot.send_message(
            chat_id=YOUR_CHAT_ID, text=text, parse_mode="Markdown"
        )
    except Exception as e:
        log.warning(f"map_watcher _notify failed: {e}")


async def start_map_watcher(bot):
    """Background loop: check price vs mapped zones every WATCH_INTERVAL_SECS."""
    if not MAP_ENABLED:
        log.info("AutoZone disabled (MAP_ENABLED=false)")
        return

    log.info("AutoZone watcher started.")

    while True:
        try:
            now_my = datetime.now(_MY_TZ)
            today = now_my.strftime("%Y-%m-%d")

            zones = get_today_zones(active_only=True)
            if not zones:
                await asyncio.sleep(WATCH_INTERVAL_SECS)
                continue

            for zone in zones:
                symbol_mt5 = zone["symbol"] + MT5_SYMBOL_SUFFIX
                direction = zone["direction"]
                price = await asyncio.get_event_loop().run_in_executor(
                    None, _get_price, symbol_mt5, direction
                )
                if price is None:
                    continue

                zone_low = float(zone["zone_low"])
                zone_high = float(zone["zone_high"])

                if not (zone_low <= price <= zone_high):
                    continue

                # Price is inside zone — fire!
                signal_id = "map_" + uuid.uuid4().hex[:8]
                sl_val = float(zone["sl"])
                tp_val = float(zone["tp"])

                signal = Signal(
                    symbol=zone["symbol"],
                    direction=direction,
                    entry_low=zone_low,
                    entry_high=zone_high,
                    sl=sl_val,
                    tps=[tp_val],
                    raw_text=f"[MAP] {zone['symbol']} {direction.upper()} {zone_low}-{zone_high}",
                    created_at=time.time(),
                )

                # Log to signals table + mark zone fired
                upsert_signal(signal_id, signal, status="pending")
                mark_zone_fired(zone["id"], signal_id)

                log.info(
                    f"Map zone #{zone['id']} fired: {zone['symbol']} "
                    f"{direction.upper()} @ {price:.2f} "
                    f"(zone {zone_low}-{zone_high})"
                )

                await _notify(bot, (
                    f"📍 *AutoZone triggered!*\n"
                    f"`{zone['symbol']}` {direction.upper()} @ `{price:.2f}`\n"
                    f"Zone: `{zone_low}–{zone_high}` | SL: `{sl_val}` | TP: `{tp_val}`\n"
                    f"_Executing trade..._"
                ))

                if LAYER_MODE:
                    from core.layer_watcher import watch_layered_entry
                    from core.state import pending
                    pending[signal_id] = signal
                    asyncio.create_task(
                        watch_layered_entry(signal, signal_id, bot,
                                            entry_mode="mapped")
                    )
                else:
                    result = await asyncio.get_event_loop().run_in_executor(
                        None, execute_trade, signal, signal_id
                    )
                    upsert_signal(signal_id, signal, status="executed")
                    await _notify(bot, result)

        except Exception as e:
            log.error(f"map_watcher error: {e}", exc_info=True)

        await asyncio.sleep(WATCH_INTERVAL_SECS)
