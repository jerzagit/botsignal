# SignalBot

Fully automatic Telegram-to-MT5 trading bot. Reads your mentor's signals, watches price, and executes trades with split entries — no tapping required.

---

## How it works

```
Hafiz posts signal in Telegram group
        ↓
Telethon reads it (as your account — no admin needed)
        ↓
Bot sends "👀 Watching..." notification to your Telegram
        ↓
Price watcher checks every 30s — waiting for price to enter zone
        ↓
Price enters zone → 6 guards run
        ↓
Trade split into N equal positions (TRADE_SPLIT)
        ↓
All positions placed on MT5 with auto-calculated lot size
        ↓
"🤖 Auto-Executed!" sent to your Telegram with all tickets
        ↓
Dashboard records trades, polls outcome every 60 seconds
```

---

## Project structure

```
signalbot/
├── bot.py                  ← Entry point — run this
├── .env                    ← Your secrets (never commit)
├── requirements.txt
│
├── core/
│   ├── config.py           ← All settings loaded from .env
│   ├── signal.py           ← Signal parser + CloseAlert parser
│   ├── risk.py             ← Lot calculator (margin % ÷ SL distance)
│   ├── mt5.py              ← MT5 connection, guards, trade execution
│   ├── listener.py         ← Telethon: watches group as your account
│   ├── notifier.py         ← Telegram bot: close alert buttons
│   ├── watcher.py          ← Price watcher: auto-executes when price enters zone
│   ├── state.py            ← In-memory pending signals + close plans
│   └── db.py               ← MySQL write functions
│
├── dashboard/
│   ├── app.py              ← Flask web dashboard (http://localhost:5000)
│   ├── poller.py           ← MT5 win/loss outcome poller (60-sec interval)
│   └── templates/
│       └── index.html      ← Dashboard UI
│
├── data/
│   ├── session             ← Telethon session (auto-created on first run)
│   ├── bot.pid             ← PID lock (prevents duplicate instances)
│   └── trades.json         ← Local trade log (also mirrored to MySQL)
│
├── db/
│   └── init.sql            ← MySQL schema (auto-applied on first run)
│
├── logs/
│   └── bot.log             ← Full log file
│
└── test_margin_guard.py    ← Unit tests for all trade guards
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
2. Copy the token
3. Search your new bot → tap **Start**

### 5 — Get your Chat ID and Group ID

- **Your Chat ID:** Send `/start` to **@userinfobot**
- **Signal Group ID:** Forward any message from the group to **@userinfobot** — use numeric ID (e.g. `-1002083967629`)

### 6 — Set up MT5

- Download MT5 from your **broker's website** (not MetaQuotes)
- **Run MT5 as Administrator** — required for Python IPC
- Enable **Algo Trading** button in toolbar (must be green)
- Find your symbol suffix in MT5 Market Watch (VT Markets uses `-STD` for live, `-VIP` for demo)

### 7 — Start MySQL

```bash
docker start mysql-docker
```

### 8 — Configure .env

```bash
copy .env.example .env
```

Fill in all values — see the full reference below.

### 9 — Run

```bash
python bot.py
```

**First run:** Telethon asks for your phone and a Telegram OTP. Enter once — session saved to `data/session`, never asked again.

You'll receive **"SignalBot is LIVE!"** on Telegram confirming it's running.

---

## Switching between DEMO and LIVE

In `.env`, comment/uncomment the account block:

```env
# ── DEMO ──────────────────────────────────────────────
MT5_LOGIN=1067995
MT5_PASSWORD=...
MT5_SERVER=VTMarkets-Demo
MT5_SYMBOL_SUFFIX=-VIP

# ── LIVE ──────────────────────────────────────────────
#MT5_LOGIN=26656038
#MT5_PASSWORD=...
#MT5_SERVER=VTMarkets-Live 5
#MT5_SYMBOL_SUFFIX=-STD
```

Restart `bot.py` after switching.

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
MT5_SERVER=YourBroker-Live 5
MT5_SYMBOL_SUFFIX=-STD

# ── Risk management ───────────────────────────────────
RISK_PERCENT=0.10              # 10% of free margin to risk per trade
MIN_LOT=0.01
MAX_LOT=0.50

# ── Trade split ───────────────────────────────────────
TRADE_SPLIT=5                  # split each signal into N equal positions

# ── Signal timing ─────────────────────────────────────
SIGNAL_EXPIRY=1800             # 30 min — watcher window
WATCH_INTERVAL_SECS=30         # how often watcher checks price

# ── Entry proximity guard ─────────────────────────────
ENTRY_MAX_DISTANCE_PIPS=50     # skip if price > 50 pips from zone

# ── Margin level guard ────────────────────────────────
MIN_MARGIN_LEVEL=300           # block if margin level < 300%

# ── Spread guard ──────────────────────────────────────
MAX_SPREAD_PIPS=3              # block if spread > 3 pips

# ── Reward:Risk ratio guard ───────────────────────────
MIN_RR_RATIO=1.4               # block if TP/SL < 1.4

# ── Auto TP enforcement ───────────────────────────────
SL_MIN_PIPS=50                 # if SL < this, auto-adjust TP
TP_ENFORCE_PIPS=70             # minimum TP distance when SL is tight

# ── Same-direction stack guard ────────────────────────
BLOCK_SAME_DIRECTION_STACK=true

# ── SL sanity warnings ────────────────────────────────
SL_PIP_SIZE=0.1
SL_WARN_MIN_PIPS=50
SL_WARN_MAX_PIPS=70

# ── MySQL ─────────────────────────────────────────────
DB_HOST=localhost
DB_PORT=3307
DB_NAME=botsignal
DB_USER=root
DB_PASSWORD=...

# ── Night agent ───────────────────────────────────────
AGENT_START_HOUR_MY=22
AGENT_END_HOUR_MY=6
AGENT_AUTO_EXECUTE=false
AGENT_ENABLED=true
```

---

## Trade guard system

Every trade passes through **6 sequential guards** before an order is sent to MT5.

| # | Guard | Default | Behaviour |
|---|-------|---------|-----------|
| 1 | **Margin level** | ≥ 300% | Block if account over-leveraged |
| 2 | **Same-direction stack** | enabled | Block if same symbol+direction already at risk. Breakeven positions are **exempt** — new entries allowed alongside them |
| 3 | **Auto TP + RR ratio** | ≥ 1.4 | If SL < 50 pips, TP auto-adjusted to 70 pips. Block if ratio still < 1.4 |
| 4 | **Spread** | ≤ 3 pips | Block if broker spread too wide (retry on spread normalise) |
| 5 | **Entry proximity** | ≤ 50 pips | Block if price too far from entry zone |
| 6 | **Lot calculation** | — | Block if margin too thin for valid lot |

---

## Trade split

Each signal is split into `TRADE_SPLIT` equal positions so you can take partial profit independently at different TP levels.

```
TRADE_SPLIT=5, lot=0.50  →  5 × 0.10 lot
TPs cycled:  pos 1,3,5 → TP1  |  pos 2,4 → TP2
```

**Risk is preserved:** if the account is too small to split without exceeding MIN_LOT, the bot automatically reduces the number of splits.

```
TRADE_SPLIT=5, lot=0.01  →  1 × 0.01 lot  (can't split below MIN_LOT)
TRADE_SPLIT=5, lot=0.05  →  5 × 0.01 lot  ✅
```

---

## Lot sizing formula

```
risk_amount  = free_margin × RISK_PERCENT
sl_in_ticks  = sl_distance / tick_size
risk_per_lot = sl_in_ticks × tick_value
lot_size     = risk_amount / risk_per_lot
lot_size     = clamp(lot_size, MIN_LOT, MAX_LOT)
lot_size     = round to broker volume step
split_lot    = lot_size / actual_splits
```

If SL hits on all positions, max loss = `RISK_PERCENT` × free margin.

---

## Signal format

```
xauusd buy @4988.50-4984.50
sl 4981.50
tp 4993.50
tp 4991.50
Trade At Your Own Risk
T.A.Y.O.R @AssistByHafizCarat
```

Supported:
- Single price: `xauusd buy @5096`
- Range entry: `xauusd sell @5096-5100`
- Multiple TPs: as many `tp PRICE` lines as needed
- Case insensitive — extra text ignored

---

## Close alert system

### Setup Failed
**Trigger:** `"setup failed"` anywhere in message.
**Action:** Button per signal group + CLOSE ALL button.

### Collect Profit *(new)*
**Trigger:** `"collect profit"`, `"mau collect"`, `"siapa mau collect"`
**Action:** 70% close (most profitable first) + 30% breakeven (free ride). Losing positions untouched.

```
💰 Collect Profit Plan
70% secure · 30% breakeven

💵 CLOSE (3 positions — lock in profit)
🔒 BREAKEVEN (2 positions — free ride)
🔵 UNTOUCHED (losing positions — original SL stays)

[✅ EXECUTE PLAN]  [❌ SKIP]
```

### Early Profit
**Trigger:** `"siapa nak collect"`, `"collect dulu"`, `"dipersilakan"`, `"take profit now"`, `"early tp"`
**Action:** Keep top `BREAKEVEN_KEEP_COUNT` at breakeven, close rest profitable, leave losses.

---

## Shutdown behaviour

When you press **Ctrl+C**:
- Bot stops cleanly
- All open positions **stay alive** in MT5 — SL/TP remain active on broker
- You receive a Telegram message: *"SignalBot stopped — positions still running"*
- Restart `python bot.py` to resume signal monitoring

**If bot crashes unexpectedly**, positions continue running with original SL/TP. No trades are auto-closed on crash.

---

## Duplicate instance protection

`bot.py` writes a PID lock to `data/bot.pid` on startup. If you try to run a second copy while one is already running, it prints the existing PID and exits instead of conflicting with Telegram.

To force-kill an old instance:
```bash
taskkill /PID <pid> /F
```

---

## Dashboard

Run: `python dashboard/app.py` → http://localhost:5000

For remote access run `ngrok http 5000` in a separate terminal.

| Badge | Meaning |
|-------|---------|
| `EXECUTED` | Trade placed via bot |
| `SKIPPED` | You tapped SKIP |
| `EXPIRED` | 30 min passed, price never reached zone |
| `PENDING` | Watcher active — waiting for price |
| `MANUAL` | Trade opened directly in MT5 |
| `WIN` | Closed in profit |
| `LOSS` | Closed at a loss |
| `OPEN` | Trade still running |

---

## Running tests

```bash
python -m pytest test_margin_guard.py -v
```

---

## Troubleshooting

| Problem | Fix |
|---------|-----|
| `Conflict: terminated by other getUpdates` | Another bot instance running — `taskkill /PID <pid> /F` or check `data/bot.pid` |
| `IPC timeout` on MT5 | Open MT5 as Administrator before running bot.py |
| `AutoTrading disabled` (code 10027) | Click **Algo Trading** button in MT5 toolbar — must be green |
| `Symbol XAUUSD not found` | Check `MT5_SYMBOL_SUFFIX` — live=`-STD`, demo=`-VIP` |
| `Invalid stops` | Signal entry/SL/TP prices don't match current price — signal may be outdated |
| `AttributeError: 'User' has no 'title'` | Use numeric group ID in `SIGNAL_GROUP` |
| `Forbidden` Telegram message | Open your bot in Telegram and press Start |
| `ModuleNotFoundError` | `pip install -r requirements.txt` |
| MT5 login failed | Check `MT5_SERVER` — must match exactly including spaces |
| Telethon OTP keeps asking | Delete `data/session*` and re-login |
| Trade blocked — margin | Margin level < 300% — close some positions |
| Trade blocked — stack | Same direction open at risk — wait or move to breakeven |
| Trade blocked — spread | Spread too wide — watcher will retry automatically |
| Trade blocked — RR ratio | TP/SL < 1.4 and TP couldn't be auto-adjusted |
| Trade skipped — proximity | Price too far — watcher keeps checking every 30s |
| Dashboard shows no data | `docker start mysql-docker` before bot.py |
| Manual trade not showing | Poller syncs every 60s — wait one cycle |
| `cryptography` error | `pip install cryptography` |

---

## Security

- **Never commit `.env`** — it's in `.gitignore`
- `data/session` is your Telegram login — treat it like a password
- `AGENT_AUTO_EXECUTE=true` means real money trades without your tap — start with `false`

**T.A.Y.O.R — Trade At Your Own Risk.**
