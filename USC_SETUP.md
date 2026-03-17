# Switching to USC (US Cent) Account

## Overview

The bot auto-adapts to any MT5 account type. Switching to USC only requires changing credentials in `.env`.

---

## Step 1: Get Your USC Account Details

From your broker (VT Markets or similar), note down:
- **Login** (account number)
- **Password**
- **Server name** (e.g. `VTMarkets-Live 3` or similar)

## Step 2: Find the Symbol Suffix

1. Open MT5 with your USC account
2. Go to **Market Watch** (Ctrl+M)
3. Find XAUUSD — note the suffix (e.g. `XAUUSD-USC`, `XAUUSD.c`, `XAUUSDc`)
4. The suffix is everything after `XAUUSD` (e.g. `-USC`, `.c`, `c`)

## Step 3: Check Min Lot

1. In MT5 Market Watch, right-click your XAUUSD symbol
2. Click **Specification**
3. Note **Minimal Volume** (usually `0.01` or `0.1` for cent accounts)
4. Note **Volume Step** (usually `0.01`)

## Step 4: Update `.env`

Change these lines in your `.env` file:

```env
# ── LIVE account (USC) ─────────────────────────────────
LIVE_MT5_LOGIN=YOUR_USC_LOGIN
LIVE_MT5_PASSWORD=YOUR_USC_PASSWORD
LIVE_MT5_SERVER=YOUR_USC_SERVER
LIVE_MT5_SYMBOL_SUFFIX=-USC          # <-- whatever suffix you found in Step 2
LIVE_MAX_SPREAD_PIPS=3
```

If the min lot is different from `0.01`:
```env
MIN_LOT=0.01    # <-- change to match MT5 Specification
```

## Step 5: Verify SL Pip Size

Check that XAUUSD price format is the same (e.g. `3000.xx`):
- If yes — no change needed (`SL_PIP_SIZE=0.1` stays)
- If price shows extra decimals (e.g. `3000.xxx`) — change `SL_PIP_SIZE=0.01`

## Step 6: Test on Demo First

```env
ENV_MODE=demo
```

1. Restart bot: `python bot.py`
2. Send `/buynow` in Telegram
3. Check MT5 — trade should appear with correct lot, SL, TP
4. Check dashboard — trade should show up

## Step 7: Go Live

```env
ENV_MODE=live
```

Restart bot. Done.

---

## What Auto-Adapts (No Changes Needed)

| Component | How It Works |
|-----------|-------------|
| Lot calculation | Uses `symbol_info().trade_tick_value` from MT5 |
| Contract size | Uses `symbol_info().trade_contract_size` from MT5 |
| Volume step | Uses `symbol_info().volume_step` from MT5 |
| Pip value | Uses `symbol_info().trade_tick_value` from MT5 |
| All 6 guards | Work on pips/percentages — account-type agnostic |
| DCA layers | Same logic — scales with margin |
| Profit Lock | Same — triggers on pip distance |

## USC vs Standard — Key Differences

| | Standard | USC (Cent) |
|---|---------|-----------|
| Balance unit | USD | US Cents (100 USC = $1) |
| $100 deposit | $100 balance | 10,000 USC balance |
| Min lot meaning | 0.01 lot = ~$1/pip | 0.01 lot = ~1 cent/pip |
| Risk per trade | Same % logic | Same % logic (just smaller $) |

The bot's risk % logic works identically — 10% of margin is 10% whether it's dollars or cents.

---

## Rollback to Standard

Just change the LIVE credentials back to your Standard account values and set `ENV_MODE=live`. Restart bot.
