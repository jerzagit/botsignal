"""
test_tp_split.py
Test TP splitting — SELL XAUUSD @5023-5026, SL 5029, TP1 5018, TP2 5016

Expected: L1 places 2 sub-orders (0.06 × 2 instead of 0.12 × 1)
  Sub-order 1 → TP1 5018
  Sub-order 2 → TP2 5016

Run:  python test_tp_split.py
Stop: Ctrl+C
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


# ── 1. Verify MT5 connection + get live price ──────────────────────────────
if not mt5_connect():
    print("❌ Cannot connect to MT5 — is it open as Administrator?")
    sys.exit(1)

symbol_full = "XAUUSD" + MT5_SYMBOL_SUFFIX
tick        = mt5.symbol_info_tick(symbol_full)
mt5.shutdown()

if tick is None:
    print(f"❌ Cannot get price for {symbol_full}")
    sys.exit(1)

current_price = tick.bid  # sell uses bid

# ── 2. Build sell signal (same as Hafiz's) ──────────────────────────────────
ENTRY_LOW  = 5023.0
ENTRY_HIGH = 5026.0
entry_mid  = (ENTRY_LOW + ENTRY_HIGH) / 2   # 5024.5
sl         = 5029.0
tp1        = 5018.0
tp2        = 5016.0

sl_pips = round(abs(entry_mid - sl) / SL_PIP_SIZE, 1)
tp_pips = round(abs(tp1 - entry_mid) / SL_PIP_SIZE, 1)
rr      = round(tp_pips / sl_pips, 2)

print("=" * 58)
print("  SignalBot — TP Split TEST (SELL)")
print("=" * 58)
print(f"  Symbol       : {symbol_full}")
print(f"  Current price: {current_price}")
print(f"  Entry zone   : {ENTRY_LOW} – {ENTRY_HIGH}")
print(f"  SL           : {sl}  ({sl_pips}p)")
print(f"  TP1          : {tp1}  ({tp_pips}p)")
print(f"  TP2          : {tp2}")
print(f"  RR           : {rr}  ({'✓' if rr >= 1.4 else '✗'})")
print(f"  Layer gap    : {LAYER2_PIPS}p  |  Max: {LAYER_COUNT}")
print("=" * 58)
print()

# ── 3. Build signal ─────────────────────────────────────────────────────────
signal_id = uuid.uuid4().hex[:8]
signal = Signal(
    symbol     = "XAUUSD",
    direction  = "sell",
    entry_low  = ENTRY_LOW,
    entry_high = ENTRY_HIGH,
    sl         = sl,
    tps        = [tp1, tp2],
    raw_text   = (
        f"xauusd sell @{ENTRY_LOW}-{ENTRY_HIGH}\n"
        f"sl {sl}\n"
        f"tp {tp1}\n"
        f"tp {tp2}\n"
        f"[TEST — TP split]"
    ),
)

# ── 4. Register in pending + MySQL ──────────────────────────────────────────
pending[signal_id] = signal
upsert_signal(signal_id, signal, status="pending")

print(f"Zone: {ENTRY_LOW}-{ENTRY_HIGH} | SL: {sl} ({sl_pips}p) | TP1: {tp1} ({tp_pips}p) | RR: {rr}")
print(f"Signal ID : {signal_id}")
print(f"LAYER_MODE: True")
print()
print("Starting layered watcher with TP splitting...")
print("Expected: L1 → 2 sub-orders (one at TP1, one at TP2)")
print("Press Ctrl+C to stop.")
print()


# ── 5. Run watcher ──────────────────────────────────────────────────────────
async def main():
    async with Bot(token=BOT_TOKEN) as bot:
        await watch_layered_entry(signal, signal_id, bot)
    print("\nSession complete.")


try:
    asyncio.run(main())
except KeyboardInterrupt:
    print("\nStopped. Check MT5 for open positions.")
