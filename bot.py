import asyncio
import logging
import os
import signal
import subprocess
import sys
import time
from collections.abc import Awaitable, Callable
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
    IS_LIVE_MODE,
    FIB_SCANNER_ENABLED,
    MAP_ENABLED,
    MT5_STARTUP_TIMEOUT_SECS,
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
SERVICE_LOCK_DIR = Path("data/service_locks")
STARTUP_FILE = Path("data/startup.timestamp")
STARTUP_COOLDOWN = 60  # seconds to wait before processing commands after restart


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
            PID_FILE.unlink()
        except Exception:
            try:
                PID_FILE.unlink()
            except Exception:
                pass

    try:
        fd = os.open(str(PID_FILE), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        with os.fdopen(fd, "w") as f:
            f.write(str(os.getpid()))
    except FileExistsError:
        try:
            old_pid = int(PID_FILE.read_text().strip())
        except Exception:
            old_pid = 0
        print(f"\nSignalBot lock exists (PID {old_pid or 'unknown'}).")
        return False

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


def _service_lock_path(name: str) -> Path:
    return SERVICE_LOCK_DIR / f"{name}.pid"


def _claim_service_lock(name: str) -> bool:
    """Return False if this service is already active or locked by a live process."""
    SERVICE_LOCK_DIR.mkdir(parents=True, exist_ok=True)
    lock_path = _service_lock_path(name)
    current_pid = os.getpid()

    for task in asyncio.all_tasks():
        if task is not asyncio.current_task() and not task.done() and task.get_name() == name:
            log.error("Service %s duplicate start blocked: task already exists.", name)
            return False

    if lock_path.exists():
        try:
            old_pid = int(lock_path.read_text().strip())
            if old_pid == current_pid:
                log.error("Service %s duplicate start blocked: lock already held by this bot.", name)
                return False
            if _pid_alive(old_pid):
                log.error("Service %s duplicate start blocked: PID %s already holds lock.", name, old_pid)
                return False
            log.warning("Service %s stale lock found (PID %s), replacing.", name, old_pid)
            lock_path.unlink()
        except Exception:
            try:
                lock_path.unlink()
            except Exception:
                return False

    try:
        fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        with os.fdopen(fd, "w") as f:
            f.write(str(current_pid))
        return True
    except FileExistsError:
        log.error("Service %s duplicate start blocked: lock was claimed concurrently.", name)
        return False


def _release_service_locks(tasks: dict[str, asyncio.Task]) -> None:
    for name in tasks:
        lock_path = _service_lock_path(name)
        try:
            if lock_path.exists():
                lock_path.unlink()
        except Exception as exc:
            log.warning("Could not remove service lock %s: %s", lock_path, exc)


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
    if live_unlocked is None or not IS_LIVE_MODE:
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

    def start_service(name: str, factory: Callable[[], Awaitable[object]]) -> None:
        if name in tasks:
            log.error("Service %s duplicate start blocked: already registered.", name)
            return
        if not _claim_service_lock(name):
            raise RuntimeError(f"Duplicate service start blocked: {name}")
        tasks[name] = asyncio.create_task(factory(), name=name)
        log.info("Service %-12s started", name)

    try:
        start_service("listener", start_listener)

        if _service_enabled("autozone", MAP_ENABLED):
            start_service("autozone", lambda: start_map_watcher(bot))

        if _service_enabled("trend", TREND_ENABLED):
            from core.trend_analyzer import start_trend_watcher

            start_service("trend", lambda: start_trend_watcher(bot))

        if _service_enabled("fib_scanner", FIB_SCANNER_ENABLED):
            from core.trend_analyzer import start_fib_scanner

            start_service("fib_scanner", lambda: start_fib_scanner(bot))

        if _service_enabled("strategy", STRATEGY_ENABLED, STRATEGY_LIVE_UNLOCKED):
            from core.strategy import start_strategy

            start_service("strategy", lambda: start_strategy(bot))

        if _service_enabled("agent", AGENT_ENABLED, AGENT_LIVE_UNLOCKED or not AGENT_AUTO_EXECUTE):
            from agent.agent import start_agent

            start_service("agent", start_agent)
    except Exception:
        for task in tasks.values():
            task.cancel()
        _release_service_locks(tasks)
        raise

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
    _release_service_locks(tasks)


async def main_async() -> int:
    app = None
    tasks: dict[str, asyncio.Task] = {}

    log.info("SignalBot starting...")
    if not acquire_lock():
        return 1

    try:
        try:
            ok, message = await asyncio.wait_for(
                asyncio.get_event_loop().run_in_executor(None, mt5_connect_test),
                timeout=MT5_STARTUP_TIMEOUT_SECS,
            )
        except asyncio.TimeoutError:
            log.error(
                "MT5 startup check timed out after %ss. Telegram notifier was not started.",
                MT5_STARTUP_TIMEOUT_SECS,
            )
            return 1
        if not ok:
            log.error("MT5 connection failed: %s", message)
            return 1
        log.info("MT5 startup check OK: %s", message)
        try:
            from core.daily_profit_sync import sync_today_closed_deals

            summary = await asyncio.get_event_loop().run_in_executor(
                None, sync_today_closed_deals
            )
            log.info(
                "Startup daily profit sync: %s closed position(s), pnl=$%.2f",
                summary["synced"],
                summary["pnl"],
            )
        except Exception as exc:
            log.error("Startup daily profit sync failed: %s", exc, exc_info=exc)

        app = await start_notifier()
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
