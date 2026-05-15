import os
import asyncio
import logging
import sys
import time

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
from core.config       import YOUR_CHAT_ID, ENV_MODE, MAP_ENABLED, TREND_ENABLED, FIB_SCANNER_ENABLED

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s %(message)s",
    handlers=[
        logging.FileHandler("logs/bot.log"),
        logging.StreamHandler()
    ]
)
log = logging.getLogger(__name__)

PID_FILE = Path("data/bot.pid")
STARTUP_FILE = Path("data/startup.timestamp")
STARTUP_COOLDOWN = 60  # seconds to wait before processing commands after restart


def _check_running_bots() -> list:
    """Check for any running SignalBot processes (excluding current)."""
    import subprocess
    bots = []
    current_pid = os.getpid()
    try:
        result = subprocess.run(
            ["wmic", "process", "where", "name='python.exe'", "get", "ProcessId,CommandLine"],
            capture_output=True, text=True, timeout=10
        )
        for line in result.stdout.strip().split('\n'):
            if 'python' in line.lower() and 'bot.py' in line.lower():
                # Extract PID from line
                parts = line.strip().split()
                if parts:
                    try:
                        pid = int(parts[-1])
                        if pid != current_pid:  # Exclude current process
                            bots.append(line)
                    except:
                        pass
    except Exception:
        pass
    return bots


def _pid_alive(pid: int) -> bool:
    try:
        result = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
            capture_output=True, text=True, timeout=5
        )
        return str(pid) in result.stdout
    except Exception:
        return False


def acquire_lock() -> bool:
    """Check for running bots and acquire lock."""
    # Check for any running bot first
    running = _check_running_bots()
    if running:
        print(f"\nFound {len(running)} running bot process(es):")
        for r in running[:3]:
            print(f"  {r}")
        print("\nPlease close other bot process(es) before starting.")
        return False
    
    # Check PID file
    PID_FILE.parent.mkdir(exist_ok=True)
    if PID_FILE.exists():
        try:
            old_pid = int(PID_FILE.read_text().strip())
            if _pid_alive(old_pid):
                print(f"\nSignalBot is already running (PID {old_pid}).")
                return False
            else:
                print(f"\nStale lock file found (PID {old_pid}), replacing.")
        except Exception:
            pass

    # Write new PID
    PID_FILE.write_text(str(os.getpid()))
    
    # Write startup timestamp for cooldown
    STARTUP_FILE.parent.mkdir(exist_ok=True)
    STARTUP_FILE.write_text(str(int(time.time())))
    
    return True


def get_startup_timestamp() -> int:
    """Get the timestamp when bot was last started."""
    try:
        if STARTUP_FILE.exists():
            return int(STARTUP_FILE.read_text().strip())
    except Exception:
        pass
    return 0


async def check_startup_cooldown() -> bool:
    """
    Check if bot recently started. Returns True if commands should be blocked (within cooldown).
    Use this before processing any trade commands.
    """
    startup_time = get_startup_timestamp()
    if startup_time == 0:
        return False
    
    elapsed = int(time.time()) - startup_time
    if elapsed < STARTUP_COOLDOWN:
        log.warning(f"Startup cooldown: {elapsed}s / {STARTUP_COOLDOWN}s — blocking command")
        return True
    return False


def reset_startup_cooldown():
    """Reset the startup timestamp to prevent stale blocking."""
    try:
        STARTUP_FILE.write_text("0")
    except Exception:
        pass


def _run():
    log.info("SignalBot starting...")
    
    if not acquire_lock():
        sys.exit(1)

    # Test MT5 connection
    if not mt5_connect_test():
        log.error("MT5 connection failed.")
        sys.exit(1)

    # Start notifier (Telegram bot)
    bot_loop = asyncio.new_event_loop()
    asyncio.set_event_loop(bot_loop)
    bot = bot_loop.run_until_complete(start_notifier())
    
    from telegram.ext import ApplicationBuilder
    
    app = ApplicationBuilder().token(os.getenv("BOT_TOKEN")).build()
    
    async def post_init(app):
        if MAP_ENABLED:
            await start_map_watcher(bot)
        if TREND_ENABLED:
            from core.trend_analyzer import start_trend
            await start_trend(bot)
    
    app.post_init = post_init
    
    log.info("Bot ready!")
    bot_loop.run_until_complete(app.run_polling())
    bot_loop.run_until_complete(bot.session.stop())


if __name__ == "__main__":
    try:
        _run()
    finally:
        if PID_FILE.exists():
            PID_FILE.unlink()
        if STARTUP_FILE.exists():
            STARTUP_FILE.unlink()
