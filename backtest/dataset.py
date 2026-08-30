"""Immutable historical dataset load / save / validate."""

from __future__ import annotations

import csv
import hashlib
import json
import math
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from backtest.interfaces import Candle
from backtest.timeframes import period_seconds

CSV_FIELDS = ("time", "open", "high", "low", "close", "tick_volume", "spread", "real_volume")


@dataclass
class TfValidation:
    timeframe: str
    rows: int
    first: str | None
    last: str | None
    missing_intervals: int
    duplicate_timestamps: int
    ohlc_violations: int
    invalid_prices: int
    unordered: bool
    issues: list[str] = field(default_factory=list)

    @property
    def status_ok(self) -> bool:
        return (
            not self.unordered
            and self.duplicate_timestamps == 0
            and self.ohlc_violations == 0
            and self.invalid_prices == 0
            and self.rows > 0
        )


@dataclass
class DatasetValidation:
    status: str  # VALID | VALID_WITH_GAPS | INVALID
    timeframes: dict[str, TfValidation]
    summary_lines: list[str] = field(default_factory=list)


def _utc_iso(unix_ts: int) -> str:
    return datetime.fromtimestamp(int(unix_ts), tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sanitize_broker_token(server: str) -> str:
    token = re.sub(r"[^A-Za-z0-9]+", "", (server or "unknown").strip()) or "unknown"
    return token[:48]


def dataset_dirname(symbol: str, broker: str, date_from: str, date_to: str) -> str:
    """Build folder name: XAUUSD_<broker>_YYYYMMDD_YYYYMMDD."""
    def _compact(d: str) -> str:
        return d.replace("-", "")[:8]

    return f"{symbol.upper()}_{sanitize_broker_token(broker)}_{_compact(date_from)}_{_compact(date_to)}"


def write_candles_csv(path: Path, candles: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(CSV_FIELDS), extrasaction="ignore")
        w.writeheader()
        for c in candles:
            row = {k: c.get(k, 0) for k in CSV_FIELDS}
            w.writerow(row)


def load_candles_csv(path: Path, timeframe: str = "") -> list[Candle]:
    if not path.is_file():
        raise FileNotFoundError(f"Missing candle file: {path}")
    out: list[Candle] = []
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            spread = row.get("spread")
            tick_vol = row.get("tick_volume")
            real_vol = row.get("real_volume")
            out.append(
                Candle(
                    time=int(float(row["time"])),
                    open=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["close"]),
                    timeframe=timeframe,
                    spread=int(float(spread)) if spread not in (None, "") else None,
                    tick_volume=int(float(tick_vol)) if tick_vol not in (None, "") else None,
                    real_volume=int(float(real_vol)) if real_vol not in (None, "") else None,
                )
            )
    return out


def candles_to_dicts(candles: list[Candle]) -> list[dict]:
    return [
        {"time": c.time, "open": c.open, "high": c.high, "low": c.low, "close": c.close}
        for c in candles
    ]


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def dataset_content_hash(dataset_dir: Path, timeframes: list[str]) -> str:
    """Hash of sorted timeframe CSV contents (deterministic)."""
    h = hashlib.sha256()
    for tf in sorted(t.upper() for t in timeframes):
        p = dataset_dir / f"{tf}.csv"
        h.update(tf.encode())
        h.update(b"\0")
        h.update(file_sha256(p).encode())
        h.update(b"\0")
    return h.hexdigest()


def validate_timeframe_candles(
    candles: list[Candle],
    timeframe: str,
    *,
    expected_from: int | None = None,
    expected_to: int | None = None,
) -> TfValidation:
    issues: list[str] = []
    if not candles:
        return TfValidation(
            timeframe=timeframe,
            rows=0,
            first=None,
            last=None,
            missing_intervals=0,
            duplicate_timestamps=0,
            ohlc_violations=0,
            invalid_prices=0,
            unordered=False,
            issues=["no rows"],
        )

    period = period_seconds(timeframe)
    times = [c.time for c in candles]
    unordered = any(times[i] >= times[i + 1] for i in range(len(times) - 1))
    if unordered:
        issues.append("timestamps not strictly ascending")

    dup = len(times) - len(set(times))
    if dup:
        issues.append(f"duplicate timestamps: {dup}")

    ohlc_violations = 0
    invalid_prices = 0
    for c in candles:
        vals = (c.open, c.high, c.low, c.close)
        if any(not math.isfinite(v) or v <= 0 for v in vals):
            invalid_prices += 1
            continue
        if not (c.low <= c.open <= c.high and c.low <= c.close <= c.high):
            ohlc_violations += 1
    if invalid_prices:
        issues.append(f"invalid/zero prices: {invalid_prices}")
    if ohlc_violations:
        issues.append(f"OHLC consistency violations: {ohlc_violations}")

    missing = 0
    # Broad gap detection: consecutive open deltas that are not multiples of period
    # or larger than period (weekend gaps counted).
    for i in range(len(times) - 1):
        delta = times[i + 1] - times[i]
        if delta <= 0:
            continue
        if delta % period != 0:
            missing += 1
        elif delta > period:
            missing += (delta // period) - 1
    if missing:
        issues.append(f"missing intervals (approx): {missing}")

    if expected_from is not None and times[0] > expected_from + period:
        issues.append(
            f"coverage starts late: first={_utc_iso(times[0])} vs requested_from={_utc_iso(expected_from)}"
        )
    if expected_to is not None and times[-1] + period < expected_to - period:
        issues.append(
            f"coverage ends early: last_close≈{_utc_iso(times[-1] + period)} vs requested_to={_utc_iso(expected_to)}"
        )

    return TfValidation(
        timeframe=timeframe,
        rows=len(candles),
        first=_utc_iso(times[0]),
        last=_utc_iso(times[-1]),
        missing_intervals=missing,
        duplicate_timestamps=dup,
        ohlc_violations=ohlc_violations,
        invalid_prices=invalid_prices,
        unordered=unordered,
        issues=issues,
    )


def validate_dataset(
    dataset_dir: Path,
    timeframes: list[str],
    *,
    expected_from: int | None = None,
    expected_to: int | None = None,
) -> DatasetValidation:
    tf_results: dict[str, TfValidation] = {}
    lines = ["DATASET VALIDATION", ""]
    any_invalid = False
    any_gaps = False

    for tf in timeframes:
        tf_u = tf.upper()
        candles = load_candles_csv(dataset_dir / f"{tf_u}.csv", timeframe=tf_u)
        v = validate_timeframe_candles(
            candles, tf_u, expected_from=expected_from, expected_to=expected_to
        )
        tf_results[tf_u] = v
        lines.append(tf_u)
        lines.append(f"Rows: {v.rows:,}")
        lines.append(f"First: {v.first}")
        lines.append(f"Last: {v.last}")
        lines.append(f"Missing intervals: {v.missing_intervals}")
        if v.issues:
            for iss in v.issues:
                lines.append(f"  - {iss}")
        lines.append("")
        if not v.status_ok:
            any_invalid = True
        elif v.missing_intervals > 0:
            any_gaps = True

    if any_invalid:
        status = "INVALID"
    elif any_gaps:
        status = "VALID_WITH_GAPS"
    else:
        status = "VALID"
    lines.append(f"Result:\n{status}")
    return DatasetValidation(status=status, timeframes=tf_results, summary_lines=lines)


def load_dataset_meta(dataset_dir: Path) -> dict[str, Any]:
    meta_path = dataset_dir / "meta.json"
    if not meta_path.is_file():
        raise FileNotFoundError(f"Missing meta.json in {dataset_dir}")
    with meta_path.open(encoding="utf-8") as f:
        return json.load(f)


def write_dataset_meta(dataset_dir: Path, meta: dict[str, Any]) -> None:
    dataset_dir.mkdir(parents=True, exist_ok=True)
    path = dataset_dir / "meta.json"
    with path.open("w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2, sort_keys=True)
        f.write("\n")


def format_validation_report(result: DatasetValidation) -> str:
    return "\n".join(result.summary_lines)
