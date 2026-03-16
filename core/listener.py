"""
core/listener.py
Telethon client — watches the mentor's Telegram group as YOUR account.
No admin rights needed. Works as long as you're a group member and can see the messages.
"""

import uuid
import logging
import asyncio

from telethon import TelegramClient, events

from core.config   import TG_API_ID, TG_API_HASH, SIGNAL_GROUP, YOUR_CHAT_ID, \
                          SIGNAL_EXPIRY, ENTRY_MAX_DISTANCE_PIPS, WATCH_INTERVAL_SECS
from core.signal   import parse_signal, parse_close_alert
from core.state    import pending
from core.notifier import send_close_confirmation, get_bot
from core.watcher  import watch_and_execute

log = logging.getLogger(__name__)


async def resolve_group(client: TelegramClient):
    """
    Resolve SIGNAL_GROUP from .env into a Telethon entity.
    Accepts:
        - Plain username:   AssistByHafizCarat
        - With @:           @AssistByHafizCarat
        - Numeric group ID: -1001234567890
    """
    target = SIGNAL_GROUP.strip()

    # Try numeric ID
    try:
        numeric_id = int(target)
        entity = await client.get_entity(numeric_id)
        log.info(f"Resolved group by numeric ID: {entity.title}")
        return entity
    except (ValueError, Exception):
        pass

    # Try username
    username = target.lstrip("@")
    entity = await client.get_entity(username)
    name = getattr(entity, "title", None) or getattr(entity, "username", str(entity))
    log.info(f"Resolved group by username: {name}")
    return entity


async def start_listener():
    """Start Telethon, resolve group, and listen for signals."""
    client = TelegramClient("data/session", TG_API_ID, TG_API_HASH)
    await client.start()   # First run: prompts phone + OTP. Session saved after.

    bot = get_bot()

    try:
        group_entity = await resolve_group(client)
    except Exception as e:
        log.error(f"Could not resolve group '{SIGNAL_GROUP}': {e}")
        await bot.send_message(
            chat_id=YOUR_CHAT_ID,
            text=(
                f"❌ *Bot startup failed!*\n\n"
                f"Could not find group: `{SIGNAL_GROUP}`\n"
                f"Check `SIGNAL_GROUP` in your `.env` file.\n\n"
                f"Error: `{e}`"
            ),
            parse_mode="Markdown"
        )
        return

    # Send live startup confirmation
    me = await client.get_me()
    await bot.send_message(
        chat_id=YOUR_CHAT_ID,
        text=(
            f"🤖 *SignalBot is LIVE!*\n\n"
            f"👤 Logged in as: `{me.first_name}`\n"
            f"📢 Watching: *{getattr(group_entity, 'title', getattr(group_entity, 'username', '?'))}*\n"
            f"🎯 Mode: Auto-execute when price enters zone\n\n"
            f"_Waiting for signals..._"
        ),
        parse_mode="Markdown"
    )
    group_name = getattr(group_entity, "title", getattr(group_entity, "username", str(group_entity)))
    log.info(f"Listening on '{group_name}' as {me.first_name}")

    @client.on(events.NewMessage(chats=group_entity))
    async def on_new_message(event):
        text = event.raw_text
        log.info(f"Group message: {text[:100]}")

        # ── Check for close alert first (setup failed / early TP) ────────────
        alert = parse_close_alert(text)
        if alert:
            reason_label = "Setup Failed" if alert.reason == "setup_failed" else "Early Profit"
            log.info(f"Close alert detected: {reason_label} symbol={alert.symbol}")
            await send_close_confirmation(bot, alert)
            return

        # ── Check for normal trade signal ─────────────────────────────────────
        signal = parse_signal(text)
        if not signal:
            log.debug("Not a trade signal, skipping.")
            return

        signal_id = uuid.uuid4().hex[:8]
        pending[signal_id] = signal
        log.info(f"Signal detected: {signal.symbol} {signal.direction.upper()} → {signal_id}")

        from core.db import upsert_signal
        upsert_signal(signal_id, signal, status="pending")

        # Send "watching" notification — no buttons, fully automatic
        direction_emoji = "🟢 BUY" if signal.direction == "buy" else "🔴 SELL"
        zone_str = (
            f"`{signal.entry_low}`"
            if signal.entry_low == signal.entry_high
            else f"`{signal.entry_low} – {signal.entry_high}`"
        )
        tps_str = " | ".join(f"`{t}`" for t in signal.tps)
        await bot.send_message(
            chat_id=YOUR_CHAT_ID,
            text=(
                f"👀 *New signal — watching...*\n\n"
                f"*{signal.symbol}* {direction_emoji}\n"
                f"Entry zone: {zone_str}\n"
                f"SL: `{signal.sl}` | TP: {tps_str}\n\n"
                f"🎯 Will auto-execute when price is within "
                f"`{ENTRY_MAX_DISTANCE_PIPS} pips` of entry\n"
                f"⏳ Watching for `{SIGNAL_EXPIRY // 60} min` "
                f"(checking every `{WATCH_INTERVAL_SECS}s`)"
            ),
            parse_mode="Markdown"
        )

        # Start the price watcher as a background asyncio task
        asyncio.create_task(watch_and_execute(signal, signal_id, bot))

    await client.run_until_disconnected()
