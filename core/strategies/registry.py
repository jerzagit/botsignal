"""
Explicit strategy registry — no filesystem discovery, no silent fallbacks.

Factories return a fresh plugin instance (important for stateful V2).
"""

from __future__ import annotations

from typing import Callable

from core.strategies.base import StrategyInfo, StrategyPlugin
from core.strategies.breakout_retest_v1 import BreakoutRetestV1, STRATEGY_NAME as V1_NAME
from core.strategies.structure_pullback_v2 import StructurePullbackV2, STRATEGY_NAME as V2_NAME
from core.strategies.structure_pullback_v2_1 import (
    StructurePullbackV21,
    STRATEGY_NAME as V21_NAME,
)
from core.strategies.structure_pullback_v2_2 import (
    StructurePullbackV22,
    STRATEGY_NAME as V22_NAME,
)

DEFAULT_STRATEGY = V1_NAME  # breakout_retest_v1

_ALIASES: dict[str, str] = {
    "breakout_retest": V1_NAME,
}

_FACTORIES: dict[str, Callable[[], StrategyPlugin]] = {
    V1_NAME: BreakoutRetestV1,
    V2_NAME: StructurePullbackV2,
    V21_NAME: StructurePullbackV21,
    V22_NAME: StructurePullbackV22,
}


def list_strategies() -> list[str]:
    return sorted(_FACTORIES.keys())


def list_strategy_info() -> list[StrategyInfo]:
    return [factory().info() for factory in _FACTORIES.values()]


def resolve_strategy_name(name: str | None) -> str:
    key = (name or "").strip()
    if not key:
        key = DEFAULT_STRATEGY
    key = _ALIASES.get(key, key)
    if key not in _FACTORIES:
        known = ", ".join(list_strategies())
        aliases = ", ".join(sorted(_ALIASES))
        raise ValueError(
            f"Unknown strategy: {name!r}. Registered: [{known}]. Aliases: [{aliases}]."
        )
    return key


def get_strategy(name: str | None = None) -> StrategyPlugin:
    """Return a fresh plugin instance for the resolved name."""
    return _FACTORIES[resolve_strategy_name(name)]()


def get_strategy_info(name: str | None = None) -> StrategyInfo:
    return get_strategy(name).info()
