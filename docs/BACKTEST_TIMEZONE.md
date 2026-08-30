# Backtest timezone notes

## MT5 API timestamp basis

The MetaTrader5 Python package returns each rate’s `time` field as a **Unix
timestamp (seconds since 1970-01-01 00:00:00 UTC)** representing the **bar open**.

Canonical replay storage therefore treats candle times as **UTC epoch seconds**.
ISO timestamps in journals use the `...Z` suffix (UTC).

## Broker display time

The broker terminal may *display* candles in server time (often UTC+2 / UTC+3
for FX/CFD brokers, or another offset). Display offset is **not** assumed by
this pipeline.

Malaysia Time (MYT, UTC+8) is **not** applied automatically. Any MYT conversion
belongs to a later presentation layer.

## Implications for replay

- `ReplayClock.cursor` is timezone-aware UTC.
- A bar with open time `T` on timeframe period `P` is **closed** at `T + P`.
- At cursor `C`, only bars with `T + P <= C` are visible (no look-ahead).
