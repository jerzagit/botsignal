"""CLI for passive V2 geometry diagnostics (no strategy replay)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backtest.catalog import default_runs_root
from backtest.v2_geometry_diagnostics import run_geometry_diagnostic


def _parse_sources(raw: list[str], runs_root: Path) -> list[tuple[Path, str]]:
    out: list[tuple[Path, str]] = []
    for item in raw:
        if ":" in item:
            run_id, strategy = item.split(":", 1)
        else:
            run_id, strategy = item, "unknown"
        out.append((runs_root / run_id, strategy))
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="V2 geometry diagnostic (passive, no strategy changes)")
    parser.add_argument("--dataset", required=True, help="Dataset directory with M15/H1 CSV")
    parser.add_argument(
        "--sources",
        nargs="+",
        required=True,
        help="Run IDs with optional strategy suffix RUN_ID:strategy_name",
    )
    parser.add_argument("--run-id", default="BT_XAUUSD_V2_GEOMETRY_DIAGNOSTIC_001")
    parser.add_argument("--runs-root", default=None)
    parser.add_argument(
        "--retest-source",
        default=None,
        help="Run dir for retest/zone audits (default: last --sources entry)",
    )
    parser.add_argument(
        "--compare-old-dir",
        default=None,
        help="Prior geometry diagnostic run dir for reconciliation artifact",
    )
    args = parser.parse_args(argv)

    runs_root = Path(args.runs_root) if args.runs_root else default_runs_root()
    sources = _parse_sources(args.sources, runs_root)
    if args.retest_source:
        retest_src = Path(args.retest_source)
        if not retest_src.is_absolute() and not retest_src.is_dir():
            retest_src = runs_root / args.retest_source
    else:
        retest_src = None
    out_dir = runs_root / args.run_id

    if args.compare_old_dir:
        old_p = Path(args.compare_old_dir)
        if not old_p.is_absolute() and not old_p.is_dir():
            old_p = runs_root / args.compare_old_dir
        compare_old = old_p
    else:
        compare_old = None

    result = run_geometry_diagnostic(
        Path(args.dataset),
        sources,
        out_dir,
        retest_source=retest_src,
        compare_old_dir=compare_old,
    )
    print(f"Geometry diagnostic written to {result['out_dir']}")
    print(f"Semantic hash: {result['semantic_hash']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
