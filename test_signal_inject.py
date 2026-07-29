"""
test_signal_inject.py
Simulate a Hafiz signal and execute it immediately (skips proximity guard).
Usage: python test_signal_inject.py
"""

from core.signal import parse_signal
from core.mt5    import execute_trade

RAW = """xauusd sell @4075-4085

sl 4090

tp 4065
tp 4055

Trade At Your Own Risk❗️"""

signal = parse_signal(RAW)

if not signal:
    print("❌ Signal parse FAILED — check the format.")
else:
    print(f"[OK] Parsed: {signal.symbol} {signal.direction.upper()} "
          f"zone={signal.entry_low}-{signal.entry_high} "
          f"SL={signal.sl} TPs={signal.tps}")

    result = execute_trade(signal, signal_id="test_inject_01", skip_proximity=True)
    print("\n" + result)