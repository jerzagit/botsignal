"""Compare two decision.jsonl runs for strategy-refactor regression."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def _key_fields(rec: dict[str, Any]) -> dict[str, Any]:
    return {
        "timestamp": rec.get("timestamp"),
        "decision": rec.get("decision"),
        "reason": rec.get("reason"),
        "entry": rec.get("entry"),
        "sl": rec.get("sl"),
        "tp": rec.get("tp"),
        "rr": rec.get("rr"),
        "direction": (rec.get("context") or {}).get("direction"),
        "action": (rec.get("context") or {}).get("action"),
    }


def load_decisions(path: Path) -> list[dict[str, Any]]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def compare_decisions(
    before: list[dict[str, Any]],
    after: list[dict[str, Any]],
) -> dict[str, Any]:
    n = max(len(before), len(after))
    different = []
    identical = 0
    for i in range(min(len(before), len(after))):
        a, b = _key_fields(before[i]), _key_fields(after[i])
        if a == b:
            identical += 1
        else:
            different.append({"index": i, "before": a, "after": b})
    missing = max(0, len(before) - len(after))
    extra = max(0, len(after) - len(before))
    return {
        "total_compared": min(len(before), len(after)),
        "before_count": len(before),
        "after_count": len(after),
        "identical": identical,
        "different": len(different),
        "missing": missing,
        "extra": extra,
        "diffs_sample": different[:20],
    }


def compare_trades(
    before_path: Path,
    after_path: Path,
) -> dict[str, Any]:
    def load(p: Path) -> dict[str, dict]:
        out = {}
        for line in p.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            t = json.loads(line)
            out[t["candidate_id"]] = t
        return out

    a, b = load(before_path), load(after_path)
    ids = sorted(set(a) | set(b))
    semantic_keys = (
        "direction",
        "entry",
        "sl",
        "tp",
        "lot",
        "outcome",
        "realized_pnl",
        "status",
    )
    different = []
    identical = 0
    for cid in ids:
        if cid not in a or cid not in b:
            different.append({"candidate_id": cid, "missing_in": "after" if cid not in b else "before"})
            continue
        sa = {k: a[cid].get(k) for k in semantic_keys}
        sb = {k: b[cid].get(k) for k in semantic_keys}
        if sa == sb:
            identical += 1
        else:
            different.append({"candidate_id": cid, "before": sa, "after": sb})
    return {
        "total": len(ids),
        "identical": identical,
        "different": len(different),
        "diffs_sample": different[:20],
    }


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--before", required=True, help="Baseline run directory")
    p.add_argument("--after", required=True, help="Post-refactor run directory")
    p.add_argument("--out", default=None, help="Write strategy_refactor_regression.json here")
    args = p.parse_args(argv)
    before_dir = Path(args.before)
    after_dir = Path(args.after)
    before_d = load_decisions(before_dir / "decisions.jsonl")
    after_d = load_decisions(after_dir / "decisions.jsonl")
    dec = compare_decisions(before_d, after_d)
    trades = None
    if (before_dir / "trades.jsonl").is_file() and (after_dir / "trades.jsonl").is_file():
        trades = compare_trades(before_dir / "trades.jsonl", after_dir / "trades.jsonl")
    before_meta = json.loads((before_dir / "meta.json").read_text(encoding="utf-8"))
    after_meta = json.loads((after_dir / "meta.json").read_text(encoding="utf-8"))
    report = {
        "before_run": str(before_dir),
        "after_run": str(after_dir),
        "decision_hash_before": before_meta.get("decision_hash"),
        "decision_hash_after": after_meta.get("decision_hash"),
        "decision_hash_identical": before_meta.get("decision_hash") == after_meta.get("decision_hash"),
        "decisions": dec,
        "trades": trades,
        "strategy_before": before_meta.get("strategy"),
        "strategy_after": after_meta.get("strategy_name") or after_meta.get("strategy"),
    }
    out = Path(args.out) if args.out else after_dir / "strategy_refactor_regression.json"
    out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(
        f"decisions identical={dec['identical']} different={dec['different']} "
        f"missing={dec['missing']} extra={dec['extra']} "
        f"hash_identical={report['decision_hash_identical']}"
    )
    if trades:
        print(f"trades identical={trades['identical']} different={trades['different']}")
    return 0 if dec["different"] == 0 and dec["missing"] == 0 and dec["extra"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
