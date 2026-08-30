"""
Safe SignalBot (bot.py) process/service control for the admin dashboard.

Prefer Windows service "SignalBot" (NSSM) when installed.
Fallback: stop bot.py PID only, then start again.

Never kills MT5 (terminal64.exe).
Never restarts the dashboard from this module.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[1]
BOT_PID_FILE = ROOT / "data" / "bot.pid"
SERVICE_NAME = "SignalBot"
START_SCRIPT = ROOT / "start_bot.ps1"


def _run_ps(command: str, timeout: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            command,
        ],
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=str(ROOT),
    )


def _service_exists() -> bool:
    try:
        r = _run_ps(f"Get-Service -Name '{SERVICE_NAME}' -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Name")
        return SERVICE_NAME.lower() in (r.stdout or "").lower()
    except Exception:
        return False


def _service_status() -> str | None:
    try:
        r = _run_ps(
            f"(Get-Service -Name '{SERVICE_NAME}' -ErrorAction SilentlyContinue).Status"
        )
        s = (r.stdout or "").strip()
        return s or None
    except Exception:
        return None


def list_bot_pids() -> list[int]:
    pids: list[int] = []
    if BOT_PID_FILE.is_file():
        try:
            raw = BOT_PID_FILE.read_text(encoding="utf-8").strip().splitlines()[0].strip()
            pid = int(raw)
            # verify still alive
            r = _run_ps(f"Get-Process -Id {pid} -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Id")
            if str(pid) in (r.stdout or ""):
                pids.append(pid)
        except Exception:
            pass
    try:
        r = _run_ps(
            r"""
Get-CimInstance Win32_Process |
  Where-Object {
    $_.Name -match '^(python|pythonw|py)\.exe$' -and
    $_.CommandLine -match 'bot\.py' -and
    $_.CommandLine -notmatch 'dashboard'
  } |
  Select-Object -ExpandProperty ProcessId
"""
        )
        for line in (r.stdout or "").splitlines():
            line = line.strip()
            if line.isdigit():
                p = int(line)
                if p not in pids:
                    pids.append(p)
    except Exception as exc:
        log.warning("list_bot_pids failed: %s", exc)
    return pids


def bot_status() -> dict[str, Any]:
    svc = _service_status() if _service_exists() else None
    pids = list_bot_pids()
    return {
        "service_name": SERVICE_NAME,
        "service_installed": svc is not None or _service_exists(),
        "service_status": svc,
        "pids": pids,
        "running": bool(pids) or (svc or "").lower() == "running",
        "mode": "windows_service" if svc is not None else "process",
    }


def _stop_bot_processes(pids: list[int]) -> list[str]:
    notes = []
    for pid in pids:
        try:
            r = _run_ps(f"Stop-Process -Id {pid} -Force -ErrorAction SilentlyContinue")
            notes.append(f"stopped PID {pid}" + (f" ({r.stderr.strip()})" if r.stderr else ""))
        except Exception as exc:
            notes.append(f"failed stop PID {pid}: {exc}")
    # cleanup pid file
    try:
        if BOT_PID_FILE.is_file():
            BOT_PID_FILE.unlink()
            notes.append("removed data/bot.pid")
    except Exception:
        pass
    return notes


def _start_bot_process() -> tuple[bool, str]:
    python = sys.executable or "python"
    # Prefer project start_bot.ps1 when present
    if START_SCRIPT.is_file():
        try:
            r = _run_ps(
                f"& '{START_SCRIPT}'",
                timeout=45,
            )
            out = ((r.stdout or "") + (r.stderr or "")).strip()
            ok = r.returncode == 0 and "[OK]" in out
            return ok, out or f"start_bot.ps1 exit={r.returncode}"
        except Exception as exc:
            return False, f"start_bot.ps1 failed: {exc}"
    try:
        # Detached start — do not wait on bot.py
        flags = 0
        if os.name == "nt":
            flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
        subprocess.Popen(
            [python, "bot.py"],
            cwd=str(ROOT),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=flags,
            close_fds=True,
        )
        time.sleep(3)
        pids = list_bot_pids()
        if pids:
            return True, f"started bot.py PIDs={pids}"
        return False, "bot.py start attempted but no process found"
    except Exception as exc:
        return False, f"start failed: {exc}"


def restart_signalbot() -> dict[str, Any]:
    """
    Restart SignalBot only.
    Returns {ok, message, mode, before, after, details}.
    """
    before = bot_status()
    details: list[str] = []

    # Path A: Windows service
    if _service_exists():
        details.append(f"using Windows service {SERVICE_NAME}")
        try:
            r = _run_ps(f"Restart-Service -Name '{SERVICE_NAME}' -Force -ErrorAction Stop", timeout=90)
            if r.returncode != 0:
                details.append((r.stderr or r.stdout or "Restart-Service failed").strip())
                # fall through to process mode
            else:
                time.sleep(2)
                after = bot_status()
                return {
                    "ok": True,
                    "message": f"Windows service {SERVICE_NAME} restarted.",
                    "mode": "windows_service",
                    "before": before,
                    "after": after,
                    "details": details + [(r.stdout or "").strip()],
                }
        except Exception as exc:
            details.append(f"service restart failed: {exc}; falling back to process mode")

    # Path B: process stop + start (no MT5 kill)
    pids = list_bot_pids()
    details.extend(_stop_bot_processes(pids))
    time.sleep(2)
    ok, msg = _start_bot_process()
    details.append(msg)
    time.sleep(2)
    after = bot_status()
    return {
        "ok": bool(ok and after.get("running")),
        "message": "SignalBot process restarted." if ok else "SignalBot restart failed.",
        "mode": "process",
        "before": before,
        "after": after,
        "details": details,
    }
