"""
SignalBot — Entry point
Starts Telethon listener + Telegram bot confirmation system.
Run: python bot.py
"""

import asyncio
import logging
import os
from dotenv import load_dotenv

load_dotenv()

from core.listener  import start_listener
from core.notifier  import start_notifier
from core.mt5       import mt5_connect_test

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("logs/bot.log"),
        logging.StreamHandler()
    ]
)
log = logging.getLogger(__name__)


async def main():
    log.info("=== SignalBot starting ===")

    # Quick MT5 sanity check on startup
    ok, msg = mt5_connect_test()
    log.info(f"MT5 check: {msg}")

    await asyncio.gather(
        start_notifier(),   # Telegram bot — handles EXECUTE/SKIP button taps
        start_listener(),   # Telethon — watches mentor's group as your account
    )


if __name__ == "__main__":
    asyncio.run(main())
