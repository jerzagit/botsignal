# SignalBot

Fully automatic Telegram-to-MT5 trading bot. Reads your mentor's signals, watches price, and builds positions using DCA-style layered entries — no tapping required.

---

## How it works

### Standard mode (`LAYER_MODE=false`)

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

### Layered DCA mode (`LAYER_MODE=true`)

```
Hafiz posts signal in Telegram group
        ↓
Bot calculates total lot + DYNAMIC layer count
  (min(LAYER_COUNT, int(total_lot / MIN_LOT)))
  → $200 account → 3 layers | $500 → 5 | $1000+ → 7
        ↓
"📍 Layer 1/N placed" — L1 fires when price enters zone
        ↓
Price dips 35 pips deeper → "📍 Layer 2/N placed" (better entry)
        ↓
Price dips 35 more pips → "📍 Layer 3/N placed" (best entry)
        ↓
(continues for all N layers)
        ↓
When upper layers hit TP → deepest layer moves to breakeven
        ↓
"🔒 L1–LN-1 TP secured → deepest layer free ride!" ♻️
```

---

## Project structure

```
signalbot/
├── bot.py                    ← Entry point — run this
├── .env                      ← Your secrets (never commit)
├── requirements.txt
│
├── core/
│   ├── config.py             ← All settings loaded from .env
│   ├── signal.py             ← Signal parser + CloseAlert parser
│   ├── risk.py               ← Lot calculator (margin % ÷ SL distance)
│   ├── mt5.py                ← MT5 connection, 6 guards, trade execution
│   ├── listener.py           ← Telethon: watches group as your account
│   ├── notifier.py           ← Telegram bot: close alert buttons
│   ├── watcher.py            ← Standard price watcher (LAYER_MODE=false)
│   ├── layer_watcher.py      ← DCA layered entry state machine (LAYER_MODE=true)
│   ├── state.py              ← In-memory pending signals + close plans
│   └── db.py                 ← MySQL write functions
│
├── dashboard/
│   ├── app.py                ← Flask web dashboard (http://localhost:5000)
│   ├── poller.py             ← MT5 win/loss outcome poller (60-sec interval)
│   └── templates/
│       └── index.html        ← Dashboard UI
│
├── data/
│   ├── session               ← Telethon session (auto-created on first run)
│   ├── bot.pid               ← PID lock (prevents duplicate instances)
│   └── trades.json           ← Local trade log (also mirrored to MySQL)
│
├── db/
│   └── init.sql              ← MySQL schema (auto-applied on first run)
│
├── logs/
│   └── bot.log               ← Full log file
│
├── test_margin_guard.py      ← Unit tests for all trade guards
├── test_layer.py             ← Live UAT test: simulates a buy signal through layered DCA
└── sim_dca.py                ← Offline profit/risk simulator for DCA scenarios
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

Change **one line** in `.env`:

```env
ENV_MODE=demo    # UAT testing
ENV_MODE=live    # real money
```

Restart `bot.py`. Log into the matching account in MT5 terminal first.
Both sets of credentials stay in `.env` — no commenting/uncommenting needed.

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

# ── Layered DCA entry ─────────────────────────────────
LAYER_MODE=false               # true = DCA layers | false = TRADE_SPLIT
LAYER_COUNT=7                  # max layers (actual count is dynamic)
LAYER2_PIPS=35                 # pip gap between each layer

# ── Trade split (used when LAYER_MODE=false) ──────────
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

## Layered DCA entry system

When `LAYER_MODE=true`, instead of placing all positions at once, the bot builds a position progressively as price moves in your favour.

### Dynamic layer count

The number of layers is calculated automatically from your account size:

```
actual_layers = min(LAYER_COUNT, int(total_lot / MIN_LOT))
```

| Free margin | Total lot (10% risk) | Layers |
|-------------|----------------------|--------|
| ~$200       | ~0.03                | 3      |
| ~$500       | ~0.05                | 5      |
| ~$1,000+    | ~0.10+               | 7      |

Your account grows → more layers placed automatically. No config change needed.

### Layer trigger prices

```
L1 entry:  price enters zone           (standard proximity guard)
L2 entry:  L1_entry − 35 pips (buy)   (35 pips deeper = better price)
L3 entry:  L1_entry − 70 pips (buy)   (70 pips deeper = even better)
LN entry:  L1_entry − N×35 pips       (each layer 35 pips further)
```

For sell signals, pips are added (higher price = better sell entry).

### TP assignment

```
L1 … LN-1  →  cycle through Hafiz's TP list (quick exits)
LN (deepest) →  furthest TP (free ride position)
```

### Breakeven trigger

When all upper layers (L1 … LN-1) close at TP:
→ deepest layer (LN) automatically moves SL to breakeven.
→ You've locked profit and LN runs risk-free to the furthest TP.

### Risk preservation

```
Max loss if all layers SL = RISK_PERCENT × free_margin   (same as today)
Max loss if only L1 placed = total_lot / LAYER_COUNT × SL_value
```

If price never reaches L2 or beyond, only L1 is at risk — a fraction of planned exposure.

### Telegram messages

```
📍 Layer 1/3 placed @ 3200.00
   XAUUSD BUY | Lot: 0.01 | TP: 3220.00
   📍 L2 triggers @ 3196.50 (35p deeper)

📍 Layer 2/3 placed @ 3196.50
   XAUUSD BUY | Lot: 0.01 | TP: 3250.00
   📍 L3 triggers @ 3193.00 (35p deeper)

📍 Layer 3/3 placed @ 3193.00
   XAUUSD BUY | Lot: 0.01 | TP: 3220.00
   🎯 All layers active — monitoring TPs

🔒 L1–L2 TP secured → L3 free ride!
   XAUUSD BUY | #12345678 moved to breakeven ♻️
```

---

## Trade guard system

Every trade — and **every layer** in DCA mode — passes through **6 sequential guards** before an order hits MT5. Guards run inside `execute_trade()` in `core/mt5.py`.

### Guard order

| # | Guard | Env var | Default | Fires when |
|---|-------|---------|---------|-----------|
| 1 | **Margin level** | `MIN_MARGIN_LEVEL` | 300% | `equity / used_margin × 100 < 300%`. Skipped if no open trades (margin = 0). |
| 2 | **Same-direction stack** | `BLOCK_SAME_DIRECTION_STACK` | true | Same symbol + direction already open at risk. Own session's DCA tickets and breakeven positions are **exempt**. |
| 3 | **Auto TP + RR ratio** | `MIN_RR_RATIO`, `SL_MIN_PIPS`, `TP_ENFORCE_PIPS` | 1.4 / 50p / 70p | Uses `entry_mid` for calculation. If SL < 50 pips, TP auto-overridden to 70 pips from mid first. Blocks if TP/SL still < 1.4 after override. |
| 4 | **Spread** | `MAX_SPREAD_PIPS` | 3 pips | Live broker spread > 3 pips. Retries automatically next interval — never fatal. |
| 5 | **Entry proximity** | `ENTRY_MAX_DISTANCE_PIPS` | 50 pips | Price > 50 pips from entry zone. **Skipped for L2+ in DCA mode** — deeper entries are intentionally outside the zone. |
| 6 | **Lot calculation** | — | — | `calculate_lot()` returns 0 — margin too thin for even MIN_LOT. |

All guard fires are logged to the `guard_events` MySQL table and visible in the dashboard Trade Guards panel.

### Guard behaviour: standard mode vs DCA mode

| Situation | Standard mode | DCA mode |
|-----------|--------------|----------|
| Any guard blocks | Trade skipped, notify | Depends on which layer (see below) |
| Spread too wide | Watcher retries next 30s | Same — retries next interval for any layer |
| L1 blocked (non-spread) | Trade skipped | **Session ends** — same consequence |
| L2+ blocked by spread | — | Retry next interval |
| L2+ blocked by other guard | — | **Layer skipped**, bot continues watching for the next layer trigger |
| Proximity guard | Blocks if price > 50p from zone | L1: active. **L2+: skipped by design** (deeper entries are always outside zone) |
| Stack guard | Blocks own prior trades | Own DCA session tickets passed as `own_tickets` — **exempt from stack check** |

### Lot sizing in DCA mode — important detail

The lot is sized **once** at signal arrival using `entry_mid` (midpoint of entry zone), not the actual execution price:

```
risk_amount  = free_margin × RISK_PERCENT        # e.g. $100 on $1,000 account
sl_pips_mid  = (entry_mid − SL) / pip_size       # e.g. 40 pips (mid to SL)
risk_per_lot = sl_pips_mid × pip_value           # e.g. $400 per lot
total_lot    = risk_amount / risk_per_lot         # e.g. 0.25 lots
lot_per_layer = total_lot / actual_layers         # e.g. 0.12 each (2 layers)
```

Because L1 executes at the **zone top** (further from SL) and L2 executes **deeper** (closer to SL), the actual dollar risk per layer differs from the designed split — but they roughly average out near the 10% budget. Worst-case combined loss across all layers is ≈ `RISK_PERCENT × free_margin`.

### SL safety cap — layers can never cross the SL

Before the watcher loop starts, `layer_watcher.py` automatically caps the layer count so no trigger price reaches or crosses the SL:

```
sl_pips      = (entry_mid − SL) / pip_size
safe_steps   = int((sl_pips − 1) / LAYER2_PIPS)   # −1 pip buffer
max_by_sl    = 1 + safe_steps
actual_layers = min(by_margin, max_by_sl)
```

Example — BUY zone 5081–5085, SL 5079, LAYER2_PIPS=35:
```
sl_pips    = 40
safe_steps = int(39 / 35) = 1  →  max_by_sl = 2
→ only 2 layers placed (L1 @ 5085, L2 @ 5081.5)
→ L3 would be @ 5078.0 — below SL → blocked before even starting
```

A runtime check also runs each tick — if the next trigger is at or beyond SL, the bot jumps straight to monitoring without placing that layer.

---

## Trade split *(standard mode only)*

When `LAYER_MODE=false`, each signal is split into `TRADE_SPLIT` equal positions so you can take partial profit independently at different TP levels.

```
TRADE_SPLIT=5, lot=0.50  →  5 × 0.10 lot
TPs cycled:  pos 1,3,5 → TP1  |  pos 2,4 → TP2
```

**Risk is preserved:** if the account is too small to split without exceeding MIN_LOT, the bot automatically reduces the number of splits.

```
TRADE_SPLIT=5, lot=0.01  →  1 × 0.01 lot  (can't split below MIN_LOT)
TRADE_SPLIT=5, lot=0.05  →  5 × 0.01 lot  ✅
```

> When `LAYER_MODE=true`, TRADE_SPLIT is ignored — each layer is always 1 order.

---

## Lot sizing formula

```
risk_amount  = free_margin × RISK_PERCENT
sl_in_ticks  = sl_distance / tick_size
risk_per_lot = sl_in_ticks × tick_value
lot_size     = risk_amount / risk_per_lot
lot_size     = clamp(lot_size, MIN_LOT, MAX_LOT)
lot_size     = round to broker volume step

Standard mode:  split_lot = lot_size / actual_splits
Layered mode:   layer_lot = lot_size / actual_layers  (dynamic)
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

### Collect Profit
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
| Trade blocked — spread | Watcher retries automatically when spread normalises |
| Trade blocked — RR ratio | TP/SL < 1.4 and TP couldn't be auto-adjusted |
| Trade skipped — proximity | Price too far — watcher keeps checking every 30s |
| L2 blocked (DCA mode) | Non-spread guard blocked L2 — bot skips it and watches for L3 |
| L2 spread retry (DCA mode) | Normal — bot retries next 30s interval until spread normalises |
| Only L1 placed then stopped | L1 SL hit before price dipped to L2 — normal risk behaviour |
| Dashboard shows no data | `docker start mysql-docker` before bot.py |
| Manual trade not showing | Poller syncs every 60s — wait one cycle |
| `cryptography` error | `pip install cryptography` |

---

## Security

- **Never commit `.env`** — it's in `.gitignore`
- `data/session` is your Telegram login — treat it like a password
- `AGENT_AUTO_EXECUTE=true` means real money trades without your tap — start with `false`

**T.A.Y.O.R — Trade At Your Own Risk.**
