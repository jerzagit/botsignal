# Strategy Plugin Architecture

## Goal

Trading strategies are interchangeable plugins. Infrastructure stays fixed:

```
Market Provider (live MT5 / backtest dataset)
        ↓
   MarketContext
        ↓
  Strategy Registry
        ↓
  Strategy Plugin (e.g. breakout_retest_v1)
        ↓
  StrategyDecision
        ↓
  Existing pre-guards (session, DD, spread, …)
        ↓
  Existing production guards / risk
        ↓
  Existing MT5 execute_trade  OR  backtest simulator
        ↓
  Existing trade management / notifications / DB
```

Changing strategy should conceptually require only:

```env
ACTIVE_STRATEGY=some_registered_name
```

## Packages

| Path | Role |
|------|------|
| `core/strategies/base.py` | `MarketContext`, `StrategyDecision`, `StrategyPlugin` Protocol |
| `core/strategies/registry.py` | Explicit registry + aliases |
| `core/strategies/breakout_retest_v1.py` | Current production algorithm (unchanged rules) |
| `core/strategies/structure_pullback_v2.py` | Experimental H4→H1→M30 S&D→M15 shift |
| `core/strategies/v2_zones.py` / `v2_structure.py` | V2 zone + pivot helpers |
| `core/strategy.py` | Live orchestrator (scan loop, pre-guards, execute_trade) |

## Multi-timeframe MarketContext

Primary access: `context.candles["M15"]`, `context.candles["M30"]`, …

Compatibility properties (no data duplication): `m15_candles`, `m30_candles`, `h1_candles`, `h4_candles`.

Factory: `build_market_context(...)` accepts either a candles map or legacy TF kwargs.

Plugins declare `required_timeframes`. Live + backtest load only those TFs (plus H1/H4 for shared trend helpers when needed).

M1 is **not** used for candidate generation.

## Registered strategies

| Name | Status | Default |
|------|--------|---------|
| `breakout_retest_v1` | stable | **YES** |
| `structure_pullback_v2` | experimental | no |

Alias: `breakout_retest` → `breakout_retest_v1`.

V2 locked rules: `docs/STRUCTURE_PULLBACK_V2.md`.

## Dashboard

- **Strategies** (`/strategies`): registry list, configure `ACTIVE_STRATEGY` via `.env`, shows runtime vs configured (restart required).
- **Backtests** (`/backtests`): run list, detail (equity/monthly Chart.js), compare two runs.
- Backtest **execution** from UI is not implemented in this phase.

## Strategy state

V2 zone lifecycle is **instance-local** (fresh plugin per `get_strategy()`). Not written to production DB. Live restart persistence is **not** implemented yet (documented limitation).

## StrategyDecision

Frozen dataclass fields (unchanged semantics):

- `action`: `wait` | `skip` | `enter`
- `reason`
- `direction`: `buy` | `sell` | `None`
- `entry`, `sl`, `tp`, `level`
- `metadata` (optional; behaviour must not depend on it)

## MarketContext

Market-focused only. Must **not** carry:

`mt5`, `account`, `balance`, `equity`, `margin`, `telegram`, `bot`, `db`,
`notifier`, `execute_trade`, `order_send`, `connection`

Required: `symbol`, `m15_candles`, `h1_direction`, `h4_direction`, `bid`, `ask`.

Optional: `timestamp`, `h1_candles`, `h4_candles`, `spread_pips`, `metadata`.

## Registry

```python
from core.strategies import get_strategy, list_strategies, resolve_strategy_name

resolve_strategy_name("breakout_retest")     # → "breakout_retest_v1"
get_strategy("breakout_retest_v1").evaluate(ctx)
```

- Default / empty → `breakout_retest_v1`
- Alias: `breakout_retest` → `breakout_retest_v1`
- Unknown name → `ValueError` (no silent fallback)

## Config

```env
ACTIVE_STRATEGY=breakout_retest_v1
```

If unset, default is `breakout_retest_v1` (same behaviour as the former hard-coded strategy).

## Live path

`bot.py` → `start_strategy` → `scan_once`:

1. Pre-guards (live lock, session, MT5 connect, daily DD, open position, spread)
2. Load M15 rates + H1/H4 via `analyze_timeframe`
3. Build `MarketContext`
4. `get_strategy(ACTIVE_STRATEGY).evaluate(context)`
5. On `enter` → `upsert_signal` + **`execute_trade`** (unchanged)

Plugins must not call MT5 `order_send`, Telegram, or DB trade writes.

## Backtest path

```bash
python -m backtest.runner --dataset ... --strategy breakout_retest_v1
python -m backtest.runner --dataset ... --strategy breakout_retest   # alias
```

Same registry + same V1 module as live. Artifacts include `strategy_name`.

## Protected infrastructure

Do not change behaviour in: `core/mt5.py`, `core/risk.py`, `core/watcher.py`,
`core/notifier.py`, `core/db.py`, `core/state.py`, Telegram / DB schema /
trade-management semantics — unless a minimal wiring change is required.

## Adding a future strategy (e.g. structure_pullback_v2)

1. Create `core/strategies/structure_pullback_v2.py` implementing `evaluate(context) -> StrategyDecision`.
2. Register it in `core/strategies/registry.py` `_REGISTRY` under `"structure_pullback_v2"`.
3. Set `ACTIVE_STRATEGY=structure_pullback_v2` (or `--strategy structure_pullback_v2`).

Do **not** modify guards, risk, MT5 execution, Telegram, DB, or the backtest
simulator to add the strategy.

Do **not** register unfinished placeholder strategies.

## Regression

```bash
python -m backtest.compare_decisions \
  --before data/backtests/BT_XAUUSD_M1_ASSISTED_001 \
  --after  data/backtests/<new_run>
```

Expect: `different=0`, `missing=0`, `extra=0`, decision hash identical
(for timestamp / decision / reason). Strategy name string in journals may
show `breakout_retest_v1` instead of the former `breakout_retest` label.
