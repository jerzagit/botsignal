"""
test_layer_inject.py
Run the real watch_layered_entry coroutine with full DB support.
"""

import asyncio
import logging
import sys
from core.signal import parse_signal
from core.db import upsert_signal
from core.layer_watcher import watch_layered_entry
from core.state import pending

logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s %(name)s: %(message)s",
    stream=sys.stdout,
)

RAW = """xauusd buy @4785-4790
sl 4770
tp 4800
tp 4810
Trade At Your Own Risk
T.A.Y.O.R @AssistByHafizCarat"""

SIGNAL_ID = "dca_live_01"


class MockBot:
    async def send_message(self, chat_id, text, parse_mode=None):
        sys.stdout.write("\n[TELEGRAM] " + text + "\n")
        sys.stdout.flush()


async def main():
    signal = parse_signal(RAW)
    sys.stdout.write(
        f"[OK] {signal.symbol} {signal.direction.upper()} "
        f"zone={signal.entry_low}-{signal.entry_high} "
        f"SL={signal.sl} TPs={signal.tps}\n"
    )
    sys.stdout.flush()

    # Save to DB so record_trade FK constraint passes
    upsert_signal(SIGNAL_ID, signal, status="pending")

    # Register in pending so watcher doesn't self-cancel
    pending[SIGNAL_ID] = signal

    bot = MockBot()
    await watch_layered_entry(signal, SIGNAL_ID, bot, entry_mode="layered_dca", skip_proximity=True)


asyncio.run(main())
