"""
Signal Recorder — logs every Telegram signal to daily CSV files.

Output: data/signals/YYYY-MM-DD.csv
Fields: timestamp, source_id, source_name, symbol, direction, entry_low, entry_high, sl, tp1, tp2, tps_json, raw_text, signal_id
"""

from __future__ import annotations

import csv
import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parents[1]
SIGNALS_DIR = ROOT / "data" / "signals"

_CSV_lock = threading.Lock()
_csv_files: dict[str, csv.writer] = {}
_csv_handles: dict[str, open] = {}

_CSV_HEADER = [
    "timestamp",
    "timestamp_utc",
    "source_id",
    "source_name",
    "symbol",
    "direction",
    "entry_low",
    "entry_high",
    "sl",
    "tp1",
    "tp2",
    "tps_json",
    "signal_id",
    "raw_text",
]

_DAILY_HEADER = [
    "timestamp",
    "source",
    "symbol",
    "direction",
    "entry",
    "sl",
    "tps",
    "signal_id",
    "outcome",
]


def _get_csv_path(dt: Optional[datetime] = None) -> Path:
    if dt is None:
        dt = datetime.now(timezone.utc)
    day_str = dt.strftime("%Y-%m-%d")
    SIGNALS_DIR.mkdir(parents=True, exist_ok=True)
    return SIGNALS_DIR / f"{day_str}.csv"


def _ensure_writer(path: Path) -> csv.writer:
    key = str(path)
    if key in _csv_files:
        return _csv_files[key]

    with _CSV_lock:
        if key in _csv_files:
            return _csv_files[key]

        file_exists = path.exists() and path.stat().st_size > 0
        fh = open(path, "a", newline="", encoding="utf-8")
        writer = csv.writer(fh)
        if not file_exists:
            writer.writerow(_CSV_HEADER)
            fh.flush()
        _csv_files[key] = writer
        _csv_handles[key] = fh
        return writer


def record_signal(
    source_id: str,
    source_name: str,
    symbol: str,
    direction: str,
    entry_low: float,
    entry_high: float,
    sl: float,
    tps: list[float],
    signal_id: str,
    raw_text: str,
    ts: Optional[datetime] = None,
) -> str:
    """Record a signal to the daily CSV. Returns the CSV file path."""
    if ts is None:
        ts = datetime.now(timezone.utc)

    entry_str = f"{entry_low}" if entry_low == entry_high else f"{entry_low}-{entry_high}"
    tps_str = ", ".join(str(t) for t in tps) if tps else ""
    tp1 = tps[0] if len(tps) > 0 else ""
    tp2 = tps[1] if len(tps) > 1 else ""

    path = _get_csv_path(ts)
    writer = _ensure_writer(path)

    with _CSV_lock:
        writer.writerow([
            ts.strftime("%Y-%m-%d %H:%M:%S"),
            ts.strftime("%Y-%m-%d %H:%M:%S UTC"),
            source_id,
            source_name,
            symbol.upper(),
            direction.lower(),
            entry_low,
            entry_high,
            sl if sl else "",
            tp1,
            tp2,
            json.dumps(tps) if tps else "",
            signal_id,
            raw_text.replace("\n", " | ").strip(),
        ])
        _csv_handles[str(path)].flush()

    return str(path)


def record_close(
    signal_id: str,
    reason: str,
    ts: Optional[datetime] = None,
) -> None:
    """Update outcome for a signal in today's CSV."""
    if ts is None:
        ts = datetime.now(timezone.utc)

    path = _get_csv_path(ts)
    if not path.exists():
        return

    with _CSV_lock:
        rows = []
        with open(path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                if row.get("signal_id") == signal_id:
                    row["outcome"] = reason
                rows.append(row)

        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=_CSV_HEADER + ["outcome"])
            writer.writeheader()
            writer.writerows(rows)


def flush_all() -> None:
    """Flush all open CSV file handles."""
    with _CSV_lock:
        for fh in _csv_handles.values():
            try:
                fh.flush()
            except Exception:
                pass


def get_today_count() -> int:
    """Return number of signals recorded today."""
    path = _get_csv_path()
    if not path.exists():
        return 0
    with open(path, "r", encoding="utf-8") as f:
        return max(0, sum(1 for _ in f) - 1)


def list_signal_files() -> list[dict]:
    """List all signal CSV files with basic stats."""
    SIGNALS_DIR.mkdir(parents=True, exist_ok=True)
    files = []
    for p in sorted(SIGNALS_DIR.glob("*.csv"), reverse=True):
        with open(p, "r", encoding="utf-8") as f:
            lines = sum(1 for _ in f) - 1
        files.append({"date": p.stem, "count": max(0, lines), "path": str(p)})
    return files
