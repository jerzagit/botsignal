"""Basket TP watcher — closes ALL open positions when total floating PnL >= target."""
import sys
import time
from datetime import datetime

sys.path.insert(0, ".")

import MetaTrader5 as mt5

from core.mt5 import mt5_connect, close_position

TARGET_USD = 200.0
CHECK_INTERVAL = 5


def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def main() -> None:
    if not mt5.initialize() or not mt5_connect():
        print(f"[{now()}] MT5 connect failed — exiting")
        sys.exit(1)

    print(f"[{now()}] Basket TP watcher started | target=+${TARGET_USD:.2f} | interval={CHECK_INTERVAL}s")

    try:
        while True:
            positions = mt5.positions_get()
            if not positions:
                time.sleep(CHECK_INTERVAL)
                continue

            total = sum(p.profit for p in positions) + sum(
                getattr(p, "swap", 0.0) for p in positions
            )
            print(f"[{now()}] open={len(positions)} floating={total:+.2f} / +{TARGET_USD:.2f}")

            if total >= TARGET_USD:
                print(f"[{now()}] *** TARGET HIT {total:+.2f} — CLOSING ALL ***")
                closed = 0
                for p in positions:
                    result = close_position(p.ticket)
                    ok = "closed" in str(result).lower()
                    closed += ok
                    print(f"[{now()}] close #{p.ticket} {p.symbol} {p.volume} -> {'OK' if ok else result}")
                print(f"[{now()}] Basket TP done: closed {closed}/{len(positions)}")
                break
            time.sleep(CHECK_INTERVAL)
    finally:
        mt5.shutdown()


if __name__ == "__main__":
    main()
