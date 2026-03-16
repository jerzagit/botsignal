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

In `.env`, comment out one block and uncomment the other:

```env
# ── DEMO account (safe for testing) ───────────────────
#MT5_LOGIN=1067995
#MT5_PASSWORD=9A7RXn!U
#MT5_SERVER=VTMarkets-Demo
#MT5_SYMBOL_SUFFIX=-VIP

# ── LIVE account (real money — be careful!) ───────────
MT5_LOGIN=26656038
MT5_PASSWORD=...
MT5_SERVER=VTMarkets-Live 5
MT5_SYMBOL_SUFFIX=-STD
```

Restart `bot.py` after switching. Log into the matching account in MT5 terminal first.

---

## Full .env Reference

```env
# ── Telegram User Account ─────────────────────────────
TG_API_ID=23476310
TG_API_HASH=...
BOT_TOKEN=...                  # from @BotFather
YOUR_CHAT_ID=516045412         # from @userinfobot
SIGNAL_GROUP=-1002083967629    # PIPS FIGHTER 2026 numeric ID

# ── MT5 Account ───────────────────────────────────────
MT5_PATH=C:\Program Files\VT Markets (Pty) MT5 Terminal\terminal64.exe
MT5_SYMBOL_SUFFIX=-STD         # live=-STD | demo=-VIP
MT5_LOGIN=26656038
MT5_PASSWORD=...
MT5_SERVER=VTMarkets-Live 5    # exact name — note: space not hyphen

# ── Risk Management ───────────────────────────────────
RISK_PERCENT=0.10              # 10% of free margin per trade
MIN_LOT=0.01                   # never go below this
MAX_LOT=0.50                   # never go above this

# ── Trade Split ───────────────────────────────────────
# Split each signal into N equal positions for partial TP management
# Bot auto-reduces splits if account too small to split further
TRADE_SPLIT=5

# ── Signal Timing ─────────────────────────────────────
SIGNAL_EXPIRY=1800             # 30 min watcher window
WATCH_INTERVAL_SECS=30         # price check interval (seconds)

# ── Guard 1: Entry Proximity ──────────────────────────
ENTRY_MAX_DISTANCE_PIPS=50

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
# Positions already at breakeven are EXEMPT — new entries allowed alongside them
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

## Trade Guard System

All guards run inside `execute_trade()` in `core/mt5.py` in this order:

| # | Guard | Env var | Default | Fires when |
|---|-------|---------|---------|-----------|
| 1 | Margin level | `MIN_MARGIN_LEVEL` | 300% | Margin level < 300% |
| 2 | Same-direction stack | `BLOCK_SAME_DIRECTION_STACK` | true | Same symbol+direction open **at risk** (breakeven exempt) |
| 3 | Auto TP + RR ratio | `MIN_RR_RATIO` | 1.4 | TP/SL < 1.4 after auto-adjust |
| 4 | Spread | `MAX_SPREAD_PIPS` | 3 pips | Broker spread > 3 pips |
| 5 | Entry proximity | `ENTRY_MAX_DISTANCE_PIPS` | 50 pips | Price > 50 pips from zone |
| 6 | Lot calculation | — | — | Margin too thin for valid lot |

---

## Risk Management

```
risk_amount  = free_margin × RISK_PERCENT
sl_in_ticks  = sl_distance / tick_size
risk_per_lot = sl_in_ticks × tick_value
lot_size     = risk_amount / risk_per_lot
lot_size     = clamp(lot_size, MIN_LOT, MAX_LOT)
split_lot    = lot_size / actual_splits
```

**If SL hits on ALL positions → max loss = RISK_PERCENT × free margin.**

Split cap — bot auto-reduces actual splits:

| Free margin | Lot | Actual splits | Per position |
|---|---|---|---|
| $143 | 0.01 | 1 | 0.01 |
| $500 | 0.05 | 5 | 0.01 |
| $2,000 | 0.50 | 5 | 0.10 |

---

## Auto TP Enforcement

| Hafiz SL | Hafiz TP | Bot action |
|---|---|---|
| < 50 pips | any | TP auto-adjusted to **70 pips** from entry |
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

## Dashboard

URL: **http://localhost:5000** | Remote: `ngrok http 5000`

| Badge | Meaning |
|-------|---------|
| `EXECUTED` | Trade placed via bot |
| `SKIPPED` | You tapped SKIP |
| `EXPIRED` | 30 min passed, price never reached zone |
| `PENDING` | Watcher active |
| `MANUAL` | Trade opened directly in MT5 |
| `WIN` / `LOSS` / `OPEN` | Trade outcome |

---

## MySQL (Docker)

| Setting | Value |
|---------|-------|
| Container | `mysql-docker` |
| Port | `3307` |
| User | `root` |
| Database | `botsignal` |
| Tables | `signals`, `trades` |

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
