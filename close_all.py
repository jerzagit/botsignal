"""Close all open positions."""
import sys
sys.path.insert(0, '.')

from core.mt5 import mt5_connect, close_position
import MetaTrader5 as mt5

mt5.initialize()
connected = mt5_connect()
if not connected:
    print("Failed to connect to MT5")
    sys.exit(1)

positions = mt5.positions_get()
print(f"Found {len(positions)} open positions")

closed = 0
for pos in positions:
    result = close_position(pos.ticket)
    if "closed" in result.lower():
        closed += 1
        print(f"Closed {pos.ticket}: {pos.symbol} {pos.volume} lots @ {pos.price_open}")
    else:
        print(f"Failed to close {pos.ticket}: {result}")

print(f"\nTotal closed: {closed}/{len(positions)}")
mt5.shutdown()