"""Simulated execution book — never calls MT5 order_send."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from backtest.account import SimulatedAccount
from backtest.interfaces import Candle
from backtest.outcomes import BarrierHit, check_bar_barriers, pnl_for_exit
from backtest.symbol_spec import SymbolSpec


@dataclass
class SimTrade:
    trade_id: str
    candidate_id: str
    symbol: str
    direction: str
    signal_time: str
    entry_time: str
    entry: float
    sl: float
    tp: float
    lot: float
    risk_usd: float
    balance_before: float
    guard_quality_summary: dict[str, int]
    status: str = "OPEN"  # OPEN | CLOSED
    exit_time: str | None = None
    exit: float | None = None
    outcome: str | None = None
    realized_pnl: float | None = None
    bars_held: int = 0
    mae: float | None = None
    mfe: float | None = None
    data_quality_warning: bool = False
    r_multiple: float | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "trade_id": self.trade_id,
            "candidate_id": self.candidate_id,
            "symbol": self.symbol,
            "direction": self.direction,
            "signal_time": self.signal_time,
            "entry_time": self.entry_time,
            "exit_time": self.exit_time,
            "entry": self.entry,
            "exit": self.exit,
            "sl": self.sl,
            "tp": self.tp,
            "lot": self.lot,
            "balance_before": self.balance_before,
            "balance_after": None
            if self.realized_pnl is None
            else round(self.balance_before + self.realized_pnl, 2),
            "risk_usd": self.risk_usd,
            "realized_pnl": self.realized_pnl,
            "outcome": self.outcome,
            "bars_held": self.bars_held,
            "mae": self.mae,
            "mfe": self.mfe,
            "r_multiple": self.r_multiple,
            "guard_quality_summary": self.guard_quality_summary,
            "data_quality_warning": self.data_quality_warning,
            "status": self.status,
            "meta": self.meta,
        }


class SimulatedExecution:
    def __init__(self, account: SimulatedAccount, spec: SymbolSpec) -> None:
        self.account = account
        self.spec = spec
        self.open: list[SimTrade] = []
        self.closed: list[SimTrade] = []
        self._seq = 0

    def _next_id(self) -> str:
        self._seq += 1
        return f"SIM{self._seq:05d}"

    def open_trade(self, **kwargs) -> SimTrade:
        t = SimTrade(trade_id=self._next_id(), **kwargs)
        margin = t.lot * self.spec.margin_per_lot
        self.account.margin_used += margin
        self.open.append(t)
        return t

    def floating_pnl(self, mark_price: float) -> float:
        total = 0.0
        for t in self.open:
            total += pnl_for_exit(
                t.direction,
                t.entry,
                mark_price,
                t.lot,
                tick_size=self.spec.tick_size,
                tick_value=self.spec.tick_value,
            )
        return total

    def process_bar(
        self,
        bar: Candle,
        ts: datetime,
        *,
        intrabar_policy: str = "conservative",
        only_engine: str | None = None,
    ) -> list[tuple[SimTrade, BarrierHit]]:
        """Update open trades against this bar; close on SL/TP."""
        hits: list[tuple[SimTrade, BarrierHit]] = []
        still_open: list[SimTrade] = []
        for t in self.open:
            eng = t.meta.get("outcome_engine", "legacy_m15")
            if only_engine and eng != only_engine:
                still_open.append(t)
                continue
            if eng == "m1":
                still_open.append(t)
                continue
            t.bars_held += 1
            # MAE/MFE vs close extremes
            if t.direction.lower() == "buy":
                adverse = t.entry - bar.low
                favor = bar.high - t.entry
            else:
                adverse = bar.high - t.entry
                favor = t.entry - bar.low
            t.mae = adverse if t.mae is None else max(t.mae, adverse)
            t.mfe = favor if t.mfe is None else max(t.mfe, favor)

            hit = check_bar_barriers(
                t.direction, t.sl, t.tp, bar, intrabar_policy=intrabar_policy
            )
            if hit is None:
                still_open.append(t)
                continue
            hits.append((t, hit))
            self._close(t, hit, ts)
        self.open = still_open
        # refresh margin
        self.account.margin_used = sum(x.lot * self.spec.margin_per_lot for x in self.open)
        mark = bar.close
        self.account.mark_equity(self.floating_pnl(mark), ts)
        return hits

    def _close(self, t: SimTrade, hit: BarrierHit, ts: datetime) -> None:
        t.status = "CLOSED"
        t.exit_time = ts.strftime("%Y-%m-%dT%H:%M:%SZ")
        if hit.outcome in ("AMBIGUOUS_INTRABAR", "AMBIGUOUS_M1_INTRABAR", "UNRESOLVED_DATA_GAP"):
            t.outcome = "AMBIGUOUS" if hit.outcome != "UNRESOLVED_DATA_GAP" else "UNRESOLVED_DATA_GAP"
            t.exit = None
            t.realized_pnl = 0.0
            t.r_multiple = None
            # do not change balance for unresolved ambiguity / data gap
        else:
            assert hit.exit_price is not None
            t.exit = hit.exit_price
            pnl = pnl_for_exit(
                t.direction,
                t.entry,
                hit.exit_price,
                t.lot,
                tick_size=self.spec.tick_size,
                tick_value=self.spec.tick_value,
            )
            t.realized_pnl = round(pnl, 2)
            if t.risk_usd and t.risk_usd > 0:
                t.r_multiple = round(pnl / t.risk_usd, 4)
            if hit.outcome == "SL_HIT":
                t.outcome = "LOSS" if pnl < 0 else "BREAKEVEN"
            elif hit.outcome == "TP_HIT":
                t.outcome = "WIN" if pnl > 0 else "BREAKEVEN"
            else:
                t.outcome = hit.outcome
            self.account.realize(t.realized_pnl, ts)
        self.closed.append(t)

    def process_m1_until(
        self,
        resolver: Any,
        cursor_unix: int,
        ts: datetime,
        *,
        intrabar_policy: str = "unresolved",
    ) -> list[tuple[SimTrade, BarrierHit]]:
        """Advance open trades using M1 chronology up to cursor."""
        from backtest.m1_resolver import M1OutcomeResolver

        assert isinstance(resolver, M1OutcomeResolver)
        hits: list[tuple[SimTrade, BarrierHit]] = []
        still_open: list[SimTrade] = []
        for t in self.open:
            if t.meta.get("outcome_engine") != "m1":
                still_open.append(t)
                continue
            entry_unix = int(t.meta.get("m1_entry_unix") or 0)
            from_after = t.meta.get("m1_last_scanned_open")
            res = resolver.walk_outcome(
                direction=t.direction,
                entry_unix=entry_unix,
                entry_price=t.entry,
                sl=t.sl,
                tp=t.tp,
                until_unix=cursor_unix,
                from_after_unix=from_after,
                intrabar_policy=intrabar_policy,
            )
            # update scan cursor to last closed M1 open at this wall time
            t.meta["m1_last_scanned_open"] = cursor_unix - 60
            if res.minutes_held is not None:
                t.bars_held = res.minutes_held  # minutes for M1 mode
            if res.data_quality_warning:
                t.data_quality_warning = True
            if res.outcome == "OPEN":
                still_open.append(t)
                continue
            if res.outcome == "AMBIGUOUS_M1_INTRABAR":
                hit = BarrierHit(
                    "AMBIGUOUS_M1_INTRABAR", None, res.exit_time_unix, res.ambiguity_reason
                )
            elif res.outcome == "UNRESOLVED_DATA_GAP":
                hit = BarrierHit("UNRESOLVED_DATA_GAP", None, None, "UNRESOLVED_DATA_GAP")
                t.meta["m1_unresolved"] = True
            elif res.outcome == "SL_HIT":
                hit = BarrierHit("SL_HIT", res.exit_price, res.exit_time_unix, res.note)
            elif res.outcome == "TP_HIT":
                hit = BarrierHit("TP_HIT", res.exit_price, res.exit_time_unix, res.note)
            else:
                still_open.append(t)
                continue
            exit_ts = ts
            if res.exit_time_unix:
                exit_ts = datetime.fromtimestamp(res.exit_time_unix, tz=timezone.utc)
            hits.append((t, hit))
            self._close(t, hit, exit_ts)
            t.meta["m1_resolution"] = res.to_dict()
            if res.outcome == "UNRESOLVED_DATA_GAP":
                t.outcome = "UNRESOLVED_DATA_GAP"
            elif res.outcome == "AMBIGUOUS_M1_INTRABAR":
                t.outcome = "AMBIGUOUS"
        self.open = still_open
        self.account.margin_used = sum(x.lot * self.spec.margin_per_lot for x in self.open)
        return hits

    def force_open_at_end(self, ts: datetime, mark: float) -> list[SimTrade]:
        out = []
        for t in list(self.open):
            t.status = "CLOSED"
            t.exit_time = ts.strftime("%Y-%m-%dT%H:%M:%SZ")
            t.exit = mark
            t.outcome = "OPEN_AT_END"
            pnl = pnl_for_exit(
                t.direction,
                t.entry,
                mark,
                t.lot,
                tick_size=self.spec.tick_size,
                tick_value=self.spec.tick_value,
            )
            t.realized_pnl = round(pnl, 2)
            if t.risk_usd and t.risk_usd > 0:
                t.r_multiple = round(pnl / t.risk_usd, 4)
            self.account.realize(t.realized_pnl, ts)
            self.closed.append(t)
            out.append(t)
        self.open = []
        self.account.margin_used = 0.0
        self.account.mark_equity(0.0, ts)
        return out
