# SignalBot — Setup & Run Guide

## Credentials Checklist

| Field | Status |
|---|---|
| `TG_API_ID` | ✅ Done |
| `TG_API_HASH` | ✅ Done |
| `BOT_TOKEN` | ✅ Done — @Hafiz_Carat_Signal_Bot |
| `YOUR_CHAT_ID` | ✅ Done |
| `SIGNAL_GROUP` | ✅ Done — PIPS FIGHTER 2026 (`-1002083967629`) |
| `MT5_PATH` | ✅ Done — VT Markets MT5 terminal path |
| `MT5_LOGIN` | ✅ Done — #26656038 (live) / #1067995 (demo) |
| `MT5_PASSWORD` | ✅ Done |
| `MT5_SERVER` | ✅ Done — `VTMarkets-Live 5` / `VTMarkets-Demo` |
| `MT5_SYMBOL_SUFFIX` | ✅ Done — `-STD` (live) / `-VIP` (demo) |
| `DB_HOST/PORT/NAME` | ✅ Done — MySQL Docker on port 3307 |

---

## Requirements

- Python 3.13 on **Windows** (MetaTrader5 package is Windows-only)
- VT Markets MT5 Terminal — download from VT Markets website
- MT5 must be opened **as Administrator** every time
- **Algo Trading button must be GREEN** in MT5 toolbar
- MySQL running via Docker (`mysql-docker` container on port 3307)
- Telegram account that is a **member** of the signal group

---

## Every Time You Run

1. `docker start mysql-docker`
2. Open **VT Markets MT5 as Administrator** and log in
3. Click **Algo Trading** button in MT5 toolbar — must be **green**
4. Run `python bot.py` in terminal
5. Check Telegram for **"SignalBot is LIVE!"** from @Hafiz_Carat_Signal_Bot
6. (Optional) Run `python dashboard/app.py` for the dashboard
7. (Optional) Run `ngrok http 5000` to access dashboard remotely

> **First run only:** Telethon asks for your phone number and a Telegram OTP.
> Enter them once — session saved to `data/session`, never asked again.

---

## Switching Between DEMO and LIVE

Change **one line** in `.env`:

```env
ENV_MODE=demo    # UAT testing
ENV_MODE=live    # real money
```

Restart `bot.py`. Log into the matching account in MT5 terminal first.
Both sets of credentials stay in `.env` — no commenting/uncommenting needed.
Spread guard auto-adjusts: 5 pips for demo, 3 pips for live.

---

## Full .env Reference

```env
# ── Telegram User Account ─────────────────────────────
TG_API_ID=23476310
TG_API_HASH=...
BOT_TOKEN=...                  # from @BotFather
YOUR_CHAT_ID=516045412         # from @userinfobot
SIGNAL_GROUP=-1002083967629    # PIPS FIGHTER 2026 numeric ID

# ── Environment Mode ──────────────────────────────────
ENV_MODE=demo                  # demo or live — switches everything below

# ── MT5 Account ───────────────────────────────────────
MT5_PATH=C:\Program Files\VT Markets (Pty) MT5 Terminal\terminal64.exe
DEMO_MT5_LOGIN=1068498
DEMO_MT5_SERVER=VTMarkets-Demo
DEMO_MT5_SYMBOL_SUFFIX=-VIP
LIVE_MT5_LOGIN=26656038
LIVE_MT5_SERVER=VTMarkets-Live 5
LIVE_MT5_SYMBOL_SUFFIX=-STD

# ── Risk Management ───────────────────────────────────
RISK_PERCENT=0.10              # 10% of free margin per trade
MIN_LOT=0.01                   # never go below this
MAX_LOT=0.50                   # never go above this

# ── Profit Lock (auto-breakeven + TP override) ───────
PROFIT_LOCK_ENABLED=true       # enable/disable
PROFIT_LOCK_PIPS=50            # trigger at +50 pips profit
PROFIT_LOCK_TP_PIPS=100        # push TP to +100 pips from entry

# ── AutoZone (auto-entry from mapped zones) ──────────
MAP_ENABLED=true               # enable/disable AutoZone

# ── Layered DCA Entry ─────────────────────────────────
# LAYER_MODE=true  → build position across N layers as price dips
# LAYER_MODE=false → original TRADE_SPLIT behaviour (default)
#
# Layer count is DYNAMIC:
#   actual_layers = min(LAYER_COUNT, int(total_lot / MIN_LOT))
#   $200 account → 3 layers | $500 → 5 layers | $1000+ → 7 layers
#
LAYER_MODE=false               # true = DCA layers | false = TRADE_SPLIT
LAYER_COUNT=7                  # max layers (actual count auto-scales with margin)
LAYER2_PIPS=35                 # pip gap between each layer (L1→L2=35p, L2→L3=70p, etc.)

# ── Trade Split (used when LAYER_MODE=false) ──────────
# Split each signal into N equal positions for partial TP management
# Bot auto-reduces splits if account too small to split further
# Ignored when LAYER_MODE=true
TRADE_SPLIT=5

# ── Signal Timing ─────────────────────────────────────
SIGNAL_EXPIRY=1800             # 30 min watcher window
WATCH_INTERVAL_SECS=30         # price check interval (seconds)

# ── Guard 1: Entry Proximity ──────────────────────────
ENTRY_MAX_DISTANCE_PIPS=50     # skip if price > 50 pips from zone
                               # (skipped for L2+ in layered mode — by design)

# ── Guard 2: Margin Level ─────────────────────────────
# margin_level = equity / used_margin × 100
# 300% = professional floor | 200% = danger zone
MIN_MARGIN_LEVEL=300

# ── Guard 3: Spread ───────────────────────────────────
MAX_SPREAD_PIPS=3

# ── Guard 4: Reward:Risk Ratio ────────────────────────
MIN_RR_RATIO=1.4               # TP must be 1.4× the SL distance

# ── Auto TP Enforcement ───────────────────────────────
# If Hafiz's SL < SL_MIN_PIPS, auto-override TP to TP_ENFORCE_PIPS
SL_MIN_PIPS=50
TP_ENFORCE_PIPS=70

# ── Guard 5: Same-Direction Stack ────────────────────
# Breakeven positions and own session layers are EXEMPT
BLOCK_SAME_DIRECTION_STACK=true

# ── SL Sanity Warnings (not a block — just a warning) ─
SL_PIP_SIZE=0.1                # 1 pip = 0.1 price units for XAUUSD
SL_WARN_MIN_PIPS=50
SL_WARN_MAX_PIPS=70

# ── Early TP / Breakeven ──────────────────────────────
BREAKEVEN_KEEP_COUNT=2

# ── MySQL Database ────────────────────────────────────
DB_HOST=localhost
DB_PORT=3307
DB_NAME=botsignal
DB_USER=root
DB_PASSWORD=rootpass

# ── Night Trading Agent ───────────────────────────────
AGENT_START_HOUR_MY=22
AGENT_END_HOUR_MY=6
AGENT_AUTO_EXECUTE=false
AGENT_ENABLED=true
```

---

## Execution Modes

### Standard mode (`LAYER_MODE=false`)

Single burst entry using `TRADE_SPLIT`:

```
Signal arrives → price enters zone → all N positions placed at once
```

### Layered DCA mode (`LAYER_MODE=true`)

Progressive entry as price moves in your favour:

```
L1 → price enters zone           (35 pips = L2 trigger for buy)
L2 → price dips 35p from L1     (35 more pips = L3 trigger)
L3 → price dips 70p from L1     (etc.)
LN → price dips (N-1)×35p from L1

Each layer split by TPs: sub_lot × num_TPs sub-orders
When all upper sub-orders TP → all deepest sub-orders move to breakeven (free ride)
```

**TP splitting in layered mode:**

Each layer's lot is split across signal TPs — one sub-order per TP:

```
Signal with 2 TPs, L1 lot = 0.12:
  Sub-order 1: 0.06 lot → TP1 (secure profit early)
  Sub-order 2: 0.06 lot → TP2 (ride for more)

Same split applies to L2, L3, etc.
```

If `sub_lot < MIN_LOT`, the split count is automatically reduced.

**Profit Lock + TP split interaction:**
- Sub-orders with short TPs (< 50p from entry) close at TP naturally — Profit Lock never fires
- Sub-orders with long TPs (≥ 50p) get Profit Lock protection: SL→breakeven, TP→+100p
- L2 enters deeper → TPs are further from L2 entry → Profit Lock more likely to fire on L2

---

## Trade Guard System

All guards run inside `execute_trade()` in `core/mt5.py` in this exact order.
In layered mode, **every layer placement runs all 6 guards independently**.

| # | Guard | Env var | Default | Fires when |
|---|-------|---------|---------|-----------|
| 1 | Margin level | `MIN_MARGIN_LEVEL` | 300% | `equity / used_margin × 100 < 300%`. Skipped if no open trades. |
| 2 | Same-direction stack | `BLOCK_SAME_DIRECTION_STACK` | true | Same symbol+direction already open at risk. Own DCA session tickets and breakeven positions are **exempt**. |
| 3 | Auto TP + RR ratio | `MIN_RR_RATIO` | 1.4 | Computed from `entry_mid`. If SL < `SL_MIN_PIPS` (50p), TP auto-set to `TP_ENFORCE_PIPS` (70p) from mid first. Blocks if TP/SL still < 1.4 after override. |
| 4 | Spread | `MAX_SPREAD_PIPS` | 3 pips | Live broker spread > 3 pips. Always retries — never fatal for any layer. |
| 5 | Entry proximity | `ENTRY_MAX_DISTANCE_PIPS` | 50 pips | Price > 50 pips from entry zone. **Skipped for L2+ in DCA mode** — deeper entries are intentionally outside zone. |
| 6 | Lot calculation | — | — | `calculate_lot()` returns 0 — margin too thin for even MIN_LOT. |

### Guard behaviour: standard vs DCA mode

| Event | Standard mode | DCA mode — L1 | DCA mode — L2+ |
|-------|--------------|--------------|----------------|
| Spread too wide | Retry next interval | Retry next interval | Retry next interval |
| Margin blocked | Trade skipped | **Session ends** | Layer skipped, watch for next |
| Stack blocked | Trade skipped | **Session ends** | Layer skipped, watch for next |
| RR ratio blocked | Trade skipped | **Session ends** | Layer skipped, watch for next |
| Proximity blocked | Trade skipped | **Session ends** | **Never fires** (skipped by design) |
| Lot = 0 | Trade skipped | **Session ends** | Layer skipped, watch for next |

> **L1 is the gate.** If L1 is blocked by anything except spread, the whole session ends.
> A spread block on any layer just waits for the spread to normalise — it never ends the session.

### SL safety cap — layers cannot cross the SL

`layer_watcher.py` enforces this **before the loop starts**:

```
sl_pips      = (entry_mid − SL) / pip_size        # e.g. 40 pips
safe_steps   = int((sl_pips − 1) / LAYER2_PIPS)   # −1 pip buffer
max_by_sl    = 1 + safe_steps                      # max safe layers
actual_layers = min(by_margin, max_by_sl)
```

Example — BUY 5081–5085, SL 5079, LAYER2_PIPS=35:
```
sl_pips = 40p  →  safe_steps = int(39/35) = 1  →  max_by_sl = 2
L1 @ 5085 (60p above SL)  ✓
L2 @ 5081.5 (25p above SL)  ✓
L3 would be @ 5078 — below SL — BLOCKED before session starts
```

A runtime check also runs each tick: if the next trigger price is at or beyond SL, the bot skips it and goes straight to monitoring.

---

## Risk Management

```
risk_amount  = free_margin × RISK_PERCENT
sl_in_ticks  = (entry_mid − SL) / tick_size      ← uses MIDPOINT, not execution price
risk_per_lot = sl_in_ticks × tick_value
total_lot    = risk_amount / risk_per_lot
total_lot    = clamp(total_lot, MIN_LOT, MAX_LOT)

Standard:  split_lot  = total_lot / actual_splits
Layered:   layer_lot  = total_lot / actual_layers  (dynamic count)
           sub_lot    = layer_lot / num_TPs        (TP splitting)
```

**Lot is calculated ONCE at signal arrival using `entry_mid`.** Layers are placed at different prices, so actual dollar risk per layer varies — but combined worst-case ≈ `RISK_PERCENT × free_margin`.

### DCA layer dollar risk breakdown (example: $1,000 account, BUY 5081–5085, SL 5079)

```
entry_mid = 5083  |  SL = 5079  |  sl_pips_mid = 40p
total_lot = $100 / (40p × $10) = 0.25 lots  →  0.12 lots/layer (2 layers)

L1 executes at 5085 (zone top):  0.12L × 60p × $10 = $72 risk
L2 executes at 5081.5 (35p deeper): 0.12L × 25p × $10 = $30 risk
Total worst-case loss = $102  (10.2% — $2 over due to zone-top entry)
```

L2 carries far less dollar risk than L1 because it enters closer to the SL. The combined risk stays close to budget. Floor rounding (0.125 → 0.12) reduces the overage further.

### Dynamic layer count

```
actual_layers = min(LAYER_COUNT, int(total_lot / MIN_LOT))
```
then capped by `max_by_sl` above.

| Free margin | Lot (10% risk) | Max by margin | Actual layers (50p SL, 35p gap) |
|---|---|---|---|
| ~$200 | ~0.03 | 3 | 2 |
| ~$500 | ~0.05 | 5 | 2 |
| ~$1,000 | ~0.10 | 7+ | 2 |
| ~$2,000 | ~0.25 | 7+ | 2 |

> With a 50-pip SL and 35-pip layer spacing, the SL cap always limits to 2 layers.
> Reduce LAYER2_PIPS (e.g. 20p) to allow more layers on tighter signals.

---

## Auto TP Enforcement

| Hafiz SL | Hafiz TP | Bot action |
|---|---|---|
| < 50 pips | any | TP auto-adjusted to **70 pips** from `entry_mid` |
| ≥ 50 pips | any | Use Hafiz's TP as-is |

You'll see this note in Telegram when TP is overridden:
```
⚙️ SL tight (30 pips) — TP auto-adjusted to 70 pips
```

---

## Close Alert System

| Trigger phrase | Reason | Action |
|---|---|---|
| `"setup failed"` | setup_failed | CLOSE per group + CLOSE ALL button |
| `"collect profit"`, `"mau collect"`, `"siapa mau collect"` | collect_profit | 70% close + 30% breakeven |
| `"siapa nak collect"`, `"collect dulu"`, `"dipersilakan"`, `"take profit now"`, `"early tp"` | early_tp | Keep top N at breakeven, close rest |

---

## Profit Lock — Auto-Breakeven + TP Override

When a position is running +50 pips profit, the poller automatically:
- Moves SL to breakeven (entry price) — risk-free
- Pushes TP to +100 pips from entry — bigger target

Checked every 60s by the dashboard poller. Only bot-placed positions (magic 20250101). Never reduces TP. With TP splitting, each sub-order is checked independently — short TPs close before Profit Lock triggers, long TPs get enhanced by it.

```env
PROFIT_LOCK_ENABLED=true
PROFIT_LOCK_PIPS=50
PROFIT_LOCK_TP_PIPS=100
```

---

## AutoZone — Auto-Entry from Mapped Zones

Map SNR levels and buy/sell zones each morning via Telegram. The bot auto-enters when price reaches a zone.

### Commands (send to @Hafiz_Carat_Signal_Bot)

```
/snr XAUUSD 5007 5014 5022 5035 5043    — set today's SNR levels
/map XAUUSD buy 5011-5014               — add a buy zone (SL/TP auto-picked)
/map XAUUSD sell 5043-5046              — add a sell zone
/zones                                   — list today's zones + SNR
/delzone 3                               — delete zone #3
/clearmap                                — clear all zones + SNR
```

### SL/TP auto-pick

- **BUY** zone: SL = nearest SNR below zone, TP = nearest SNR above zone
- **SELL** zone: SL = nearest SNR above zone, TP = nearest SNR below zone
- If no SNR level found → `/map` rejected with error

### Behaviour

- Checks price every 30s (same `WATCH_INTERVAL_SECS`)
- All 6 guards apply (margin, stack, RR, spread, proximity, lot)
- Follows `LAYER_MODE` — DCA or direct, same as signals
- One-shot: zone fires once, then marked as fired
- Zones expire at midnight Malaysia time
- Fully automatic — no EXECUTE/SKIP button needed
- Dashboard shows AutoZone panel with SNR levels and zone status

---

## Dashboard

URL: **http://localhost:5000** | Remote: `ngrok http 5000`

| Badge | Meaning |
|-------|---------|
| `EXECUTED` | Trade placed via bot |
| `SKIPPED` | You tapped SKIP |
| `EXPIRED` | 30 min passed, price never reached zone |
| `PENDING` | Watcher active |
| `MANUAL` | Trade opened directly in MT5 |
| `MAP` | Trade triggered by AutoZone |
| `WIN` / `LOSS` / `OPEN` | Trade outcome |

---

## MySQL (Docker)

| Setting | Value |
|---------|-------|
| Container | `mysql-docker` |
| Port | `3307` |
| User | `root` |
| Database | `botsignal` |
| Tables | `signals`, `trades`, `guard_events`, `snr_levels`, `mapping_zones` |

```bash
docker start mysql-docker    # start
docker stop mysql-docker     # stop
docker logs mysql-docker     # view logs
```

---

## Troubleshooting

| Problem | Fix |
|---------|-----|
| `Conflict: terminated by other getUpdates` | Two bot instances running — check `data/bot.pid`, kill old PID with `taskkill /PID <pid> /F` |
| `IPC timeout` | Open MT5 as Administrator before bot.py |
| `AutoTrading disabled` (code 10027) | Click **Algo Trading** in MT5 toolbar — must be green |
| `Symbol XAUUSD not found` | Check `MT5_SYMBOL_SUFFIX` — live=`-STD`, demo=`-VIP` |
| `Invalid stops` | Signal prices outdated — price moved far from entry zone |
| `AttributeError: 'User' has no 'title'` | Use numeric group ID in `SIGNAL_GROUP` |
| `Forbidden` Telegram message | Open bot in Telegram and press Start |
| `ModuleNotFoundError` | `pip install -r requirements.txt` |
| MT5 login failed | Check `MT5_SERVER` — exact match including spaces |
| Telethon OTP loop | Delete `data/session*` and re-login |
| Trade blocked — margin | Close losing positions to free up margin |
| Trade blocked — stack | Same direction open at risk — close or move to breakeven |
| Trade blocked — spread | Watcher retries automatically when spread normalises |
| Trade blocked — RR ratio | TP/SL < 1.4 even after auto-adjust |
| L1 blocked (DCA mode) | Fatal — session ends, check guard_events in dashboard |
| L2+ blocked, not spread (DCA) | Layer skipped — bot continues watching for next layer trigger |
| Only L1 placed, then stopped | L1 SL hit before price dipped to L2 — normal behaviour |
| "All layers stopped out" message | All SL hit — full loss for this signal (expected risk) |
| Dashboard no data | `docker start mysql-docker` before bot.py |
| Manual trade missing | Poller syncs every 60s — wait one cycle |
| `cryptography` error | `pip install cryptography` |

---

## Notes

- `.env` is gitignored — never commit it
- `data/session` is your Telegram login — treat like a password
- Bot uses PID lock (`data/bot.pid`) — only one instance allowed at a time
- Positions stay alive when bot stops — SL/TP managed by broker
- Night agent active 10 PM – 6 AM MYT — covers London + NY session
- In layered mode, `layer_sessions` dict in `core/layer_watcher.py` tracks all active sessions
- AutoZone watcher starts 3s after notifier init — requires `MAP_ENABLED=true`
- AutoZone uses `entry_mode='mapped'` in trades table to distinguish from signal entries
