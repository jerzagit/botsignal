# SignalBot 🤖📈

Auto-trade your mentor's Telegram signals on MT5 — with smart risk management and a night trading agent.

---

## How it works

```
Mentor posts signal in group
        ↓
Telethon reads it (as your account — no admin needed)
        ↓
Bot calculates lot size from your margin %
        ↓
Sends you: EXECUTE ✅ / SKIP ❌
        ↓
You tap → trade placed on MT5
        ↓  (if agent is on + night hours)
Agent auto-executes while you sleep 🌙
```

---

## Project structure

```
signalbot/
├── bot.py              ← Entry point — run this
├── .env                ← Your secrets (never commit this)
├── .env.example        ← Template — copy to .env
├── requirements.txt
│
├── core/
│   ├── config.py       ← All settings from .env
│   ├── signal.py       ← Signal parser (understands mentor's format)
│   ├── risk.py         ← Auto lot calculator (margin % ÷ SL distance)
│   ├── mt5.py          ← MT5 connection + trade execution + trade log
│   ├── listener.py     ← Telethon: watches group as your account
│   ├── notifier.py     ← Telegram bot: sends you EXECUTE/SKIP buttons
│   └── state.py        ← Shared pending signals dict
│
├── agent/
│   └── agent.py        ← Night trading agent (10 PM–6 AM MYT)
│
├── dashboard/          ← (Coming next) Web dashboard for trade history
│
├── data/
│   ├── session         ← Telethon session file (auto-created)
│   └── trades.json     ← Trade log (auto-created, feeds dashboard)
│
└── logs/
    └── bot.log         ← Full log file
```

---

## Quick start

### 1 — Install Python 3.11+

Download from https://python.org. Tick **"Add Python to PATH"** during install.

### 2 — Clone / open in VSCode

Open this folder in VSCode. Install the **Python** extension if you haven't.

### 3 — Install dependencies

```bash
pip install -r requirements.txt
```

### 4 — Get your Telegram API keys

1. Go to https://my.telegram.org
2. Log in with your phone
3. Click **API development tools**
4. Fill any app name → **Create**
5. Copy `App api_id` and `App api_hash`

### 5 — Create your bot

1. Open Telegram → search **@BotFather**
2. Send `/newbot`
3. Follow prompts → copy the **token** (looks like `123456789:AAF...`)
4. **Important:** search your new bot by name → tap **Start** (otherwise it can't message you)

### 6 — Get your Chat ID

1. Open Telegram → search **@userinfobot**
2. Send `/start`
3. Copy your **Id** number

### 7 — Find your MT5 server name

1. Open MetaTrader 5
2. File → Open an Account
3. Server name shown there (e.g. `Exness-MT5Real`, `ICMarketsEU-MT5`)

### 8 — Configure .env

```bash
cp .env.example .env
```

Open `.env` and fill in everything:

```env
TG_API_ID=12345678
TG_API_HASH=your_hash_here
BOT_TOKEN=123456789:AAF...
YOUR_CHAT_ID=987654321
SIGNAL_GROUP=AssistByHafizCarat

MT5_LOGIN=12345678
MT5_PASSWORD=your_password
MT5_SERVER=YourBroker-Server

RISK_PERCENT=0.05        # 5% of free margin per trade
MIN_LOT=0.01
MAX_LOT=0.50

AGENT_START_HOUR_MY=22   # 10 PM Malaysia time
AGENT_END_HOUR_MY=6      # 6 AM Malaysia time
AGENT_AUTO_EXECUTE=false # set true only when you're confident
AGENT_ENABLED=true
```

### 9 — Run

```bash
python bot.py
```

**First run:** Telethon will ask for your phone number → then a Telegram OTP code.
Enter them and press Enter. After that, a session file is saved — you won't be asked again.

You'll receive a Telegram message from your bot confirming it's live and which group it's watching.

---

## Risk management — how lot sizing works

Every trade risks a fixed **% of your free margin**, automatically sized to the SL distance.

**Formula:**
```
risk_amount  = free_margin × RISK_PERCENT
sl_distance  = |entry_price − stop_loss|
risk_per_lot = (sl_distance ÷ tick_size) × tick_value
lot_size     = risk_amount ÷ risk_per_lot
lot_size     = clamp(lot_size, MIN_LOT, MAX_LOT)
lot_size     = round to broker's volume step
```

**Example (XAUUSD):**
```
Free margin:  $1,000
Risk %:       5%  →  risk_amount = $50
SL distance:  7 points (e.g. entry 5097, SL 5103)
Tick value:   $1 per tick per lot (XAUUSD typical)
risk_per_lot: 7 ÷ 0.01 × $0.01 = $7 per lot (approx)
lot_size:     $50 ÷ $7 ≈ 0.07 lots → rounds to 0.07
```

This means:
- When your account grows → lot size automatically increases
- When your account shrinks → lot size automatically decreases
- You can NEVER lose more than RISK_PERCENT of your margin per trade (in theory, before slippage)

Guardrails: `MIN_LOT` and `MAX_LOT` prevent the formula from going wild.

---

## Signal format

The bot understands your mentor's exact format:

```
xauusd sell @5096-5100
sl 5103
tp 5092
tp 5090
Trade At Your Own Risk
T.A.Y.O.R @AssistByHafizCarat
```

It also handles:
- Single entry price: `xauusd buy @5096`
- Range entry: `xauusd sell @5096-5100`
- Multiple TPs: as many `tp PRICE` lines as needed
- Case insensitive: XAUUSD or xauusd both work
- Extra text (like T.A.Y.O.R) is ignored

---

## Night trading agent

The agent runs on your machine while you sleep. Malaysia night = London + New York session = best time for gold (XAUUSD).

**Active window (default):** 10 PM → 6 AM MYT

| Malaysia Time | Session       |
|--------------|---------------|
| 2:00 PM      | London Open   |
| 8:00 PM      | New York Open |
| 10:00 PM     | NY + Overlap  |
| 6:00 AM      | NY Close      |

**Two modes:**

| `AGENT_AUTO_EXECUTE` | What happens |
|---|---|
| `false` (default) | Still sends you EXECUTE/SKIP buttons. You stay in control. |
| `true` | Executes automatically. You get a notification after. |

**Recommendation:** Start with `false`. Switch to `true` only after several weeks of trusting the signals and the lot sizing.

**Hourly heartbeat:** While active, the agent pings you every hour so you know it's alive.

---

## Dashboard (coming next)

All executed trades are saved to `data/trades.json`. This file is structured to feed a web dashboard showing:

- Open trades
- Trade history with P&L
- Win rate, avg RR ratio
- Lot size history
- Session performance (London vs NY)

Dashboard will be built as a separate `dashboard/` module — web-based, runs locally.

---

## Running with Claude Code (VSCode)

This project is structured to work directly in Claude Code:

1. Open the `signalbot/` folder in VSCode
2. Claude Code can read, edit, and run any file here
3. Suggested Claude Code tasks:
   - "Add a /status command to the bot that shows open trades"
   - "Build the dashboard in dashboard/"
   - "Add a trailing stop feature to mt5.py"
   - "Write tests for the signal parser"

---

## Troubleshooting

| Problem | Fix |
|---|---|
| `ModuleNotFoundError` | Run `pip install -r requirements.txt` again |
| MT5 login failed | Check MT5_LOGIN, MT5_PASSWORD, MT5_SERVER in .env |
| Group not found | Try the full username without @ first. If that fails, use the numeric group ID. |
| Bot not messaging you | Make sure you opened the bot in Telegram and pressed Start |
| Telethon OTP keeps asking | Delete `data/session*` files and re-login |
| Trade not executing | Make sure MT5 is open and logged in on your PC |
| Lot too small/large | Adjust RISK_PERCENT, MIN_LOT, MAX_LOT in .env |

---

## Security notes

- **Never commit `.env`** to git. It's in `.gitignore`.
- The `data/session` file is your Telegram login — treat it like a password.
- `AGENT_AUTO_EXECUTE=true` means real money trades without your tap. Start with `false`.
- This bot is a tool. Your mentor's signals are not guaranteed profit.

**T.A.Y.O.R — Trade At Your Own Risk.**
