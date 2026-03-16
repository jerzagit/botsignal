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
| `MT5_LOGIN` | ✅ Done — #26656038 |
| `MT5_PASSWORD` | ✅ Done |
| `MT5_SERVER` | ✅ Done — `VTMarkets-Live 5` (note: space not hyphen) |
| `MT5_SYMBOL_SUFFIX` | ✅ Done — `-STD` (VT Markets appends this to all symbols) |
| `DB_HOST/PORT/NAME` | ✅ Done — MySQL Docker on port 3307 |

---

## Requirements

- Python 3.13 on **Windows** (MetaTrader5 package is Windows-only)
- VT Markets MT5 Terminal — download from VT Markets website
- MT5 must be opened **as Administrator** every time
- MySQL running via Docker (`mysql-docker` container on port 3307)
- Telegram account that is a **member** of the signal group

---

## Every Time You Run

1. `docker start mysql-docker`
2. Open **VT Markets MT5 as Administrator** and log in
3. Enable **"Allow algorithmic trading"** in MT5 → Tools → Options → Expert Advisors
4. Run `python bot.py` (or double-click `run_all.bat` for bot + dashboard together)
5. Check Telegram for **"SignalBot is LIVE!"** from @Hafiz_Carat_Signal_Bot
6. Open **http://localhost:5000** for the dashboard (if running separately: `python dashboard/app.py`)

> **First run only:** Telethon asks for your phone number and a Telegram OTP.
> Enter them once — session saved to `data/session`, never asked again.

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
MT5_SYMBOL_SUFFIX=-STD         # VT Markets appends -STD to all symbols
MT5_LOGIN=26656038
MT5_PASSWORD=...
MT5_SERVER=VTMarkets-Live 5    # exact name — note: space not hyphen

# ── Risk Management ───────────────────────────────────
RISK_PERCENT=0.05              # 5% of free margin per trade
MIN_LOT=0.01                   # never go below this
MAX_LOT=0.50                   # never go above this

# ── Signal Timing ─────────────────────────────────────
SIGNAL_EXPIRY=1800             # 30 min — gives price time to reach entry zone

# ── Guard 1: Entry Proximity ──────────────────────────
# Block trade if price is more than N pips from Hafiz's entry zone
ENTRY_MAX_DISTANCE_PIPS=50

# ── Guard 2: Margin Level ─────────────────────────────
# Block trade if margin level is below this %
# margin_level = equity / used_margin × 100
# 300% = professional floor | 200% = danger zone
MIN_MARGIN_LEVEL=300

# ── Guard 3: Spread ───────────────────────────────────
# Block trade if broker spread exceeds this many pips
# XAUUSD normal: 1–2 pips | Wide spread = news / off-hours
MAX_SPREAD_PIPS=3

# ── Guard 4: Reward:Risk Ratio ────────────────────────
# Block trade if TP1 / SL < this ratio
# 1.0 = minimum (TP must at least equal SL)
MIN_RR_RATIO=1.0

# ── Guard 5: Same-Direction Stack ────────────────────
# Block new trade if same symbol + direction already open
# Prevents doubling exposure on small account
BLOCK_SAME_DIRECTION_STACK=true

# ── SL Sanity Warnings (not a block — just a warning) ─
SL_PIP_SIZE=0.1                # 1 pip = 0.1 price units for XAUUSD
SL_WARN_MIN_PIPS=50            # warn if SL tighter than this
SL_WARN_MAX_PIPS=70            # warn if SL wider than this

# ── Early TP / Breakeven ──────────────────────────────
BREAKEVEN_KEEP_COUNT=2         # positions to keep running at breakeven on early TP signal

# ── MySQL Database ────────────────────────────────────
DB_HOST=localhost
DB_PORT=3307
DB_NAME=botsignal
DB_USER=root
DB_PASSWORD=rootpass

# ── Night Trading Agent ───────────────────────────────
AGENT_START_HOUR_MY=22         # 10 PM Malaysia time
AGENT_END_HOUR_MY=6            # 6 AM Malaysia time
AGENT_AUTO_EXECUTE=false       # true = trades automatically while you sleep
AGENT_ENABLED=true
```

---

## Trade Guard System

All guards run inside `execute_trade()` in `core/mt5.py` in this order:

| # | Guard | Env var | Default | Fires when |
|---|-------|---------|---------|-----------|
| 1 | Margin level | `MIN_MARGIN_LEVEL` | 300% | Margin level < 300% (skip if no open trades) |
| 2 | Same-direction stack | `BLOCK_SAME_DIRECTION_STACK` | true | Same symbol + direction already open |
| 3 | Reward:Risk ratio | `MIN_RR_RATIO` | 1.0 | TP1 / SL < 1.0 |
| 4 | Spread | `MAX_SPREAD_PIPS` | 3 pips | Broker spread > 3 pips |
| 5 | Entry proximity | `ENTRY_MAX_DISTANCE_PIPS` | 50 pips | Price > 50 pips from entry zone |
| 6 | Lot calculation | — | — | Margin too thin for a valid lot |

All guards are tested in `test_margin_guard.py` (23 unit tests, no live MT5 required).

```bash
python -m pytest test_margin_guard.py -v
```

---

## Risk Management

```
risk_amount  = free_margin × RISK_PERCENT
sl_in_ticks  = sl_distance / tick_size
risk_per_lot = sl_in_ticks × tick_value
lot_size     = risk_amount / risk_per_lot
lot_size     = clamp(lot_size, MIN_LOT, MAX_LOT)
lot_size     = round to broker volume step
```

- `free_margin` already accounts for open trades and floating P&L
- One order per signal — no layering
- Small account (< $200) will usually clamp to `MIN_LOT = 0.01`

---

## Close Alert System

| Trigger | Keywords | Action |
|---------|----------|--------|
| Setup failed | `"setup failed"` | Show CLOSE per signal group + CLOSE ALL button |
| Early profit | `"profit Xpips"`, `"siapa nak collect"`, `"collect dulu"`, `"dipersilakan"`, `"take profit now"`, `"early tp"` | Breakeven plan: keep top N at breakeven, close rest profitable, leave losses |

---

## Dashboard

URL: **http://localhost:5000**

| Badge | Meaning |
|-------|---------|
| `EXECUTED` | Trade placed via bot |
| `SKIPPED` | You tapped SKIP |
| `EXPIRED` | 30 min passed without action |
| `PENDING` | Waiting for your tap |
| `MANUAL` | Trade opened directly in MT5 (not via bot) |
| `WIN` | Closed in profit |
| `LOSS` | Closed at a loss |
| `OPEN` | Trade still running |

Win/loss detection polls MT5 every **60 seconds**. Manual trades (opened directly in MT5) are detected automatically and shown with a `MANUAL` badge.

---

## MySQL (Docker)

| Setting | Value |
|---------|-------|
| Container | `mysql-docker` |
| Port | `3307` (mapped from internal 3306) |
| User | `root` |
| Database | `botsignal` |
| Tables | `signals`, `trades` |
| Password | `rootpass` |

To find the password if forgotten:
```bash
docker inspect mysql-docker | findstr MYSQL_ROOT_PASSWORD
```

---

## Troubleshooting

| Problem | Fix |
|---------|-----|
| `IPC timeout` | Open MT5 as Administrator before running bot.py |
| `Symbol XAUUSD not found` | Check `MT5_SYMBOL_SUFFIX` in .env — VT Markets uses `-STD` |
| `AttributeError: 'User' has no 'title'` | Use numeric group ID in `SIGNAL_GROUP` |
| `Forbidden` Telegram message | Open your bot in Telegram and press Start |
| `ModuleNotFoundError` | Run `pip install -r requirements.txt` |
| MT5 login failed | Check `MT5_SERVER` — must match exactly including spaces |
| MT5 error 10027 | Enable "Allow algorithmic trading" in MT5 → Tools → Options → Expert Advisors |
| Telethon OTP keeps asking | Delete `data/session*` files and re-login |
| Trade blocked — margin level | Margin level below 300% — close losing positions first |
| Trade blocked — stack | Same direction already open — wait for it to close |
| Trade blocked — spread | Spread too wide — wait for market to calm (usually after news) |
| Trade blocked — RR ratio | Hafiz's TP is smaller than SL — not a good trade |
| Trade skipped — proximity | Price too far from entry — tap EXECUTE again when closer |
| Dashboard shows no trades | `docker start mysql-docker` must run before bot.py |
| Manual trade not appearing | Poller syncs every 60 seconds — wait one cycle |
| `cryptography` error on MySQL | `pip install cryptography` |

---

## Notes

- `.env` is gitignored — never commit it
- `data/session` is your Telegram login — treat it like a password
- Trade log saved to `data/trades.json` (local) and MySQL (dashboard)
- Night agent active 10 PM – 6 AM MYT — covers London + NY session
