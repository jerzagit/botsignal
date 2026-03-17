"""
test_layer.py
Simulates a Hafiz BUY signal going through the full layered DCA entry system.

Signal: BUY XAUUSD 4996–5000  |  SL 50 pips  |  TP 80 pips
  entry_mid = 4998.0
  SL  = 4993.0  (50p below mid)
  TP  = 5006.0  (80p above mid)
  RR  = 1.6 ✓

Watcher waits for price to drop into zone (≤5000), then L1 fires.


Run:  python test_layer.py
Stop: Ctrl+C  (positions stay alive in MT5)
"""

import sys
import os
import uuid
import asyncio
import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    stream=sys.stdout,
)

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv
load_dotenv()

import MetaTrader5 as mt5

from telegram import Bot

from core.mt5           import mt5_connect
from core.signal        import Signal
from core.config        import MT5_SYMBOL_SUFFIX, SL_PIP_SIZE, LAYER_COUNT, LAYER2_PIPS, \
                               BOT_TOKEN
from core.db            import upsert_signal
from core.state         import pending
from core.layer_watcher import watch_layered_entry


# ── 1. Verify MT5 connection + get live price for info ────────────────────────
if not mt5_connect():
    print("❌ Cannot connect to MT5 — is it open as Administrator?")
    sys.exit(1)

symbol_full = "XAUUSD" + MT5_SYMBOL_SUFFIX
tick        = mt5.symbol_info_tick(symbol_full)
mt5.shutdown()

if tick is None:
    print(f"❌ Cannot get price for {symbol_full}")
    sys.exit(1)

current_price = tick.ask

# ── 2. Build signal exactly as Hafiz would send it ────────────────────────────
#    BUY @ 5006  |  SL 5000  |  TP 5020  |  RR=2.33
ENTRY_LOW  = 5006.0
ENTRY_HIGH = 5006.0
entry_mid  = 5006.0
sl         = 5000.0
tp1        = 5020.0

sl_pips = round((entry_mid - sl)  / SL_PIP_SIZE, 1)
tp_pips = round((tp1 - entry_mid) / SL_PIP_SIZE, 1)
rr      = round(tp_pips / sl_pips, 2)

# Layer trigger preview
l2_trigger = round(ENTRY_HIGH - LAYER2_PIPS * SL_PIP_SIZE, 2)

print("=" * 58)
print("  SignalBot — Layered DCA Entry TEST")
print("=" * 58)
print(f"  Symbol       : {symbol_full}")
print(f"  Current price: {current_price}  (watcher waits for zone)")
print(f"  Entry zone   : {ENTRY_LOW} – {ENTRY_HIGH}  (mid {entry_mid})")
print(f"  SL           : {sl}  ({sl_pips}p below mid)")
print(f"  TP           : {tp1}  ({tp_pips}p above mid)")
print(f"  RR           : {rr}  ({'✓ PASS' if rr >= 1.4 else '✗ FAIL — guard will block'}  min=1.4)")
print(f"  Layer gap    : {LAYER2_PIPS} pips  |  Max configured: {LAYER_COUNT}")
print(f"  L1 fires at  : ≤{ENTRY_HIGH}  (price enters zone)")
print(f"  L2 fires at  : ~{l2_trigger}  ({LAYER2_PIPS}p below L1)")
print("=" * 58)
print()

# ── 3. Build signal (mimics what listener.py produces) ────────────────────────
signal_id = uuid.uuid4().hex[:8]
signal = Signal(
    symbol     = "XAUUSD",
    direction  = "buy",
    entry_low  = ENTRY_LOW,
    entry_high = ENTRY_HIGH,
    sl         = sl,
    tps        = [tp1],
    raw_text   = (
        f"xauusd buy @{ENTRY_HIGH}-{ENTRY_LOW}\n"
        f"sl {sl}\n"
        f"tp {tp1}\n"
        f"[TEST SIGNAL — layered DCA UAT]"
    ),
)

# ── 4. Register in pending + MySQL ────────────────────────────────────────────
pending[signal_id] = signal
upsert_signal(signal_id, signal, status="pending")

print(f"Signal ID : {signal_id}")
print(f"Pending   : {signal_id in pending}")
print()
print("Starting layered watcher — check Telegram for layer notifications.")
print("Press Ctrl+C to stop (open positions stay alive in MT5).")
print()


# ── 5. Run watch_layered_entry ────────────────────────────────────────────────
async def main():
    async with Bot(token=BOT_TOKEN) as bot:
        await watch_layered_entry(signal, signal_id, bot)
    print("\nLayered session complete.")


try:
    asyncio.run(main())
except KeyboardInterrupt:
    print("\nStopped. Check MT5 for open positions.")
    print(f"Dashboard: http://localhost:5000")
