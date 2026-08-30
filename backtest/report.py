"""Funnel / summary helpers for decision journals."""

from __future__ import annotations

from collections import Counter
from typing import Any


def decision_label(action: str, direction: str | None) -> str:
    """Map production StrategyDecision to journal labels."""
    a = (action or "").lower()
    if a == "wait":
        return "WAIT"
    if a == "skip":
        return "SKIP"
    if a == "enter":
        d = (direction or "").lower()
        if d == "buy":
            return "BUY"
        if d == "sell":
            return "SELL"
        return "ENTER"
    return action.upper() if action else "UNKNOWN"


def build_funnel(records: list[dict[str, Any]]) -> dict[str, Any]:
    labels = Counter(r.get("decision") for r in records)
    reasons_wait = Counter(
        r.get("reason") for r in records if r.get("decision") == "WAIT"
    )
    reasons_skip = Counter(
        r.get("reason") for r in records if r.get("decision") == "SKIP"
    )
    return {
        "total_evaluations": len(records),
        "WAIT": labels.get("WAIT", 0),
        "SKIP": labels.get("SKIP", 0),
        "BUY": labels.get("BUY", 0),
        "SELL": labels.get("SELL", 0),
        "wait_reasons": dict(reasons_wait.most_common(20)),
        "skip_reasons": dict(reasons_skip.most_common(20)),
    }


def format_funnel(funnel: dict[str, Any]) -> str:
    lines = [
        f"TOTAL EVALUATIONS: {funnel['total_evaluations']:,}",
        "",
        f"WAIT:\n{funnel['WAIT']:,}",
        "",
        f"SKIP:\n{funnel['SKIP']:,}",
        "",
        f"BUY:\n{funnel['BUY']:,}",
        "",
        f"SELL:\n{funnel['SELL']:,}",
    ]
    if funnel.get("wait_reasons"):
        lines.append("")
        lines.append("Top WAIT reasons:")
        for k, v in list(funnel["wait_reasons"].items())[:10]:
            lines.append(f"  {v}: {k}")
    if funnel.get("skip_reasons"):
        lines.append("")
        lines.append("Top SKIP reasons:")
        for k, v in list(funnel["skip_reasons"].items())[:10]:
            lines.append(f"  {v}: {k}")
    return "\n".join(lines)
