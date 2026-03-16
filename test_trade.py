"""
test_trade.py
Places a real 0.01 BUY on XAUUSD-STD at current market price.
  SL  = entry - 50 pips (5.0 pts)
  TP  = entry + 50 pips (5.0 pts)
Records the signal + trade in MySQL so it shows on the dashboard.

Run: python test_trade.py
"""

import sys, os
sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv
load_dotenv()

import uuid
import MetaTrader5 as mt5

from core.mt5    import mt5_connect
from core.signal import Signal
from core.mt5    import execute_trade
from core.db     import upsert_signal
from core.config import MT5_SYMBOL_SUFFIX, SL_PIP_SIZE

symbol     = "XAUUSD" + MT5_SYMBOL_SUFFIX
pip_size   = SL_PIP_SIZE          # 0.1 price units per pip
sl_pips    = 50                    # 50 pip SL
tp_pips    = 80                    # 80 pip TP

# ── Get current market price ──────────────────────────────────────────────────
if not mt5_connect():
    print("❌ Cannot connect to MT5")
    sys.exit(1)

tick = mt5.symbol_info_tick(symbol)
mt5.shutdown()

if tick is None:
    print(f"❌ Cannot get price for {symbol}")
    sys.exit(1)

entry = tick.ask                           # BUY fills at ask
sl    = round(entry - sl_pips * pip_size, 2)
tp    = round(entry + tp_pips * pip_size, 2)

print(f"Symbol : {symbol}")
print(f"Entry  : {entry}")
print(f"SL     : {sl}  ({sl_pips} pips below)")
print(f"TP     : {tp}  ({tp_pips} pips above)")
print()

# ── Build a Signal matching Hafiz's format ────────────────────────────────────
signal_id = uuid.uuid4().hex[:8]
signal = Signal(
    symbol      = "XAUUSD",
    direction   = "buy",
    entry_low   = entry,
    entry_high  = entry,
    sl          = sl,
    tps         = [tp],
    raw_text    = f"xauusd buy @{entry}\nsl {sl}\ntp {tp}\n[TEST TRADE]",
)

# ── Log signal to MySQL (shows in dashboard as 'pending' → 'executed') ────────
upsert_signal(signal_id, signal, status="pending")

# ── Execute trade on MT5 (also logs to MySQL trades table) ───────────────────
print("Placing order...")
result = execute_trade(signal, signal_id)
print(result)

# ── Mark signal as executed ───────────────────────────────────────────────────
upsert_signal(signal_id, signal, status="executed")
print(f"\nSignal ID: {signal_id} — check dashboard at http://localhost:5000")
