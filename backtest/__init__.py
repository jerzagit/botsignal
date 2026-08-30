"""
Isolated historical replay package.

IMPORTANT:
- Importing this package must NOT affect live trading.
- Live bot code paths must NOT import these modules (except optional pure
  helpers already living under core/).
- RUN_MODE=BACKTEST + fail-closed safety required before any replay execution.
"""

from backtest.clock import LiveClock, ReplayClock
from backtest.safety import assert_backtest_safe

__all__ = [
    "LiveClock",
    "ReplayClock",
    "assert_backtest_safe",
]
