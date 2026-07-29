"""
test_signal_inject.py

Safe Hafiz signal smoke test.

Default mode is parse-only and will NOT send an order to MT5.
Live execution requires both:
  1. python test_signal_inject.py --live
  2. CONFIRM_LIVE_INJECT=YES
"""

import argparse
import os

from core.signal import parse_signal

RAW = """xauusd sell @4075-4085

sl 4090

tp 4065
tp 4055

Trade At Your Own Risk"""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--live",
        action="store_true",
        help="Actually execute the parsed signal on MT5. Requires CONFIRM_LIVE_INJECT=YES.",
    )
    args = parser.parse_args()

    signal = parse_signal(RAW)
    if not signal:
        print("Signal parse FAILED - check the format.")
        return 1

    print(
        f"[OK] Parsed: {signal.symbol} {signal.direction.upper()} "
        f"zone={signal.entry_low}-{signal.entry_high} "
        f"SL={signal.sl} TPs={signal.tps}"
    )

    if not args.live:
        print("[SAFE] Dry run only. No MT5 order was sent.")
        return 0

    if os.getenv("CONFIRM_LIVE_INJECT") != "YES":
        print("Blocked live inject. Set CONFIRM_LIVE_INJECT=YES and pass --live to execute.")
        return 2

    from core.mt5 import execute_trade

    result = execute_trade(signal, signal_id="test_inject_01", skip_proximity=True)
    print("\n" + result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
