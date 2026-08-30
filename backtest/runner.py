"""
Historical replay runner — decisions (+ optional guard/trade simulation).

Usage:
  set RUN_MODE=BACKTEST
  python -m backtest.runner --dataset ... --strategy breakout_retest_v1
  python -m backtest.runner --dataset ... --strategy breakout_retest   # alias → v1
  python -m backtest.runner --dataset ... --simulate-trades --initial-balance 10000
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backtest.account import SimulatedAccount
from backtest.clock import ReplayClock
from backtest.dataset import (
    candles_to_dicts,
    dataset_content_hash,
    format_validation_report,
    load_candles_csv,
    load_dataset_meta,
    validate_dataset,
)
from backtest.execution import SimulatedExecution
from backtest.costs import CostModel, apply_costs_to_trade_pnl
from backtest.fills import resolve_fill_price
from backtest.gap_analysis import analyze_gaps
from backtest.gitmeta import git_branch, git_commit
from backtest.guards import OpenPositionView, evaluate_strategy_guards
from backtest.m1_resolver import M1OutcomeResolver, classify_outcome_change
from backtest.notify import ReplayNotificationCollector
from backtest.performance import (
    assess_confidence,
    build_guard_funnel,
    build_performance,
    format_guard_funnel,
    format_performance,
    monthly_breakdown,
    summarize_guard_qualities,
    write_csv,
)
from backtest.provider import HistoricalReplayProvider, LookAheadError
from backtest.report import build_funnel, decision_label, format_funnel
from backtest.safety import assert_backtest_safe, assert_not_live_sinks
from backtest.spread_validation import analyze_spreads
from backtest.symbol_spec import resolve_symbol_spec
from core.config import SL_PIP_SIZE, STRATEGY_BREAKOUT_LOOKBACK, TREND_EMA_LONG
from core.strategies.base import MarketContext, build_market_context
from core.strategies.registry import get_strategy, resolve_strategy_name
from core.trend_analyzer import analyze_candles


DEFAULT_FILL_POLICY = "CANDIDATE_ENTRY_AT_SIGNAL_BAR_CLOSE"


def _issue(sink: list[dict], severity: str, code: str, message: str, **extra: Any) -> None:
    sink.append(
        {
            "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "severity": severity,
            "code": code,
            "message": message,
            **extra,
        }
    )


def _stable_hash(obj: Any) -> str:
    payload = json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode()).hexdigest()


def _config_snapshot() -> dict[str, Any]:
    from core import config as cfg

    keys = [
        "STRATEGY_SYMBOL",
        "STRATEGY_TIMEFRAME",
        "ACTIVE_STRATEGY",
        "STRATEGY_BREAKOUT_LOOKBACK",
        "STRATEGY_RETEST_TOLERANCE_PIPS",
        "STRATEGY_CONFIRM_BODY_RATIO",
        "STRATEGY_SWING_BUFFER_PIPS",
        "STRATEGY_TP_R_MULTIPLE",
        "STRATEGY_MIN_RR",
        "STRATEGY_RISK_PERCENT",
        "STRATEGY_DAILY_DRAWDOWN_PERCENT",
        "MIN_RR_RATIO",
        "MAX_SPREAD_PIPS",
        "MAX_DAILY_LOSS_USD",
        "SESSION_FILTER_ENABLED",
        "SESSION_START_HOUR_UTC",
        "SESSION_END_HOUR_UTC",
        "BLOCK_SAME_DIRECTION_STACK",
        "STACK_MODE",
        "MIN_MARGIN_LEVEL",
        "SL_PIP_SIZE",
        "SL_MIN_PIPS",
        "TP_ENFORCE_PIPS",
        "TREND_EMA_SHORT",
        "TREND_EMA_LONG",
        "TREND_RSI_PERIOD",
    ]
    return {k: getattr(cfg, k, None) for k in keys}


def _next_run_id(runs_root: Path, symbol: str, date_from: str, date_to: str) -> str:
    base = f"BT_{symbol.upper()}_{date_from.replace('-', '')}_{date_to.replace('-', '')}"
    runs_root.mkdir(parents=True, exist_ok=True)
    n = 1
    while True:
        rid = f"{base}_{n:03d}"
        if not (runs_root / rid).exists():
            return rid
        n += 1


def _trend_from_closed(candles_dicts: list[dict]) -> tuple[str, dict | None]:
    if len(candles_dicts) < TREND_EMA_LONG + 5:
        return "NEUTRAL", None
    closes = [c["close"] for c in candles_dicts]
    highs = [c["high"] for c in candles_dicts]
    lows = [c["low"] for c in candles_dicts]
    if any(not math.isfinite(x) for x in closes + highs + lows):
        return "NEUTRAL", {"error": "NaN indicator input"}
    result = analyze_candles(closes, highs, lows)
    if result is None:
        return "NEUTRAL", None
    return result.get("overall", "NEUTRAL"), result


def _quality_summary(trace_dict: dict) -> dict[str, int]:
    from collections import Counter

    c = Counter(g.get("quality") for g in trace_dict.get("guards", []))
    return dict(c)


def run_replay(
    dataset_dir: Path,
    *,
    strategy: str = "breakout_retest_v1",
    run_id: str | None = None,
    runs_root: Path | None = None,
    m15_window: int | None = None,
    max_bars: int | None = None,
    simulate_trades: bool = False,
    initial_balance: float | None = None,
    intrabar_policy: str = "conservative",
    spread_policy: str = "historical",
    fixed_spread_pips: float | None = None,
    symbol_spec_path: str | Path | None = None,
    fill_policy: str = DEFAULT_FILL_POLICY,
    commission_per_lot: float = 0.0,
    slippage_pips: float = 0.0,
    swap_policy: str = "none",
    outcome_engine: str = "legacy_m15",  # legacy_m15 | m1
    m1_intrabar_policy: str = "unresolved",
) -> dict[str, Any]:
    assert_backtest_safe("runner.run_replay")
    assert_not_live_sinks(mt5_enabled=False, telegram_enabled=False)

    strategy = resolve_strategy_name(strategy)
    plugin = get_strategy(strategy)
    required_tfs = tuple(getattr(plugin, "required_timeframes", ("M15", "H1", "H4")))
    v2_diag = None
    if strategy in ("structure_pullback_v2", "structure_pullback_v2_1", "structure_pullback_v2_2"):
        from backtest.v2_diagnostics import V2DiagnosticCollector

        v2_diag = V2DiagnosticCollector()
    if simulate_trades and (initial_balance is None or initial_balance <= 0):
        raise ValueError("--initial-balance > 0 is required with --simulate-trades")

    dataset_dir = Path(dataset_dir)
    meta = load_dataset_meta(dataset_dir)
    # Load union of dataset TFs + strategy-required (skip missing optional until validated)
    meta_tfs = [t.upper() for t in meta.get("timeframes", ["M15", "H1", "H4"])]
    timeframes = list(dict.fromkeys([*meta_tfs, *[t.upper() for t in required_tfs]]))
    missing_required = [t for t in required_tfs if t.upper() not in meta_tfs and t.upper() != "M1"]
    if missing_required:
        raise RuntimeError(
            f"Dataset missing required timeframes for {strategy}: {missing_required}. "
            f"Available: {meta_tfs}. Download with: python -m backtest.download_history "
            f"--into {dataset_dir} --timeframes {','.join(missing_required)}"
        )
    validation = validate_dataset(dataset_dir, [t for t in timeframes if (dataset_dir / f"{t}.csv").is_file()])
    if validation.status == "INVALID":
        raise RuntimeError("Dataset INVALID — refusing replay.\n" + format_validation_report(validation))

    symbol = meta.get("symbol", "XAUUSD")
    date_from = meta.get("date_from", "unknown")
    date_to = meta.get("date_to", "unknown")
    runs_root = runs_root or (ROOT / "data" / "backtests")
    run_id = run_id or _next_run_id(runs_root, symbol, str(date_from), str(date_to))
    out_dir = runs_root / run_id
    out_dir.mkdir(parents=True, exist_ok=True)

    issues: list[dict] = []
    events_journal: list[dict] = []
    if validation.status == "VALID_WITH_GAPS":
        _issue(issues, "MEDIUM", "dataset_gap", "Dataset validated with gaps", status=validation.status)

    provider_probe = HistoricalReplayProvider(
        dataset_dir, ReplayClock(cursor=datetime(1970, 1, 1, tzinfo=timezone.utc))
    )
    events = provider_probe.m15_close_events()
    if not events:
        raise RuntimeError("No M15 candles in dataset")

    start_cursor = events[0][0]
    clock = ReplayClock(cursor=start_cursor)
    provider = HistoricalReplayProvider(dataset_dir, clock, symbol=symbol, timeframes=timeframes)

    window = m15_window or max(STRATEGY_BREAKOUT_LOOKBACK + 5, 50)
    htf_count = 100

    decisions: list[dict] = []
    candidate_audit: list[dict] = []
    guards_journal: list[dict] = []
    equity_rows: list[dict] = []
    seen_ts: set[str] = set()
    last_ts: datetime | None = None
    start_wall = datetime.now(timezone.utc)
    m15_processed = 0
    total_events = len(events) if max_bars is None else min(len(events), max_bars)
    progress_every = max(100, total_events // 20)

    spec = resolve_symbol_spec(symbol_spec_path)
    if simulate_trades and symbol_spec_path is None:
        _issue(
            issues,
            "MEDIUM",
            "symbol_spec_assumption",
            "Using ASSUMPTION SymbolSpec — pass --symbol-spec for EXACT_BROKER_METADATA",
        )
    if simulate_trades and spec.margin_per_lot <= 0:
        # keep simulation runnable but flag
        from dataclasses import replace

        spec = replace(spec, margin_per_lot=1000.0)
        _issue(
            issues,
            "HIGH",
            "margin_per_lot_missing",
            "margin_per_lot missing/zero in spec — fell back to 1000 for SIMULATED margin only",
        )

    cost_model = CostModel(
        commission_per_lot=float(commission_per_lot or 0),
        slippage_pips=float(slippage_pips or 0),
        swap_policy=swap_policy or "none",
    )
    account = SimulatedAccount(initial_balance=float(initial_balance or 0) or 1.0) if simulate_trades else None
    execution = SimulatedExecution(account, spec) if simulate_trades and account else None
    notifier = ReplayNotificationCollector() if simulate_trades else None
    cand_seq = 0
    fill_policy_used = fill_policy
    outcome_engine = (outcome_engine or "legacy_m15").lower()
    if outcome_engine not in ("legacy_m15", "m1"):
        raise ValueError("outcome_engine must be legacy_m15 or m1")

    m1_resolver: M1OutcomeResolver | None = None
    if outcome_engine == "m1":
        m1_path = dataset_dir / "M1.csv"
        if not m1_path.is_file():
            raise FileNotFoundError(
                f"M1.csv required for outcome_engine=m1 — not found in {dataset_dir}"
            )
        m1_candles = load_candles_csv(m1_path, timeframe="M1")
        m1_resolver = M1OutcomeResolver(m1_candles, point=spec.point)
        print(
            f"[replay] M1 loaded: {len(m1_candles):,} bars "
            f"coverage={m1_candles[0].time}→{m1_candles[-1].time}",
            flush=True,
        )

    # Index next-bar opens for fill policy
    m15_by_open = {c.time: c for c in provider_probe._series.get("M15", [])}

    mode = "simulate" if simulate_trades else "decisions-only"
    print(
        f"[replay] starting {strategy} on {symbol}: {total_events:,} M15 closes "
        f"mode={mode} spec={spec.quality} (dataset={dataset_dir.name})",
        flush=True,
    )

    for close_dt, m15_bar in events:
        if max_bars is not None and m15_processed >= max_bars:
            break
        try:
            clock.advance_to(close_dt)
        except ValueError as e:
            _issue(issues, "CRITICAL", "out_of_order_processing", str(e), timestamp=close_dt.isoformat())
            continue

        ts_key = close_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        if ts_key in seen_ts:
            _issue(issues, "HIGH", "duplicate_evaluation_timestamp", ts_key)
            continue
        seen_ts.add(ts_key)
        if last_ts is not None and close_dt < last_ts:
            _issue(issues, "CRITICAL", "out_of_order_processing", f"{close_dt} < {last_ts}")
        last_ts = close_dt
        m15_processed += 1
        if m15_processed == 1 or m15_processed % progress_every == 0 or m15_processed == total_events:
            pct = 100.0 * m15_processed / total_events if total_events else 100.0
            elapsed = (datetime.now(timezone.utc) - start_wall).total_seconds()
            rate = m15_processed / elapsed if elapsed > 0 else 0.0
            eta = (total_events - m15_processed) / rate if rate > 0 else 0.0
            print(
                f"[replay] {m15_processed:,}/{total_events:,} ({pct:.1f}%) "
                f"ts={ts_key} elapsed={elapsed:.0f}s eta~{eta:.0f}s",
                flush=True,
            )

        # 1) Update open positions to this cursor
        if execution is not None and account is not None:
            hits = []
            if outcome_engine == "m1" and m1_resolver is not None:
                hits.extend(
                    execution.process_m1_until(
                        m1_resolver,
                        int(close_dt.timestamp()),
                        close_dt,
                        intrabar_policy=m1_intrabar_policy,
                    )
                )
                # legacy-fallback trades (pre-M1 coverage) still use M15 bars
                hits.extend(
                    execution.process_bar(
                        m15_bar,
                        close_dt,
                        intrabar_policy=intrabar_policy,
                        only_engine="legacy_m15",
                    )
                )
            else:
                hits = execution.process_bar(m15_bar, close_dt, intrabar_policy=intrabar_policy)
            for trade, hit in hits:
                ev = hit.outcome
                if outcome_engine == "m1":
                    events_journal.append(
                        {
                            "timestamp": ts_key,
                            "event_type": f"M1_{ev}" if trade.meta.get("outcome_engine") == "m1" else ev,
                            "trade_id": trade.trade_id,
                            "candidate_id": trade.candidate_id,
                            "context": {"exit": hit.exit_price, "note": hit.note},
                        }
                    )
                else:
                    events_journal.append(
                        {
                            "timestamp": ts_key,
                            "event_type": hit.outcome,
                            "trade_id": trade.trade_id,
                            "candidate_id": trade.candidate_id,
                            "context": {"exit": hit.exit_price, "note": hit.note},
                        }
                    )
                notifier.emit(
                    ts_key,
                    hit.outcome,
                    f"{trade.trade_id} {hit.outcome}",
                    classification="BACKTEST_ONLY_EVENT",
                    candidate_id=trade.candidate_id,
                    trade_id=trade.trade_id,
                )
                if trade.outcome in ("AMBIGUOUS", "UNRESOLVED_DATA_GAP"):
                    _issue(issues, "MEDIUM", "ambiguous_or_unresolved", trade.trade_id, timestamp=ts_key)
            equity_rows.append(
                {
                    "timestamp": ts_key,
                    "balance": round(account.balance, 2),
                    "equity": round(account.equity, 2),
                    "realized_pnl": round(account.realized_pnl, 2),
                    "drawdown": round(account.peak_equity - account.equity, 2),
                    "drawdown_pct": round(
                        100.0 * (account.peak_equity - account.equity) / account.peak_equity, 4
                    )
                    if account.peak_equity
                    else 0.0,
                }
            )

        try:
            use_stub = strategy == "breakout_retest_v1"
            candle_map = provider.candles_map_for_strategy(
                required_tfs,
                m15_window=window,
                htf_window=htf_count,
                use_m15_forming_stub=use_stub,
            )
            # Ensure H1/H4 always available for trend (V1)
            if "H1" not in candle_map:
                candle_map["H1"] = provider.closed_dicts("H1", htf_count)
            if "H4" not in candle_map:
                candle_map["H4"] = provider.closed_dicts("H4", htf_count)
            provider.assert_no_future_access()
        except LookAheadError as e:
            _issue(issues, "CRITICAL", "future_candle_accessed", str(e), timestamp=ts_key)
            continue
        except Exception as e:
            _issue(issues, "CRITICAL", "strategy_exception", f"provider: {e}", timestamp=ts_key)
            continue

        h1_dicts = list(candle_map.get("H1") or [])
        h4_dicts = list(candle_map.get("H4") or [])
        if len(h1_dicts) == 0 or len(h4_dicts) == 0:
            _issue(
                issues,
                "MEDIUM",
                "missing_required_timeframe_history",
                f"H1={len(h1_dicts)} H4={len(h4_dicts)} at {ts_key}",
                timestamp=ts_key,
            )

        h1_dir, h1_ctx = _trend_from_closed(h1_dicts)
        h4_dir, h4_ctx = _trend_from_closed(h4_dicts)

        try:
            bid, ask = provider.get_tick(symbol)
            market_ctx = build_market_context(
                symbol=symbol,
                timestamp=ts_key,
                candles=candle_map,
                h1_direction=h1_dir,
                h4_direction=h4_dir,
                bid=bid,
                ask=ask,
            )
            decision = plugin.evaluate(market_ctx)
            if v2_diag is not None:
                # Passive observer — must not alter decision
                v2_diag.after_evaluate(
                    plugin,
                    market_ctx,
                    decision,
                    ts_key,
                    cursor_unix=int(close_dt.timestamp()),
                )
        except Exception as e:
            _issue(issues, "CRITICAL", "strategy_exception", str(e), timestamp=ts_key)
            continue

        label = decision_label(decision.action, decision.direction)
        context = {
            "m15_close": m15_bar.close,
            "m15_open_time": m15_bar.time,
            "h1_direction": h1_dir,
            "h4_direction": h4_dir,
            "h1_bars_visible": len(h1_dicts),
            "h4_bars_visible": len(h4_dicts),
            "m30_bars_visible": len(candle_map.get("M30") or []),
            "h1": h1_ctx,
            "h4": h4_ctx,
            "action": decision.action,
            "direction": decision.direction,
            "level": decision.level,
        }
        rec: dict[str, Any] = {
            "timestamp": ts_key,
            "symbol": symbol,
            "strategy": strategy,
            "decision": label,
            "reason": decision.reason,
            "context": context,
        }
        if decision.action == "enter":
            rec["entry"] = decision.entry
            rec["sl"] = decision.sl
            rec["tp"] = decision.tp
            if decision.entry and decision.sl and decision.tp:
                risk = abs(decision.entry - decision.sl)
                reward = abs(decision.tp - decision.entry)
                rec["rr"] = round(reward / risk, 4) if risk > 0 else None
        decisions.append(rec)

        # 2–6) Candidate → guards → maybe open
        if simulate_trades and execution and account and notifier and decision.action == "enter":
            cand_seq += 1
            candidate_id = f"CAND_{cand_seq:04d}_{label}"
            events_journal.append(
                {
                    "timestamp": ts_key,
                    "event_type": "CANDIDATE_CREATED",
                    "candidate_id": candidate_id,
                    "context": {"direction": label, "entry": decision.entry, "sl": decision.sl, "tp": decision.tp},
                }
            )
            notifier.emit(
                ts_key,
                "CANDIDATE_CREATED",
                f"{candidate_id} {label}",
                classification="BACKTEST_ONLY_EVENT",
                candidate_id=candidate_id,
            )

            open_views = [
                OpenPositionView(t.trade_id, t.direction, t.entry, t.sl, t.lot) for t in execution.open
            ]
            trace = evaluate_strategy_guards(
                ts=close_dt,
                direction=decision.direction or "buy",
                entry=float(decision.entry),
                sl=float(decision.sl),
                tp=float(decision.tp),
                account=account,
                open_positions=open_views,
                spec=spec,
                spread_points=m15_bar.spread,
                spread_policy=spread_policy,
                fixed_spread_pips=fixed_spread_pips,
            )
            trace_dict = trace.to_dict()
            guards_journal.append(
                {
                    "timestamp": ts_key,
                    "candidate_id": candidate_id,
                    "direction": label,
                    "entry": decision.entry,
                    "sl": decision.sl,
                    "tp": decision.tp,
                    "strategy_rr": rec.get("rr"),
                    **trace_dict,
                }
            )

            audit_row = {
                "candidate_id": candidate_id,
                "timestamp": ts_key,
                "direction": label,
                "entry": decision.entry,
                "sl": decision.sl,
                "tp": decision.tp,
                "strategy_rr": rec.get("rr"),
                "h1_direction": h1_dir,
                "h4_direction": h4_dir,
                "guard_final": trace.final,
                "first_block_reason": trace.blocked_by or "",
                "would_execute": trace.final == "WOULD_EXECUTE",
                "entry_time": "",
                "exit_time": "",
                "outcome": "",
                "lot": trace.lot or "",
                "pnl": "",
                "R": "",
                "notification_count": 0,
                "issue_count": 0,
                "rr_after_adjust": trace.rr_after_adjust,
                "fill_policy": fill_policy_used,
            }
            if v2_diag is not None:
                v2_diag.note_guard(
                    candidate_id,
                    trace.blocked_by,
                    trace.final == "WOULD_EXECUTE",
                    rec.get("rr"),
                )
                if v2_diag.candidates:
                    v2_diag.candidates[-1]["guard_result"] = trace.final
                    v2_diag.candidates[-1]["first_block_reason"] = trace.blocked_by
                    v2_diag.candidates[-1]["would_execute"] = trace.final == "WOULD_EXECUTE"

            if trace.final == "BLOCKED":
                events_journal.append(
                    {
                        "timestamp": ts_key,
                        "event_type": "TRADE_BLOCKED",
                        "candidate_id": candidate_id,
                        "context": {"blocked_by": trace.blocked_by},
                    }
                )
                events_journal.append(
                    {
                        "timestamp": ts_key,
                        "event_type": "GUARD_FAIL",
                        "candidate_id": candidate_id,
                        "context": {"guard": trace.blocked_by},
                    }
                )
                notifier.emit(
                    ts_key,
                    "TRADE_BLOCKED",
                    f"{candidate_id} blocked by {trace.blocked_by}",
                    classification="BACKTEST_ONLY_EVENT",
                    candidate_id=candidate_id,
                )
            else:
                cursor_unix = int(close_dt.timestamp())
                next_c = m15_by_open.get(cursor_unix)
                next_open = next_c.open if next_c else None
                eff_tp = trace.effective_tps[0] if trace.effective_tps else float(decision.tp)
                trade_meta: dict[str, Any] = {
                    "fill_policy": fill_policy_used,
                    "spread_pips": trace.spread_pips,
                    "symbol_spec_quality": spec.quality,
                    "outcome_engine": "legacy_m15",
                }
                fill: float
                fill_q: str
                entry_time_str = ts_key

                if outcome_engine == "m1" and m1_resolver is not None:
                    m1_first = m1_resolver.times[0] if m1_resolver.times else None
                    if m1_first is not None and cursor_unix >= m1_first:
                        fill, entry_unix, pol = m1_resolver.resolve_entry(
                            signal_close_unix=cursor_unix,
                            direction=decision.direction or "buy",
                            candidate_entry=float(decision.entry),
                            spread_points=m15_bar.spread,
                            cursor_unix=cursor_unix,
                        )
                        fill_q = "M1_APPROXIMATED"
                        trade_meta.update(
                            {
                                "outcome_engine": "m1",
                                "fill_policy": pol,
                                "fill_quality": fill_q,
                                "m1_entry_unix": entry_unix,
                            }
                        )
                        entry_time_str = datetime.fromtimestamp(
                            entry_unix, tz=timezone.utc
                        ).strftime("%Y-%m-%dT%H:%M:%SZ")
                        events_journal.append(
                            {
                                "timestamp": ts_key,
                                "event_type": "M1_ENTRY_RESOLVED",
                                "candidate_id": candidate_id,
                                "context": {"entry": fill, "entry_unix": entry_unix, "policy": pol},
                            }
                        )
                    else:
                        fill, fill_q = resolve_fill_price(
                            fill_policy_used,
                            candidate_entry=float(decision.entry),
                            signal_bar_close=float(m15_bar.close),
                            next_bar_open=next_open,
                        )
                        trade_meta.update(
                            {
                                "outcome_engine": "legacy_m15",
                                "fill_quality": fill_q,
                                "m1_fallback": "NO_M1_COVERAGE",
                            }
                        )
                        _issue(
                            issues,
                            "MEDIUM",
                            "m1_coverage_fallback",
                            f"{candidate_id} before M1 history — legacy M15 outcome",
                            timestamp=ts_key,
                        )
                else:
                    fill, fill_q = resolve_fill_price(
                        fill_policy_used,
                        candidate_entry=float(decision.entry),
                        signal_bar_close=float(m15_bar.close),
                        next_bar_open=next_open,
                    )
                    trade_meta["fill_quality"] = fill_q

                trade = execution.open_trade(
                    candidate_id=candidate_id,
                    symbol=symbol,
                    direction=decision.direction or "buy",
                    signal_time=ts_key,
                    entry_time=entry_time_str,
                    entry=fill,
                    sl=float(decision.sl),
                    tp=eff_tp,
                    lot=float(trace.lot or 0),
                    risk_usd=float(trace.risk_usd or 0),
                    balance_before=account.balance,
                    guard_quality_summary=_quality_summary(trace_dict),
                    meta=trade_meta,
                )
                events_journal.append(
                    {
                        "timestamp": ts_key,
                        "event_type": "TRADE_OPENED",
                        "candidate_id": candidate_id,
                        "trade_id": trade.trade_id,
                        "context": {
                            "entry": fill,
                            "lot": trade.lot,
                            "tp": eff_tp,
                            "outcome_engine": trade_meta["outcome_engine"],
                        },
                    }
                )
                events_journal.append(
                    {
                        "timestamp": ts_key,
                        "event_type": "GUARD_PASS",
                        "candidate_id": candidate_id,
                        "trade_id": trade.trade_id,
                        "context": {},
                    }
                )
                notifier.emit(
                    ts_key,
                    "TRADE_OPENED",
                    f"{trade.trade_id} opened",
                    classification="BACKTEST_ONLY_EVENT",
                    candidate_id=candidate_id,
                    trade_id=trade.trade_id,
                )
                audit_row["entry_time"] = entry_time_str
                audit_row["lot"] = trade.lot

            candidate_audit.append(audit_row)

    # End of data: flush M1 then close remaining
    if execution is not None and account is not None and last_ts is not None:
        if outcome_engine == "m1" and m1_resolver is not None:
            execution.process_m1_until(
                m1_resolver,
                int(last_ts.timestamp()),
                last_ts,
                intrabar_policy=m1_intrabar_policy,
            )
        mark = events[min(m15_processed, len(events)) - 1][1].close if m15_processed else 0.0
        for t in execution.force_open_at_end(last_ts, mark):
            events_journal.append(
                {
                    "timestamp": last_ts.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "event_type": "TRADE_CLOSED",
                    "trade_id": t.trade_id,
                    "candidate_id": t.candidate_id,
                    "context": {"outcome": "OPEN_AT_END", "exit": t.exit},
                }
            )

    finish_wall = datetime.now(timezone.utc)
    funnel = build_funnel(decisions)
    decision_hash = _stable_hash(
        [{"t": d["timestamp"], "d": d["decision"], "r": d["reason"]} for d in decisions]
    )

    # Enrich candidate audit with trade outcomes
    trades_by_cand = {}
    if execution is not None:
        for t in execution.closed:
            trades_by_cand[t.candidate_id] = t.to_dict()
        for row in candidate_audit:
            td = trades_by_cand.get(row["candidate_id"])
            if td:
                row["exit_time"] = td.get("exit_time") or ""
                row["outcome"] = td.get("outcome") or ""
                row["pnl"] = td.get("realized_pnl") if td.get("realized_pnl") is not None else ""
                row["R"] = td.get("r_multiple") if td.get("r_multiple") is not None else ""
                if not row.get("entry_time"):
                    row["entry_time"] = td.get("entry_time") or ""
                if not row.get("lot"):
                    row["lot"] = td.get("lot") or ""

    raw_buy_sell = sum(1 for d in decisions if d["decision"] in ("BUY", "SELL"))
    if simulate_trades:
        if len(candidate_audit) != raw_buy_sell:
            _issue(
                issues,
                "CRITICAL",
                "candidate_count_mismatch",
                f"audit={len(candidate_audit)} decisions_buy_sell={raw_buy_sell}",
            )
            raise RuntimeError(
                f"Candidate accounting failed: audit {len(candidate_audit)} != BUY+SELL {raw_buy_sell}"
            )

    # Gap + spread analysis
    gap_report = {}
    spread_report = {}
    confidence_components: dict[str, str] = {}
    if simulate_trades:
        m15_series = provider_probe._series.get("M15", [])
        cand_times = set()
        cand_bar_opens = set()
        for row in candidate_audit:
            try:
                ct = int(
                    datetime.strptime(row["timestamp"], "%Y-%m-%dT%H:%M:%SZ")
                    .replace(tzinfo=timezone.utc)
                    .timestamp()
                )
                cand_times.add(ct)
                cand_bar_opens.add(ct - 900)
            except Exception:
                pass
        trade_windows: list[tuple[int, int]] = []
        if execution is not None:
            for tr in execution.closed:
                try:
                    ent = int(
                        datetime.strptime(tr.entry_time, "%Y-%m-%dT%H:%M:%SZ")
                        .replace(tzinfo=timezone.utc)
                        .timestamp()
                    )
                    ex = ent
                    if tr.exit_time:
                        ex = int(
                            datetime.strptime(tr.exit_time, "%Y-%m-%dT%H:%M:%SZ")
                            .replace(tzinfo=timezone.utc)
                            .timestamp()
                        )
                    trade_windows.append((ent, ex))
                except Exception:
                    pass
        gap_report = analyze_gaps(
            m15_series, "M15", candidate_times=cand_times, trade_windows=trade_windows
        )
        spread_report = analyze_spreads(
            m15_series, spec, candidate_bar_times=cand_bar_opens
        )

    # Performance
    performance = None
    performance_cost = None
    guard_funnel = None
    confidence = None
    confidence_reasons: list[str] = []
    confidence_components: dict[str, str] = {}
    trades_out: list[dict] = []
    performance_cost = None
    if simulate_trades and execution and account:
        trades_out = [t.to_dict() for t in execution.closed]
        guard_funnel = build_guard_funnel(candidate_audit)
        performance = build_performance(
            trades_out,
            initial_balance=float(initial_balance),
            ending_balance=account.balance,
            account_snapshot=account.snapshot(),
        )
        # Cost scenario (separate — does not overwrite RAW)
        if not cost_model.is_raw:
            adj_trades = []
            net_adj = 0.0
            for t in trades_out:
                raw = t.get("realized_pnl")
                if raw is None or t.get("exit") is None:
                    adj_trades.append(t)
                    continue
                br = apply_costs_to_trade_pnl(
                    direction=t["direction"],
                    entry=t["entry"],
                    exit_price=t["exit"],
                    lot=t["lot"],
                    raw_pnl=float(raw),
                    cost=cost_model,
                    pip_size=SL_PIP_SIZE,
                    tick_size=spec.tick_size,
                    tick_value=spec.tick_value,
                )
                tt = dict(t)
                tt["realized_pnl"] = br["adjusted_pnl"]
                tt["cost_breakdown"] = br
                adj_trades.append(tt)
                net_adj += br["adjusted_pnl"]
            # rebuild lightweight performance for cost scenario
            performance_cost = build_performance(
                adj_trades,
                initial_balance=float(initial_balance),
                ending_balance=float(initial_balance) + net_adj,
                account_snapshot={
                    **account.snapshot(),
                    "max_drawdown_usd": None,
                    "max_drawdown_pct": None,
                },
            )
            performance_cost["costs"] = cost_model.to_dict()

        confidence, confidence_reasons, confidence_components = assess_confidence(
            validation_status=validation.status,
            ambiguous=performance["ambiguous"],
            spread_policy=spread_policy,
            fill_policy=fill_policy_used,
            suspicious_gaps=gap_report.get("suspicious_count", 0),
            material_gaps=gap_report.get("materially_affected_gaps", 0),
            symbol_spec_quality=spec.quality,
            spread_conversion_validated=bool(spread_report.get("conversion_validated")),
            costs_modelled=not cost_model.is_raw,
            outcome_engine=outcome_engine,
            m1_coverage_partial=bool(
                m1_resolver
                and any(
                    (t.get("meta") or {}).get("m1_fallback") == "NO_M1_COVERAGE" for t in trades_out
                )
            ),
        )

    sim_hash_payload = {
        "dataset_hash": meta.get("data_checksum_sha256") or dataset_content_hash(dataset_dir, timeframes),
        "decision_hash": decision_hash,
        "config": _config_snapshot(),
        "strategy": strategy,
        "simulate_trades": simulate_trades,
        "initial_balance": initial_balance,
        "intrabar_policy": intrabar_policy,
        "spread_policy": spread_policy,
        "fixed_spread_pips": fixed_spread_pips,
        "outcome_engine": outcome_engine,
        "fill_policy": fill_policy_used,
        "symbol_spec": spec.to_dict(),
        "cost_model": cost_model.to_dict(),
        "candidate_audit": candidate_audit,
        "trades": trades_out,
        "performance": performance,
    }
    run_hash = _stable_hash(sim_hash_payload if simulate_trades else {
        "dataset_hash": sim_hash_payload["dataset_hash"],
        "decision_hash": decision_hash,
        "config": _config_snapshot(),
        "strategy": strategy,
        "count": len(decisions),
    })

    # Persist
    with (out_dir / "decisions.jsonl").open("w", encoding="utf-8") as f:
        for rec in decisions:
            f.write(json.dumps(rec, sort_keys=True) + "\n")
    with (out_dir / "issues.jsonl").open("w", encoding="utf-8") as f:
        for iss in issues:
            f.write(json.dumps(iss, sort_keys=True) + "\n")
    with (out_dir / "funnel.json").open("w", encoding="utf-8") as f:
        json.dump(funnel, f, indent=2, sort_keys=True)
        f.write("\n")
    with (out_dir / "funnel.txt").open("w", encoding="utf-8") as f:
        f.write(format_funnel(funnel) + "\n")

    if simulate_trades:
        with (out_dir / "events.jsonl").open("w", encoding="utf-8") as f:
            for e in events_journal:
                f.write(json.dumps(e, sort_keys=True) + "\n")
        with (out_dir / "guards.jsonl").open("w", encoding="utf-8") as f:
            for g in guards_journal:
                f.write(json.dumps(g, sort_keys=True) + "\n")
        with (out_dir / "trades.jsonl").open("w", encoding="utf-8") as f:
            for t in trades_out:
                f.write(json.dumps(t, sort_keys=True) + "\n")
        write_csv(out_dir / "candidate_audit.csv", candidate_audit)
        write_csv(out_dir / "equity_curve.csv", equity_rows)
        months = monthly_breakdown(trades_out)
        write_csv(out_dir / "monthly.csv", months)
        with (out_dir / "guard_funnel.json").open("w", encoding="utf-8") as f:
            json.dump(guard_funnel, f, indent=2, sort_keys=True)
            f.write("\n")
        with (out_dir / "guard_funnel.txt").open("w", encoding="utf-8") as f:
            f.write(format_guard_funnel(guard_funnel or {}) + "\n")
        with (out_dir / "performance.json").open("w", encoding="utf-8") as f:
            payload = {
                "scenario": "RAW_PRICE_BASELINE",
                "performance": performance,
                "confidence": confidence,
                "confidence_reasons": confidence_reasons,
                "confidence_components": confidence_components,
                "guard_qualities": summarize_guard_qualities(guards_journal),
                "spread_validation": spread_report,
                "symbol_spec_quality": spec.quality,
            }
            json.dump(payload, f, indent=2, sort_keys=True)
            f.write("\n")
        with (out_dir / "performance.txt").open("w", encoding="utf-8") as f:
            f.write(
                format_performance(
                    performance or {},
                    confidence=confidence or "N/A",
                    confidence_reasons=confidence_reasons,
                    components=confidence_components,
                )
                + "\n"
            )
        if performance_cost is not None:
            with (out_dir / "performance_cost_scenario.json").open("w", encoding="utf-8") as f:
                json.dump(
                    {
                        "scenario": "COST_SCENARIO",
                        "performance": performance_cost,
                        "cost_model": cost_model.to_dict(),
                    },
                    f,
                    indent=2,
                    sort_keys=True,
                )
                f.write("\n")
        with (out_dir / "notification_replay.jsonl").open("w", encoding="utf-8") as f:
            for n in notifier.notes if notifier else []:
                f.write(json.dumps(n.to_dict(), sort_keys=True) + "\n")
        with (out_dir / "gap_analysis.json").open("w", encoding="utf-8") as f:
            json.dump(gap_report, f, indent=2, sort_keys=True)
            f.write("\n")
        if outcome_engine == "m1" and m1_resolver is not None:
            m1_gap = analyze_gaps(m1_resolver.candles, "M1")
            with (out_dir / "gap_analysis_m1.json").open("w", encoding="utf-8") as f:
                json.dump(m1_gap, f, indent=2, sort_keys=True)
                f.write("\n")
        with (out_dir / "spread_validation.json").open("w", encoding="utf-8") as f:
            json.dump(spread_report, f, indent=2, sort_keys=True)
            f.write("\n")

    run_meta = {
        "run_id": run_id,
        "git_branch": git_branch(ROOT),
        "git_commit": git_commit(ROOT),
        "symbol": symbol,
        "dataset_id": meta.get("dataset_id") or dataset_dir.name,
        "dataset_path": str(dataset_dir),
        "dataset_hash": meta.get("data_checksum_sha256")
        or dataset_content_hash(dataset_dir, timeframes),
        "from": date_from,
        "to": date_to,
        "strategy": strategy,
        "strategy_name": strategy,
        "simulate_trades": simulate_trades,
        "initial_balance": initial_balance,
        "intrabar_policy": intrabar_policy,
        "spread_policy": spread_policy,
        "fixed_spread_pips": fixed_spread_pips,
        "fill_policy": (
            "NEXT_M1_AVAILABLE"
            if outcome_engine == "m1"
            else fill_policy_used
        ),
        "legacy_fill_policy": "CANDIDATE_ENTRY_AT_SIGNAL_BAR_CLOSE",
        "m1_entry_policy": "NEXT_M1_AVAILABLE" if outcome_engine == "m1" else None,
        "outcome_engine": outcome_engine,
        "m1_intrabar_policy": m1_intrabar_policy if outcome_engine == "m1" else None,
        "m1_rows": len(m1_resolver.candles) if m1_resolver else None,
        "symbol_spec": spec.to_dict() if simulate_trades else None,
        "symbol_spec_path": str(symbol_spec_path) if symbol_spec_path else None,
        "cost_model": cost_model.to_dict() if simulate_trades else None,
        "config_snapshot": _config_snapshot(),
        "config_hash": _stable_hash(_config_snapshot()),
        "start_time": start_wall.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "finish_time": finish_wall.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "m15_closed_bars_processed": m15_processed,
        "decision_count": len(decisions),
        "raw_buy_sell_candidates": raw_buy_sell,
        "candidate_audit_rows": len(candidate_audit),
        "decision_hash": decision_hash,
        "run_hash": run_hash,
        "validation_status": validation.status,
        "issue_count": len(issues),
        "confidence": confidence,
        "confidence_components": confidence_components if simulate_trades else None,
        "costs": cost_model.to_dict()["label"] if simulate_trades else "N/A",
        "gap_summary": {
            "raw": gap_report.get("raw_gap_count"),
            "weekend": gap_report.get("expected_weekend"),
            "broker_breaks": gap_report.get("expected_broker_breaks"),
            "holidays": gap_report.get("expected_holidays"),
            "suspicious": gap_report.get("suspicious_count"),
            "material": gap_report.get("materially_affected_gaps"),
        }
        if gap_report
        else None,
    }
    v2_diag_summary = None
    if v2_diag is not None:
        v2_diag_summary = v2_diag.write_artifacts(
            out_dir,
            getattr(plugin, "stats", {}) or {},
            guard_funnel,
        )
        run_meta["v2_diagnostics"] = True
        run_meta["plugin_stats"] = getattr(plugin, "stats", {})

    with (out_dir / "meta.json").open("w", encoding="utf-8") as f:
        json.dump(run_meta, f, indent=2, sort_keys=True)
        f.write("\n")

    return {
        "run_id": run_id,
        "out_dir": str(out_dir),
        "funnel": funnel,
        "guard_funnel": guard_funnel,
        "performance": performance,
        "run_hash": run_hash,
        "decision_hash": decision_hash,
        "v2_diagnostics": v2_diag_summary,
        "issues": issues,
        "m15_processed": m15_processed,
        "meta": run_meta,
        "candidate_audit_rows": len(candidate_audit),
        "raw_buy_sell": raw_buy_sell,
        "confidence": confidence,
        "confidence_reasons": confidence_reasons,
        "confidence_components": confidence_components if simulate_trades else {},
        "trades": trades_out,
        "gap_report": gap_report,
        "spread_report": spread_report,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Replay strategy plugin (+ optional trade sim). "
        "Default: breakout_retest_v1. Alias: breakout_retest → breakout_retest_v1."
    )
    parser.add_argument("--dataset", required=True)
    parser.add_argument(
        "--strategy",
        default="breakout_retest_v1",
        help="Registered strategy name (alias: breakout_retest → breakout_retest_v1)",
    )
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--max-bars", type=int, default=None)
    parser.add_argument("--simulate-trades", action="store_true")
    parser.add_argument("--initial-balance", type=float, default=None)
    parser.add_argument("--intrabar-policy", default="conservative", choices=["conservative", "unresolved"])
    parser.add_argument(
        "--spread-policy",
        default="historical",
        choices=["historical", "unavailable", "fixed"],
    )
    parser.add_argument("--fixed-spread-pips", type=float, default=None)
    parser.add_argument("--symbol-spec", default=None, help="Path to captured broker_specs JSON")
    parser.add_argument(
        "--fill-policy",
        default=DEFAULT_FILL_POLICY,
        choices=[
            "CANDIDATE_ENTRY_AT_SIGNAL_BAR_CLOSE",
            "SIGNAL_BAR_CLOSE",
            "NEXT_M15_OPEN",
            "CANDIDATE_PRICE",
        ],
    )
    parser.add_argument("--commission-per-lot", type=float, default=0.0)
    parser.add_argument("--slippage-pips", type=float, default=0.0)
    parser.add_argument("--swap-policy", default="none", choices=["none", "zero"])
    parser.add_argument(
        "--outcome-engine",
        default="legacy_m15",
        choices=["legacy_m15", "m1"],
        help="legacy_m15 = Phase H M15 OHLC outcomes; m1 = M1-assisted chronology",
    )
    parser.add_argument(
        "--m1-intrabar-policy",
        default="unresolved",
        choices=["unresolved", "conservative"],
    )
    args = parser.parse_args(argv)

    if not os.getenv("RUN_MODE"):
        os.environ["RUN_MODE"] = "BACKTEST"
    assert_backtest_safe("runner.main")

    result = run_replay(
        Path(args.dataset),
        strategy=args.strategy,
        run_id=args.run_id,
        max_bars=args.max_bars,
        simulate_trades=args.simulate_trades,
        initial_balance=args.initial_balance,
        intrabar_policy=args.intrabar_policy,
        spread_policy=args.spread_policy,
        fixed_spread_pips=args.fixed_spread_pips,
        symbol_spec_path=args.symbol_spec,
        fill_policy=args.fill_policy,
        commission_per_lot=args.commission_per_lot,
        slippage_pips=args.slippage_pips,
        swap_policy=args.swap_policy,
        outcome_engine=args.outcome_engine,
        m1_intrabar_policy=args.m1_intrabar_policy,
    )
    print(format_funnel(result["funnel"]))
    if result.get("guard_funnel"):
        print("\n" + format_guard_funnel(result["guard_funnel"]))
    if result.get("performance"):
        print(
            "\n"
            + format_performance(
                result["performance"],
                confidence=result.get("confidence") or "N/A",
                confidence_reasons=result.get("confidence_reasons") or [],
                components=result.get("confidence_components") or {},
            )
        )
    print(f"\nRun ID: {result['run_id']}")
    print(f"Output: {result['out_dir']}")
    print(f"Run hash: {result['run_hash']}")
    print(f"Issues: {len(result['issues'])}")
    if result.get("raw_buy_sell") is not None:
        print(f"Candidates accounted: {result['candidate_audit_rows']} / {result['raw_buy_sell']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
