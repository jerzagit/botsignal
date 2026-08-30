# Backtest guard applicability matrix (Phase C)

Source of truth: `core/mt5.py::execute_trade` as audited on branch `dev`.
Strategy path: `entry_mode="strategy"`, `skip_proximity=True`, no `source_id`,
`risk_percent=STRATEGY_RISK_PERCENT`.

## Production order (strategy path)

| # | Guard | Applicable | Historical input | Simulation quality | Notes |
|---|-------|------------|------------------|--------------------|-------|
| 0 | session | YES | ReplayClock UTC hour | **EXACT** | Same UTC hour window as production |
| X | daily_loss_usd | YES | Simulated realized losses | **SIMULATED** | Production uses process-memory `get_daily_loss()` fed by dashboard poller — **PRODUCTION_GAP** |
| S | strategy_daily_drawdown | YES (pre-execute) | Simulated equity | **SIMULATED** | `STRATEGY_DAILY_DRAWDOWN_PERCENT` of equity; only in `scan_once`, not inside `execute_trade` |
| DB | database | YES in live | N/A for market baseline | **NOT_APPLICABLE** | Infrastructure; auto-pass for performance baseline |
| Conn | mt5_connect | YES in live | N/A | **NOT_APPLICABLE** | Infrastructure |
| 1 | account_info | YES in live | SimulatedAccount | **SIMULATED** | |
| 2 | margin_level | YES | Simulated open book | **SIMULATED** | Exact broker margin unavailable; simplified model |
| 3 | same_direction_stack | YES | Simulated open book | **SIMULATED** | Strategy has no `source_id` → counts all same-dir at-risk |
| 3b | dca_layer_cap | NO for strategy L1 | — | **NOT_APPLICABLE** | No `own_tickets` on strategy entry |
| 4a | tp_auto_adjust | YES | Candidate SL/TP | **EXACT** | Soft adjust only |
| 4b | rr_ratio | YES | Candidate after adjust | **EXACT** | Uses `MIN_RR_RATIO` vs strategy `STRATEGY_MIN_RR` |
| Sym | symbol_available | YES in live | Dataset presence | **NOT_APPLICABLE** | Dataset implies symbol existed |
| Tick | tick_available | YES in live | OHLC proxy | **NOT_APPLICABLE** / fill **APPROXIMATED** |
| 5 | spread | YES | M15 `spread` column (points) | **EXACT** if point size known; else **APPROXIMATED** | Default point=0.01 labelled in meta |
| 6 | proximity | NO | — | **NOT_APPLICABLE** | Strategy sets `skip_proximity=True` |
| Lot | lot_calc | YES | Equity + SymbolSpec | **SIMULATED** | Needs tick_size/tick_value snapshot |
| Src | source_risk | NO | — | **NOT_APPLICABLE** | No `source_id` on strategy Signal |

## Entry / fill semantics

Production: market order at live **ask (BUY)** / **bid (SELL)** at `order_send` time.
Replay baseline fill policy: **CANDIDATE_ENTRY_AT_SIGNAL_BAR_CLOSE**

- Candidate `entry` from strategy (replay used bid=ask=M15 close ± half historical spread when available).
- Outcome walk starts on the **next** M15 bar (avoids same-bar look-ahead from fill-at-close).
- Quality: **APPROXIMATED** (no tick tape).

## Intrabar policy (baseline)

`--intrabar-policy conservative` → if same bar touches SL and TP → **SL first**.

## Costs

Commission / swap / slippage: **NOT MODELLED** (zero) unless explicitly configured later.
