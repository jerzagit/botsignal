import asyncio
import logging
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

if sys.stdout.encoding != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

load_dotenv()

from core.config import (  # noqa: E402
    AGENT_ENABLED,
    AGENT_LIVE_UNLOCKED,
    AGENT_AUTO_EXECUTE,
    ENV_MODE,
    FIB_SCANNER_ENABLED,
    MAP_ENABLED,
    STRATEGY_ENABLED,
    STRATEGY_LIVE_UNLOCKED,
    TREND_ENABLED,
)
from core.listener import start_listener  # noqa: E402
from core.map_watcher import start_map_watcher  # noqa: E402
from core.mt5 import mt5_connect_test, mt5_disconnect  # noqa: E402
from core.notifier import get_bot, start_notifier  # noqa: E402

Path("logs").mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("logs/bot.log", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger(__name__)

PID_FILE = Path("data/bot.pid")
STARTUP_FILE = Path("data/startup.timestamp")
STARTUP_COOLDOWN = 60  # seconds to wait before processing commands after restart


def _check_running_bots() -> list:
    """Check for any running SignalBot processes (excluding current)."""
    bots = []
    current_pid = os.getpid()
    try:
        result = subprocess.run(
            ['wmic', 'process', 'where', "name='python.exe'", 'get', 'ProcessId,CommandLine'],
            capture_output=True,
            text=True,
            timeout=10,
        )
        for line in result.stdout.strip().split("\n"):
            if "bot.py" not in line.lower():
                continue
            parts = line.strip().split()
            if len(parts) < 2:
                continue
            try:
                pid = int(parts[0])
            except ValueError:
                try:
                    pid = int(parts[-1])
                except ValueError:
                    continue
            if pid != current_pid:
                bots.append(line)
    except Exception:
        pass
    return bots


def _pid_alive(pid: int) -> bool:
    try:
        result = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return str(pid) in result.stdout
    except Exception:
        return False


def acquire_lock() -> bool:
    """Check for running bots and acquire lock."""
    PID_FILE.parent.mkdir(exist_ok=True)
    if PID_FILE.exists():
        try:
            old_pid = int(PID_FILE.read_text().strip())
            if _pid_alive(old_pid):
                print(f"\nSignalBot is already running (PID {old_pid}).")
                return False
            print(f"\nStale lock file found (PID {old_pid}), replacing.")
        except Exception:
            pass

    running_bots = _check_running_bots()
    if running_bots:
        print("\nSignalBot is already running:")
        for bot in running_bots:
            print(f"  {bot}")
        return False

    PID_FILE.write_text(str(os.getpid()))
    STARTUP_FILE.parent.mkdir(exist_ok=True)
    STARTUP_FILE.write_text(str(int(time.time())))
    return True


def release_lock() -> None:
    for path in (PID_FILE, STARTUP_FILE):
        try:
            if path.exists():
                path.unlink()
        except Exception as exc:
            log.warning("Could not remove %s: %s", path, exc)


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
    Check if bot recently started. Returns True if commands should be blocked.
    Use this before processing any trade commands.
    """
    startup_time = get_startup_timestamp()
    if startup_time == 0:
        return False

    elapsed = int(time.time()) - startup_time
    if elapsed < STARTUP_COOLDOWN:
        log.warning("Startup cooldown: %ss / %ss - blocking command", elapsed, STARTUP_COOLDOWN)
        return True
    return False


def reset_startup_cooldown():
    """Reset the startup timestamp to prevent stale blocking."""
    try:
        STARTUP_FILE.write_text("0")
    except Exception:
        pass


def _service_enabled(name: str, enabled: bool, live_unlocked: bool | None = None) -> bool:
    if live_unlocked is None or ENV_MODE != "live":
        log.info("Service %-12s %s", name, "enabled" if enabled else "disabled")
        return enabled

    if enabled and live_unlocked:
        log.info("Service %-12s enabled (live unlocked)", name)
        return True
    if enabled:
        log.warning("Service %-12s enabled but live auto-trading is locked", name)
        return True
    log.info("Service %-12s disabled", name)
    return False


def _create_service_tasks(bot) -> dict[str, asyncio.Task]:
    tasks: dict[str, asyncio.Task] = {}

    tasks["listener"] = asyncio.create_task(start_listener(), name="listener")

    if _service_enabled("autozone", MAP_ENABLED):
        tasks["autozone"] = asyncio.create_task(start_map_watcher(bot), name="autozone")

    if _service_enabled("trend", TREND_ENABLED):
        from core.trend_analyzer import start_trend_watcher

        tasks["trend"] = asyncio.create_task(start_trend_watcher(bot), name="trend")

    if _service_enabled("fib_scanner", FIB_SCANNER_ENABLED):
        from core.trend_analyzer import start_fib_scanner

        tasks["fib_scanner"] = asyncio.create_task(start_fib_scanner(bot), name="fib_scanner")

    if _service_enabled("strategy", STRATEGY_ENABLED, STRATEGY_LIVE_UNLOCKED):
        from core.strategy import start_strategy

        tasks["strategy"] = asyncio.create_task(start_strategy(bot), name="strategy")

    if _service_enabled("agent", AGENT_ENABLED, AGENT_LIVE_UNLOCKED or not AGENT_AUTO_EXECUTE):
        from agent.agent import start_agent

        tasks["agent"] = asyncio.create_task(start_agent(), name="agent")

    return tasks


async def _stop_notifier(app) -> None:
    if app is None:
        return
    try:
        if app.updater and getattr(app.updater, "running", False):
            await app.updater.stop()
        if getattr(app, "running", False):
            await app.stop()
        await app.shutdown()
        log.info("Telegram notifier stopped.")
    except Exception as exc:
        log.warning("Telegram notifier shutdown failed: %s", exc)


async def _cancel_tasks(tasks: dict[str, asyncio.Task]) -> None:
    for task in tasks.values():
        if not task.done():
            task.cancel()
    if tasks:
        await asyncio.gather(*tasks.values(), return_exceptions=True)


async def main_async() -> int:
    app = None
    tasks: dict[str, asyncio.Task] = {}

    log.info("SignalBot starting...")
    if not acquire_lock():
        return 1

    try:
        mt5_future = asyncio.ensure_future(
            asyncio.get_event_loop().run_in_executor(None, mt5_connect_test)
        )
        app, (ok, message) = await asyncio.gather(
            start_notifier(),
            mt5_future,
        )
        if not ok:
            log.error("MT5 connection failed: %s", message)
            return 1
        log.info("MT5 startup check OK: %s", message)

        bot = get_bot()
        tasks = _create_service_tasks(bot)

        log.info(
            "Bot ready | env=%s | services=%s",
            ENV_MODE,
            ", ".join(tasks.keys()) if tasks else "none",
        )

        done, _ = await asyncio.wait(tasks.values(), return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            name = task.get_name()
            if task.cancelled():
                log.warning("Service %s was cancelled unexpectedly.", name)
                continue
            exc = task.exception()
            if exc:
                log.error("Service %s crashed: %s", name, exc, exc_info=exc)
            else:
                log.error("Service %s stopped unexpectedly.", name)
        return 1

    finally:
        await _cancel_tasks(tasks)
        await _stop_notifier(app)
        mt5_disconnect()
        release_lock()


def _install_signal_handlers():
    if os.name != "nt":
        return
    try:
        signal.signal(signal.SIGBREAK, lambda *_: raise_keyboard_interrupt())
    except Exception:
        pass


def raise_keyboard_interrupt():
    raise KeyboardInterrupt


if __name__ == "__main__":
    _install_signal_handlers()
    try:
        sys.exit(asyncio.run(main_async()))
    except KeyboardInterrupt:
        log.info("SignalBot stopped by user.")
        release_lock()
        sys.exit(0)
