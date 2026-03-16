"""
test_layer.py
Simulates a Hafiz BUY signal going through the full layered DCA entry system.

- Fetches current XAUUSD price from MT5
- Builds a realistic signal (entry = current price, SL -55p, TP1 +70p, TP2 +120p)
- Adds it to `pending` (as listener.py would)
- Logs to MySQL
- Runs watch_layered_entry() — same path as the live bot

L1 fires immediately (price is already in zone).
L2 fires when price drops 35 pips from L1 entry.
L3 fires when price drops 70 pips from L1 entry.

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


# ── 1. Get current market price ───────────────────────────────────────────────
if not mt5_connect():
    print("❌ Cannot connect to MT5 — is it open as Administrator?")
    sys.exit(1)

symbol_full = "XAUUSD" + MT5_SYMBOL_SUFFIX
tick        = mt5.symbol_info_tick(symbol_full)
mt5.shutdown()

if tick is None:
    print(f"❌ Cannot get price for {symbol_full}")
    sys.exit(1)

entry  = tick.ask
sl     = round(entry - 50 * SL_PIP_SIZE, 2)   # 50 pip SL
tp1    = round(entry + 80 * SL_PIP_SIZE, 2)   # TP1: 80 pips  (RR = 1.6 ✓)
tp2    = round(entry + 140 * SL_PIP_SIZE, 2)  # TP2: 140 pips (free ride target)

print("=" * 55)
print("  SignalBot — Layered DCA Entry TEST")
print("=" * 55)
print(f"  Symbol  : {symbol_full}")
print(f"  Entry   : {entry}  (current ask — L1 fires immediately)")
print(f"  SL      : {sl}  (50 pips below)")
print(f"  TP1     : {tp1}  (80 pips — RR 1.6 ✓ — upper layers)")
print(f"  TP2     : {tp2}  (140 pips — deepest layer free ride)")
print(f"  Max layers configured : {LAYER_COUNT}")
print(f"  L2 trigger  : ~{round(entry - LAYER2_PIPS * SL_PIP_SIZE, 2)}  ({LAYER2_PIPS}p below L1)")
print(f"  L3 trigger  : ~{round(entry - 2 * LAYER2_PIPS * SL_PIP_SIZE, 2)}  ({LAYER2_PIPS*2}p below L1)")
print("=" * 55)
print()

# ── 2. Build signal (mimics what listener.py produces) ────────────────────────
signal_id = uuid.uuid4().hex[:8]
signal = Signal(
    symbol     = "XAUUSD",
    direction  = "buy",
    entry_low  = entry,
    entry_high = entry,
    sl         = sl,
    tps        = [tp1, tp2],
    raw_text   = (
        f"xauusd buy @{entry}\n"
        f"sl {sl}\n"
        f"tp {tp1}\n"
        f"tp {tp2}\n"
        f"[TEST SIGNAL — layered DCA UAT]"
    ),
)

# ── 3. Register in pending + MySQL ────────────────────────────────────────────
pending[signal_id] = signal
upsert_signal(signal_id, signal, status="pending")

print(f"Signal ID : {signal_id}")
print(f"Pending   : {signal_id in pending}")
print()
print("Starting layered watcher — check Telegram for layer notifications.")
print("Press Ctrl+C to stop (open positions stay alive in MT5).")
print()


# ── 4. Run watch_layered_entry ────────────────────────────────────────────────
async def main():
    async with Bot(token=BOT_TOKEN) as bot:
        await watch_layered_entry(signal, signal_id, bot)
    print("\nLayered session complete.")


try:
    asyncio.run(main())
except KeyboardInterrupt:
    print("\nStopped. Check MT5 for open positions.")
    print(f"Dashboard: http://localhost:5000")
