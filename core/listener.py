"""
core/listener.py
Telethon client - watches configured Telegram signal sources as YOUR account.
No admin rights needed. Works as long as you're a group/channel member and can
see the messages.
"""

import asyncio
import logging
import uuid

from telethon import TelegramClient, events
from telethon import utils as tg_utils

from core.config import (
    TG_API_ID, TG_API_HASH, SIGNAL_GROUP, SIGNAL_SOURCES, YOUR_CHAT_ID,
    SIGNAL_EXPIRY, ENTRY_MAX_DISTANCE_PIPS, WATCH_INTERVAL_SECS,
    LAYER_MODE, LAYER_COUNT, LAYER2_PIPS,
)
from core.signal import parse_close_alert
from core.parsers import parse_with_profile
from core.state import pending
from core.notifier import send_close_confirmation, get_bot
from core.watcher import watch_and_execute
from core.layer_watcher import watch_layered_entry

log = logging.getLogger(__name__)


async def resolve_group(client: TelegramClient):
    """
    Backward-compatible resolver for the legacy SIGNAL_GROUP value.
    Accepts username, @username, or numeric group/channel ID.
    """
    return await resolve_group_for_target(client, SIGNAL_GROUP)


async def resolve_group_for_target(client: TelegramClient, target: str):
    """Resolve one Telegram group/channel target."""
    target = target.strip()

    try:
        numeric_id = int(target)
        entity = await client.get_entity(numeric_id)
        name = getattr(entity, "title", None) or getattr(entity, "username", str(entity))
        log.info("Resolved Telegram source by numeric ID: %s", name)
        return entity
    except (ValueError, Exception):
        pass

    entity = await client.get_entity(target.lstrip("@"))
    name = getattr(entity, "title", None) or getattr(entity, "username", str(entity))
    log.info("Resolved Telegram source by username: %s", name)
    return entity


async def resolve_sources(client: TelegramClient):
    """Resolve every configured Telegram source into a Telethon entity."""
    resolved = []
    for source in SIGNAL_SOURCES:
        entity = await resolve_group_for_target(client, source.chat)
        peer_id = tg_utils.get_peer_id(entity)
        resolved.append((source, entity, peer_id))
        name = getattr(entity, "title", None) or getattr(entity, "username", str(entity))
        log.info(
            "Resolved source %s (%s) by %s: %s",
            source.source_id, source.parser_profile, source.chat, name
        )
    return resolved


async def start_listener():
    """Start Telethon, resolve sources, and listen for signals."""
    client = TelegramClient("data/session", TG_API_ID, TG_API_HASH)
    await client.start()   # First run: prompts phone + OTP. Session saved after.

    bot = get_bot()

    try:
        resolved_sources = await resolve_sources(client)
    except Exception as e:
        log.error("Could not resolve configured signal source: %s", e)
        await bot.send_message(
            chat_id=YOUR_CHAT_ID,
            text=(
                "❌ *Bot startup failed!*\n\n"
                "Could not find one configured signal source.\n"
                "Check `SIGNAL_SOURCES` or `SIGNAL_GROUP` in your `.env` file.\n\n"
                f"Error: `{e}`"
            ),
            parse_mode="Markdown"
        )
        return

    group_entities = [entity for _, entity, _ in resolved_sources]
    source_by_peer_id = {peer_id: source for source, _, peer_id in resolved_sources}
    source_by_entity_id = {
        getattr(entity, "id", None): source for source, entity, _ in resolved_sources
    }

    watch_lines = []
    for source, entity, _ in resolved_sources:
        title = getattr(entity, "title", None) or getattr(entity, "username", source.chat)
        mode = "auto" if source.auto_execute else "dry-run"
        watch_lines.append(
            f"- `{source.source_id}` {title} ({source.risk_percent*100:.1f}% risk, {mode})"
        )

    from core.config import ENV_MODE
    env_label = "🔴 LIVE" if ENV_MODE == "live" else "🟢 DEMO (UAT)"
    me = await client.get_me()
    await bot.send_message(
        chat_id=YOUR_CHAT_ID,
        text=(
            "🤖 *SignalBot is LIVE!*\n\n"
            f"⚙️ Environment: *{env_label}*\n"
            f"👤 Logged in as: `{me.first_name}`\n"
            f"📢 Watching {len(group_entities)} source(s):\n"
            + "\n".join(watch_lines) + "\n"
            "🎯 Mode: Auto-execute when price enters zone\n\n"
            "_Waiting for signals..._"
        ),
        parse_mode="Markdown"
    )
    log.info("Listening on %d Telegram source(s) as %s", len(group_entities), me.first_name)

    @client.on(events.NewMessage(chats=group_entities))
    async def on_new_message(event):
        text = event.raw_text
        source = (
            source_by_peer_id.get(event.chat_id)
            or source_by_entity_id.get(getattr(event.chat, "id", None))
            or SIGNAL_SOURCES[0]
        )
        log.info("Group message [%s]: %s", source.source_id, text[:100])

        alert = parse_close_alert(text)
        if alert:
            reason_label = (
                "Setup Failed" if alert.reason == "setup_failed" else
                "Collect Profit" if alert.reason == "collect_profit" else
                "Early Profit"
            )
            log.info("Close alert detected [%s]: %s symbol=%s",
                     source.source_id, reason_label, alert.symbol)
            await send_close_confirmation(bot, alert)
            return

        signal = parse_with_profile(source.parser_profile, text)
        if not signal:
            log.debug("Not a trade signal, skipping.")
            return

        signal.source_id = source.source_id
        signal.source_name = source.name
        signal.parser_profile = source.parser_profile
        signal.telegram_chat_id = str(event.chat_id)
        signal.source_risk_percent = source.risk_percent

        signal_id = f"{source.source_id}_{uuid.uuid4().hex[:8]}"
        pending[signal_id] = signal
        log.info(
            "Signal detected [%s]: %s %s -> %s",
            source.source_id, signal.symbol, signal.direction.upper(), signal_id
        )

        from core.db import upsert_signal
        upsert_signal(signal_id, signal, status="pending")

        direction_emoji = "🟢 BUY" if signal.direction == "buy" else "🔴 SELL"
        zone_str = (
            f"`{signal.entry_low}`"
            if signal.entry_low == signal.entry_high
            else f"`{signal.entry_low} - {signal.entry_high}`"
        )
        tps_str = " | ".join(f"`{t}`" for t in signal.tps)

        if not source.auto_execute:
            await bot.send_message(
                chat_id=YOUR_CHAT_ID,
                text=(
                    "🧪 *Signal parsed - dry run only*\n\n"
                    f"Source: `{source.source_id}` | Parser: `{source.parser_profile}`\n"
                    f"*{signal.symbol}* {direction_emoji}\n"
                    f"Entry zone: {zone_str}\n"
                    f"SL: `{signal.sl}` | TP: {tps_str}\n\n"
                    "_No trade placed because this source has auto_execute=false._"
                ),
                parse_mode="Markdown"
            )
            upsert_signal(signal_id, signal, status="dry_run")
            pending.pop(signal_id, None)
            return

        if LAYER_MODE:
            mode_line = (
                f"🔢 Layered DCA mode - up to `{LAYER_COUNT}` layers "
                f"(`{LAYER2_PIPS}p` apart, dynamic count)"
            )
            watcher_task = watch_layered_entry(signal, signal_id, bot)
        else:
            mode_line = (
                "🎯 Will auto-execute when price is within "
                f"`{ENTRY_MAX_DISTANCE_PIPS} pips` of entry"
            )
            watcher_task = watch_and_execute(signal, signal_id, bot)

        await bot.send_message(
            chat_id=YOUR_CHAT_ID,
            text=(
                "👀 *New signal - watching...*\n\n"
                f"Source: `{source.source_id}` | Risk bucket: `{source.risk_percent*100:.1f}%`\n"
                f"*{signal.symbol}* {direction_emoji}\n"
                f"Entry zone: {zone_str}\n"
                f"SL: `{signal.sl}` | TP: {tps_str}\n\n"
                f"{mode_line}\n"
                f"⏳ Watching for `{SIGNAL_EXPIRY // 60} min` "
                f"(checking every `{WATCH_INTERVAL_SECS}s`)"
            ),
            parse_mode="Markdown"
        )

        asyncio.create_task(watcher_task)

    await client.run_until_disconnected()
