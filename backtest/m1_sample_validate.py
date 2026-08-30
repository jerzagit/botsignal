"""
Optional M1 sample validation around executed trades (no baseline replacement).

Usage (requires MT5 + prior simulation trades.jsonl):
  set RUN_MODE=BACKTEST
  python -m backtest.m1_sample_validate \
    --run-dir data/backtests/BT_... \
    --symbol XAUUSD \
    --wins 10 --losses 10
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

from backtest.safety import assert_backtest_safe


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--symbol", default="XAUUSD")
    parser.add_argument("--wins", type=int, default=10)
    parser.add_argument("--losses", type=int, default=10)
    args = parser.parse_args(argv)
    if not os.getenv("RUN_MODE"):
        os.environ["RUN_MODE"] = "BACKTEST"
    assert_backtest_safe("m1_sample_validate")

    run_dir = Path(args.run_dir)
    trades = [
        json.loads(l)
        for l in (run_dir / "trades.jsonl").read_text(encoding="utf-8").splitlines()
        if l.strip()
    ]
    wins = [t for t in trades if t.get("outcome") == "WIN"][: args.wins]
    losses = [t for t in trades if t.get("outcome") == "LOSS"][: args.losses]
    sample = wins + losses
    if not sample:
        print("No trades to sample")
        return 1

    import MetaTrader5 as mt5
    from core.config import MT5_PATH, MT5_SYMBOL_SUFFIX

    ok = bool(mt5.initialize(path=MT5_PATH)) if MT5_PATH else False
    if not ok:
        ok = bool(mt5.initialize())
    if not ok:
        raise RuntimeError(f"MT5 initialize failed: {mt5.last_error()}")

    try:
        sym = args.symbol if args.symbol.endswith(MT5_SYMBOL_SUFFIX or "") else args.symbol + (MT5_SYMBOL_SUFFIX or "")
        findings = []
        for t in sample:
            ent = datetime.strptime(t["entry_time"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
            ex = (
                datetime.strptime(t["exit_time"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
                if t.get("exit_time")
                else ent + timedelta(hours=6)
            )
            rates = mt5.copy_rates_range(
                sym,
                mt5.TIMEFRAME_M1,
                ent - timedelta(hours=1),
                ex + timedelta(hours=1),
            )
            n = 0 if rates is None else len(rates)
            # Check if both SL and TP appear in M1 range ordering vs M15 conservative result
            sl, tp = t["sl"], t["tp"]
            direction = t["direction"]
            first = None
            if rates is not None:
                for r in rates:
                    hi, lo = float(r["high"]), float(r["low"])
                    if direction == "buy":
                        hit_sl = lo <= sl
                        hit_tp = hi >= tp
                    else:
                        hit_sl = hi >= sl
                        hit_tp = lo <= tp
                    if hit_sl and hit_tp:
                        first = "AMBIGUOUS_M1"
                        break
                    if hit_sl:
                        first = "SL_FIRST"
                        break
                    if hit_tp:
                        first = "TP_FIRST"
                        break
            findings.append(
                {
                    "trade_id": t["trade_id"],
                    "m15_outcome": t.get("outcome"),
                    "m1_bars": n,
                    "m1_first_touch": first,
                    "agreement": (
                        (first == "SL_FIRST" and t.get("outcome") == "LOSS")
                        or (first == "TP_FIRST" and t.get("outcome") == "WIN")
                        or first is None
                    ),
                }
            )
        out = {
            "sample_size": len(sample),
            "wins": len(wins),
            "losses": len(losses),
            "findings": findings,
            "note": "Diagnostic only — does not replace M15 baseline results.",
        }
        out_path = run_dir / "m1_sample_validation.json"
        out_path.write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
        agree = sum(1 for f in findings if f["agreement"])
        print(f"Wrote {out_path}")
        print(f"M1 sample agreements with M15 outcome: {agree}/{len(findings)}")
        return 0
    finally:
        try:
            mt5.shutdown()
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
