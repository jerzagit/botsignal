"""
Capture non-secret MT5 symbol/account trading metadata for backtests.

Usage:
  set RUN_MODE=BACKTEST
  python -m backtest.capture_symbol_spec --symbol XAUUSD

Writes: data/backtests/broker_specs/<Server>_<SYMBOL>.json
Never stores passwords. Never calls order_send.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backtest.gitmeta import git_branch, git_commit
from backtest.safety import assert_backtest_safe


def _sanitize(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "", s or "unknown")[:48] or "unknown"


def _sessions_to_list(sessions) -> list[dict[str, Any]]:
    out = []
    if sessions is None:
        return out
    try:
        for day_idx, day in enumerate(sessions):
            for sess in day:
                # MetaTrader5 Session objects: day, open/close in minutes
                open_m = int(getattr(sess, "time_from", getattr(sess, "from", 0)) or 0)
                close_m = int(getattr(sess, "time_to", getattr(sess, "to", 0)) or 0)
                if open_m == 0 and close_m == 0:
                    continue
                out.append(
                    {
                        "day_index": day_idx,
                        "open_minutes": open_m,
                        "close_minutes": close_m,
                    }
                )
    except Exception as e:
        return [{"error": str(e)}]
    return out


def capture_symbol_spec(symbol: str, *, out_dir: Path | None = None) -> Path:
    assert_backtest_safe("capture_symbol_spec")
    import MetaTrader5 as mt5
    from core.config import MT5_PATH, MT5_SERVER, MT5_SYMBOL_SUFFIX

    # Soft attach: read whichever account is already open (no password, no order_send).
    # Prefer attach-existing so we can capture the same broker used for history download.
    ok = False
    if MT5_PATH:
        ok = bool(mt5.initialize(path=MT5_PATH))
    if not ok:
        ok = bool(mt5.initialize())
    if not ok:
        raise RuntimeError(
            f"MT5 initialize failed: {mt5.last_error()}. Open the Eightcap terminal first."
        )

    try:
        account = mt5.account_info()
        server = (account.server if account else None) or MT5_SERVER or "unknown"
        symbol_mt5 = symbol if symbol.endswith(MT5_SYMBOL_SUFFIX or "") else symbol + (MT5_SYMBOL_SUFFIX or "")
        info = mt5.symbol_info(symbol_mt5)
        if info is None:
            mt5.symbol_select(symbol_mt5, True)
            info = mt5.symbol_info(symbol_mt5)
        if info is None:
            raise RuntimeError(f"symbol_info failed for {symbol_mt5}: {mt5.last_error()}")

        tick = mt5.symbol_info_tick(symbol_mt5)
        price = float(tick.ask) if tick else float(info.ask or info.bid or 0)

        measured_margin = None
        measured_profit_buy_10 = None
        measured_profit_sell_10 = None
        if price > 0:
            m = mt5.order_calc_margin(mt5.ORDER_TYPE_BUY, symbol_mt5, 1.0, price)
            if m is not None:
                measured_margin = float(m)
            pt = float(info.point) or 0.01
            pb = mt5.order_calc_profit(
                mt5.ORDER_TYPE_BUY, symbol_mt5, 1.0, price, price + 10 * pt
            )
            ps = mt5.order_calc_profit(
                mt5.ORDER_TYPE_SELL, symbol_mt5, 1.0, price, price - 10 * pt
            )
            if pb is not None:
                measured_profit_buy_10 = float(pb)
            if ps is not None:
                measured_profit_sell_10 = float(ps)

        session_quotes = _sessions_to_list(getattr(info, "session_quotes", None))
        session_trades = _sessions_to_list(getattr(info, "session_trades", None))

        payload = {
            "symbol": symbol.upper(),
            "symbol_mt5": symbol_mt5,
            "broker_server": server,
            "broker_login_id": int(account.login) if account else None,
            "account_currency": getattr(account, "currency", None) if account else None,
            "account_leverage": int(account.leverage) if account else None,
            "digits": int(info.digits),
            "point": float(info.point),
            "trade_tick_size": float(info.trade_tick_size),
            "trade_tick_value": float(info.trade_tick_value),
            "trade_tick_value_profit": float(getattr(info, "trade_tick_value_profit", 0) or 0) or None,
            "trade_tick_value_loss": float(getattr(info, "trade_tick_value_loss", 0) or 0) or None,
            "trade_contract_size": float(info.trade_contract_size),
            "volume_min": float(info.volume_min),
            "volume_max": float(info.volume_max),
            "volume_step": float(info.volume_step),
            "trade_mode": int(info.trade_mode),
            "trade_stops_level": int(info.trade_stops_level),
            "trade_freeze_level": int(info.trade_freeze_level),
            "currency_base": info.currency_base,
            "currency_profit": info.currency_profit,
            "currency_margin": info.currency_margin,
            "margin_initial": float(getattr(info, "margin_initial", 0) or 0) or None,
            "margin_maintenance": float(getattr(info, "margin_maintenance", 0) or 0) or None,
            "margin_hedged": float(getattr(info, "margin_hedged", 0) or 0) or None,
            "margin_per_lot": measured_margin,
            "measured_margin_per_lot": measured_margin,
            "measured_at_price": price,
            "order_calc_profit_buy_10pts_1lot": measured_profit_buy_10,
            "order_calc_profit_sell_10pts_1lot": measured_profit_sell_10,
            "session_quotes": session_quotes,
            "session_trades": session_trades,
            "captured_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "git_branch": git_branch(ROOT),
            "git_commit": git_commit(ROOT),
            "quality": "EXACT_BROKER_METADATA",
            "notes": (
                "Captured via MetaTrader5 initialize(attach) + symbol_info + "
                "order_calc_margin/profit. No orders sent. No password stored. "
                "Does not require matching bot ENV_MODE login."
            ),
        }

        out_dir = out_dir or (ROOT / "data" / "backtests" / "broker_specs")
        out_dir.mkdir(parents=True, exist_ok=True)
        fname = f"{_sanitize(server)}_{symbol.upper()}.json"
        out_path = out_dir / fname
        out_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(f"Wrote {out_path}")
        print(
            f"login={payload['broker_login_id']} server={server} "
            f"point={payload['point']} tick_size={payload['trade_tick_size']} "
            f"tick_value={payload['trade_tick_value']} margin/lot={payload['margin_per_lot']}"
        )
        return out_path
    finally:
        try:
            mt5.shutdown()
        except Exception:
            pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Capture MT5 symbol metadata for backtests.")
    parser.add_argument("--symbol", default="XAUUSD")
    parser.add_argument("--out-dir", default=None)
    args = parser.parse_args(argv)
    if not os.getenv("RUN_MODE"):
        os.environ["RUN_MODE"] = "BACKTEST"
    assert_backtest_safe("capture_symbol_spec.main")
    capture_symbol_spec(args.symbol.upper(), out_dir=Path(args.out_dir) if args.out_dir else None)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
