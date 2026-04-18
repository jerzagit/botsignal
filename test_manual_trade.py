import sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

"""Test /buynow command - direct execute."""
import sys
sys.path.insert(0, '.')

from core.signal import Signal
from core.mt5 import execute_trade
from core.watcher import _get_price

MANUAL_SYMBOL = "XAUUSD"
MANUAL_SL_PIPS = 50
MANUAL_TP1_PIPS = 50
MANUAL_TP2_PIPS = 80

symbol = MANUAL_SYMBOL + "-STDc"
price = _get_price(symbol, "buy")
print(f"Current {symbol} price: {price}")

entry = price
sl = price - (MANUAL_SL_PIPS * 0.1)
tp1 = price + (MANUAL_TP1_PIPS * 0.1)
tp2 = price + (MANUAL_TP2_PIPS * 0.1)

signal = Signal(
    symbol=MANUAL_SYMBOL,
    direction="buy",
    entry_low=entry,
    entry_high=entry,
    sl=sl,
    tps=[tp1, tp2],
    raw_text="manual test",
)

print(f"Executing: {signal.symbol} {signal.direction.upper()} @ {entry} SL={sl} TP1={tp1} TP2={tp2}")
result = execute_trade(signal, signal_id="manual_test_01", skip_proximity=True)
print(f"Result: {result}")