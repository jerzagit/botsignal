# SignalBot

Telegram-to-MT5 trading bot with a **strategy plugin architecture**, **historical replay/backtesting**, and a **research dashboard**.

This document describes how to use the feature branch that adds those research capabilities.

---

## Feature Branch

This README matches:

```text
feature/backtest-research-dashboard
```

Clone and checkout:

```powershell
git clone https://github.com/jerzagit/botsignal.git
cd botsignal
git checkout feature/backtest-research-dashboard
```

> Cloning `master` alone does **not** guarantee these features until this branch is merged.

Current untracked/local helpers (for example `gateway_launch.ps1`) are **not** part of the first feature commit. Do not rely on files that are not in the repository.

---

## What This Branch Adds

Verified in this branch:

| Area | What you get |
|------|----------------|
| Strategy plugins | Explicit registry under `core/strategies/` |
| V1 baseline | `breakout_retest_v1` (default / production-compatible) |
| Experimental V2 | `structure_pullback_v2`, `structure_pullback_v2_1`, `structure_pullback_v2_2` |
| Historical replay | `backtest/` package — decisions, optional simulated trades |
| Safety gate | Backtests require `RUN_MODE=BACKTEST` (or `REPLAY` / `SIM`) |
| Geometry diagnostics | Passive RR / entry / SL / TP research (not live strategy changes) |
| Research UI | Dashboard `/backtests` list, detail, funnel, compare |
| Schema | Fresh DB via `db/init.sql` (no user data shipped) |
| Ignore hygiene | Datasets, run artifacts, broker captures, `.env` stay local |

**Important:** V2 / V2.1 / V2.2 are **experimental research strategies**. They are not claimed profitable or production-ready. Default remains V1.

---

## Architecture Overview

```text
Market data (live MT5  OR  historical dataset CSVs)
        |
        v
  MarketContext  (multi-TF candles)
        |
        v
  Strategy plugin  (registry: ACTIVE_STRATEGY / --strategy)
        |
        v
  StrategyDecision  (WAIT / SKIP / BUY / SELL + entry/SL/TP)
        |
        v
  Guards / risk  (session, margin, spread, RR, …)
        |
        +---- LIVE ----> MT5 execute_trade + Telegram + MySQL
        |
        +---- BACKTEST -> Simulated fills / funnel / performance artifacts
```

Strategy plugins evaluate market context and return decisions. They do **not** own MT5 order placement, Telegram delivery, or production DB writes. Live orchestration stays in `core/strategy.py` / `core/mt5.py`; replay stays in `backtest/runner.py`.

Live bot code must **not** import `backtest.*` for production paths (see `backtest/README.md`).

---

## Prerequisites

Confirmed from repository files (do not invent stricter pins):

| Requirement | Notes |
|-------------|--------|
| **Windows** | MetaTrader5 Python package is Windows-oriented |
| **Python** | Use a version compatible with `requirements.txt` |
| **pip packages** | `requirements.txt` (`telethon`, `python-telegram-bot`, `MetaTrader5`, `python-dotenv`, `pymysql`, `cryptography`, `flask`, `pytest`, …) |
| **MySQL** | Schema in `db/init.sql`; Docker Compose service maps host **3308** → container 3306 |
| **MetaTrader 5** | Required for live trading and for downloading broker history |
| **Telegram** | Bot token + Telethon user API for signal listening / notifications |
| **PowerShell** | Optional helpers such as `start_bot.ps1` |
| **Laragon / PHP (optional)** | Committed `index.php` can reverse-proxy Laragon URL → Flask `:5000` |

---

## Clone and Checkout

```powershell
git clone https://github.com/jerzagit/botsignal.git
cd botsignal
git checkout feature/backtest-research-dashboard
```

---

## Python Environment Setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

---

## Environment Configuration

Authoritative template: `.env.example`.

```powershell
Copy-Item .env.example .env
```

Then edit `.env`. **Never commit `.env`** — it is gitignored.

Variable groups (names only; fill your own values):

| Group | Examples |
|-------|----------|
| Dashboard auth | `PORTAL_USERNAME`, `PORTAL_PASSWORD_HASH`, `FLASK_SECRET_KEY` |
| Runtime account mode | `ENV_MODE` = `demo` / `live` / `live2` / `live3` |
| Telegram | `TG_API_ID`, `TG_API_HASH`, `BOT_TOKEN`, `YOUR_CHAT_ID`, `SIGNAL_GROUP`, optional `SIGNAL_SOURCES` |
| MT5 | `MT5_PATH`, `DEMO_*` / `LIVE_*` login fields, suffixes, spread caps |
| Database | `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`, `DB_PASSWORD` |
| Strategy | `ACTIVE_STRATEGY`, `STRATEGY_ENABLED`, `STRATEGY_LIVE_UNLOCKED`, symbol/TF/risk knobs |

Backtest safety env (set when running replay/download — not the same as `ENV_MODE`):

```powershell
$env:RUN_MODE = "BACKTEST"
```

Allowed backtest modes: `BACKTEST`, `REPLAY`, `SIM` (`backtest/safety.py`).

---

## Database Setup

### Fresh install

1. Start MySQL (example via Compose in this repo):

```powershell
docker compose up -d
```

Compose publishes MySQL on **localhost:3308** and mounts `db/init.sql` for first container init.

2. Align `.env` with your instance (example from `.env.example`):

```env
DB_HOST=localhost
DB_PORT=3308
DB_NAME=botsignal
DB_USER=root
DB_PASSWORD=...
```

3. If the database is empty and was **not** initialized by Docker’s entrypoint, apply schema:

```powershell
# Example using mysql client — adjust user/host/port to match .env
mysql -h 127.0.0.1 -P 3308 -u root -p botsignal < db/init.sql
```

Or execute `db/init.sql` in any MySQL client against an empty database.

**Fresh installs should use `db/init.sql`.** It already includes the final definitions from historical migrations.

### Existing / older databases

Upgrade helpers (do **not** re-run blindly on a fresh init):

- `db/migrate_add_candles.sql` — idempotent `CREATE TABLE IF NOT EXISTS`
- `db/migrate_add_signal_sources.sql` — **non-idempotent** `ALTER TABLE` (fails if columns already exist)

### What is / is not included

| Included | Not included |
|----------|----------------|
| Table/column/index definitions | Trade rows, Telegram IDs, MT5 logins |
| Empty schema skeleton | Portal password hashes from anyone’s machine |
| Migration SQL for upgrades | Runtime state dumps |

---

## Running the Dashboard

Direct Flask (committed path):

```powershell
python dashboard/app.py
```

- Listens on `http://127.0.0.1:5000` (`dashboard/app.py`).
- First visit may force portal `/setup` if `PORTAL_PASSWORD_HASH` is empty.

Optional Laragon URL (committed `index.php` + root `.htaccess`): keep the same browser path under Laragon while proxying to Flask on port 5000. Start Flask first (`python dashboard/app.py`).

Research pages (after login):

- `/backtests` — run list / filters
- `/backtests/<run_id>` — funnel, geometry, performance
- `/strategies` — registry + `ACTIVE_STRATEGY` configuration UI

Backtest **execution from the UI is not implemented** — runs are produced by CLI and read from disk.

---

## Running SignalBot

```powershell
python bot.py
```

Or PowerShell helper (starts `bot.py` if not already running):

```powershell
.\start_bot.ps1
```

### Preconditions

- `.env` configured
- MySQL reachable with schema applied
- MT5 terminal available / Algo Trading enabled for live execution
- Telegram credentials configured for listener / notifications

### Safety

- Prefer `ENV_MODE=demo` until settings are reviewed.
- Full-auto strategy path requires `STRATEGY_ENABLED=true`. In live modes, also review `STRATEGY_LIVE_UNLOCKED`.
- Do **not** enable live auto-trading until MT5 account, risk limits, and Telegram configuration are verified.

`ENV_MODE` selects which MT5 credential block is used (`demo` / `live` / `live2` / `live3`). It is separate from backtest `RUN_MODE`.

---

## Strategy Plugins

Package: `core/strategies/`  
Registry: `core/strategies/registry.py` (explicit factories — no filesystem discovery)

Select live strategy in `.env`:

```env
ACTIVE_STRATEGY=breakout_retest_v1
STRATEGY_ENABLED=false
```

Restart the bot / dashboard processes after changing `ACTIVE_STRATEGY`.

### Available Strategies

| Registry key | Status | Notes |
|--------------|--------|--------|
| `breakout_retest_v1` | **PRODUCTION DEFAULT** | Alias: `breakout_retest` |
| `structure_pullback_v2` | Experimental | H4→H1→M30 S&D→M15 |
| `structure_pullback_v2_1` | Experimental | Lifecycle / confirmation refinements |
| `structure_pullback_v2_2` | Experimental | Local M15 structure trigger research |

Details: [docs/STRATEGY_ARCHITECTURE.md](docs/STRATEGY_ARCHITECTURE.md), [docs/STRUCTURE_PULLBACK_V2.md](docs/STRUCTURE_PULLBACK_V2.md).

---

## Historical Backtesting

### Safety

```powershell
$env:RUN_MODE = "BACKTEST"
```

Replay never calls `mt5.order_send` or production Telegram sinks when the safety gate is honored.

### Preparing Historical Data

Datasets are **not** committed (gitignored under `data/backtests/datasets/*/`).

Expected layout:

```text
data/backtests/datasets/<DATASET_ID>/
  M15.csv
  H1.csv
  H4.csv
  meta.json
  M30.csv   # needed for V2 family
  M1.csv    # optional — M1-assisted outcomes / chronology
```

Placeholder notes: [data/backtests/datasets/README.md](data/backtests/datasets/README.md).

| Timeframe | Typical use |
|-----------|-------------|
| **M15** | Primary decision bar for V1 and V2 confirmation |
| **M30** | V2 supply/demand zones |
| **H1 / H4** | Trend / structure filters |
| **M1** | Optional outcome engine (`--outcome-engine m1`); not used for candidate generation |

### Downloading History

Requires an open MT5 terminal the Python API can attach to (no password in the CLI):

```powershell
$env:RUN_MODE = "BACKTEST"
python -m backtest.download_history --symbol XAUUSD --from 2026-01-01 --to 2026-08-01 --timeframes M15,H1,H4
```

Append more timeframes into an existing folder:

```powershell
python -m backtest.download_history --symbol XAUUSD --from 2026-01-01 --to 2026-08-01 --timeframes M30,M1 --into data/backtests/datasets/<DATASET_ID>
```

Flags from `backtest/download_history.py`: `--symbol`, `--from`, `--to`, `--timeframes`, `--out-root`, `--into`.

---

## Running Backtests

Decisions only (PowerShell — one line each):

```powershell
$env:RUN_MODE = "BACKTEST"
python -m backtest.runner --dataset data/backtests/datasets/<DATASET_ID> --strategy breakout_retest_v1 --run-id BT_LOCAL_V1_001 --spread-policy historical
```

```powershell
python -m backtest.runner --dataset data/backtests/datasets/<DATASET_ID> --strategy structure_pullback_v2 --run-id BT_LOCAL_V2_001 --spread-policy historical
```

```powershell
python -m backtest.runner --dataset data/backtests/datasets/<DATASET_ID> --strategy structure_pullback_v2_1 --run-id BT_LOCAL_V21_001 --spread-policy historical
```

```powershell
python -m backtest.runner --dataset data/backtests/datasets/<DATASET_ID> --strategy structure_pullback_v2_2 --run-id BT_LOCAL_V22_001 --spread-policy historical
```

With simulated trades / performance (V1-style example):

```powershell
python -m backtest.runner --dataset data/backtests/datasets/<DATASET_ID> --strategy breakout_retest_v1 --run-id BT_LOCAL_V1_SIM_001 --simulate-trades --initial-balance 10000 --intrabar-policy conservative --spread-policy historical --outcome-engine m1 --symbol-spec data/backtests/broker_specs/<YOUR_SPEC>.json
```

Useful flags (from `backtest/runner.py`): `--run-id`, `--simulate-trades`, `--initial-balance`, `--intrabar-policy`, `--spread-policy`, `--fixed-spread-pips`, `--symbol-spec`, `--fill-policy`, `--outcome-engine` (`legacy_m15` \| `m1`), `--m1-intrabar-policy`, `--max-bars`.

Compare two decision journals:

```powershell
python -m backtest.compare_decisions --before data/backtests/<BASELINE_RUN> --after data/backtests/<NEW_RUN>
```

More: [backtest/README.md](backtest/README.md).

---

## Geometry Diagnostics

Research-only passive analysis of candidate geometry / RR decay. **Does not change strategy rules.**

```powershell
$env:RUN_MODE = "BACKTEST"
python -m backtest.run_geometry_diagnostic --dataset data/backtests/datasets/<DATASET_ID> --sources <V2_RUN>:structure_pullback_v2 <V21_RUN>:structure_pullback_v2_1 <V22_RUN>:structure_pullback_v2_2 --run-id BT_LOCAL_GEOMETRY_001 --retest-source <V22_RUN>
```

Earlier-entry RR scenarios are **observational only** — not valid live strategy entries.

---

## Research Dashboard

After runs exist under `data/backtests/BT_*/` (or any `data/backtests/<run_id>/`):

1. Start `python dashboard/app.py`
2. Open **Backtests**
3. Inspect funnel metrics (zones, retests, confirmations, candidates, RR pass, would-execute)
4. Open a run for geometry / performance detail
5. Compare runs when selecting multiple IDs

Artifacts are discovered from the local filesystem catalog (`backtest/catalog.py`). Regenerating runs updates what the UI can show; there is no separate remote backtest service.

---

## Generated Artifacts

```text
data/backtests/<run_id>/
  meta.json
  decisions.jsonl
  issues.jsonl
  funnel.json / funnel.txt
  performance.*          # when --simulate-trades
  v2_*.json(l)           # V2 diagnostics when applicable
```

These directories are **gitignored** (`data/backtests/BT_*/`). Regenerate locally. Do not commit them.

---

## Testing

Core suite (no live MT5 terminal required for these modules):

```powershell
python -m pytest test_geometry_math.py test_v2_geometry_diagnostics.py test_strategy_plugins.py test_structure_pullback_v2.py test_structure_pullback_v2_1.py test_structure_pullback_v2_2.py test_v2_diagnostics.py test_backtest_infra.py test_backtest_replay.py test_backtest_simulate.py test_backtest_phase_h.py test_backtest_m1.py -q
```

Environment-dependent / live-oriented tests (may need MT5 or session hours):

- `test_margin_guard.py` — can fail outside configured trading session hours
- `test_layer.py` / similar live UAT scripts — require MT5 connectivity

Do not assume the full default pytest discovery set passes without a configured terminal and session.

---

## Local / Private Files Not Included

Intentionally excluded (see `.gitignore`):

| Path / pattern | Why |
|----------------|-----|
| `.env` | Secrets |
| `data/backtests/datasets/*/` | Large broker history |
| `data/backtests/BT_*/` | Generated run artifacts |
| `data/backtests/broker_specs/` | Account-linked broker captures |
| `*.db`, `*.sqlite`, `*.sqlite3` | Local databases |
| `logs/`, `*.log` | Runtime logs |
| `.pytest_cache/`, `__pycache__/` | Caches |
| `.claude/` | Local tooling |
| `backup/`, `exports/`, `*.bak` | Scratch / dumps |
| `data/session*` | Telethon session |

Another developer must create `.env`, initialize MySQL, and obtain/download their own datasets.

---

## Repository Safety

- Never commit secrets or Telethon sessions.
- Prefer demo / locked strategy flags until reviewed.
- Backtest path is fail-closed without `RUN_MODE` in `{BACKTEST, REPLAY, SIM}`.
- Experimental strategies are for research — not a promise of live edge.

**T.A.Y.O.R — Trade At Your Own Risk.**

---

## Known Limitations

- Transaction costs: commission/swap/slippage often **not modelled** unless you pass cost flags.
- M1 history may be partial; M1 outcome engine can fall back for uncovered ranges.
- Tick-level tape is not available — fills/outcomes use bar (and optional M1) approximations.
- V2 family: experimental; small sample of candidates on shared datasets is common.
- Broker symbol specs for high-fidelity lot/margin simulation are local captures (gitignored).
- Historical datasets and run outputs are not shipped with the clone.
- V2 live restart persistence of zone state is not implemented (see strategy architecture docs).
- Dashboard does not execute backtests; CLI only.

---

## Project Documentation

| Doc | Topic |
|-----|--------|
| [backtest/README.md](backtest/README.md) | Replay package overview |
| [docs/STRATEGY_ARCHITECTURE.md](docs/STRATEGY_ARCHITECTURE.md) | Plugin architecture |
| [docs/STRUCTURE_PULLBACK_V2.md](docs/STRUCTURE_PULLBACK_V2.md) | V2 locked rules |
| [docs/BACKTEST_FEASIBILITY.md](docs/BACKTEST_FEASIBILITY.md) | Feasibility notes |
| [docs/BACKTEST_GUARD_MATRIX.md](docs/BACKTEST_GUARD_MATRIX.md) | Guard matrix |
| [docs/BACKTEST_TIMEZONE.md](docs/BACKTEST_TIMEZONE.md) | Candle time basis |
| [docs/BACKTEST_READINESS_REPORT.md.txt](docs/BACKTEST_READINESS_REPORT.md.txt) | Readiness report |

---

## Fresh Clone Checklist

- [ ] `git checkout feature/backtest-research-dashboard`
- [ ] `Copy-Item .env.example .env` and fill values
- [ ] Start MySQL / align `DB_*`
- [ ] Apply `db/init.sql` (or use Compose first-init)
- [ ] `pip install -r requirements.txt`
- [ ] Configure MT5 + Telegram only if you need live / download / notifications
- [ ] Download or place a dataset under `data/backtests/datasets/`
- [ ] Set `ACTIVE_STRATEGY` / CLI `--strategy` deliberately
- [ ] Run the core pytest list above
- [ ] `python dashboard/app.py`
- [ ] Run `python bot.py` only after reviewing `ENV_MODE` and live unlocks

---

## Live Trading Quick Reference

Entry point: `python bot.py`  
Dashboard: `python dashboard/app.py` → `http://127.0.0.1:5000`  
Account switch: `ENV_MODE=demo|live|live2|live3`  
Guards, DCA, AutoZone, Profit Lock, and Telegram command behavior remain implemented in `core/` — configure via `.env.example` keys. Prefer demo until risk settings are reviewed.
