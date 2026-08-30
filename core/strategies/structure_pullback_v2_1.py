"""
structure_pullback_v2_1 — lifecycle experiment only.

Identical to structure_pullback_v2 except:
  consume_on_leave_zone = False

After first retest, leaving the M30 zone does NOT consume the setup.
The zone stays WAITING_CONFIRMATION until M15 confirmation, M30 invalidation,
or end of run (direction gate unchanged — blocks trigger, does not invent new expiry).
"""

from __future__ import annotations

from core.strategies.structure_pullback_v2 import StructurePullbackV2

STRATEGY_NAME = "structure_pullback_v2_1"
REQUIRED_TIMEFRAMES = ("M15", "M30", "H1", "H4")


class StructurePullbackV21(StructurePullbackV2):
    name = STRATEGY_NAME
    required_timeframes = REQUIRED_TIMEFRAMES
    version = "2.1"
    display_name = "Structure Pullback V2.1"
    description = (
        "Lifecycle experiment: continues waiting for M15 confirmation after "
        "price leaves the M30 zone while the zone remains valid."
    )

    def __init__(self) -> None:
        super().__init__(consume_on_leave_zone=False)
