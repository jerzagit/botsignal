# SignalBot — Historical Replay Package

Isolated from the live bot. Live `bot.py` must **not** import `backtest.*`.

## Safety

- Set `RUN_MODE=BACKTEST` before download or replay.
- Callers use `assert_backtest_safe()` (fail-closed).
- Replay never calls `mt5.order_send` or production Telegram.

## Commands

```bash
# Decisions only
set RUN_MODE=BACKTEST
python -m backtest.runner --dataset data/backtests/datasets/<id> --strategy breakout_retest_v1
# Alias: --strategy breakout_retest → breakout_retest_v1

# Guards + simulated trades + performance
python -m backtest.runner --dataset data/backtests/datasets/<id> --strategy breakout_retest_v1 ^
  --simulate-trades --initial-balance 10000 --intrabar-policy conservative --spread-policy historical
```

Strategy plugins: `docs/STRATEGY_ARCHITECTURE.md`

Guard matrix: `docs/BACKTEST_GUARD_MATRIX.md`

## Layout

```
backtest/
  clock.py              LiveClock / ReplayClock
  interfaces.py         Candle + provider protocols
  safety.py             RUN_MODE gate
  timeframes.py         period / closed-bar helpers
  dataset.py            CSV load/save + validation
  download_history.py   MT5 → immutable dataset
  provider.py           HistoricalReplayProvider (no look-ahead)
  runner.py             decision replay CLI
  report.py             funnel stats
  gitmeta.py            branch/commit for journals

data/backtests/datasets/<id>/{M15,H1,H4}.csv + meta.json
data/backtests/<run_id>/{decisions,issues}.jsonl + meta.json + funnel.*
```

## Timezone

See `docs/BACKTEST_TIMEZONE.md`. Candles stored as UTC Unix open times.
