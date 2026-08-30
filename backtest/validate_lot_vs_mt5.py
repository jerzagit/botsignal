"""
Validate simulated lot/profit math against MT5 order_calc_* (no orders).

Usage:
  set RUN_MODE=BACKTEST
  python -m backtest.validate_lot_vs_mt5 --symbol XAUUSD --symbol-spec path.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backtest.safety import assert_backtest_safe
from backtest.symbol_spec import load_symbol_spec


def _classify(rel_err: float) -> str:
    ae = abs(rel_err)
    if ae <= 0.01:
        return "MATCH"
    if ae <= 0.05:
        return "ACCEPTABLE_DIFFERENCE"
    return "MISMATCH"


def validate(symbol: str, spec_path: Path) -> dict:
    assert_backtest_safe("validate_lot_vs_mt5")
    import MetaTrader5 as mt5
    from core.config import MT5_PATH, MT5_SYMBOL_SUFFIX

    spec = load_symbol_spec(spec_path)
    ok = bool(mt5.initialize(path=MT5_PATH)) if MT5_PATH else False
    if not ok:
        ok = bool(mt5.initialize())
    if not ok:
        raise RuntimeError(f"MT5 initialize failed: {mt5.last_error()}")

    try:
        symbol_mt5 = symbol if symbol.endswith(MT5_SYMBOL_SUFFIX or "") else symbol + (MT5_SYMBOL_SUFFIX or "")
        tick = mt5.symbol_info_tick(symbol_mt5)
        if tick is None:
            raise RuntimeError("No tick")
        price = float(tick.ask)
        rows = []
        for lot in (0.01, 0.05, 0.10):
            for side, otype in (("BUY", mt5.ORDER_TYPE_BUY), ("SELL", mt5.ORDER_TYPE_SELL)):
                m_mt5 = mt5.order_calc_margin(otype, symbol_mt5, lot, price)
                m_sim = (spec.margin_per_lot or 0) * lot
                if m_mt5 is None or not spec.margin_per_lot:
                    margin_status = "NOT_AVAILABLE"
                    margin_err = None
                else:
                    margin_err = (m_sim - float(m_mt5)) / float(m_mt5) if m_mt5 else None
                    margin_status = _classify(margin_err or 0)

                move = 10 * spec.tick_size
                if side == "BUY":
                    exit_px = price + move
                    p_mt5 = mt5.order_calc_profit(mt5.ORDER_TYPE_BUY, symbol_mt5, lot, price, exit_px)
                    p_sim = (move / spec.tick_size) * spec.tick_value * lot
                else:
                    exit_px = price - move
                    p_mt5 = mt5.order_calc_profit(mt5.ORDER_TYPE_SELL, symbol_mt5, lot, price, exit_px)
                    p_sim = (move / spec.tick_size) * spec.tick_value * lot

                if p_mt5 is None:
                    profit_status = "NOT_AVAILABLE"
                    profit_err = None
                else:
                    profit_err = (p_sim - float(p_mt5)) / float(p_mt5) if p_mt5 else None
                    profit_status = _classify(profit_err or 0)

                rows.append(
                    {
                        "side": side,
                        "lot": lot,
                        "price": price,
                        "margin_mt5": m_mt5,
                        "margin_sim": m_sim,
                        "margin_rel_err": margin_err,
                        "margin_status": margin_status,
                        "profit_mt5": p_mt5,
                        "profit_sim": p_sim,
                        "profit_rel_err": profit_err,
                        "profit_status": profit_status,
                    }
                )
        return {"symbol": symbol_mt5, "spec": str(spec_path), "rows": rows}
    finally:
        try:
            mt5.shutdown()
        except Exception:
            pass


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", default="XAUUSD")
    parser.add_argument("--symbol-spec", required=True)
    parser.add_argument("--out", default=None)
    args = parser.parse_args(argv)
    if not os.getenv("RUN_MODE"):
        os.environ["RUN_MODE"] = "BACKTEST"
    result = validate(args.symbol.upper(), Path(args.symbol_spec))
    text = json.dumps(result, indent=2)
    print(text)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
