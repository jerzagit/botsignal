"""
Download XAUUSD (or other) historical candles from the configured MT5 broker.

Usage:
  set RUN_MODE=BACKTEST
  python -m backtest.download_history --symbol XAUUSD --from 2026-01-01 --to 2026-08-01 --timeframes M15,H1,H4
  python -m backtest.download_history --symbol XAUUSD --from 2026-01-01 --to 2026-08-01 --timeframes M1 \\
      --into data/backtests/datasets/XAUUSD_EightcapDemo_20260101_20260801

Soft-attaches to the already-open MT5 terminal (no password, no order_send).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backtest.dataset import (
    dataset_content_hash,
    dataset_dirname,
    format_validation_report,
    load_dataset_meta,
    sanitize_broker_token,
    validate_dataset,
    write_candles_csv,
    write_dataset_meta,
)
from backtest.gitmeta import git_branch, git_commit
from backtest.safety import assert_backtest_safe
from backtest.timeframes import PERIOD_SECONDS

_TF_NAME_TO_ATTR = {
    "M1": "TIMEFRAME_M1",
    "M5": "TIMEFRAME_M5",
    "M15": "TIMEFRAME_M15",
    "M30": "TIMEFRAME_M30",
    "H1": "TIMEFRAME_H1",
    "H4": "TIMEFRAME_H4",
    "D1": "TIMEFRAME_D1",
}


def _parse_date(s: str) -> datetime:
    s = s.strip()
    for fmt in ("%Y-%m-%d", "%Y%m%d", "%Y-%m-%dT%H:%M:%S"):
        try:
            dt = datetime.strptime(s, fmt)
            return dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    raise argparse.ArgumentTypeError(f"Invalid date: {s!r} (use YYYY-MM-DD)")


def _rates_to_rows(rates) -> list[dict]:
    rows = []
    for r in rates:
        rows.append(
            {
                "time": int(r["time"] if hasattr(r, "dtype") else r[0]),
                "open": float(r["open"] if hasattr(r, "dtype") else r[1]),
                "high": float(r["high"] if hasattr(r, "dtype") else r[2]),
                "low": float(r["low"] if hasattr(r, "dtype") else r[3]),
                "close": float(r["close"] if hasattr(r, "dtype") else r[4]),
                "tick_volume": int(r["tick_volume"] if hasattr(r, "dtype") else r[5]),
                "spread": int(r["spread"] if hasattr(r, "dtype") else r[6]),
                "real_volume": int(r["real_volume"] if hasattr(r, "dtype") else r[7]),
            }
        )
    return rows


def _fetch_rates_chunked(mt5, symbol_mt5: str, tf_const: int, dt_from: datetime, dt_to: datetime, tf_name: str):
    """
    copy_rates_range can fail for large M1 ranges (-2 Invalid params).
    Fetch month-sized chunks and concatenate.
    """
    chunk_days = 14 if tf_name.upper() == "M1" else 120
    all_rows: list[dict] = []
    seen = set()
    cur = dt_from
    while cur < dt_to:
        nxt = min(cur + timedelta(days=chunk_days), dt_to)
        print(f"[download] {tf_name} chunk {cur.date()} → {nxt.date()}...", flush=True)
        rates = mt5.copy_rates_range(symbol_mt5, tf_const, cur, nxt)
        if rates is None or len(rates) == 0:
            # try smaller weekly chunk for M1
            if tf_name.upper() == "M1" and chunk_days > 7:
                mid = min(cur + timedelta(days=7), dt_to)
                rates = mt5.copy_rates_range(symbol_mt5, tf_const, cur, mid)
                if rates is None or len(rates) == 0:
                    err = mt5.last_error()
                    print(f"[download] WARN empty {cur.date()}-{mid.date()}: {err}", flush=True)
                    cur = mid
                    continue
                rows = _rates_to_rows(rates)
                for r in rows:
                    if r["time"] not in seen:
                        seen.add(r["time"])
                        all_rows.append(r)
                cur = mid
                continue
            err = mt5.last_error()
            print(f"[download] WARN empty {cur.date()}-{nxt.date()}: {err}", flush=True)
            cur = nxt
            continue
        rows = _rates_to_rows(rates)
        for r in rows:
            if r["time"] not in seen:
                seen.add(r["time"])
                all_rows.append(r)
        cur = nxt
    all_rows.sort(key=lambda r: r["time"])
    return all_rows


def _mt5_soft_init():
    import MetaTrader5 as mt5
    from core.config import MT5_PATH

    ok = bool(mt5.initialize(path=MT5_PATH)) if MT5_PATH else False
    if not ok:
        ok = bool(mt5.initialize())
    if not ok:
        raise RuntimeError(f"MT5 initialize failed: {mt5.last_error()}. Open terminal first.")
    return mt5


def download_history(
    symbol: str,
    date_from: datetime,
    date_to: datetime,
    timeframes: list[str],
    *,
    out_root: Path | None = None,
    into: Path | None = None,
) -> Path:
    assert_backtest_safe("download_history")
    mt5 = _mt5_soft_init()
    from core.config import MT5_SERVER, MT5_SYMBOL_SUFFIX

    if date_to <= date_from:
        raise ValueError("--to must be after --from")
    for tf in timeframes:
        if tf.upper() not in PERIOD_SECONDS:
            raise ValueError(f"Unsupported timeframe: {tf}")

    try:
        account = mt5.account_info()
        server = (account.server if account else None) or MT5_SERVER or "unknown"
        login = int(account.login) if account else None
        symbol_mt5 = symbol if symbol.endswith(MT5_SYMBOL_SUFFIX or "") else symbol + (MT5_SYMBOL_SUFFIX or "")

        info = mt5.symbol_info(symbol_mt5)
        if info is None:
            if not mt5.symbol_select(symbol_mt5, True):
                raise RuntimeError(f"Symbol not available: {symbol_mt5}")
        else:
            mt5.symbol_select(symbol_mt5, True)

        dt_from = date_from.astimezone(timezone.utc).replace(tzinfo=None)
        dt_to = date_to.astimezone(timezone.utc).replace(tzinfo=None)

        if into is not None:
            dataset_dir = Path(into)
            dataset_dir.mkdir(parents=True, exist_ok=True)
            existing = {}
            if (dataset_dir / "meta.json").is_file():
                existing = load_dataset_meta(dataset_dir)
            dirname = existing.get("dataset_id") or dataset_dir.name
        else:
            out_root = out_root or (ROOT / "data" / "backtests" / "datasets")
            dirname = dataset_dirname(
                symbol,
                sanitize_broker_token(server),
                date_from.strftime("%Y%m%d"),
                date_to.strftime("%Y%m%d"),
            )
            dataset_dir = out_root / dirname
            dataset_dir.mkdir(parents=True, exist_ok=True)
            existing = {}

        row_counts: dict[str, int] = dict(existing.get("row_counts") or {})
        for tf in timeframes:
            tf_u = tf.upper()
            print(f"[download] fetching {tf_u}...", flush=True)
            attr = _TF_NAME_TO_ATTR[tf_u]
            tf_const = getattr(mt5, attr)
            rows = _fetch_rates_chunked(mt5, symbol_mt5, tf_const, dt_from, dt_to, tf_u)
            if not rows:
                raise RuntimeError(f"No rates for {symbol_mt5} {tf_u}: {mt5.last_error()}")
            write_candles_csv(dataset_dir / f"{tf_u}.csv", rows)
            row_counts[tf_u] = len(rows)
            print(f"[download] {tf_u}: {len(rows):,} rows", flush=True)

        all_tfs = sorted(set([*(existing.get("timeframes") or []), *[t.upper() for t in timeframes]]))
        # Validate only newly downloaded + existing CSVs that exist
        present = [tf for tf in all_tfs if (dataset_dir / f"{tf}.csv").is_file()]
        expected_from = int(date_from.timestamp())
        expected_to = int(date_to.timestamp())
        validation = validate_dataset(
            dataset_dir,
            present,
            expected_from=expected_from,
            expected_to=expected_to,
        )
        print(format_validation_report(validation))
        if validation.status == "INVALID":
            raise RuntimeError("Dataset validation INVALID — refusing to finalize meta.")

        content_hash = dataset_content_hash(dataset_dir, present)
        meta = {
            **existing,
            "symbol": symbol.upper(),
            "symbol_mt5": symbol_mt5,
            "broker_server": server,
            "broker_login_id": login,
            "date_from": existing.get("date_from") or date_from.strftime("%Y-%m-%d"),
            "date_to": existing.get("date_to") or date_to.strftime("%Y-%m-%d"),
            "timezone": existing.get("timezone")
            or {
                "candle_time_basis": "MT5 rate time = bar OPEN Unix seconds",
                "canonical_storage": "UTC (Unix epoch)",
            },
            "timeframes": present,
            "row_counts": row_counts,
            "download_timestamp_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "data_checksum_sha256": content_hash,
            "validation_status": validation.status,
            "git_branch": git_branch(ROOT),
            "git_commit": git_commit(ROOT),
            "dataset_id": dirname,
        }
        write_dataset_meta(dataset_dir, meta)
        print(f"\nDataset written: {dataset_dir}")
        print(f"Checksum: {content_hash}")
        return dataset_dir
    finally:
        try:
            mt5.shutdown()
        except Exception:
            pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Download historical candles for backtest datasets.")
    parser.add_argument("--symbol", default="XAUUSD")
    parser.add_argument("--from", dest="date_from", required=True, type=_parse_date)
    parser.add_argument("--to", dest="date_to", required=True, type=_parse_date)
    parser.add_argument("--timeframes", default="M15,H1,H4")
    parser.add_argument("--out-root", default=None)
    parser.add_argument(
        "--into",
        default=None,
        help="Append timeframes into an existing dataset directory",
    )
    args = parser.parse_args(argv)

    if not os.getenv("RUN_MODE"):
        os.environ["RUN_MODE"] = "BACKTEST"
    assert_backtest_safe("download_history.main")

    tfs = [t.strip().upper() for t in args.timeframes.split(",") if t.strip()]
    download_history(
        args.symbol.upper(),
        args.date_from,
        args.date_to,
        tfs,
        out_root=Path(args.out_root) if args.out_root else None,
        into=Path(args.into) if args.into else None,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
