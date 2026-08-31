"""Production-ordered strategy guard evaluation for historical replay."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from core.config import (
    BLOCK_SAME_DIRECTION_STACK,
    MAX_DAILY_LOSS_USD,
    MAX_SPREAD_PIPS,
    MIN_LOT,
    MIN_MARGIN_LEVEL,
    MIN_RR_RATIO,
    SESSION_END_HOUR_UTC,
    SESSION_FILTER_ENABLED,
    SESSION_START_HOUR_UTC,
    SL_MIN_PIPS,
    SL_PIP_SIZE,
    STACK_MODE,
    STRATEGY_DAILY_DRAWDOWN_PERCENT,
    STRATEGY_MAX_SL_PIPS,
    STRATEGY_RISK_PERCENT,
    TP_ENFORCE_PIPS,
)
from backtest.account import SimulatedAccount
from backtest.lot import calculate_lot_pure
from backtest.symbol_spec import SymbolSpec


QUALITY_EXACT = "EXACT"
QUALITY_SIMULATED = "SIMULATED"
QUALITY_APPROXIMATED = "APPROXIMATED"
QUALITY_NOT_AVAILABLE = "NOT_AVAILABLE"
QUALITY_NOT_APPLICABLE = "NOT_APPLICABLE"

RESULT_PASS = "PASS"
RESULT_FAIL = "FAIL"
RESULT_NOT_EVALUATED = "NOT_EVALUATED"
RESULT_ADJUSTED = "ADJUSTED"


@dataclass
class GuardResult:
    guard: str
    result: str
    quality: str
    values: dict[str, Any] = field(default_factory=dict)
    category: str = "TRADING"  # TRADING | INFRASTRUCTURE


@dataclass
class GuardTrace:
    guards: list[GuardResult]
    final: str  # WOULD_EXECUTE | BLOCKED
    blocked_by: str | None = None
    effective_tps: list[float] = field(default_factory=list)
    rr_after_adjust: float | None = None
    lot: float | None = None
    risk_usd: float | None = None
    lot_explanation: str = ""
    fill_bid: float | None = None
    fill_ask: float | None = None
    fill_price: float | None = None
    spread_pips: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "guards": [
                {
                    "guard": g.guard,
                    "result": g.result,
                    "quality": g.quality,
                    "values": g.values,
                    "category": g.category,
                }
                for g in self.guards
            ],
            "final": self.final,
            "blocked_by": self.blocked_by,
            "effective_tps": self.effective_tps,
            "rr_after_adjust": self.rr_after_adjust,
            "lot": self.lot,
            "risk_usd": self.risk_usd,
            "lot_explanation": self.lot_explanation,
            "fill_bid": self.fill_bid,
            "fill_ask": self.fill_ask,
            "fill_price": self.fill_price,
            "spread_pips": self.spread_pips,
        }


@dataclass
class OpenPositionView:
    trade_id: str
    direction: str
    entry: float
    sl: float
    lot: float


def _session_ok(hour_utc: int) -> bool:
    if not SESSION_FILTER_ENABLED:
        return True
    if SESSION_START_HOUR_UTC <= SESSION_END_HOUR_UTC:
        return SESSION_START_HOUR_UTC <= hour_utc < SESSION_END_HOUR_UTC
    return hour_utc >= SESSION_START_HOUR_UTC or hour_utc < SESSION_END_HOUR_UTC


def _spread_pips_from_points(spread_points: int | None, point: float) -> float | None:
    if spread_points is None:
        return None
    return (float(spread_points) * float(point)) / SL_PIP_SIZE


def evaluate_strategy_guards(
    *,
    ts: datetime,
    direction: str,
    entry: float,
    sl: float,
    tp: float,
    account: SimulatedAccount,
    open_positions: list[OpenPositionView],
    spec: SymbolSpec,
    spread_points: int | None,
    spread_policy: str = "historical",
    fixed_spread_pips: float | None = None,
    skip_remaining_after_fail: bool = True,
) -> GuardTrace:
    """
    Replay strategy-path guards in production order.
    Short-circuits on first hard FAIL (later guards = NOT_EVALUATED).
    """
    guards: list[GuardResult] = []
    blocked_by: str | None = None
    failed = False

    def add(g: GuardResult) -> None:
        nonlocal failed, blocked_by
        if failed and skip_remaining_after_fail and g.result not in (
            RESULT_NOT_EVALUATED,
        ):
            # caller should mark remaining as NOT_EVALUATED
            return
        guards.append(g)
        if g.result == RESULT_FAIL and g.category == "TRADING":
            failed = True
            blocked_by = g.guard
        elif g.result == RESULT_FAIL and g.category == "INFRASTRUCTURE":
            # infrastructure fails don't count for market baseline blocks
            pass

    def skip_rest(names: list[str]) -> None:
        for name in names:
            guards.append(
                GuardResult(name, RESULT_NOT_EVALUATED, QUALITY_NOT_APPLICABLE, {})
            )

    remaining = [
        "daily_loss_usd",
        "strategy_daily_drawdown",
        "database",
        "mt5_connect",
        "account_info",
        "margin_level",
        "same_direction_stack",
        "dca_layer_cap",
        "tp_auto_adjust",
        "rr_ratio",
        "symbol_available",
        "tick_available",
        "spread",
        "proximity",
        "lot_calc",
        "source_risk",
    ]

    # 0 session
    hour = ts.astimezone(ts.tzinfo).hour if ts.tzinfo else ts.hour
    # use UTC hour explicitly
    hour = ts.utctimetuple().tm_hour
    ok = _session_ok(hour)
    add(
        GuardResult(
            "session",
            RESULT_PASS if ok else RESULT_FAIL,
            QUALITY_EXACT,
            {
                "hour_utc": hour,
                "start": SESSION_START_HOUR_UTC,
                "end": SESSION_END_HOUR_UTC,
                "enabled": SESSION_FILTER_ENABLED,
            },
        )
    )
    if failed:
        skip_rest(remaining)
        return GuardTrace(guards=guards, final="BLOCKED", blocked_by=blocked_by)

    # X daily loss USD
    account.ensure_day(ts)
    if MAX_DAILY_LOSS_USD > 0:
        daily = account.daily_realized_loss
        ok = daily < MAX_DAILY_LOSS_USD
        add(
            GuardResult(
                "daily_loss_usd",
                RESULT_PASS if ok else RESULT_FAIL,
                QUALITY_SIMULATED,
                {
                    "daily_realized_loss": daily,
                    "limit": MAX_DAILY_LOSS_USD,
                    "note": "PRODUCTION_GAP: live uses process-memory fed by dashboard poller",
                },
            )
        )
    else:
        add(
            GuardResult(
                "daily_loss_usd",
                RESULT_PASS,
                QUALITY_NOT_APPLICABLE,
                {"limit": 0, "note": "disabled"},
            )
        )
    if failed:
        skip_rest(remaining[1:])
        return GuardTrace(guards=guards, final="BLOCKED", blocked_by=blocked_by)

    # Strategy daily drawdown % (scan_once pre-check)
    max_dd = account.equity * (STRATEGY_DAILY_DRAWDOWN_PERCENT / 100.0)
    ok = account.daily_realized_loss < max_dd if max_dd > 0 else True
    add(
        GuardResult(
            "strategy_daily_drawdown",
            RESULT_PASS if ok else RESULT_FAIL,
            QUALITY_SIMULATED,
            {
                "daily_realized_loss": account.daily_realized_loss,
                "limit_usd": round(max_dd, 2),
                "percent": STRATEGY_DAILY_DRAWDOWN_PERCENT,
                "equity": account.equity,
            },
        )
    )
    if failed:
        skip_rest(remaining[2:])
        return GuardTrace(guards=guards, final="BLOCKED", blocked_by=blocked_by)

    # Infrastructure — pass for market baseline
    for name, note in (
        ("database", "INFRASTRUCTURE_GUARD excluded from market performance baseline"),
        ("mt5_connect", "Dataset already downloaded; no live connect"),
        ("account_info", "Using SimulatedAccount"),
    ):
        cat = "INFRASTRUCTURE" if name != "account_info" else "TRADING"
        q = QUALITY_NOT_APPLICABLE if name != "account_info" else QUALITY_SIMULATED
        add(GuardResult(name, RESULT_PASS, q, {"note": note}, category=cat))

    # margin
    ml = account.margin_level()
    if account.margin_used <= 0:
        add(
            GuardResult(
                "margin_level",
                RESULT_PASS,
                QUALITY_SIMULATED,
                {"margin_used": 0, "note": "no open margin — first trade allowed"},
            )
        )
    else:
        ok = ml >= MIN_MARGIN_LEVEL
        add(
            GuardResult(
                "margin_level",
                RESULT_PASS if ok else RESULT_FAIL,
                QUALITY_SIMULATED,
                {"margin_level": round(ml, 2), "required": MIN_MARGIN_LEVEL},
            )
        )
    if failed:
        skip_rest(
            [
                "same_direction_stack",
                "dca_layer_cap",
                "tp_auto_adjust",
                "rr_ratio",
                "symbol_available",
                "tick_available",
                "spread",
                "proximity",
                "lot_calc",
                "source_risk",
            ]
        )
        return GuardTrace(guards=guards, final="BLOCKED", blocked_by=blocked_by)

    # stack
    stack_reduced = False
    existing_lot = 0.0
    if BLOCK_SAME_DIRECTION_STACK:
        at_risk = [
            p
            for p in open_positions
            if p.direction.lower() == direction.lower()
            and round(p.sl, 2) != round(p.entry, 2)
        ]
        if at_risk:
            if STACK_MODE == "reduce":
                existing_lot = sum(p.lot for p in at_risk)
                stack_reduced = True
                add(
                    GuardResult(
                        "same_direction_stack",
                        RESULT_ADJUSTED,
                        QUALITY_SIMULATED,
                        {
                            "mode": "reduce",
                            "existing_lot": existing_lot,
                            "count": len(at_risk),
                        },
                    )
                )
            else:
                add(
                    GuardResult(
                        "same_direction_stack",
                        RESULT_FAIL,
                        QUALITY_SIMULATED,
                        {"mode": STACK_MODE, "count": len(at_risk)},
                    )
                )
        else:
            add(
                GuardResult(
                    "same_direction_stack",
                    RESULT_PASS,
                    QUALITY_SIMULATED,
                    {"count": 0},
                )
            )
    else:
        add(
            GuardResult(
                "same_direction_stack",
                RESULT_PASS,
                QUALITY_NOT_APPLICABLE,
                {"enabled": False},
            )
        )
    if failed:
        skip_rest(
            [
                "dca_layer_cap",
                "tp_auto_adjust",
                "rr_ratio",
                "symbol_available",
                "tick_available",
                "spread",
                "proximity",
                "lot_calc",
                "source_risk",
            ]
        )
        return GuardTrace(guards=guards, final="BLOCKED", blocked_by=blocked_by)

    add(
        GuardResult(
            "dca_layer_cap",
            RESULT_PASS,
            QUALITY_NOT_APPLICABLE,
            {"note": "strategy entry has no own_tickets"},
        )
    )

    # TP adjust + RR
    entry_mid = float(entry)
    sl_distance = abs(entry_mid - sl)
    sl_pips_calc = sl_distance / SL_PIP_SIZE if SL_PIP_SIZE else 0
    effective_tp = float(tp)
    adjusted = False
    if sl_pips_calc < SL_MIN_PIPS:
        min_tp_pts = TP_ENFORCE_PIPS * SL_PIP_SIZE
        if abs(effective_tp - entry_mid) < min_tp_pts:
            effective_tp = round(
                entry_mid + min_tp_pts if direction.lower() == "buy" else entry_mid - min_tp_pts,
                2,
            )
            adjusted = True
    add(
        GuardResult(
            "tp_auto_adjust",
            RESULT_ADJUSTED if adjusted else RESULT_PASS,
            QUALITY_EXACT,
            {
                "sl_pips": round(sl_pips_calc, 2),
                "sl_min_pips": SL_MIN_PIPS,
                "tp_before": tp,
                "tp_after": effective_tp,
            },
        )
    )
    tp_distance = abs(effective_tp - entry_mid)
    rr_ratio = tp_distance / sl_distance if sl_distance > 0 else 0.0
    ok = rr_ratio >= MIN_RR_RATIO
    add(
        GuardResult(
            "rr_ratio",
            RESULT_PASS if ok else RESULT_FAIL,
            QUALITY_EXACT,
            {
                "rr": round(rr_ratio, 4),
                "minimum": MIN_RR_RATIO,
                "sl_distance": sl_distance,
                "tp_distance": tp_distance,
            },
        )
    )
    if failed:
        skip_rest(
            [
                "symbol_available",
                "tick_available",
                "spread",
                "proximity",
                "lot_calc",
                "source_risk",
            ]
        )
        return GuardTrace(
            guards=guards,
            final="BLOCKED",
            blocked_by=blocked_by,
            effective_tps=[effective_tp],
            rr_after_adjust=rr_ratio,
        )

    # max SL cap
    if STRATEGY_MAX_SL_PIPS > 0:
        sl_pips_actual = sl_distance / SL_PIP_SIZE if SL_PIP_SIZE > 0 else 0
        ok = sl_pips_actual <= STRATEGY_MAX_SL_PIPS
        add(
            GuardResult(
                "max_sl_cap",
                RESULT_PASS if ok else RESULT_FAIL,
                QUALITY_EXACT,
                {
                    "sl_pips": round(sl_pips_actual, 2),
                    "max_sl_pips": STRATEGY_MAX_SL_PIPS,
                },
            )
        )
    else:
        add(
            GuardResult(
                "max_sl_cap",
                RESULT_PASS,
                QUALITY_NOT_APPLICABLE,
                {"note": "disabled (STRATEGY_MAX_SL_PIPS=0)"},
            )
        )
    if failed:
        skip_rest(
            [
                "symbol_available",
                "tick_available",
                "spread",
                "proximity",
                "lot_calc",
                "source_risk",
            ]
        )
        return GuardTrace(
            guards=guards,
            final="BLOCKED",
            blocked_by=blocked_by,
            effective_tps=[effective_tp],
            rr_after_adjust=rr_ratio,
        )

    add(
        GuardResult(
            "symbol_available",
            RESULT_PASS,
            QUALITY_NOT_APPLICABLE,
            {"note": "symbol present in dataset"},
            category="INFRASTRUCTURE",
        )
    )
    add(
        GuardResult(
            "tick_available",
            RESULT_PASS,
            QUALITY_APPROXIMATED,
            {"note": "OHLC+spread proxy; no tick tape"},
            category="INFRASTRUCTURE",
        )
    )

    # spread
    if spread_policy == "unavailable":
        add(
            GuardResult(
                "spread",
                RESULT_PASS,
                QUALITY_NOT_AVAILABLE,
                {"policy": "unavailable", "note": "guard excluded from baseline block"},
            )
        )
        spread_pips = None
        half = 0.0
    elif spread_policy == "fixed":
        spread_pips = float(fixed_spread_pips or 0.0)
        ok = spread_pips <= MAX_SPREAD_PIPS
        add(
            GuardResult(
                "spread",
                RESULT_PASS if ok else RESULT_FAIL,
                QUALITY_APPROXIMATED,
                {
                    "policy": "fixed",
                    "spread_pips": spread_pips,
                    "max": MAX_SPREAD_PIPS,
                    "assumption": True,
                },
            )
        )
        half = (spread_pips * SL_PIP_SIZE) / 2.0
    else:
        # historical
        spread_pips = _spread_pips_from_points(spread_points, spec.point)
        if spread_pips is None:
            add(
                GuardResult(
                    "spread",
                    RESULT_PASS,
                    QUALITY_NOT_AVAILABLE,
                    {"policy": "historical", "note": "no spread on bar"},
                )
            )
            half = 0.0
        else:
            ok = spread_pips <= MAX_SPREAD_PIPS
            add(
                GuardResult(
                    "spread",
                    RESULT_PASS if ok else RESULT_FAIL,
                    QUALITY_EXACT if spec.point else QUALITY_APPROXIMATED,
                    {
                        "policy": "historical",
                        "spread_points": spread_points,
                        "point": spec.point,
                        "spread_pips": round(spread_pips, 4),
                        "max": MAX_SPREAD_PIPS,
                    },
                )
            )
            half = (spread_pips * SL_PIP_SIZE) / 2.0
    if failed:
        skip_rest(["proximity", "lot_calc", "source_risk"])
        return GuardTrace(
            guards=guards,
            final="BLOCKED",
            blocked_by=blocked_by,
            effective_tps=[effective_tp],
            rr_after_adjust=rr_ratio,
            spread_pips=spread_pips,
        )

    # proximity — strategy skips
    add(
        GuardResult(
            "proximity",
            RESULT_PASS,
            QUALITY_NOT_APPLICABLE,
            {"note": "strategy path skip_proximity=True"},
        )
    )

    # fill prices from close ± half spread
    fill_bid = entry - half
    fill_ask = entry + half
    fill_price = fill_ask if direction.lower() == "buy" else fill_bid

    lot, risk_usd, lot_expl = calculate_lot_pure(
        equity=account.equity,
        free_margin=account.free_margin(),
        entry=entry_mid,
        sl=sl,
        risk_percent=STRATEGY_RISK_PERCENT,
        spec=spec,
    )
    if stack_reduced:
        if lot <= existing_lot:
            add(
                GuardResult(
                    "lot_calc",
                    RESULT_FAIL,
                    QUALITY_SIMULATED,
                    {
                        "raw_lot": lot,
                        "existing_lot": existing_lot,
                        "note": "stack reduce exhausted risk budget",
                    },
                )
            )
            skip_rest(["source_risk"])
            return GuardTrace(
                guards=guards,
                final="BLOCKED",
                blocked_by="lot_calc",
                effective_tps=[effective_tp],
                rr_after_adjust=rr_ratio,
                spread_pips=spread_pips,
                fill_bid=fill_bid,
                fill_ask=fill_ask,
                fill_price=fill_price,
            )
        lot = max(MIN_LOT, round(lot - existing_lot, 2))
        lot_expl = f"stack-reduced lot={lot} (existing={existing_lot})"
    if lot <= 0:
        add(
            GuardResult(
                "lot_calc",
                RESULT_FAIL,
                QUALITY_SIMULATED,
                {"explanation": lot_expl},
            )
        )
        skip_rest(["source_risk"])
        return GuardTrace(
            guards=guards,
            final="BLOCKED",
            blocked_by="lot_calc",
            effective_tps=[effective_tp],
            rr_after_adjust=rr_ratio,
            spread_pips=spread_pips,
            fill_bid=fill_bid,
            fill_ask=fill_ask,
            fill_price=fill_price,
        )
    add(
        GuardResult(
            "lot_calc",
            RESULT_PASS,
            QUALITY_SIMULATED,
            {"lot": lot, "risk_usd": round(risk_usd, 2), "explanation": lot_expl},
        )
    )

    add(
        GuardResult(
            "source_risk",
            RESULT_PASS,
            QUALITY_NOT_APPLICABLE,
            {"note": "strategy Signal has no source_id"},
        )
    )

    return GuardTrace(
        guards=guards,
        final="WOULD_EXECUTE",
        blocked_by=None,
        effective_tps=[effective_tp],
        rr_after_adjust=rr_ratio,
        lot=lot,
        risk_usd=risk_usd,
        lot_explanation=lot_expl,
        fill_bid=fill_bid,
        fill_ask=fill_ask,
        fill_price=fill_price,
        spread_pips=spread_pips,
    )
