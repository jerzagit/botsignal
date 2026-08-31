"""
Standalone signal scraper — records all Telegram signals to daily CSV.
No bot API. No trade execution. Just recording.
"""

import asyncio
import logging
import os
import sys
import uuid
import io

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

os.environ["RUN_MODE"] = "RECORD"
sys.path.insert(0, os.path.dirname(__file__))

from telethon import TelegramClient, events
from telethon import utils as tg_utils

from core.config import TG_API_ID, TG_API_HASH, TG_PHONE, SIGNAL_SOURCES
from core.parsers import parse_with_profile
from core.signal import parse_close_alert
from core.signal_recorder import record_signal, record_close, get_today_count

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stderr)],
)
log = logging.getLogger("signal_scraper")


signal_count = 0
close_count = 0


def _safe_print(msg: str) -> None:
    try:
        print(msg, flush=True)
    except Exception:
        log.info(msg)


async def main():
    global signal_count, close_count

    client = TelegramClient("data/session", TG_API_ID, TG_API_HASH)

    if TG_PHONE:
        await client.start(phone=TG_PHONE)
    else:
        await client.start()

    me = await client.get_me()
    _safe_print("=" * 60)
    _safe_print("  SIGNAL SCRAPER - RECORDING MODE")
    _safe_print("=" * 60)
    _safe_print("  Logged in as: %s (%s)" % (me.first_name, me.id))
    _safe_print("  Channels: %d" % len(SIGNAL_SOURCES))
    for s in SIGNAL_SOURCES:
        _safe_print("    - %s (%s) parser=%s" % (s.source_id, s.name, s.parser_profile))
    _safe_print("  Output: data/signals/YYYY-MM-DD.csv")
    _safe_print("  Today so far: %d signals" % get_today_count())
    _safe_print("=" * 60)
    _safe_print("  Listening... Press Ctrl+C to stop.")
    _safe_print("")

    group_entities = []
    source_by_peer_id = {}
    source_by_entity_id = {}

    for source in SIGNAL_SOURCES:
        try:
            numeric_id = int(source.chat)
            entity = await client.get_entity(numeric_id)
        except Exception:
            entity = await client.get_entity(source.chat.lstrip("@"))

        peer_id = tg_utils.get_peer_id(entity)
        title = getattr(entity, "title", None) or getattr(entity, "username", source.chat)
        _safe_print("  Resolved: %s -> %s (peer=%s)" % (source.source_id, title, peer_id))

        group_entities.append(entity)
        source_by_peer_id[peer_id] = source
        source_by_entity_id[getattr(entity, "id", None)] = source

    _safe_print("")
    _safe_print("  Watching %d channel(s)..." % len(group_entities))
    _safe_print("")

    def _on_message(event):
        try:
            asyncio.ensure_future(_handle_message(event, client))
        except Exception as e:
            log.error("Event dispatch error: %s", e)

    async def _handle_message(event, client):
        global signal_count, close_count

        text = event.raw_text
        source = (
            source_by_peer_id.get(event.chat_id)
            or source_by_entity_id.get(getattr(event.chat, "id", None))
            or SIGNAL_SOURCES[0]
        )

        ts = event.date

        alert = parse_close_alert(text)
        if alert:
            close_count += 1
            record_close(
                signal_id="%s_close_%d" % (source.source_id, close_count),
                reason=alert.reason,
                ts=ts,
            )
            _safe_print("  [%s] CLOSE [%s]: %s %s" % (
                ts.strftime("%H:%M"), source.source_id, alert.reason, alert.symbol or ""
            ))
            return

        signal = parse_with_profile(source.parser_profile, text)
        if not signal:
            return

        signal_id = "%s_%s" % (source.source_id, uuid.uuid4().hex[:8])
        signal_count += 1

        if signal.entry_low == signal.entry_high:
            entry_str = str(signal.entry_low)
        else:
            entry_str = "%s-%s" % (signal.entry_low, signal.entry_high)

        tps_str = ", ".join(str(t) for t in signal.tps) if signal.tps else "none"

        record_signal(
            source_id=source.source_id,
            source_name=source.name,
            symbol=signal.symbol,
            direction=signal.direction,
            entry_low=signal.entry_low,
            entry_high=signal.entry_high,
            sl=signal.sl,
            tps=signal.tps,
            signal_id=signal_id,
            raw_text=text,
            ts=ts,
        )

        direction_icon = "BUY" if signal.direction == "buy" else "SELL"
        _safe_print("  [%s] %s [%s] %s @ %s SL=%s TP=%s [total: %d]" % (
            ts.strftime("%H:%M"), direction_icon, source.source_id,
            signal.symbol, entry_str, signal.sl, tps_str, signal_count
        ))

    client.on(events.NewMessage(chats=group_entities))(_on_message)

    try:
        await client.run_until_disconnected()
    except KeyboardInterrupt:
        _safe_print("\n  Stopped. Recorded %d signals, %d closes." % (signal_count, close_count))
    finally:
        await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
