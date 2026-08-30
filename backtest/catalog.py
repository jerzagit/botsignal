"""Lightweight backtest run catalog — filesystem is source of truth."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def default_runs_root() -> Path:
    return Path(__file__).resolve().parents[1] / "data" / "backtests"


def _safe_load(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def list_run_dirs(runs_root: Path | None = None) -> list[Path]:
    root = runs_root or default_runs_root()
    if not root.is_dir():
        return []
    skip = {"datasets", "broker_specs"}
    out = []
    for p in sorted(root.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True):
        if p.is_dir() and p.name not in skip and (p / "meta.json").is_file():
            out.append(p)
    return out


def _infer_run_type(run_dir: Path, meta: dict[str, Any]) -> str:
    if meta.get("geometry_diagnostic") or meta.get("run_type") == "v2_geometry_diagnostic":
        return "geometry_diagnostic"
    if meta.get("v2_diagnostics"):
        return "diagnostic"
    rid = run_dir.name.upper()
    if "REGRESS" in rid:
        return "regression"
    return "strategy_backtest"


def _funnel_counts(run_dir: Path, meta: dict[str, Any], guards: dict[str, Any]) -> dict[str, Any]:
    stats = meta.get("plugin_stats") or {}
    v2_funnel = _safe_load(run_dir / "v2_funnel.json") or {}
    stages = {s.get("stage"): s for s in (v2_funnel.get("stages") or []) if isinstance(s, dict)}
    def stage_pass(name: str) -> Any:
        s = stages.get(name)
        return s.get("pass") if s else None

    candidates = guards.get("candidates") or stage_pass("candidates") or stats.get("candidates")
    rr_pass = guards.get("rr_pass") or stage_pass("rr_ge_min")
    if rr_pass is None and guards.get("would_execute") is not None and candidates:
        # approximate from guard funnel blocks
        rr_pass = None

    return {
        "valid_zones": stats.get("valid_zones") or stage_pass("valid_zones"),
        "first_retests": stats.get("first_retests") or stage_pass("first_retests"),
        "reaction_pivots": stats.get("local_reaction_pivots"),
        "local_trigger_pivots": stats.get("local_trigger_pivots"),
        "confirmations": stats.get("m15_confirmations") or stage_pass("m15_confirmations"),
        "candidates": candidates,
        "rr_pass": rr_pass,
        "would_execute": guards.get("would_execute"),
    }


def summarize_run(run_dir: Path) -> dict[str, Any]:
    meta = _safe_load(run_dir / "meta.json") or {}
    perf_doc = _safe_load(run_dir / "performance.json") or {}
    perf = perf_doc.get("performance") or {}
    funnel = _safe_load(run_dir / "funnel.json") or {}
    guards = _safe_load(run_dir / "guard_funnel.json") or {}
    run_type = _infer_run_type(run_dir, meta)
    fc = _funnel_counts(run_dir, meta, guards)
    trades_raw = perf.get("trades")
    try:
        trades_n = int(trades_raw) if trades_raw is not None else 0
    except (TypeError, ValueError):
        trades_n = 0
    return {
        "run_id": meta.get("run_id") or run_dir.name,
        "path": str(run_dir),
        "run_type": run_type,
        "strategy": meta.get("strategy_name") or meta.get("strategy") or "N/A",
        "symbol": meta.get("symbol") or "N/A",
        "dataset_id": meta.get("dataset_id") or "N/A",
        "from": meta.get("from") or "N/A",
        "to": meta.get("to") or "N/A",
        "run_date": meta.get("finish_time") or meta.get("start_time") or "N/A",
        "confidence": meta.get("confidence") or perf_doc.get("confidence") or "N/A",
        "trades": trades_n,
        "trades_display": trades_n,
        "win_rate_pct": perf.get("win_rate_pct"),
        "net_pnl": perf.get("net_pnl"),
        "return_pct": perf.get("return_pct"),
        "profit_factor": perf.get("profit_factor"),
        "max_drawdown_pct": perf.get("max_drawdown_pct"),
        "outcome_engine": meta.get("outcome_engine") or "N/A",
        "evaluations": funnel.get("total_evaluations", meta.get("decision_count", "N/A")),
        "would_execute": fc.get("would_execute") if fc.get("would_execute") is not None else guards.get("would_execute", "N/A"),
        "valid_zones": fc.get("valid_zones"),
        "first_retests": fc.get("first_retests"),
        "confirmations": fc.get("confirmations"),
        "candidates": fc.get("candidates"),
        "rr_pass": fc.get("rr_pass"),
        "reaction_pivots": fc.get("reaction_pivots"),
        "local_trigger_pivots": fc.get("local_trigger_pivots"),
        "decision_hash": meta.get("decision_hash"),
        "semantic_hash": meta.get("semantic_hash") or (_safe_load(run_dir / "v2_geometry_summary.json") or {}).get("semantic_hash"),
        "status": "ok" if perf else ("decisions_only" if funnel else "partial"),
    }


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except Exception:
            continue
    return rows


def load_run_detail(run_dir: Path) -> dict[str, Any]:
    summary = summarize_run(run_dir)
    meta = _safe_load(run_dir / "meta.json") or {}
    perf_doc = _safe_load(run_dir / "performance.json") or {}
    funnel = _safe_load(run_dir / "funnel.json") or {}
    guards = _safe_load(run_dir / "guard_funnel.json") or {}
    gap = _safe_load(run_dir / "gap_analysis.json") or {}
    spread = _safe_load(run_dir / "spread_validation.json") or {}
    monthly = []
    mpath = run_dir / "monthly.csv"
    if mpath.is_file():
        lines = mpath.read_text(encoding="utf-8").splitlines()
        if lines:
            headers = lines[0].split(",")
            for line in lines[1:]:
                cols = line.split(",")
                monthly.append(dict(zip(headers, cols)))
    equity = []
    epath = run_dir / "equity_curve.csv"
    if epath.is_file():
        lines = epath.read_text(encoding="utf-8").splitlines()
        if lines:
            headers = lines[0].split(",")
            for line in lines[1:]:
                cols = line.split(",")
                equity.append(dict(zip(headers, cols)))

    v2_funnel = _safe_load(run_dir / "v2_funnel.json")
    v2_summary = _safe_load(run_dir / "v2_diagnostic_summary.json")
    v2_rejections = _safe_load(run_dir / "v2_rejection_reasons.json")
    v2_m15 = _safe_load(run_dir / "v2_m15_trigger_diagnostics.json")
    v2_geometry = _safe_load(run_dir / "v2_candidate_geometry.json")
    v2_reaction = _safe_load(run_dir / "v2_reaction_analysis.json")
    v2_near = _safe_load(run_dir / "v2_near_miss_analysis.json")
    v2_zones = _load_jsonl(run_dir / "v2_zone_audit.jsonl")
    v2_retests = _load_jsonl(run_dir / "v2_retest_audit.jsonl")
    has_v2_diag = v2_funnel is not None or (run_dir / "v2_diagnostic_summary.txt").is_file()
    has_geometry_diag = (run_dir / "v2_geometry_summary.json").is_file()

    v2_geometry_summary = _safe_load(run_dir / "v2_geometry_summary.json")
    v2_geometry_decomp = _load_jsonl(run_dir / "v2_candidate_geometry_decomposition.jsonl")
    v2_geometry_matrix = _load_jsonl(run_dir / "v2_rr_matrix.jsonl")
    v2_geometry_decay = _load_jsonl(run_dir / "v2_rr_decay.jsonl")
    geom_recon = _safe_load(run_dir / "geometry_diagnostic_reconciliation.json")

    research = {
        "run_type": _infer_run_type(run_dir, meta),
        "evaluations": funnel.get("total_evaluations") or meta.get("decision_count"),
        "valid_zones": (meta.get("plugin_stats") or {}).get("valid_zones"),
        "first_retests": (meta.get("plugin_stats") or {}).get("first_retests"),
        "reaction_pivots": (meta.get("plugin_stats") or {}).get("local_reaction_pivots"),
        "local_trigger_pivots": (meta.get("plugin_stats") or {}).get("local_trigger_pivots"),
        "confirmations": (meta.get("plugin_stats") or {}).get("m15_confirmations"),
        "candidates": guards.get("candidates") or (meta.get("plugin_stats") or {}).get("candidates"),
        "rr_pass": guards.get("rr_pass"),
        "would_execute": guards.get("would_execute"),
        "trades": (perf_doc.get("performance") or {}).get("trades"),
    }

    return {
        **summary,
        "meta": meta,
        "performance": perf_doc.get("performance") or {},
        "confidence_components": perf_doc.get("confidence_components") or meta.get("confidence_components"),
        "funnel": funnel,
        "guard_funnel": guards,
        "gap_summary": {
            "raw": gap.get("raw_gap_count", meta.get("gap_summary", {}).get("raw") if isinstance(meta.get("gap_summary"), dict) else None),
            "suspicious": gap.get("suspicious_count", meta.get("gap_summary", {}).get("suspicious") if isinstance(meta.get("gap_summary"), dict) else None),
            "material": gap.get("materially_affected_gaps", meta.get("gap_summary", {}).get("material") if isinstance(meta.get("gap_summary"), dict) else None),
        },
        "spread_validation": spread or perf_doc.get("spread_validation"),
        "monthly": monthly,
        "equity_curve": equity,
        "hashes": {
            "run_hash": meta.get("run_hash"),
            "decision_hash": meta.get("decision_hash"),
            "config_hash": meta.get("config_hash"),
            "dataset_hash": meta.get("dataset_hash"),
        },
        "fill_policy": meta.get("fill_policy"),
        "cost_model": meta.get("cost_model"),
        "artifacts_present": sorted(p.name for p in run_dir.iterdir() if p.is_file()),
        "has_v2_diagnostics": has_v2_diag,
        "v2_funnel": v2_funnel,
        "v2_diagnostic_summary": v2_summary,
        "v2_rejection_reasons": v2_rejections,
        "v2_m15_trigger_diagnostics": v2_m15,
        "v2_candidate_geometry": v2_geometry,
        "v2_reaction_analysis": v2_reaction,
        "v2_near_miss_analysis": v2_near,
        "v2_zone_audit": v2_zones,
        "v2_retest_audit": v2_retests,
        "has_geometry_diagnostics": has_geometry_diag,
        "v2_geometry_summary": v2_geometry_summary,
        "v2_geometry_decomposition": v2_geometry_decomp,
        "v2_geometry_matrix": v2_geometry_matrix,
        "v2_geometry_decay": v2_geometry_decay,
        "geometry_reconciliation": geom_recon,
        "research_summary": research,
    }


def compare_runs(a: Path, b: Path) -> dict[str, Any]:
    da, db = load_run_detail(a), load_run_detail(b)
    keys = [
        ("strategy", "strategy"),
        ("dataset_id", "dataset_id"),
        ("evaluations", "evaluations"),
        ("valid_zones", "valid_zones"),
        ("first_retests", "first_retests"),
        ("confirmations", "confirmations"),
        ("candidates", "candidates"),
        ("rr_pass", "rr_pass"),
        ("would_execute", "would_execute"),
        ("trades", "trades"),
        ("win_rate_pct", "win_rate_pct"),
        ("net_pnl", "net_pnl"),
        ("return_pct", "return_pct"),
        ("profit_factor", "profit_factor"),
        ("max_drawdown_pct", "max_drawdown_pct"),
    ]
    rows = []
    for label, key in keys:
        va, vb = da.get(key, "N/A"), db.get(key, "N/A")
        # also try performance nested
        if va == "N/A":
            va = (da.get("performance") or {}).get(key, "N/A")
        if vb == "N/A":
            vb = (db.get("performance") or {}).get(key, "N/A")
        diff = None
        try:
            diff = round(float(vb) - float(va), 4)
        except Exception:
            diff = "N/A"
        rows.append({"metric": label, "a": va, "b": vb, "diff": diff})
    return {"a": da["run_id"], "b": db["run_id"], "rows": rows, "detail_a": da, "detail_b": db}
