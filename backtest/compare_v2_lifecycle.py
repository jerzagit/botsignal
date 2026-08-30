"""Compare structure_pullback_v2 vs v2_1 lifecycle outcomes (diagnostic only)."""

from __future__ import annotations

import json
import statistics
from pathlib import Path
from typing import Any


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def _safe_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _rr_bands(rrs: list[float]) -> dict[str, int]:
    bands = {"<0.5": 0, "0.5-1.0": 0, "1.0-1.4": 0, "1.4-2.0": 0, "2.0-3.0": 0, ">3.0": 0}
    for r in rrs:
        if r < 0.5:
            bands["<0.5"] += 1
        elif r < 1.0:
            bands["0.5-1.0"] += 1
        elif r < 1.4:
            bands["1.0-1.4"] += 1
        elif r < 2.0:
            bands["1.4-2.0"] += 1
        elif r <= 3.0:
            bands["2.0-3.0"] += 1
        else:
            bands[">3.0"] += 1
    return bands


def compare_lifecycle_runs(v2_dir: Path, v21_dir: Path, out_dir: Path | None = None) -> dict[str, Any]:
    """Per-zone first-retest comparison + strategy summary. Writes into v21_dir by default."""
    v2_dir = Path(v2_dir)
    v21_dir = Path(v21_dir)
    out_dir = Path(out_dir) if out_dir else v21_dir

    v2_re = {r["zone_id"]: r for r in _load_jsonl(v2_dir / "v2_retest_audit.jsonl")}
    v21_re = {r["zone_id"]: r for r in _load_jsonl(v21_dir / "v2_retest_audit.jsonl")}
    v2_cand = (_safe_json(v2_dir / "v2_candidate_geometry.json") or {}).get("candidates") or []
    v21_cand = (_safe_json(v21_dir / "v2_candidate_geometry.json") or {}).get("candidates") or []
    v2_meta = _safe_json(v2_dir / "meta.json")
    v21_meta = _safe_json(v21_dir / "meta.json")
    v2_gf = _safe_json(v2_dir / "guard_funnel.json")
    v21_gf = _safe_json(v21_dir / "guard_funnel.json")
    v2_perf = (_safe_json(v2_dir / "performance.json") or {}).get("performance") or {}
    v21_perf = (_safe_json(v21_dir / "performance.json") or {}).get("performance") or {}
    v2_sum = _safe_json(v2_dir / "v2_diagnostic_summary.json")
    v21_sum = _safe_json(v21_dir / "v2_diagnostic_summary.json")
    v2_stats = v2_meta.get("plugin_stats") or {}
    v21_stats = v21_meta.get("plugin_stats") or {}

    zone_ids = sorted(set(v2_re) | set(v21_re))
    rows = []
    for zid in zone_ids:
        a, b = v2_re.get(zid), v21_re.get(zid)
        cand_b = next((c for c in v21_cand if c.get("zone_id") == zid), None)
        cand_a = next((c for c in v2_cand if c.get("zone_id") == zid), None)
        rows.append(
            {
                "zone_id": zid,
                "v2_final_result": (a or {}).get("final_result"),
                "v2_primary_reason": (a or {}).get("primary_reason"),
                "v2_1_final_result": (b or {}).get("final_result"),
                "v2_1_primary_reason": (b or {}).get("primary_reason"),
                "v2_1_left_zone": bool((b or {}).get("left_zone_before_confirmation") or (b or {}).get("left_zone_after_retest")),
                "v2_1_bars_to_confirmation": (b or {}).get("bars_to_confirmation"),
                "v2_1_m15_confirmation_ts": (b or {}).get("m15_confirmation_ts"),
                "v2_candidate": bool(cand_a),
                "v2_1_candidate": bool(cand_b),
                "v2_1_rr": (cand_b or {}).get("rr"),
                "v2_1_entry": (cand_b or {}).get("entry"),
                "v2_1_sl": (cand_b or {}).get("sl"),
                "v2_1_tp": (cand_b or {}).get("tp"),
                "v2_1_guard": (cand_b or {}).get("guard_result") or (cand_b or {}).get("first_block_reason"),
                "changed": ((a or {}).get("primary_reason") != (b or {}).get("primary_reason")),
            }
        )

    # Key six: left-zone + timeout under V2
    key6 = [
        r
        for r in rows
        if (r["v2_primary_reason"] or "")
        in ("LEFT_ZONE_WITHOUT_CONFIRMATION", "M15_CONFIRMATION_TIMEOUT")
    ]

    inv12 = [r for r in rows if r["v2_primary_reason"] == "ZONE_INVALIDATED"]
    inv_still = sum(1 for r in inv12 if r["v2_1_primary_reason"] == "ZONE_INVALIDATED")
    inv_changed = [r for r in inv12 if r["v2_1_primary_reason"] != "ZONE_INVALIDATED"]

    rrs = [float(c["rr"]) for c in v21_cand if c.get("rr") is not None]
    rr_dist = {
        "candidate_count": len(v21_cand),
        "min": min(rrs) if rrs else None,
        "median": statistics.median(rrs) if rrs else None,
        "mean": round(statistics.mean(rrs), 4) if rrs else None,
        "max": max(rrs) if rrs else None,
        "bands": _rr_bands(rrs),
        "rr_ge_1_4": sum(1 for r in rrs if r >= 1.4),
    }

    v2_conf = int(v2_stats.get("m15_confirmations") or len(v2_cand))
    v21_conf = int(v21_stats.get("m15_confirmations") or len(v21_cand))
    v2_we = int(v2_gf.get("would_execute") or 0)
    v21_we = int(v21_gf.get("would_execute") or 0)

    # Attribution: confirmations/candidates present in V2.1 but not V2 for same zone
    v2_conf_zones = {c["zone_id"] for c in v2_cand}
    v21_only_cands = [c for c in v21_cand if c.get("zone_id") not in v2_conf_zones]
    # Also zones that confirmed under V2.1 where V2 reason was left-zone / timeout
    lifecycle_attributed = [
        r
        for r in rows
        if r["v2_1_candidate"]
        and not r["v2_candidate"]
        and r["v2_primary_reason"]
        in ("LEFT_ZONE_WITHOUT_CONFIRMATION", "M15_CONFIRMATION_TIMEOUT", "ZONE_INVALIDATED", "OTHER", "NO_M15_TRIGGER_SWING", "M15_SHIFT_NOT_CONFIRMED", "M15_WICK_BREAK_ONLY")
    ]

    comparison = {
        "v2_run": v2_dir.name,
        "v2_1_run": v21_dir.name,
        "zone_rows": rows,
        "key_six": key6,
        "invalidated_12": {
            "count": len(inv12),
            "remain_invalidated": inv_still,
            "changed_unexpectedly": inv_changed,
        },
        "rr_distribution": rr_dist,
        "summary": {
            "first_retests_v2": len(v2_re),
            "first_retests_v2_1": len(v21_re),
            "m15_confirmations": {"v2": v2_conf, "v2_1": v21_conf},
            "candidates": {"v2": len(v2_cand), "v2_1": len(v21_cand)},
            "would_execute": {"v2": v2_we, "v2_1": v21_we},
            "trades": {"v2": v2_perf.get("trades", 0), "v2_1": v21_perf.get("trades", 0)},
            "wins": {"v2": v2_perf.get("wins", 0), "v2_1": v21_perf.get("wins", 0)},
            "losses": {"v2": v2_perf.get("losses", 0), "v2_1": v21_perf.get("losses", 0)},
            "win_rate_pct": {"v2": v2_perf.get("win_rate_pct"), "v2_1": v21_perf.get("win_rate_pct")},
            "net_pnl": {"v2": v2_perf.get("net_pnl", 0), "v2_1": v21_perf.get("net_pnl", 0)},
            "profit_factor": {"v2": v2_perf.get("profit_factor"), "v2_1": v21_perf.get("profit_factor")},
            "expectancy": {"v2": v2_perf.get("expectancy"), "v2_1": v21_perf.get("expectancy")},
            "total_r": {"v2": v2_perf.get("total_r"), "v2_1": v21_perf.get("total_r")},
            "max_drawdown_pct": {
                "v2": v2_perf.get("max_drawdown_pct"),
                "v2_1": v21_perf.get("max_drawdown_pct"),
            },
        },
        "attribution": {
            "additional_confirmations": max(0, v21_conf - v2_conf),
            "additional_candidates": max(0, len(v21_cand) - len(v2_cand)),
            "additional_would_execute": max(0, v21_we - v2_we),
            "v2_1_only_candidate_zones": [c.get("zone_id") for c in v21_only_cands],
            "lifecycle_attributed_rows": lifecycle_attributed,
            "left_zone_continued_waiting": v21_stats.get("left_zone_continued_waiting"),
            "NOTE": "Additional outcomes attributed when V2.1 produces candidates V2 did not, with identical zone detection rules.",
        },
        "plugin_stats": {"v2": v2_stats, "v2_1": v21_stats},
        "v2_diagnostic_outcomes": (v2_sum or {}).get("first_retest_outcomes"),
        "v2_1_diagnostic_outcomes": (v21_sum or {}).get("first_retest_outcomes"),
    }

    (out_dir / "v2_vs_v2_1_lifecycle_comparison.json").write_text(
        json.dumps(comparison, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    lines = [
        "V2 vs V2.1 LIFECYCLE COMPARISON",
        f"V2 run:   {v2_dir.name}",
        f"V2.1 run: {v21_dir.name}",
        "",
        f"First retests: {len(v2_re)} vs {len(v21_re)}",
        f"M15 confirmations: {v2_conf} vs {v21_conf}",
        f"Candidates: {len(v2_cand)} vs {len(v21_cand)}",
        f"Would execute: {v2_we} vs {v21_we}",
        f"Net P/L: {v2_perf.get('net_pnl', 0)} vs {v21_perf.get('net_pnl', 0)}",
        "",
        "PER-ZONE",
    ]
    for r in rows:
        lines.append(
            f"  {r['zone_id']}: V2={r['v2_primary_reason']} → V2.1={r['v2_1_primary_reason']}"
            f" cand={r['v2_1_candidate']} rr={r['v2_1_rr']}"
        )
    lines += ["", "KEY SIX (V2 left-zone / timeout)"]
    for r in key6:
        lines.append(
            f"  {r['zone_id']}: {r['v2_primary_reason']} → {r['v2_1_primary_reason']} "
            f"cand={r['v2_1_candidate']} rr={r['v2_1_rr']} guard={r['v2_1_guard']}"
        )
    lines += [
        "",
        f"Invalidated-12 remain invalidated: {inv_still}/{len(inv12)}",
        f"Invalidated changed unexpectedly: {len(inv_changed)}",
        "",
        "RR DISTRIBUTION (V2.1)",
        json.dumps(rr_dist, indent=2),
        "",
        "ATTRIBUTION",
        json.dumps(comparison["attribution"], indent=2),
        "",
    ]
    (out_dir / "v2_vs_v2_1_lifecycle_comparison.txt").write_text("\n".join(lines), encoding="utf-8")

    # Strategy comparison artifact
    strat = {
        "summary": comparison["summary"],
        "attribution": comparison["attribution"],
        "rr_distribution_v2_1": rr_dist,
    }
    (out_dir / "strategy_comparison_v2_vs_v2_1.json").write_text(
        json.dumps(strat, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    s = comparison["summary"]
    (out_dir / "strategy_comparison_v2_vs_v2_1.txt").write_text(
        "\n".join(
            [
                "STRATEGY COMPARISON V2 vs V2.1",
                f"M15 confirmations: {s['m15_confirmations']['v2']} vs {s['m15_confirmations']['v2_1']}",
                f"Candidates: {s['candidates']['v2']} vs {s['candidates']['v2_1']}",
                f"Would execute: {s['would_execute']['v2']} vs {s['would_execute']['v2_1']}",
                f"Trades: {s['trades']['v2']} vs {s['trades']['v2_1']}",
                f"Wins/Losses: {s['wins']['v2']}/{s['losses']['v2']} vs {s['wins']['v2_1']}/{s['losses']['v2_1']}",
                f"Net P/L: {s['net_pnl']['v2']} vs {s['net_pnl']['v2_1']}",
                f"PF: {s['profit_factor']['v2']} vs {s['profit_factor']['v2_1']}",
                f"Max DD%: {s['max_drawdown_pct']['v2']} vs {s['max_drawdown_pct']['v2_1']}",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return comparison


def main() -> int:
    import argparse

    p = argparse.ArgumentParser()
    p.add_argument("--v2", required=True)
    p.add_argument("--v2-1", required=True)
    p.add_argument("--out", default=None)
    args = p.parse_args()
    compare_lifecycle_runs(Path(args.v2), Path(args.v2_1), Path(args.out) if args.out else None)
    print("Wrote comparison artifacts")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
