"""Historical market data provider — no look-ahead past ReplayClock.cursor."""

from __future__ import annotations

import bisect
from datetime import datetime, timezone
from pathlib import Path

from backtest.clock import ReplayClock
from backtest.dataset import candles_to_dicts, load_candles_csv, load_dataset_meta
from backtest.interfaces import Candle
from backtest.timeframes import is_bar_closed, period_seconds


class LookAheadError(RuntimeError):
    """Raised when code attempts to read a candle not yet closed at cursor."""


class HistoricalReplayProvider:
    """
    Loads persisted M15/H1/H4 candles and exposes only bars closed by the clock.

    Candle.time is MT5 bar OPEN time (Unix UTC seconds).
    A bar is visible iff open_time + period_seconds <= cursor_unix.
    """

    def __init__(
        self,
        dataset_dir: Path | str,
        clock: ReplayClock,
        *,
        symbol: str | None = None,
        timeframes: list[str] | None = None,
    ) -> None:
        self.dataset_dir = Path(dataset_dir)
        self.clock = clock
        self.meta = load_dataset_meta(self.dataset_dir) if (self.dataset_dir / "meta.json").is_file() else {}
        self.symbol = (symbol or self.meta.get("symbol") or "XAUUSD").upper()
        tfs = timeframes or self.meta.get("timeframes") or ["M15", "H1", "H4"]
        self.timeframes = [t.upper() for t in tfs]
        self._series: dict[str, list[Candle]] = {}
        self._open_times: dict[str, list[int]] = {}
        for tf in self.timeframes:
            path = self.dataset_dir / f"{tf}.csv"
            if not path.is_file():
                continue
            series = load_candles_csv(path, timeframe=tf)
            self._series[tf] = series
            self._open_times[tf] = [c.time for c in series]
        # Max open_time exposed per timeframe at last query (look-ahead guard)
        self._last_max_open: dict[str, int] = {}
        self._last_cursor: int | None = None

    @property
    def cursor_unix(self) -> int:
        return int(self.clock.time())

    def closed_candles(self, timeframe: str) -> list[Candle]:
        tf = timeframe.upper()
        if tf not in self._series:
            raise KeyError(f"Timeframe not in dataset: {tf}")
        cursor = self.cursor_unix
        # Closed iff open + period <= cursor  =>  open <= cursor - period
        max_open = cursor - period_seconds(tf)
        end = bisect.bisect_right(self._open_times[tf], max_open)
        out = self._series[tf][:end]
        self._last_cursor = cursor
        self._last_max_open[tf] = out[-1].time if out else -1
        return out

    def get_candles(self, symbol: str, timeframe: str, count: int) -> list[Candle]:
        """Protocol-compatible: last `count` closed candles at cursor."""
        closed = self.closed_candles(timeframe)
        if count <= 0:
            return []
        return closed[-count:]

    def get_tick(self, symbol: str) -> tuple[float, float]:
        """
        Synthetic tick from last closed M15 close (bid=ask=close).
        No live MT5 tick feed in Phase B/E.
        """
        m15 = self.closed_candles("M15")
        if not m15:
            raise RuntimeError("No closed M15 candles at cursor for tick")
        px = m15[-1].close
        return px, px

    def m15_strategy_window(self, count: int) -> list[dict]:
        """
        Build candle dicts for breakout_retest_v1 (includes forming stub).

        Live MT5 windows include a forming bar as the last element; the pure
        evaluator uses candles[:-1]. Historical rows are completed bars only,
        so we append a flat forming stub at the next open (no future OHLC).
        """
        closed = self.closed_candles("M15")
        if not closed:
            return []
        window = closed[-count:] if count > 0 else closed
        last = window[-1]
        forming_open = last.time + period_seconds("M15")
        # At an M15 close event, forming_open == cursor. Never use a future open.
        if forming_open > self.cursor_unix:
            raise LookAheadError(
                f"Forming stub open {forming_open} is after cursor {self.cursor_unix}"
            )
        forming = {
            "time": forming_open,
            "open": last.close,
            "high": last.close,
            "low": last.close,
            "close": last.close,
        }
        return candles_to_dicts(window) + [forming]

    def closed_dicts(self, timeframe: str, count: int | None = None) -> list[dict]:
        """Last `count` closed candles as dicts (no forming stub, no lookahead)."""
        closed = self.closed_candles(timeframe)
        if not closed:
            return []
        if count is None or count <= 0:
            window = closed
        else:
            window = closed[-count:]
        return candles_to_dicts(window)

    def candles_map_for_strategy(
        self,
        required_timeframes: list[str] | tuple[str, ...],
        *,
        m15_window: int,
        htf_window: int,
        use_m15_forming_stub: bool,
    ) -> dict[str, list[dict]]:
        """Generic TF map for MarketContext.candles."""
        out: dict[str, list[dict]] = {}
        for tf in required_timeframes:
            tf_u = tf.upper()
            if tf_u not in self._series:
                continue
            if tf_u == "M15" and use_m15_forming_stub:
                out["M15"] = self.m15_strategy_window(m15_window)
            else:
                n = m15_window if tf_u == "M15" else htf_window
                out[tf_u] = self.closed_dicts(tf_u, n)
        return out

    def assert_no_future_access(self) -> None:
        """Verify last closed-candle queries never exposed a bar still open at cursor."""
        if self._last_cursor is None:
            return
        cursor = self._last_cursor
        for tf, open_time in self._last_max_open.items():
            if open_time < 0:
                continue
            if not is_bar_closed(open_time, tf, cursor):
                raise LookAheadError(
                    f"Future candle accessed: {tf} open={open_time} cursor={cursor}"
                )
            # Next bar in series must not be closed at this cursor
            series = self._series[tf]
            # find index of open_time
            for i, c in enumerate(series):
                if c.time == open_time:
                    if i + 1 < len(series) and is_bar_closed(series[i + 1].time, tf, cursor):
                        raise LookAheadError(
                            f"Closed set incomplete/inconsistent for {tf} at cursor={cursor}"
                        )
                    break


    def m15_close_events(self) -> list[tuple[datetime, Candle]]:
        """Chronological M15 bar-close moments driving replay."""
        events: list[tuple[datetime, Candle]] = []
        for c in self._series.get("M15", []):
            close_ts = c.time + period_seconds("M15")
            events.append(
                (datetime.fromtimestamp(close_ts, tz=timezone.utc), c)
            )
        return events
