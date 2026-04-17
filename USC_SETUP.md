# Account Switching Guide

## Overview

The bot supports four account configurations. All switching is done via `.env` only — no code changes needed. The bot auto-adapts lot sizing, pip values, and contract sizes from live MT5 symbol specs.

| Account type | `ENV_MODE` | Credentials prefix | Current account |
|---|---|---|---|
| Demo STD (testing) | `demo` | `DEMO_MT5_*` | #1067995 — VTMarkets-Demo |
| Live STD (standard USD) | `live` | `LIVE_MT5_*` | — (not set up yet) |
| Live USC (US Cent) | `live` | `LIVE_MT5_*` | #26578318 — VTMarkets-Live3 |

---

## Current `.env` State

```env
ENV_MODE=demo                        # active: demo STD

DEMO_MT5_LOGIN=1067995
DEMO_MT5_PASSWORD=321Trade!@
DEMO_MT5_SERVER=VTMarkets-Demo
DEMO_MT5_SYMBOL_SUFFIX=-VIP
DEMO_MAX_SPREAD_PIPS=5

LIVE_MT5_LOGIN=26578318              # USC live account
LIVE_MT5_PASSWORD=321Trade!@
LIVE_MT5_SERVER=VTMarkets-Live3
LIVE_MT5_SYMBOL_SUFFIX=-STDc         # USC/cent suffix
LIVE_MAX_SPREAD_PIPS=5
```

---

## Scenario 1 — Switch Demo → Live USC

1. Log into account `#26578318` in MT5 terminal (server: `VTMarkets-Live3`)
2. Update `.env`:
   ```env
   ENV_MODE=live
   ```
3. Restart `python bot.py`

No other changes needed. `LIVE_MT5_*` credentials are already set.

---

## Scenario 2 — Switch Live USC → Live STD

When you get a Standard (USD) live account from VT Markets:

1. Log into your new STD account in MT5
2. Check the symbol suffix in MT5 Market Watch (Ctrl+M) — find XAUUSD and note the suffix (e.g. `XAUUSD-STD` → suffix is `-STD`)
3. Update `.env`:
   ```env
   ENV_MODE=live

   LIVE_MT5_LOGIN=YOUR_STD_LOGIN
   LIVE_MT5_PASSWORD=YOUR_STD_PASSWORD
   LIVE_MT5_SERVER=YOUR_STD_SERVER       # e.g. VTMarkets-Live 5
   LIVE_MT5_SYMBOL_SUFFIX=-STD           # update to match what you found
   LIVE_MAX_SPREAD_PIPS=3                # tighter spread for STD accounts
   ```
4. Restart `python bot.py`

---

## Scenario 3 — Switch Live STD → Live USC (rollback)

1. Log into account `#26578318` in MT5 terminal (server: `VTMarkets-Live3`)
2. Update `.env`:
   ```env
   ENV_MODE=live

   LIVE_MT5_LOGIN=26578318
   LIVE_MT5_PASSWORD=321Trade!@
   LIVE_MT5_SERVER=VTMarkets-Live3
   LIVE_MT5_SYMBOL_SUFFIX=-STDc
   LIVE_MAX_SPREAD_PIPS=5
   ```
3. Restart `python bot.py`

---

## Scenario 4 — Switch to Demo for Testing

Any time you want to test on demo before going live:

1. Log into account `#1067995` in MT5 terminal (server: `VTMarkets-Demo`)
2. Update `.env`:
   ```env
   ENV_MODE=demo
   ```
3. Restart `python bot.py`

Demo credentials (`DEMO_MT5_*`) are already saved — no other changes needed.

---

## How to Find the Symbol Suffix

1. Open MT5 with the account you're switching to
2. Go to **Market Watch** (Ctrl+M)
3. Find XAUUSD — the full name shows the suffix (e.g. `XAUUSD-STD`)
4. The suffix is everything after `XAUUSD` (e.g. `-STD`, `-STDc`, `-VIP`)

If XAUUSD is not visible in Market Watch, right-click → Show All, then search for it.

---

## STD vs USC — Key Differences

| | Standard (STD) | USC (US Cent) |
|---|---|---|
| Balance unit | USD | US Cents (100 USC = $1 USD) |
| $100 deposit | $100 balance | 10,000 USC balance |
| Min lot risk | 0.01 lot ≈ $1/pip | 0.01 lot ≈ $0.01/pip |
| Risk % logic | Same | Same (scales to cent denomination) |
| Typical suffix | `-STD` | `-STDc` |
| Typical server | VTMarkets-Live 5 | VTMarkets-Live3 |

The bot's lot calculation uses live MT5 tick values (`symbol_info().trade_tick_value`) — it auto-adapts to USD or cent denomination without any code changes.

---

## Troubleshooting

| Problem | Fix |
|---|---|
| `Symbol XAUUSD not found` | Wrong `MT5_SYMBOL_SUFFIX` — check Market Watch and update `.env` |
| MT5 login failed | Wrong `MT5_SERVER` — must match exactly (including spaces and capitalisation) |
| Lot calculated as 0 | Account balance too low for `MIN_LOT` with current `RISK_PERCENT` — lower `RISK_PERCENT` or increase balance |
| Spread always blocked | USC accounts can have wider spreads — raise `LIVE_MAX_SPREAD_PIPS` to `5` |
| Wrong account shown in dashboard | `ENV_MODE` mismatch — check `.env` and restart bot |
