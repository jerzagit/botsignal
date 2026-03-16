"""
agent/agent.py
Night Trading Agent — runs on YOUR machine while you sleep.

Schedule (Malaysia time, UTC+8):
    Active:  10 PM → 6 AM  (London open + New York session)
    Sleeping: 6 AM → 10 PM (avoid choppy Asia daytime for gold)

When AGENT_AUTO_EXECUTE=true in .env:
    - Signals are executed automatically (no EXECUTE button needed)
    - You still get a Telegram notification of every trade taken

When AGENT_AUTO_EXECUTE=false (default):
    - Agent sends the normal confirmation message
    - You still need to tap EXECUTE (safe mode)

Run standalone:
    python -m agent.agent

Or it starts automatically alongside bot.py when
AGENT_ENABLED=true in .env.
"""

import asyncio
import logging
import time
from datetime import datetime, timezone, timedelta

from core.config  import (
    YOUR_CHAT_ID,
    AGENT_START_HOUR_MY, AGENT_END_HOUR_MY,
    AGENT_AUTO_EXECUTE,
)
from core.state   import pending
from core.signal  import Signal
from core.mt5     import execute_trade
from core.notifier import get_bot, send_confirmation

log = logging.getLogger(__name__)

MY_TZ = timezone(timedelta(hours=8))   # Malaysia = UTC+8


def is_agent_active() -> bool:
    """Returns True if current Malaysia time is within the agent's active window."""
    now_my = datetime.now(MY_TZ)
    hour   = now_my.hour

    start = AGENT_START_HOUR_MY   # e.g. 22
    end   = AGENT_END_HOUR_MY     # e.g. 6

    if start > end:
        # Window wraps midnight: active from 22→23→0→...→6
        return hour >= start or hour < end
    else:
        return start <= hour < end


def session_name() -> str:
    """Returns current trading session name based on MY time."""
    now_my = datetime.now(MY_TZ)
    hour   = now_my.hour
    if 14 <= hour < 18:
        return "London Open"
    elif 20 <= hour < 24:
        return "New York Open"
    elif 0 <= hour < 6:
        return "New York Late / Overlap"
    else:
        return "Off-session"


async def agent_handle_signal(signal: Signal, signal_id: str):
    """
    Called by the listener when a signal arrives during agent hours.
    If auto-execute is on → trades immediately and notifies you.
    If auto-execute is off → sends normal confirmation buttons.
    """
    bot = get_bot()

    if not is_agent_active():
        log.info(f"Agent inactive (MY hour={datetime.now(MY_TZ).hour}) — skipping auto-execute.")
        # Fall back to normal confirmation even if AGENT_AUTO_EXECUTE=true
        await send_confirmation(bot, signal, signal_id)
        return

    if AGENT_AUTO_EXECUTE:
        session = session_name()
        log.info(f"[AGENT] Auto-executing {signal.symbol} {signal.direction} ({session})")

        # Notify you first
        direction_emoji = "🔴 SELL" if signal.direction == "sell" else "🟢 BUY"
        await bot.send_message(
            chat_id=YOUR_CHAT_ID,
            text=(
                f"🤖 *Agent Auto-Executing*\n\n"
                f"*{signal.symbol}* {direction_emoji}\n"
                f"Session: `{session}`\n"
                f"SL: `{signal.sl}` | TPs: `{' → '.join(str(t) for t in signal.tps)}`\n\n"
                f"_Lot size calculated from your margin..._"
            ),
            parse_mode="Markdown"
        )

        # Execute
        result = await asyncio.get_event_loop().run_in_executor(None, execute_trade, signal, signal_id)
        await bot.send_message(chat_id=YOUR_CHAT_ID, text=result, parse_mode="Markdown")

    else:
        # Safe mode — still ask for confirmation even during agent hours
        await send_confirmation(bot, signal, signal_id)


async def agent_status_loop():
    """
    Sends you a status ping every hour during active window,
    so you know the agent is alive while you sleep.
    """
    bot = get_bot()
    while True:
        await asyncio.sleep(3600)   # check every hour
        if is_agent_active():
            now_my = datetime.now(MY_TZ).strftime("%I:%M %p")
            session = session_name()
            await bot.send_message(
                chat_id=YOUR_CHAT_ID,
                text=(
                    f"🕐 *Agent Heartbeat* — {now_my} MYT\n"
                    f"Session: `{session}`\n"
                    f"Status: Watching for signals 👀"
                ),
                parse_mode="Markdown"
            )


async def start_agent():
    """Start the agent (heartbeat loop). Signal handling is hooked in listener.py."""
    bot = get_bot()
    now_my = datetime.now(MY_TZ).strftime("%I:%M %p")
    mode   = "AUTO-EXECUTE 🚀" if AGENT_AUTO_EXECUTE else "Confirm mode 🔒"

    await bot.send_message(
        chat_id=YOUR_CHAT_ID,
        text=(
            f"🌙 *Night Agent Started*\n\n"
            f"Current MY time: `{now_my}`\n"
            f"Active window: `{AGENT_START_HOUR_MY}:00 – {AGENT_END_HOUR_MY}:00 MYT`\n"
            f"Mode: {mode}\n\n"
            f"_Agent will {'auto-execute' if AGENT_AUTO_EXECUTE else 'confirm'} signals "
            f"during the active window._"
        ),
        parse_mode="Markdown"
    )

    log.info(f"Agent started | mode={'AUTO' if AGENT_AUTO_EXECUTE else 'CONFIRM'} | "
             f"window={AGENT_START_HOUR_MY}:00-{AGENT_END_HOUR_MY}:00 MYT")

    await agent_status_loop()
