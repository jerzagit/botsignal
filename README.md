# SignalBot

Auto-trades your mentor's Telegram signals on MT5 — with a multi-layer guard system, dashboard, and night agent.

---

## How it works

```
Mentor posts signal in Telegram group
        ↓
Telethon reads it (as your account — no admin needed)
        ↓
Bot runs pre-trade guard checks (6 layers)
        ↓
Sends you confirmation: EXECUTE ✅ / SKIP ❌  (30-min window)
        ↓
You tap EXECUTE → guard checks run again at execution time
        ↓
Trade placed on MT5 with auto-calculated lot size
        ↓
Dashboard records the trade, polls outcome every 60 seconds
        ↓  (if agent is on + night hours)
Agent auto-executes while you sleep 🌙
```

---

## Project structure

```
signalbot/
├── bot.py                  ← Entry point — run this
├── .env                    ← Your secrets (never commit)
├── .env.example            ← Template — copy to .env
├── requirements.txt
├── run_all.bat             ← Starts bot + dashboard together (Windows)
│
├── core/
│   ├── config.py           ← All settings loaded from .env
│   ├── signal.py           ← Signal parser + CloseAlert parser
│   ├── risk.py             ← Lot calculator (margin % ÷ SL distance)
│   ├── mt5.py              ← MT5 connection, guards, trade execution
│   ├── listener.py         ← Telethon: watches group as your account
│   ├── notifier.py         ← Telegram bot: EXECUTE/SKIP/Close buttons
│   ├── state.py            ← In-memory pending signals + close plans
│   └── db.py               ← MySQL write functions
│
├── agent/
│   └── agent.py            ← Night trading agent (10 PM–6 AM MYT)
│
├── dashboard/
│   ├── app.py              ← Flask web dashboard (http://localhost:5000)
│   ├── poller.py           ← MT5 win/loss outcome poller (60-sec interval)
│   └── templates/
│       └── index.html      ← Dashboard UI
│
├── data/
│   ├── session             ← Telethon session (auto-created on first run)
│   └── trades.json         ← Local trade log (also mirrored to MySQL)
│
├── logs/
│   └── bot.log             ← Full log file
│
└── test_margin_guard.py    ← Unit tests for all trade guards (23 tests)
```

---

## Quick start

### 1 — Install Python 3.11+ (Windows only)

Download from https://python.org. Tick **"Add Python to PATH"** during install.

> MetaTrader5 Python library is Windows-only.

### 2 — Install dependencies

```bash
pip install -r requirements.txt
```

### 3 — Get your Telegram API keys

1. Go to https://my.telegram.org → **API development tools**
2. Fill any app name → **Create**
3. Copy `App api_id` and `App api_hash`

### 4 — Create your Telegram bot

1. Open Telegram → search **@BotFather** → `/newbot`
2. Copy the token (looks like `123456789:AAF...`)
3. Search your new bot → tap **Start** (required before it can message you)

### 5 — Get your Chat ID and Group ID

- **Your Chat ID:** Send `/start` to **@userinfobot**
- **Signal Group ID:** Forward any message from the group to **@userinfobot** — use the numeric ID (e.g. `-1002083967629`), not the username

### 6 — Set up MT5

- Download MT5 from your **broker's website** (not MetaQuotes)
- **Always run MT5 as Administrator** — required for Python IPC
- Find your server name at the bottom-right of the MT5 window (e.g. `VTMarkets-Live 5`)
- Find your symbol suffix in MT5 Market Watch (VT Markets uses `-STD`, e.g. `XAUUSD-STD`)

### 7 — Start MySQL

```bash
docker start mysql-docker
```

The `botsignal` database and tables are created automatically on first run.

### 8 — Configure .env

```bash
copy .env.example .env
```

Fill in all values — see the full reference below.

### 9 — Run

```bash
# Bot only
python bot.py

# Bot + Dashboard together
run_all.bat
```

**First run:** Telethon asks for your phone and a Telegram OTP. Enter them once — session saved to `data/session`, never asked again.

You'll receive **"SignalBot is LIVE!"** confirming it's running.

---

## .env reference

```env
# ── Telegram ─────────────────────────────────────────
TG_API_ID=...
TG_API_HASH=...
BOT_TOKEN=...                  # from @BotFather
YOUR_CHAT_ID=...               # from @userinfobot
SIGNAL_GROUP=-1002083967629    # numeric group ID

# ── MT5 ──────────────────────────────────────────────
MT5_PATH=C:\Program Files\YourBroker MT5 Terminal\terminal64.exe
MT5_LOGIN=...
MT5_PASSWORD=...
MT5_SERVER=YourBroker-Live 5   # exact name from MT5 status bar
MT5_SYMBOL_SUFFIX=-STD         # suffix your broker appends (VT Markets = -STD)

# ── Risk management ───────────────────────────────────
RISK_PERCENT=0.05              # % of free margin to risk per trade
MIN_LOT=0.01                   # minimum lot size
MAX_LOT=0.50                   # maximum lot size

# ── Signal timing ─────────────────────────────────────
SIGNAL_EXPIRY=1800             # 30 min — gives price time to reach entry zone

# ── Entry proximity guard ─────────────────────────────
ENTRY_MAX_DISTANCE_PIPS=50     # skip trade if price is >50 pips from entry zone

# ── Margin level guard ────────────────────────────────
MIN_MARGIN_LEVEL=300           # block if margin level < 300% (professional floor)

# ── Spread guard ──────────────────────────────────────
MAX_SPREAD_PIPS=3              # block if broker spread > 3 pips (news/off-hours)

# ── Reward:Risk ratio guard ───────────────────────────
MIN_RR_RATIO=1.0               # block if TP < SL (bad risk math)

# ── Same-direction stack guard ────────────────────────
BLOCK_SAME_DIRECTION_STACK=true  # block adding same direction on small account

# ── SL sanity warnings ────────────────────────────────
SL_PIP_SIZE=0.1                # 1 pip = 0.1 price units for XAUUSD
SL_WARN_MIN_PIPS=50            # warn if SL tighter than 50 pips
SL_WARN_MAX_PIPS=70            # warn if SL wider than 70 pips

# ── Early TP / breakeven ──────────────────────────────
BREAKEVEN_KEEP_COUNT=2         # positions to keep at breakeven on early TP

# ── MySQL ─────────────────────────────────────────────
DB_HOST=localhost
DB_PORT=3307
DB_NAME=botsignal
DB_USER=root
DB_PASSWORD=...

# ── Night agent ───────────────────────────────────────
AGENT_START_HOUR_MY=22         # 10 PM Malaysia time
AGENT_END_HOUR_MY=6            # 6 AM Malaysia time
AGENT_AUTO_EXECUTE=false       # true = trades while you sleep (use with caution)
AGENT_ENABLED=true
```

---

## Trade guard system

Every trade passes through **6 sequential guards** before an order is sent to MT5. All thresholds are configurable in `.env`.

| # | Guard | Default | Blocks when... |
|---|-------|---------|---------------|
| 1 | **Margin level** | ≥ 300% | Account is over-leveraged |
| 2 | **Same-direction stack** | enabled | Already have same symbol + direction open |
| 3 | **Reward:Risk ratio** | ≥ 1.0 | TP1 is smaller than SL (bad trade math) |
| 4 | **Spread** | ≤ 3 pips | Broker spread is too wide (news / off-hours) |
| 5 | **Entry proximity** | ≤ 50 pips | Price is far from Hafiz's entry zone (early signal) |
| 6 | **Lot calculation** | — | Margin too thin to calculate a valid lot |

When a guard fires, you receive a clear Telegram message explaining exactly why — no silent failures.

---

## Lot sizing formula

```
risk_amount  = free_margin × RISK_PERCENT
sl_in_ticks  = sl_distance / tick_size
risk_per_lot = sl_in_ticks × tick_value
lot_size     = risk_amount / risk_per_lot
lot_size     = clamp(lot_size, MIN_LOT, MAX_LOT)
lot_size     = round to broker volume step
```

- `free_margin` already reflects open trades and floating P&L
- One order per signal — no layering
- Small account (< $200) will usually clamp to `MIN_LOT = 0.01`

---

## Signal format

```
xauusd sell @5096-5100
sl 5103
tp 5092
tp 5090
Trade At Your Own Risk
T.A.Y.O.R @AssistByHafizCarat
```

Supported variations:
- Single price: `xauusd buy @5096`
- Range entry: `xauusd sell @5096-5100`
- Multiple TPs: as many `tp PRICE` lines as needed
- Case insensitive
- Extra text (T.A.Y.O.R etc.) is ignored

---

## Close alert system

The bot also detects Hafiz's early-close messages and sends you action buttons.

### Setup Failed
Triggered by: `"setup failed"` anywhere in the message.

Button per signal group + **CLOSE ALL** button.

### Early Profit / Collect
Triggered by any of:
- `"profit Xpips"` (e.g. "profit 40pips")
- `"siapa nak collect"`
- `"collect dulu"`
- `"dipersilakan"`
- `"take profit now"`
- `"early tp"`

**Breakeven plan shown:**
- Top `BREAKEVEN_KEEP_COUNT` profitable positions → SL moved to entry (breakeven)
- Remaining profitable positions → closed
- Losing positions → left untouched (original SL remains)

---

## Dashboard

Run: `python dashboard/app.py` → open http://localhost:5000

| Section | What you see |
|---------|-------------|
| Stat cards | Total signals, executed, skipped, expired, open trades, win rate |
| P&L bar | Wins, losses, total profit/loss in USD |
| Signal log | Timestamp, symbol, direction, entry zone, SL, TPs, status badge |
| Trade rows | Ticket, lot, entry price, close price, outcome (WIN / LOSS / OPEN) |

**Status badges:**
- `EXECUTED` — trade placed
- `SKIPPED` — you skipped it
- `EXPIRED` — 30 min passed without action
- `PENDING` — waiting for your tap
- `MANUAL` — trade opened directly in MT5 (not via bot)

**Win/Loss detection:** Poller queries MT5 deal history every **60 seconds**. Captures both bot-placed and manually placed/closed trades.

Dashboard auto-refreshes every 30 seconds.

---

## Night trading agent

Active window (default): **10 PM → 6 AM MYT** — covers London open + New York session.

| `AGENT_AUTO_EXECUTE` | Behaviour |
|---|---|
| `false` (default) | Sends EXECUTE/SKIP buttons — you stay in control |
| `true` | Executes automatically — you get a notification after |

Start with `false`. Switch to `true` only after weeks of trusting the signals.

---

## Running tests

```bash
python -m pytest test_margin_guard.py -v
```

23 unit tests covering all 6 guards: margin level, stack, RR ratio, spread, entry proximity, and a live MT5 reflection test.

---

## Troubleshooting

| Problem | Fix |
|---------|-----|
| `IPC timeout` on MT5 | Open MT5 as Administrator before running bot.py |
| `Symbol XAUUSD not found` | Check `MT5_SYMBOL_SUFFIX` in .env (VT Markets = `-STD`) |
| `AttributeError: 'User' has no 'title'` | Use numeric group ID in `SIGNAL_GROUP` |
| `Forbidden` sending Telegram message | Open your bot and press Start |
| `ModuleNotFoundError` | Run `pip install -r requirements.txt` |
| MT5 login failed | Check `MT5_SERVER` — must match exactly including spaces |
| Telethon OTP keeps asking | Delete `data/session*` and re-login |
| Trade blocked — margin level | Margin level below 300% — close some positions |
| Trade blocked — stack guard | Already have same direction open — wait for it to close |
| Trade blocked — spread | Spread too wide — wait for market to settle |
| Trade blocked — RR ratio | Hafiz's TP is smaller than SL — not worth the risk |
| Trade skipped — entry proximity | Price too far from entry zone — tap again when price is closer |
| Dashboard shows no data | Run `docker start mysql-docker` before bot.py |
| Manual trade not showing | Dashboard poller syncs every 60 sec — wait one minute |

---

## Security

- **Never commit `.env`** — it's in `.gitignore`
- `data/session` is your Telegram login — treat it like a password
- `AGENT_AUTO_EXECUTE=true` means real money trades without your tap — start with `false`

**T.A.Y.O.R — Trade At Your Own Risk.**
