"""
Strategy plugin contracts.

MarketContext is market-focused only — no MT5, Telegram, DB, account, or
execution objects. Strategies return StrategyDecision; guards/risk/execution
remain outside the plugin.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol, Sequence, runtime_checkable


# Fields that must never appear on MarketContext (architecture guard).
FORBIDDEN_CONTEXT_FIELDS = frozenset(
    {
        "mt5",
        "account",
        "balance",
        "equity",
        "margin",
        "telegram",
        "bot",
        "db",
        "notifier",
        "execute_trade",
        "order_send",
        "connection",
    }
)


@dataclass(frozen=True)
class StrategyDecision:
    action: str
    reason: str
    direction: str | None = None
    entry: float | None = None
    sl: float | None = None
    tp: float | None = None
    level: float | None = None
    metadata: dict[str, Any] | None = None


@dataclass(frozen=True)
class StrategyInfo:
    """Registry metadata for UI / docs (not used for trading decisions)."""

    name: str
    display_name: str
    description: str
    version: str
    required_timeframes: tuple[str, ...]
    status: str  # stable | experimental


@dataclass(frozen=True)
class MarketContext:
    """
    Strategy-facing market snapshot (no execution / account / IO).

    Primary candle access: context.candles["M15"], context.candles["M30"], ...
    Compatibility: context.m15_candles / h1_candles / h4_candles (views into candles).
    """

    symbol: str
    bid: float
    ask: float
    candles: Mapping[str, Sequence[dict]] = field(default_factory=dict)
    h1_direction: str = "NEUTRAL"
    h4_direction: str = "NEUTRAL"
    timestamp: str | int | None = None
    spread_pips: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # Normalize TF keys to upper-case without copying candle lists.
        norm = {str(k).upper(): v for k, v in dict(self.candles).items()}
        object.__setattr__(self, "candles", norm)
        for bad in FORBIDDEN_CONTEXT_FIELDS:
            if bad in self.metadata:
                raise ValueError(f"MarketContext.metadata must not include {bad!r}")

    def tf(self, timeframe: str) -> list[dict]:
        series = self.candles.get(str(timeframe).upper())
        return list(series) if series is not None else []

    @property
    def m15_candles(self) -> list[dict]:
        return self.tf("M15")

    @property
    def h1_candles(self) -> list[dict]:
        return self.tf("H1")

    @property
    def h4_candles(self) -> list[dict]:
        return self.tf("H4")

    @property
    def m30_candles(self) -> list[dict]:
        return self.tf("M30")


def build_market_context(
    *,
    symbol: str,
    bid: float,
    ask: float,
    candles: Mapping[str, Sequence[dict]] | None = None,
    m15_candles: Sequence[dict] | None = None,
    h1_candles: Sequence[dict] | None = None,
    h4_candles: Sequence[dict] | None = None,
    m30_candles: Sequence[dict] | None = None,
    h1_direction: str = "NEUTRAL",
    h4_direction: str = "NEUTRAL",
    timestamp: str | int | None = None,
    spread_pips: float | None = None,
    metadata: dict[str, Any] | None = None,
) -> MarketContext:
    """Factory supporting both generic candles map and legacy TF kwargs."""
    merged: dict[str, Sequence[dict]] = dict(candles or {})
    if m15_candles is not None:
        merged["M15"] = m15_candles
    if m30_candles is not None:
        merged["M30"] = m30_candles
    if h1_candles is not None:
        merged["H1"] = h1_candles
    if h4_candles is not None:
        merged["H4"] = h4_candles
    return MarketContext(
        symbol=symbol,
        bid=bid,
        ask=ask,
        candles=merged,
        h1_direction=h1_direction,
        h4_direction=h4_direction,
        timestamp=timestamp,
        spread_pips=spread_pips,
        metadata=metadata or {},
    )


@runtime_checkable
class StrategyPlugin(Protocol):
    name: str
    required_timeframes: tuple[str, ...]

    def evaluate(self, context: MarketContext) -> StrategyDecision:
        """Return WAIT / SKIP / enter(+direction) without side effects."""
        ...

    def info(self) -> StrategyInfo:
        ...
