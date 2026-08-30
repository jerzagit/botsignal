"""Broker symbol specification — assumption vs captured MT5 metadata."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any


QUALITY_ASSUMPTION = "ASSUMPTION"
QUALITY_EXACT = "EXACT_BROKER_METADATA"


@dataclass(frozen=True)
class SymbolSpec:
    symbol: str = "XAUUSD"
    digits: int = 2
    point: float = 0.01
    tick_size: float = 0.01
    tick_value: float = 1.0
    volume_step: float = 0.01
    volume_min: float = 0.01
    volume_max: float = 100.0
    margin_per_lot: float = 1000.0
    contract_size: float | None = None
    currency_base: str | None = None
    currency_profit: str | None = None
    currency_margin: str | None = None
    broker_server: str | None = None
    trade_tick_value_profit: float | None = None
    trade_tick_value_loss: float | None = None
    quality: str = QUALITY_ASSUMPTION
    source_path: str | None = None
    raw: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        return d


def default_xauusd_spec() -> SymbolSpec:
    return SymbolSpec()


def load_symbol_spec(path: Path | str) -> SymbolSpec:
    """
    Load captured broker metadata JSON.
    Refuses silent partial fallback — required numeric fields must be present.
    """
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"Symbol spec not found: {p}")
    data = json.loads(p.read_text(encoding="utf-8"))
    required = [
        "symbol",
        "digits",
        "point",
        "trade_tick_size",
        "trade_tick_value",
        "volume_step",
    ]
    missing = [k for k in required if data.get(k) is None]
    if missing:
        raise ValueError(f"Symbol spec missing required fields: {missing}")

    tick_value = float(data["trade_tick_value"])
    # Prefer explicit measured margin_per_lot; else leave 0 and let caller compute
    margin = data.get("margin_per_lot")
    if margin is None:
        margin = data.get("measured_margin_per_lot", 0.0)

    return SymbolSpec(
        symbol=str(data["symbol"]).upper(),
        digits=int(data["digits"]),
        point=float(data["point"]),
        tick_size=float(data["trade_tick_size"]),
        tick_value=tick_value,
        volume_step=float(data["volume_step"]),
        volume_min=float(data.get("volume_min") or data.get("volume_min_lot") or 0.01),
        volume_max=float(data.get("volume_max") or 100.0),
        margin_per_lot=float(margin or 0.0),
        contract_size=float(data["trade_contract_size"]) if data.get("trade_contract_size") is not None else None,
        currency_base=data.get("currency_base"),
        currency_profit=data.get("currency_profit"),
        currency_margin=data.get("currency_margin"),
        broker_server=data.get("broker_server") or data.get("server"),
        trade_tick_value_profit=(
            float(data["trade_tick_value_profit"])
            if data.get("trade_tick_value_profit") is not None
            else None
        ),
        trade_tick_value_loss=(
            float(data["trade_tick_value_loss"])
            if data.get("trade_tick_value_loss") is not None
            else None
        ),
        quality=QUALITY_EXACT,
        source_path=str(p),
        raw=data,
    )


def resolve_symbol_spec(path: Path | str | None) -> SymbolSpec:
    if path is None:
        return default_xauusd_spec()
    return load_symbol_spec(path)
