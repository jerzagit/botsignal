"""Dashboard display formatters — consistent — / 0 / YES/NO."""

from __future__ import annotations

from typing import Any


def fmt_metric(value: Any, kind: str = "auto") -> str:
    if value is None or value == "N/A" or value == "None":
        return "—"
    if kind == "dash_if_none":
        return "—" if value is None else str(value)
    if kind == "pct":
        try:
            return f"{float(value):.2f}%"
        except (TypeError, ValueError):
            return "—"
    if kind == "money":
        try:
            return f"{float(value):.2f}"
        except (TypeError, ValueError):
            return "—"
    if kind == "int":
        try:
            return str(int(value))
        except (TypeError, ValueError):
            return "—"
    if kind == "bool":
        if value is True:
            return "YES"
        if value is False:
            return "NO"
        return "—"
    return str(value)


def fmt_win_rate(trades: Any, win_rate: Any) -> str:
    try:
        t = int(trades)
    except (TypeError, ValueError):
        return "—"
    if t <= 0:
        return "—"
    return fmt_metric(win_rate, "pct")


def fmt_profit_factor(trades: Any, pf: Any) -> str:
    try:
        t = int(trades)
    except (TypeError, ValueError):
        return "—"
    if t <= 0:
        return "—"
    return fmt_metric(pf, "money")


def register_template_filters(app) -> None:
    app.jinja_env.filters["fmt_metric"] = fmt_metric
    app.jinja_env.filters["fmt_win_rate"] = fmt_win_rate
    app.jinja_env.filters["fmt_profit_factor"] = fmt_profit_factor
