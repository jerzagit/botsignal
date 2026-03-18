"""
SignalBot — Entry point
Starts Telethon listener + Telegram bot confirmation system.
Run: python bot.py
"""

import os
import asyncio
import logging
import sys

# Fix Windows cp1252 encoding crash on emoji/unicode in logs
if sys.stdout.encoding != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
import subprocess
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

from core.listener     import start_listener
from core.notifier     import start_notifier, get_bot
from core.map_watcher  import start_map_watcher
from core.mt5          import mt5_connect_test
from core.config       import YOUR_CHAT_ID, ENV_MODE, MAP_ENABLED, TREND_ENABLED

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("logs/bot.log"),
        logging.StreamHandler()
    ]
)
log = logging.getLogger(__name__)

PID_FILE = Path("data/bot.pid")


def _pid_alive(pid: int) -> bool:
    """Check if a process with given PID is still running (Windows)."""
    try:
        result = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
            capture_output=True, text=True, timeout=5
        )
        return str(pid) in result.stdout
    except Exception:
        return False


def acquire_lock() -> bool:
    """Write PID lock file. Returns False if another instance is already running."""
    PID_FILE.parent.mkdir(exist_ok=True)
    if PID_FILE.exists():
        try:
            old_pid = int(PID_FILE.read_text().strip())
            if _pid_alive(old_pid):
                print(f"\n❌ Another SignalBot is already running (PID {old_pid}).")
                print(f"   Stop it first, then run bot.py again.")
                print(f"   To force kill: taskkill /PID {old_pid} /F\n")
                return False
        except Exception:
            pass
        PID_FILE.unlink(missing_ok=True)

    PID_FILE.write_text(str(os.getpid()))
    return True


def release_lock():
    PID_FILE.unlink(missing_ok=True)


async def main():
    env_label = "LIVE" if ENV_MODE == "live" else "DEMO (UAT)"
    log.info(f"=== SignalBot starting === Environment: {env_label}")

    # Quick MT5 sanity check on startup
    ok, msg = mt5_connect_test()
    log.info(f"MT5 check: {msg}")

    tasks = [
        start_notifier(),   # Telegram bot — handles button taps
        start_listener(),   # Telethon — watches mentor's group as your account
    ]

    if MAP_ENABLED:
        async def _delayed_map_watcher():
            """Wait briefly for notifier to initialize, then start map watcher."""
            await asyncio.sleep(3)
            bot = get_bot()
            await start_map_watcher(bot)

        tasks.append(_delayed_map_watcher())
        log.info("AutoZone enabled — watcher will start after notifier init")

    if TREND_ENABLED:
        from core.trend_analyzer import start_trend_watcher

        async def _delayed_trend_watcher():
            """Wait briefly for notifier to initialize, then start trend watcher."""
            await asyncio.sleep(3)
            bot = get_bot()
            await start_trend_watcher(bot)

        tasks.append(_delayed_trend_watcher())
        log.info("Trend analyzer enabled — watcher will start after notifier init")

    await asyncio.gather(*tasks)


async def on_shutdown():
    """Notify you when bot stops. Positions are LEFT OPEN — SL/TP still active."""
    log.info("Bot stopped — open positions remain active in MT5 (SL/TP still live).")
    try:
        bot = get_bot()
        await bot.send_message(
            chat_id=YOUR_CHAT_ID,
            text=(
                "🛑 *SignalBot stopped*\n\n"
                "⚠️ Open positions are still running in MT5.\n"
                "SL and TP remain active on the broker — trades will close automatically.\n\n"
                "_Restart bot.py to resume signal monitoring._"
            ),
            parse_mode="Markdown"
        )
    except Exception as e:
        log.error(f"Could not send shutdown notification: {e}")


if __name__ == "__main__":
    if not acquire_lock():
        sys.exit(1)

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(main())
    except KeyboardInterrupt:
        log.info("KeyboardInterrupt — shutting down")
        loop.run_until_complete(on_shutdown())
    finally:
        release_lock()
        loop.close()
