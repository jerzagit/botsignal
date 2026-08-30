"""
Night Trading Agent - runs on your machine while you sleep.

Schedule uses Malaysia time (UTC+8). When auto-execute is enabled, live mode
still requires AGENT_LIVE_UNLOCKED=true before the agent can place trades.
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from core.config import (
    AGENT_AUTO_EXECUTE,
    AGENT_END_HOUR_MY,
    AGENT_LIVE_UNLOCKED,
    AGENT_START_HOUR_MY,
    ENV_MODE,
    IS_LIVE_MODE,
    YOUR_CHAT_ID,
)
from core.mt5 import execute_trade
from core.notifier import get_bot, send_confirmation
from core.signal import Signal

log = logging.getLogger(__name__)

MY_TZ = timezone(timedelta(hours=8))


def is_agent_active() -> bool:
    """Return True if current Malaysia time is inside the agent window."""
    hour = datetime.now(MY_TZ).hour
    start = AGENT_START_HOUR_MY
    end = AGENT_END_HOUR_MY
    if start > end:
        return hour >= start or hour < end
    return start <= hour < end


def session_name() -> str:
    """Return the current trading session name based on Malaysia time."""
    hour = datetime.now(MY_TZ).hour
    if 14 <= hour < 18:
        return "London Open"
    if 20 <= hour < 24:
        return "New York Open"
    if 0 <= hour < 6:
        return "New York Late / Overlap"
    return "Off-session"


def _live_auto_locked() -> bool:
    return IS_LIVE_MODE and AGENT_AUTO_EXECUTE and not AGENT_LIVE_UNLOCKED


async def agent_handle_signal(signal: Signal, signal_id: str):
    """
    Handle a signal during agent hours.
    Auto-execute only when enabled and live mode is explicitly unlocked.
    """
    bot = get_bot()

    if not is_agent_active():
        log.info("Agent inactive (MY hour=%s) - using confirmation flow.", datetime.now(MY_TZ).hour)
        await send_confirmation(bot, signal, signal_id)
        return

    if _live_auto_locked():
        log.warning("[AGENT] Live auto-execute blocked by AGENT_LIVE_UNLOCKED=false.")
        await send_confirmation(bot, signal, signal_id)
        return

    if not AGENT_AUTO_EXECUTE:
        await send_confirmation(bot, signal, signal_id)
        return

    session = session_name()
    log.info("[AGENT] Auto-executing %s %s (%s)", signal.symbol, signal.direction, session)

    direction_label = "SELL" if signal.direction == "sell" else "BUY"
    await bot.send_message(
        chat_id=YOUR_CHAT_ID,
        text=(
            f"*Agent Auto-Executing*\n\n"
            f"*{signal.symbol}* {direction_label}\n"
            f"Session: `{session}`\n"
            f"SL: `{signal.sl}` | TPs: `{' -> '.join(str(t) for t in signal.tps)}`\n\n"
            f"_Lot size calculated from your margin..._"
        ),
        parse_mode="Markdown",
    )

    result = await asyncio.get_event_loop().run_in_executor(None, execute_trade, signal, signal_id)
    await bot.send_message(chat_id=YOUR_CHAT_ID, text=result, parse_mode="Markdown")


async def agent_status_loop():
    """Send an hourly heartbeat during the active window."""
    bot = get_bot()
    while True:
        await asyncio.sleep(3600)
        if is_agent_active():
            now_my = datetime.now(MY_TZ).strftime("%I:%M %p")
            await bot.send_message(
                chat_id=YOUR_CHAT_ID,
                text=(
                    f"*Agent Heartbeat* - {now_my} MYT\n"
                    f"Session: `{session_name()}`\n"
                    f"Status: Watching for signals"
                ),
                parse_mode="Markdown",
            )


async def start_agent():
    """Start the agent heartbeat loop."""
    bot = get_bot()
    now_my = datetime.now(MY_TZ).strftime("%I:%M %p")
    live_auto_locked = _live_auto_locked()
    mode = (
        "Confirm mode (live auto locked)"
        if live_auto_locked
        else ("AUTO-EXECUTE" if AGENT_AUTO_EXECUTE else "Confirm mode")
    )

    await bot.send_message(
        chat_id=YOUR_CHAT_ID,
        text=(
            f"*Night Agent Started*\n\n"
            f"Current MY time: `{now_my}`\n"
            f"Active window: `{AGENT_START_HOUR_MY}:00 - {AGENT_END_HOUR_MY}:00 MYT`\n"
            f"Mode: {mode}\n\n"
            f"_Agent will {'confirm' if live_auto_locked else ('auto-execute' if AGENT_AUTO_EXECUTE else 'confirm')} "
            f"signals during the active window._"
        ),
        parse_mode="Markdown",
    )

    log.info(
        "Agent started | mode=%s | window=%s:00-%s:00 MYT",
        "CONFIRM_LIVE_LOCKED" if live_auto_locked else ("AUTO" if AGENT_AUTO_EXECUTE else "CONFIRM"),
        AGENT_START_HOUR_MY,
        AGENT_END_HOUR_MY,
    )

    await agent_status_loop()
