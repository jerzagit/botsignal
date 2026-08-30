"""Compare legacy M15 vs M1-assisted run artifacts."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from backtest.m1_resolver import classify_outcome_change
from backtest.performance import write_csv


def compare(legacy_dir: Path, m1_dir: Path, out_dir: Path | None = None) -> dict:
    out_dir = out_dir or m1_dir
    leg_trades = {
        json.loads(l)["candidate_id"]: json.loads(l)
        for l in (legacy_dir / "trades.jsonl").read_text(encoding="utf-8").splitlines()
        if l.strip()
    }
    m1_trades = {
        json.loads(l)["candidate_id"]: json.loads(l)
        for l in (m1_dir / "trades.jsonl").read_text(encoding="utf-8").splitlines()
        if l.strip()
    }
    rows = []
    changes = Counter()
    for cid in sorted(set(leg_trades) | set(m1_trades)):
        a = leg_trades.get(cid, {})
        b = m1_trades.get(cid, {})
        cls = classify_outcome_change(a.get("outcome"), b.get("outcome"))
        if a.get("entry") != b.get("entry") and cls == "NO_CHANGE":
            if (a.get("realized_pnl") or 0) != (b.get("realized_pnl") or 0):
                cls = "PNL_CHANGED_ONLY"
            else:
                cls = "ENTRY_CHANGED_ONLY"
        changes[cls] += 1
        rows.append(
            {
                "trade_id": b.get("trade_id") or a.get("trade_id"),
                "candidate_id": cid,
                "direction": b.get("direction") or a.get("direction"),
                "signal_time": b.get("signal_time") or a.get("signal_time"),
                "legacy_entry": a.get("entry"),
                "m1_entry": b.get("entry"),
                "legacy_exit_time": a.get("exit_time"),
                "m1_exit_time": b.get("exit_time"),
                "legacy_outcome": a.get("outcome"),
                "m1_outcome": b.get("outcome"),
                "legacy_pnl": a.get("realized_pnl"),
                "m1_pnl": b.get("realized_pnl"),
                "outcome_changed": cls != "NO_CHANGE",
                "pnl_difference": round(
                    float(b.get("realized_pnl") or 0) - float(a.get("realized_pnl") or 0), 2
                ),
                "legacy_bars_held": a.get("bars_held"),
                "m1_minutes_held": b.get("bars_held"),
                "resolution_quality": (b.get("meta") or {}).get("fill_quality")
                or (b.get("meta") or {}).get("outcome_engine"),
                "ambiguity_reason": (b.get("meta") or {}).get("m1_resolution", {}).get(
                    "ambiguity_reason", ""
                )
                if isinstance((b.get("meta") or {}).get("m1_resolution"), dict)
                else "",
                "data_quality_warning": b.get("data_quality_warning"),
                "change_class": cls,
            }
        )

    leg_perf = json.loads((legacy_dir / "performance.json").read_text(encoding="utf-8"))
    m1_perf = json.loads((m1_dir / "performance.json").read_text(encoding="utf-8"))
    lp = leg_perf.get("performance") or {}
    mp = m1_perf.get("performance") or {}
    leg_meta = json.loads((legacy_dir / "meta.json").read_text(encoding="utf-8"))
    m1_meta = json.loads((m1_dir / "meta.json").read_text(encoding="utf-8"))

    summary = {
        "legacy_run": str(legacy_dir),
        "m1_run": str(m1_dir),
        "candidates_legacy": leg_meta.get("raw_buy_sell_candidates"),
        "candidates_m1": m1_meta.get("raw_buy_sell_candidates"),
        "would_execute_legacy": (json.loads((legacy_dir / "guard_funnel.json").read_text()) or {}).get(
            "would_execute"
        ),
        "would_execute_m1": (json.loads((m1_dir / "guard_funnel.json").read_text()) or {}).get(
            "would_execute"
        ),
        "change_counts": dict(changes),
        "changed_trades": sum(1 for r in rows if r["outcome_changed"]),
        "total_reconciled": len(rows),
        "changed_pct": round(
            100.0 * sum(1 for r in rows if r["outcome_changed"]) / len(rows), 2
        )
        if rows
        else 0,
        "metrics": {
            "legacy": {
                "net_pnl": lp.get("net_pnl"),
                "profit_factor": lp.get("profit_factor"),
                "max_drawdown_pct": lp.get("max_drawdown_pct"),
                "win_rate_pct": lp.get("win_rate_pct"),
                "wins": lp.get("wins"),
                "losses": lp.get("losses"),
                "ending_balance": lp.get("ending_balance"),
                "open_at_end": lp.get("open_at_end"),
                "ambiguous": lp.get("ambiguous"),
            },
            "m1": {
                "net_pnl": mp.get("net_pnl"),
                "profit_factor": mp.get("profit_factor"),
                "max_drawdown_pct": mp.get("max_drawdown_pct"),
                "win_rate_pct": mp.get("win_rate_pct"),
                "wins": mp.get("wins"),
                "losses": mp.get("losses"),
                "ending_balance": mp.get("ending_balance"),
                "open_at_end": mp.get("open_at_end"),
                "ambiguous": mp.get("ambiguous"),
            },
        },
    }

    write_csv(out_dir / "m1_reconciliation.csv", rows)
    (out_dir / "comparison_legacy_vs_m1.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    lm, mm = summary["metrics"]["legacy"], summary["metrics"]["m1"]
    txt = "\n".join(
        [
            "LEGACY M15 vs M1-ASSISTED",
            f"Candidates:     {summary['candidates_legacy']} vs {summary['candidates_m1']}",
            f"Would execute:  {summary['would_execute_legacy']} vs {summary['would_execute_m1']}",
            f"Wins:           {lm['wins']} vs {mm['wins']}",
            f"Losses:         {lm['losses']} vs {mm['losses']}",
            f"Win rate:       {lm['win_rate_pct']} vs {mm['win_rate_pct']}",
            f"Net P/L:        {lm['net_pnl']} vs {mm['net_pnl']}",
            f"Profit factor:  {lm['profit_factor']} vs {mm['profit_factor']}",
            f"Max DD %:       {lm['max_drawdown_pct']} vs {mm['max_drawdown_pct']}",
            f"Ending balance: {lm['ending_balance']} vs {mm['ending_balance']}",
            f"Changed trades: {summary['changed_trades']} ({summary['changed_pct']}%)",
            "Change classes:",
            *[f"  {k}: {v}" for k, v in sorted(changes.items())],
        ]
    )
    (out_dir / "comparison_legacy_vs_m1.txt").write_text(txt + "\n", encoding="utf-8")
    (out_dir / "m1_summary.json").write_text(
        json.dumps(
            {
                "m1_performance": mp,
                "change_counts": dict(changes),
                "confidence": m1_perf.get("confidence"),
                "confidence_components": m1_perf.get("confidence_components"),
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    print(txt)
    return summary


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--legacy", required=True)
    p.add_argument("--m1", required=True)
    p.add_argument("--out", default=None)
    args = p.parse_args(argv)
    compare(Path(args.legacy), Path(args.m1), Path(args.out) if args.out else None)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
