"""
core/listener.py
Telethon client — watches the mentor's Telegram group as YOUR account.
No admin rights needed. Works as long as you're a group member and can see the messages.
"""

import uuid
import logging
import asyncio

from telethon import TelegramClient, events

from core.config  import TG_API_ID, TG_API_HASH, SIGNAL_GROUP, YOUR_CHAT_ID
from core.signal  import parse_signal
from core.state   import pending
from core.notifier import send_confirmation, get_bot

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
    log.info(f"Resolved group by username: {entity.title}")
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
            f"📢 Watching: *{group_entity.title}*\n"
            f"🎯 Mode: Confirm before execute\n\n"
            f"_Waiting for signals..._"
        ),
        parse_mode="Markdown"
    )
    log.info(f"Listening on '{group_entity.title}' as {me.first_name}")

    @client.on(events.NewMessage(chats=group_entity))
    async def on_new_message(event):
        text = event.raw_text
        log.info(f"Group message: {text[:100]}")

        signal = parse_signal(text)
        if not signal:
            log.debug("Not a trade signal, skipping.")
            return

        signal_id = uuid.uuid4().hex[:8]
        pending[signal_id] = signal
        log.info(f"Signal detected: {signal.symbol} {signal.direction.upper()} → {signal_id}")
        await send_confirmation(bot, signal, signal_id)

    await client.run_until_disconnected()
