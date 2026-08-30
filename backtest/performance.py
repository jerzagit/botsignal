"""Performance metrics, monthly breakdown, guard funnel, confidence."""

from __future__ import annotations

import csv
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any


def summarize_guard_qualities(traces: list[dict]) -> dict[str, int]:
    counts: Counter = Counter()
    for tr in traces:
        for g in tr.get("guards", []):
            counts[g.get("quality", "?")] += 1
    return dict(counts)


def build_guard_funnel(candidates: list[dict]) -> dict[str, Any]:
    raw = len(candidates)
    first_blocks: Counter = Counter()
    would = 0
    blocked = 0
    for c in candidates:
        final = c.get("guard_final")
        if final == "WOULD_EXECUTE":
            would += 1
        else:
            blocked += 1
            first_blocks[c.get("first_block_reason") or "unknown"] += 1
    return {
        "raw_candidates": raw,
        "would_execute": would,
        "blocked": blocked,
        "first_blocking_reasons": dict(first_blocks.most_common()),
    }


def format_guard_funnel(gf: dict[str, Any]) -> str:
    lines = [
        f"RAW CANDIDATES                   {gf['raw_candidates']}",
        f"WOULD EXECUTE                    {gf['would_execute']}",
        f"BLOCKED                          {gf['blocked']}",
        "",
        "FIRST BLOCKING REASON",
    ]
    for k, v in gf.get("first_blocking_reasons", {}).items():
        lines.append(f"  {k:28s} {v}")
    return "\n".join(lines)


def build_performance(
    trades: list[dict],
    *,
    initial_balance: float,
    ending_balance: float,
    account_snapshot: dict,
) -> dict[str, Any]:
    closed = [t for t in trades if t.get("outcome") != "OPEN"]
    # include OPEN_AT_END in trades list as closed-with-mark
    resolved = [t for t in trades if t.get("outcome") in ("WIN", "LOSS", "BREAKEVEN")]
    wins = [t for t in resolved if t.get("outcome") == "WIN"]
    losses = [t for t in resolved if t.get("outcome") == "LOSS"]
    be = [t for t in resolved if t.get("outcome") == "BREAKEVEN"]
    amb = [t for t in trades if t.get("outcome") == "AMBIGUOUS"]
    open_end = [t for t in trades if t.get("outcome") == "OPEN_AT_END"]

    gross_profit = sum(t.get("realized_pnl") or 0 for t in wins)
    gross_loss = abs(sum(t.get("realized_pnl") or 0 for t in losses))
    net = sum(t.get("realized_pnl") or 0 for t in trades if t.get("realized_pnl") is not None)
    win_rate = (len(wins) / len(resolved) * 100.0) if resolved else None
    pf = (gross_profit / gross_loss) if gross_loss > 0 else (None if gross_profit == 0 else float("inf"))
    avg_win = (gross_profit / len(wins)) if wins else 0.0
    avg_loss = (gross_loss / len(losses)) if losses else 0.0
    expectancy = (net / len(resolved)) if resolved else None
    r_list = [t["r_multiple"] for t in resolved if t.get("r_multiple") is not None]
    total_r = sum(r_list) if r_list else 0.0
    avg_r = (total_r / len(r_list)) if r_list else None

    # consecutive
    seq = [t.get("outcome") for t in trades if t.get("outcome") in ("WIN", "LOSS")]
    max_cw = max_cl = cur_w = cur_l = 0
    for o in seq:
        if o == "WIN":
            cur_w += 1
            cur_l = 0
            max_cw = max(max_cw, cur_w)
        else:
            cur_l += 1
            cur_w = 0
            max_cl = max(max_cl, cur_l)

    buy = [t for t in trades if (t.get("direction") or "").lower() == "buy"]
    sell = [t for t in trades if (t.get("direction") or "").lower() == "sell"]

    def side_stats(rows: list[dict]) -> dict:
        r = [t for t in rows if t.get("outcome") in ("WIN", "LOSS", "BREAKEVEN")]
        w = [t for t in r if t.get("outcome") == "WIN"]
        l = [t for t in r if t.get("outcome") == "LOSS"]
        return {
            "trades": len(rows),
            "wins": len(w),
            "losses": len(l),
            "pnl": round(sum(t.get("realized_pnl") or 0 for t in rows), 2),
        }

    holds = [t.get("bars_held") or 0 for t in trades]
    ret_pct = (
        100.0 * (ending_balance - initial_balance) / initial_balance if initial_balance else None
    )

    return {
        "initial_balance": initial_balance,
        "ending_balance": round(ending_balance, 2),
        "net_pnl": round(net, 2),
        "return_pct": round(ret_pct, 4) if ret_pct is not None else None,
        "trades": len(trades),
        "resolved_trades": len(resolved),
        "wins": len(wins),
        "losses": len(losses),
        "breakeven": len(be),
        "ambiguous": len(amb),
        "open_at_end": len(open_end),
        "win_rate_pct": round(win_rate, 2) if win_rate is not None else None,
        "win_rate_note": "denominator = WIN+LOSS+BREAKEVEN only; AMBIGUOUS excluded",
        "gross_profit": round(gross_profit, 2),
        "gross_loss": round(gross_loss, 2),
        "profit_factor": (round(pf, 4) if isinstance(pf, float) and pf != float("inf") else pf),
        "expectancy_per_trade": round(expectancy, 4) if expectancy is not None else None,
        "average_win": round(avg_win, 2),
        "average_loss": round(avg_loss, 2),
        "average_r": round(avg_r, 4) if avg_r is not None else None,
        "total_r": round(total_r, 4),
        "max_consecutive_wins": max_cw,
        "max_consecutive_losses": max_cl,
        "max_drawdown_usd": account_snapshot.get("max_drawdown_usd"),
        "max_drawdown_pct": account_snapshot.get("max_drawdown_pct"),
        "average_bars_held": round(sum(holds) / len(holds), 2) if holds else 0,
        "buy": side_stats(buy),
        "sell": side_stats(sell),
        "costs": {
            "commission": 0.0,
            "swap": 0.0,
            "slippage": 0.0,
            "label": "NOT MODELLED",
        },
    }


def format_performance(
    p: dict[str, Any],
    *,
    confidence: str,
    confidence_reasons: list[str],
    components: dict[str, str] | None = None,
) -> str:
    lines = [
        "PERFORMANCE BASELINE",
        f"Initial balance: {p['initial_balance']}",
        f"Ending balance:  {p['ending_balance']}",
        f"Net P/L:         {p['net_pnl']}",
        f"Return %:        {p['return_pct']}",
        "",
        f"Trades: {p['trades']}  W:{p['wins']} L:{p['losses']} BE:{p['breakeven']} "
        f"AMB:{p['ambiguous']} OPEN_END:{p['open_at_end']}",
        f"Win rate: {p['win_rate_pct']}%  ({p['win_rate_note']})",
        f"Profit factor: {p['profit_factor']}",
        f"Expectancy: {p['expectancy_per_trade']}",
        f"Total R: {p['total_r']}  Avg R: {p['average_r']}",
        f"Max DD: ${p['max_drawdown_usd']} ({p['max_drawdown_pct']}%)",
        "",
        f"BUY:  {p['buy']}",
        f"SELL: {p['sell']}",
        "",
        f"Costs: {p['costs']['label']}",
        "",
        f"BACKTEST CONFIDENCE: {confidence}",
    ]
    if components:
        lines.append("Components:")
        for k, v in components.items():
            lines.append(f"  {k}: {v}")
    for r in confidence_reasons:
        lines.append(f"  - {r}")
    return "\n".join(lines)


def monthly_breakdown(trades: list[dict]) -> list[dict]:
    buckets: dict[str, list[dict]] = defaultdict(list)
    for t in trades:
        ts = t.get("exit_time") or t.get("entry_time") or ""
        month = ts[:7] if len(ts) >= 7 else "unknown"
        buckets[month].append(t)
    rows = []
    for month in sorted(buckets):
        items = buckets[month]
        resolved = [x for x in items if x.get("outcome") in ("WIN", "LOSS", "BREAKEVEN")]
        w = sum(1 for x in resolved if x.get("outcome") == "WIN")
        l = sum(1 for x in resolved if x.get("outcome") == "LOSS")
        pnl = sum(x.get("realized_pnl") or 0 for x in items)
        gp = sum(x.get("realized_pnl") or 0 for x in items if (x.get("realized_pnl") or 0) > 0)
        gl = abs(sum(x.get("realized_pnl") or 0 for x in items if (x.get("realized_pnl") or 0) < 0))
        pf = (gp / gl) if gl > 0 else None
        wr = (100.0 * w / len(resolved)) if resolved else None
        rows.append(
            {
                "month": month,
                "trades": len(items),
                "wins": w,
                "losses": l,
                "win_pct": round(wr, 2) if wr is not None else None,
                "pnl": round(pnl, 2),
                "profit_factor": round(pf, 4) if pf is not None else None,
            }
        )
    return rows


def write_csv(path: Path, rows: list[dict], fieldnames: list[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields = fieldnames or list(rows[0].keys())
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)


def assess_confidence(
    *,
    validation_status: str,
    ambiguous: int,
    spread_policy: str,
    fill_policy: str,
    suspicious_gaps: int,
    material_gaps: int = 0,
    symbol_spec_quality: str = "ASSUMPTION",
    spread_conversion_validated: bool = False,
    costs_modelled: bool = False,
    lot_validated: str | None = None,
    outcome_engine: str = "legacy_m15",
    m1_coverage_partial: bool = False,
) -> tuple[str, list[str], dict[str, str]]:
    """
    Returns (overall, reasons, components).
    Components use HIGH/MEDIUM/LOW independently of strategy profitability.
    """
    components: dict[str, str] = {
        "strategy_decision": "HIGH",
        "trend_data": "HIGH",
        "dataset_continuity": "HIGH",
        "spread": "MEDIUM",
        "lot_sizing": "MEDIUM",
        "margin": "MEDIUM",
        "entry_fill": "MEDIUM",
        "intrabar_outcome": "MEDIUM",
        "m1_execution_chronology": "LOW",
        "costs": "LOW",
    }
    reasons: list[str] = []

    if symbol_spec_quality == "EXACT_BROKER_METADATA":
        components["lot_sizing"] = "HIGH"
        components["margin"] = "HIGH" if lot_validated != "MISMATCH" else "MEDIUM"
        reasons.append("broker SymbolSpec EXACT_BROKER_METADATA")
    else:
        components["lot_sizing"] = "MEDIUM"
        components["margin"] = "MEDIUM"
        reasons.append("SymbolSpec still ASSUMPTION")

    if spread_policy == "historical" and spread_conversion_validated:
        components["spread"] = "HIGH"
        reasons.append("historical spread points×point validated against broker point")
    elif spread_policy == "historical":
        components["spread"] = "MEDIUM"
        reasons.append("historical spread used; point size not broker-exact")
    elif spread_policy == "unavailable":
        components["spread"] = "LOW"
        reasons.append("spread guard unavailable")
    else:
        components["spread"] = "LOW"
        reasons.append("fixed spread assumption")

    if suspicious_gaps == 0 and validation_status in ("VALID", "VALID_WITH_GAPS"):
        components["dataset_continuity"] = "HIGH" if material_gaps == 0 else "MEDIUM"
    elif material_gaps > 0:
        components["dataset_continuity"] = "MEDIUM" if material_gaps < 5 else "LOW"
        reasons.append(f"{material_gaps} suspicious gaps with material trade/candidate impact")
    else:
        components["dataset_continuity"] = "MEDIUM"
        reasons.append(f"dataset {validation_status}; suspicious={suspicious_gaps} (reclassified)")

    if outcome_engine == "m1":
        components["entry_fill"] = "MEDIUM"
        components["intrabar_outcome"] = "MEDIUM" if ambiguous == 0 else "LOW"
        components["m1_execution_chronology"] = "MEDIUM" if not m1_coverage_partial else "MEDIUM"
        reasons.append("M1-assisted chronology (M1_APPROXIMATED; not exact tick tape)")
        if m1_coverage_partial:
            reasons.append("M1 history does not cover full dataset — early trades used legacy M15 fallback")
        if ambiguous:
            reasons.append(f"{ambiguous} ambiguous/unresolved M1 outcomes")
    else:
        components["entry_fill"] = "MEDIUM"
        components["m1_execution_chronology"] = "LOW"
        reasons.append(f"fill policy {fill_policy} remains OHLC-approximated (no tick tape)")
        if ambiguous == 0:
            components["intrabar_outcome"] = "MEDIUM"
            reasons.append("no ambiguous SL/TP same-bar events under conservative policy")
        else:
            components["intrabar_outcome"] = "LOW" if ambiguous > 10 else "MEDIUM"
            reasons.append(f"{ambiguous} ambiguous intrabar outcomes")

    if costs_modelled:
        components["costs"] = "MEDIUM"
        reasons.append("cost scenario applied (still an assumption unless broker fee schedule captured)")
    else:
        components["costs"] = "LOW"
        reasons.append("commission/swap/slippage NOT MODELLED in RAW baseline")

    rank = {"HIGH": 3, "MEDIUM": 2, "LOW": 1}
    critical = {k: v for k, v in components.items() if k != "costs"}
    overall_rank = min(rank[v] for v in critical.values())
    overall = {3: "HIGH", 2: "MEDIUM", 1: "LOW"}[overall_rank]
    if overall == "HIGH":
        overall = "MEDIUM"
        reasons.append("overall capped at MEDIUM without exact tick fill reconstruction")
    return overall, reasons, components
