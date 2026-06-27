# SignalBot — Commercialization Roadmap

## Vision
Turn the personal Telegram-to-MT5 bot into a multi-user SaaS platform where signal
providers publish trades and subscribers auto-copy them to their own MT5 accounts.

---

## Phase 1: Foundation (Single-User Polish)

| Item | Why |
|------|-----|
| Set up MySQL | Zone persistence, trade history, dashboard |
| Fix `asyncio.FIRST_COMPLETED` exit bug | Bot should NOT die when one service crashes |
| Add graceful listener reconnection | Telethon disconnects kills the bot today |
| Replace JSON trade log with DB | Single source of truth |
| Add `/status` command | Quick health check via Telegram |
| Add daily P&L report | Auto-sent at midnight |

---

## Phase 2: Multi-Signal-Provider

Instead of hardcoding one Telegram group, allow the bot to follow **N signal providers**
simultaneously, each with their own config:

```
signal_providers:
  - name: hafiz
    group: "PIPS FIGHTER 2026"
    risk_percent: 0.05
    symbol_filter: [XAUUSD]
    max_daily_loss: 50
  - name: john
    group: "@johnsignals"
    risk_percent: 0.02
    symbol_filter: [EURUSD, GBPUSD]
    max_daily_loss: 20
```

Each provider gets:
- Independent risk % and guards
- Independent daily loss limit
- Own P&L tracking

---

## Phase 3: Multi-User (Paid Subscriptions)

This is the big leap — users connect their own MT5 accounts.

### Architecture

```
                    ┌──────────────────┐
                    │   Web Dashboard  │
                    │  (Flask/React)   │
                    └──────┬───────────┘
                           │ REST API
                    ┌──────▼───────────┐
                    │   Core Engine    │
                    │ (single process) │
                    └──────┬───────────┘
                           │
        ┌──────────────────┼──────────────────┐
        ▼                  ▼                  ▼
┌──────────────┐  ┌──────────────┐  ┌──────────────┐
│  MT5 Relay   │  │  MT5 Relay   │  │  MT5 Relay   │
│ (User A)     │  │ (User B)     │  │ (User C)     │
│ VPS #1       │  │ VPS #2       │  │ VPS #3       │
└──────────────┘  └──────────────┘  └──────────────┘
```

### Key Components

**MT5 Relay Agent** (lightweight per-user):
- Tiny Python script running on the user's VPS
- Connects to their MT5 account
- Receives orders from core engine via WebSocket/API
- Reports back fills, P&L, margin status

**Core Engine** (your server):
- Listens to signal providers
- Calculates lots per user (each user has independent risk %)
- Sends orders to each user's relay
- Tracks all trades in central DB

**Web Dashboard**:
- User signup/login with subscription tiers
- Connect MT5 credentials (stored encrypted)
- Choose which signal providers to follow
- Real-time P&L, open positions, trade history
- Risk settings per provider per user

### Subscription Tiers (Example)

| Tier | Price | Signals | Max Accounts | Risk Control |
|------|-------|---------|-------------|-------------|
| Basic | $9/mo | 1 provider | 1 | Fixed % only |
| Pro | $29/mo | All providers | 3 | Full guards |
| Elite | $79/mo | All + priority | 10 | Everything + API |

### Payment Integration
- Stripe/Paddle for recurring billing
- Telegram bot handles signup link
- Auto-provision relay agent on user's VPS

---

## Phase 4: Own Signal Engine (Replace Hafiz Dependency)

Move beyond copying other people's signals:

1. **AI/ML signals** — train on historical data, generate own entry/exit
2. **Strategy marketplace** — let users subscribe to community strategies
3. **Copy-trading** — users follow top performers on the platform

---

## Technical Must-Haves Before Scaling

| Concern | Solution |
|---------|----------|
| No single point of failure | Core engine on cloud (AWS/GCP), relays on user VPS |
| MT5 disconnections | Auto-reconnect with exponential backoff, health pings |
| Order execution race conditions | Per-user mutex, FIFO queue for orders |
| Credential security | AES-256 encryption at rest, per-user encryption keys |
| Rate limits / Telegram bans | Multiple Telethon clients with staggered reconnects |
| Database scaling | Postgres over MySQL for production, read replicas |
| Logging & monitoring | Centralized logging (ELK), uptime monitoring (Better Uptime) |

---

## Go-To-Market

1. **Validate** — Offer to 5-10 fellow traders manually (you run their relay)
2. **MVP Dashboard** — Basic Flask dashboard, manual onboarding
3. **Self-serve** — Stripe + automated relay deployment
4. **Scale** — Marketing via Telegram trading groups, affiliate program

---

## Quick Wins (Do This Week)

- [ ] Set up MySQL — get persistent trade log working
- [ ] Fix the FIRST_COMPLETED crash — bot should survive a service crash
- [ ] Add `/signals` command — list recent signals with status
- [ ] Add daily P&L auto-report

These alone make the bot more reliable and commercially presentable.

---

## Public Dashboard — Deploy Today

A token-protected public dashboard already exists at:

| File | Purpose |
|------|---------|
| `dashboard/public_app.py` | Public Flask app (strips MT5 balance, tickets, lots) |
| `dashboard/templates/public_login.html` | Token entry page |

### What it shows publicly
- Total signals, executed, skipped, expired
- Win rate, wins, losses, open trades
- 30-day P&L bar chart
- Guard status (session, margin, stack, spread, etc.)
- Signal log with entry zone, direction, outcome

### What it hides
- Account balance, equity, free margin
- Ticket numbers, lot sizes, entry prices
- Individual trade P&L values

### How to deploy (free tier)

**Option 1: Render.com** (easiest)
1. Push code to a GitHub repo
2. Create a `requirements.txt`:
   ```
   flask>=3.0
   pymysql>=1.0
   python-dotenv>=1.0
   cryptography>=41.0
   ```
3. On Render: New Web Service → select repo
4. Start command: `python dashboard/public_app.py`
5. Set env vars: `PUBLIC_ACCESS_TOKEN`, `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`, `DB_PASSWORD`

**Option 2: PythonAnywhere** (has free MySQL)
1. Upload code to PythonAnywhere
2. Create a MySQL database in their dashboard
3. Set up a web app pointing to `dashboard/public_app.py`
4. Same env vars as above

**Option 3: VPS** (most control)
```
# Install deps
pip install flask pymysql python-dotenv cryptography

# Set env vars
export PUBLIC_ACCESS_TOKEN=mysecrettoken
export PUBLIC_PORT=8080
export DB_HOST=your-server-ip

# Run
python dashboard/public_app.py
```

### Important
- Your MySQL database must be accessible from the cloud host (open firewall or use a cloud DB)
- For true production, add a reverse proxy (nginx/caddy) with HTTPS
- Change `FLASK_SECRET_KEY` to a fixed value so session survives restarts
