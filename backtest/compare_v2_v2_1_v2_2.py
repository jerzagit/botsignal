"""Three-way comparison: structure_pullback_v2 / v2_1 / v2_2."""

from __future__ import annotations

import json
import statistics
from pathlib import Path
from typing import Any


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def _safe(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _rrs(cands: list[dict]) -> list[float]:
    return [float(c["rr"]) for c in cands if c.get("rr") is not None]


def _rr_summary(cands: list[dict]) -> dict[str, Any]:
    rrs = _rrs(cands)
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
    return {
        "count": len(cands),
        "min": min(rrs) if rrs else None,
        "median": statistics.median(rrs) if rrs else None,
        "mean": round(statistics.mean(rrs), 4) if rrs else None,
        "max": max(rrs) if rrs else None,
        "bands": bands,
        "rr_ge_1_4": sum(1 for r in rrs if r >= 1.4),
    }


def _latency_bars(retests: dict[str, dict], cands: list[dict]) -> list[int]:
    out = []
    for c in cands:
        zid = c.get("zone_id")
        r = retests.get(zid) or {}
        b = r.get("bars_to_confirmation")
        if b is None and r.get("zone_retest_at") and r.get("m15_confirmation_ts"):
            # approximate from timestamps / 900
            b = int((int(r["m15_confirmation_ts"]) - int(r["zone_retest_at"])) / 900)
        if b is not None:
            out.append(int(b))
        elif c.get("timestamp") and r.get("zone_retest_at"):
            # fallback skip
            pass
    return out


def _lat_dist(bars: list[int]) -> dict[str, int]:
    d = {"0-4": 0, "5-8": 0, "9-16": 0, "17-32": 0, "33-48": 0, "49+": 0}
    for b in bars:
        if b <= 4:
            d["0-4"] += 1
        elif b <= 8:
            d["5-8"] += 1
        elif b <= 16:
            d["9-16"] += 1
        elif b <= 32:
            d["17-32"] += 1
        elif b <= 48:
            d["33-48"] += 1
        else:
            d["49+"] += 1
    return d


def compare_three(v2_dir: Path, v21_dir: Path, v22_dir: Path, out_dir: Path | None = None) -> dict[str, Any]:
    v2_dir, v21_dir, v22_dir = Path(v2_dir), Path(v21_dir), Path(v22_dir)
    out_dir = Path(out_dir) if out_dir else v22_dir

    def pack(d: Path):
        re = {r["zone_id"]: r for r in _load_jsonl(d / "v2_retest_audit.jsonl")}
        cands = (_safe(d / "v2_candidate_geometry.json") or {}).get("candidates") or []
        meta = _safe(d / "meta.json")
        gf = _safe(d / "guard_funnel.json")
        perf = (_safe(d / "performance.json") or {}).get("performance") or {}
        stats = meta.get("plugin_stats") or {}
        return re, cands, meta, gf, perf, stats

    r2, c2, m2, g2, p2, s2 = pack(v2_dir)
    r21, c21, m21, g21, p21, s21 = pack(v21_dir)
    r22, c22, m22, g22, p22, s22 = pack(v22_dir)

    zone_ids = sorted(set(r2) | set(r21) | set(r22))
    rows = []
    for zid in zone_ids:
        a, b, c = r2.get(zid, {}), r21.get(zid, {}), r22.get(zid, {})
        cand22 = next((x for x in c22 if x.get("zone_id") == zid), None)
        cand21 = next((x for x in c21 if x.get("zone_id") == zid), None)
        cand2 = next((x for x in c2 if x.get("zone_id") == zid), None)
        if cand22 and cand2:
            attr = "ALREADY_V2_CANDIDATE"
        elif cand22 and cand21:
            attr = "ALREADY_V2_1_CANDIDATE"
        elif cand22:
            attr = "NEW_DUE_TO_LOCAL_STRUCTURE"
        else:
            attr = None
        ls = (c.get("local_structure") if isinstance(c.get("local_structure"), dict) else None) or {}
        # also from candidate metadata
        if cand22:
            ls = cand22.get("local_structure") or ls
        rows.append(
            {
                "zone_id": zid,
                "v2_reason": a.get("primary_reason"),
                "v2_1_reason": b.get("primary_reason"),
                "v2_2_reason": c.get("primary_reason"),
                "v2_2_reaction_pivot": ls.get("reaction_price") or c.get("reaction_pivot_price"),
                "v2_2_trigger_pivot": ls.get("trigger_price") or c.get("local_trigger_price") or (cand22 or {}).get("m15_trigger_swing"),
                "v2_2_confirmation": (cand22 or {}).get("timestamp") or c.get("m15_confirmation_ts"),
                "v2_2_entry": (cand22 or {}).get("entry"),
                "v2_2_sl": (cand22 or {}).get("sl"),
                "v2_2_tp": (cand22 or {}).get("tp"),
                "v2_2_rr": (cand22 or {}).get("rr"),
                "v2_2_guard": (cand22 or {}).get("guard_result") or (cand22 or {}).get("first_block_reason"),
                "v2_1_rr": (cand21 or {}).get("rr"),
                "v2_rr": (cand2 or {}).get("rr"),
                "attribution": attr,
            }
        )

    lat2 = _latency_bars(r2, c2)
    lat21 = _latency_bars(r21, c21)
    lat22 = _latency_bars(r22, c22)

    summary = {
        "first_retests": {"v2": len(r2), "v2_1": len(r21), "v2_2": len(r22)},
        "m15_confirmations": {
            "v2": int(s2.get("m15_confirmations") or len(c2)),
            "v2_1": int(s21.get("m15_confirmations") or len(c21)),
            "v2_2": int(s22.get("m15_confirmations") or len(c22)),
        },
        "candidates": {"v2": len(c2), "v2_1": len(c21), "v2_2": len(c22)},
        "rr_ge_1_4": {
            "v2": _rr_summary(c2)["rr_ge_1_4"],
            "v2_1": _rr_summary(c21)["rr_ge_1_4"],
            "v2_2": _rr_summary(c22)["rr_ge_1_4"],
        },
        "would_execute": {
            "v2": int(g2.get("would_execute") or 0),
            "v2_1": int(g21.get("would_execute") or 0),
            "v2_2": int(g22.get("would_execute") or 0),
        },
        "trades": {"v2": p2.get("trades", 0), "v2_1": p21.get("trades", 0), "v2_2": p22.get("trades", 0)},
        "wins": {"v2": p2.get("wins", 0), "v2_1": p21.get("wins", 0), "v2_2": p22.get("wins", 0)},
        "losses": {"v2": p2.get("losses", 0), "v2_1": p21.get("losses", 0), "v2_2": p22.get("losses", 0)},
        "net_pnl": {"v2": p2.get("net_pnl", 0), "v2_1": p21.get("net_pnl", 0), "v2_2": p22.get("net_pnl", 0)},
        "profit_factor": {"v2": p2.get("profit_factor"), "v2_1": p21.get("profit_factor"), "v2_2": p22.get("profit_factor")},
        "max_drawdown_pct": {
            "v2": p2.get("max_drawdown_pct"),
            "v2_1": p21.get("max_drawdown_pct"),
            "v2_2": p22.get("max_drawdown_pct"),
        },
        "median_rr": {
            "v2": _rr_summary(c2)["median"],
            "v2_1": _rr_summary(c21)["median"],
            "v2_2": _rr_summary(c22)["median"],
        },
        "median_latency_bars": {
            "v2": statistics.median(lat2) if lat2 else None,
            "v2_1": statistics.median(lat21) if lat21 else None,
            "v2_2": statistics.median(lat22) if lat22 else None,
        },
    }

    v22_only = [x for x in c22 if x.get("zone_id") not in {c.get("zone_id") for c in c21}]
    payload = {
        "runs": {"v2": v2_dir.name, "v2_1": v21_dir.name, "v2_2": v22_dir.name},
        "summary": summary,
        "rr_distribution": {"v2": _rr_summary(c2), "v2_1": _rr_summary(c21), "v2_2": _rr_summary(c22)},
        "latency": {
            "v2": {"bars": lat2, "distribution": _lat_dist(lat2)},
            "v2_1": {"bars": lat21, "distribution": _lat_dist(lat21)},
            "v2_2": {"bars": lat22, "distribution": _lat_dist(lat22)},
        },
        "zone_rows": rows,
        "attribution": {
            "additional_confirmations_vs_v2_1": max(
                0, summary["m15_confirmations"]["v2_2"] - summary["m15_confirmations"]["v2_1"]
            ),
            "additional_candidates_vs_v2_1": max(0, len(c22) - len(c21)),
            "additional_rr_ge_1_4_vs_v2_1": max(
                0, summary["rr_ge_1_4"]["v2_2"] - summary["rr_ge_1_4"]["v2_1"]
            ),
            "additional_would_execute_vs_v2_1": max(
                0, summary["would_execute"]["v2_2"] - summary["would_execute"]["v2_1"]
            ),
            "v2_2_only_zones": [c.get("zone_id") for c in v22_only],
            "plugin_stats_v2_2": s22,
        },
        "NOTE": "V2.2 differs from V2.1 only by POST_RETEST_LOCAL_STRUCTURE M15 trigger reference.",
    }

    (out_dir / "v2_v2_1_v2_2_retest_comparison.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    lines = [
        "V2 / V2.1 / V2.2 RETEST COMPARISON",
        f"Runs: {v2_dir.name} | {v21_dir.name} | {v22_dir.name}",
        "",
        f"{'Metric':22} {'V2':>8} {'V2.1':>8} {'V2.2':>8}",
        f"{'First retests':22} {summary['first_retests']['v2']:>8} {summary['first_retests']['v2_1']:>8} {summary['first_retests']['v2_2']:>8}",
        f"{'M15 confirmations':22} {summary['m15_confirmations']['v2']:>8} {summary['m15_confirmations']['v2_1']:>8} {summary['m15_confirmations']['v2_2']:>8}",
        f"{'Candidates':22} {summary['candidates']['v2']:>8} {summary['candidates']['v2_1']:>8} {summary['candidates']['v2_2']:>8}",
        f"{'RR >= 1.4':22} {summary['rr_ge_1_4']['v2']:>8} {summary['rr_ge_1_4']['v2_1']:>8} {summary['rr_ge_1_4']['v2_2']:>8}",
        f"{'Would execute':22} {summary['would_execute']['v2']:>8} {summary['would_execute']['v2_1']:>8} {summary['would_execute']['v2_2']:>8}",
        f"{'Trades':22} {summary['trades']['v2']:>8} {summary['trades']['v2_1']:>8} {summary['trades']['v2_2']:>8}",
        f"{'Median RR':22} {str(summary['median_rr']['v2']):>8} {str(summary['median_rr']['v2_1']):>8} {str(summary['median_rr']['v2_2']):>8}",
        f"{'Median latency':22} {str(summary['median_latency_bars']['v2']):>8} {str(summary['median_latency_bars']['v2_1']):>8} {str(summary['median_latency_bars']['v2_2']):>8}",
        "",
        "PER ZONE",
    ]
    for r in rows:
        lines.append(
            f"  {r['zone_id']}: V2={r['v2_reason']} | V2.1={r['v2_1_reason']} | V2.2={r['v2_2_reason']} "
            f"rr={r['v2_2_rr']} attr={r['attribution']}"
        )
    (out_dir / "v2_v2_1_v2_2_retest_comparison.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")

    (out_dir / "strategy_comparison_v2_v2_1_v2_2.json").write_text(
        json.dumps({"summary": summary, "attribution": payload["attribution"]}, indent=2, sort_keys=True)
        + "\n",
        encoding="utf-8",
    )
    (out_dir / "strategy_comparison_v2_v2_1_v2_2.txt").write_text("\n".join(lines[:14]) + "\n", encoding="utf-8")
    return payload


def main() -> int:
    import argparse

    p = argparse.ArgumentParser()
    p.add_argument("--v2", required=True)
    p.add_argument("--v2-1", required=True)
    p.add_argument("--v2-2", required=True)
    p.add_argument("--out", default=None)
    a = p.parse_args()
    compare_three(Path(a.v2), Path(a.v2_1), Path(a.v2_2), Path(a.out) if a.out else None)
    print("Wrote three-way comparison")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
