"""
structure_pullback_v2_2 — post-retest local M15 structure-shift experiment.

Base: V2.1 lifecycle (consume_on_leave_zone=False).
Only change vs V2.1: m15_trigger_mode = POST_RETEST_LOCAL_STRUCTURE.
"""

from __future__ import annotations

from core.strategies.structure_pullback_v2 import (
    M15_TRIGGER_POST_RETEST_LOCAL,
    StructurePullbackV2,
)

STRATEGY_NAME = "structure_pullback_v2_2"
REQUIRED_TIMEFRAMES = ("M15", "M30", "H1", "H4")


class StructurePullbackV22(StructurePullbackV2):
    name = STRATEGY_NAME
    required_timeframes = REQUIRED_TIMEFRAMES
    version = "2.2"
    display_name = "Structure Pullback V2.2"
    description = "Post-retest local M15 structure-shift experiment."

    def __init__(self) -> None:
        super().__init__(
            consume_on_leave_zone=False,
            m15_trigger_mode=M15_TRIGGER_POST_RETEST_LOCAL,
        )
