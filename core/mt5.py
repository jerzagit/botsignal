"""
core/mt5.py
MT5 connection management and trade execution.
"""

import logging
import json
import time
from pathlib import Path

import MetaTrader5 as mt5

from core.config import MT5_LOGIN, MT5_PASSWORD, MT5_SERVER
from core.signal import Signal
from core.risk   import calculate_lot

log = logging.getLogger(__name__)
TRADE_LOG = Path("data/trades.json")


# ── Connection ────────────────────────────────────────────────────────────────

def mt5_connect() -> bool:
    """Connect and login to MT5. Returns True on success."""
    if not mt5.initialize():
        log.error(f"MT5 initialize() failed: {mt5.last_error()}")
        return False
    if not mt5.login(MT5_LOGIN, password=MT5_PASSWORD, server=MT5_SERVER):
        log.error(f"MT5 login failed: {mt5.last_error()}")
        mt5.shutdown()
        return False
    return True


def mt5_connect_test() -> tuple[bool, str]:
    """Startup sanity check — returns (ok, message)."""
    if not mt5_connect():
        return False, f"Login failed: {mt5.last_error()}"
    info = mt5.account_info()
    msg = (
        f"Connected as #{info.login} | "
        f"Balance: ${info.balance:,.2f} | "
        f"Free margin: ${info.margin_free:,.2f}"
    )
    mt5.shutdown()
    return True, msg


# ── Trade execution ───────────────────────────────────────────────────────────

def execute_trade(signal: Signal) -> str:
    """
    Place a market order on MT5 for the given signal.
    Lot size is auto-calculated from margin % risk.
    All TPs from the signal are noted; MT5 is set to TP1 (first target).
    Returns a human-readable result message.
    """
    if not mt5_connect():
        return "❌ Could not connect to MT5."

    symbol = signal.symbol

    # Ensure symbol is visible in Market Watch
    info = mt5.symbol_info(symbol)
    if info is None:
        mt5.shutdown()
        return f"❌ Symbol `{symbol}` not found in MT5."
    if not info.visible:
        mt5.symbol_select(symbol, True)

    # Auto lot size from risk %
    lot, lot_explanation = calculate_lot(signal)
    if lot == 0.0:
        mt5.shutdown()
        return f"❌ Lot calculation failed:\n{lot_explanation}"

    # Current market price
    tick = mt5.symbol_info_tick(symbol)
    if tick is None:
        mt5.shutdown()
        return "❌ Could not fetch current price from MT5."

    order_type = mt5.ORDER_TYPE_BUY if signal.direction == "buy" else mt5.ORDER_TYPE_SELL
    price      = tick.ask if signal.direction == "buy" else tick.bid
    tp         = signal.tps[0]   # MT5 manages TP1; remaining TPs noted for manual management

    request = {
        "action":       mt5.TRADE_ACTION_DEAL,
        "symbol":       symbol,
        "volume":       lot,
        "type":         order_type,
        "price":        price,
        "sl":           signal.sl,
        "tp":           tp,
        "deviation":    20,
        "magic":        20250101,
        "comment":      "SignalBot",
        "type_time":    mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,
    }

    result = mt5.order_send(request)
    mt5.shutdown()

    if result.retcode != mt5.TRADE_RETCODE_DONE:
        return (
            f"❌ *Trade failed*\n"
            f"Code: `{result.retcode}`\n"
            f"Reason: `{result.comment}`"
        )

    # Log the trade to data/trades.json for dashboard later
    _log_trade(signal, lot, price, result.order)

    direction_emoji = "🔴" if signal.direction == "sell" else "🟢"
    tps_str = " → ".join(str(t) for t in signal.tps)

    return (
        f"✅ *Trade Executed!*\n\n"
        f"{direction_emoji} `{symbol} {signal.direction.upper()}`\n"
        f"Entry: `{price}`\n"
        f"SL: `{signal.sl}`\n"
        f"TPs: `{tps_str}`\n\n"
        f"{lot_explanation}\n\n"
        f"🎫 Ticket: `{result.order}`"
    )


# ── Trade log (feeds dashboard later) ────────────────────────────────────────

def _log_trade(signal: Signal, lot: float, entry: float, ticket: int):
    TRADE_LOG.parent.mkdir(exist_ok=True)
    trades = []
    if TRADE_LOG.exists():
        try:
            trades = json.loads(TRADE_LOG.read_text())
        except Exception:
            trades = []

    trades.append({
        "ticket":    ticket,
        "symbol":    signal.symbol,
        "direction": signal.direction,
        "entry":     entry,
        "sl":        signal.sl,
        "tps":       signal.tps,
        "lot":       lot,
        "time":      time.strftime("%Y-%m-%d %H:%M:%S"),
        "status":    "open",
    })

    TRADE_LOG.write_text(json.dumps(trades, indent=2))
    log.info(f"Trade logged: ticket={ticket} {signal.symbol} {signal.direction} lot={lot}")
