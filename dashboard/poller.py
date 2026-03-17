"""
dashboard/poller.py
Win/Loss Outcome Poller — runs as a daemon thread inside Flask.
Every 5 minutes: checks MT5 deal history for open trades and updates outcome.
"""

import time
import logging
import threading
from datetime import datetime, timezone, timedelta

import MetaTrader5 as mt5

from core.db  import get_conn, update_trade_outcome, ensure_manual_trade
from core.mt5 import mt5_connect
from core.config import (
    MT5_SYMBOL_SUFFIX, SL_PIP_SIZE,
    PROFIT_LOCK_ENABLED, PROFIT_LOCK_PIPS, PROFIT_LOCK_TP_PIPS,
)

log = logging.getLogger(__name__)
POLL_INTERVAL = 60   # 1 minute


def get_open_tickets() -> list:
    """Return all trade tickets that don't yet have an outcome."""
    try:
        conn = get_conn()
        with conn.cursor() as cur:
            cur.execute("SELECT ticket FROM trades WHERE outcome IS NULL")
            rows = cur.fetchall()
        conn.close()
        return [r["ticket"] for r in rows]
    except Exception as e:
        log.error(f"poller.get_open_tickets failed: {e}")
        return []


def check_ticket(ticket: int):
    """
    Look up a ticket in MT5 deal history.
    A closed position has an OUT deal (entry == DEAL_ENTRY_OUT).

    MT5 demo accounts reuse position IDs across sessions, so we must:
    1. First check if the position is still open — if so, skip entirely.
    2. Only match the LAST (most recent) OUT deal with our magic number.
    """
    # If position is still open in MT5, it's not closed — skip
    open_pos = mt5.positions_get(ticket=ticket)
    if open_pos:
        return  # Still open — no need to check history

    date_from = datetime.now(timezone.utc) - timedelta(days=90)
    date_to   = datetime.now(timezone.utc) + timedelta(days=1)

    deals = mt5.history_deals_get(date_from, date_to, position=ticket)
    if not deals:
        return

    # Find the LAST OUT deal with our magic (most recent close of this ticket)
    last_out = None
    for deal in reversed(deals):
        if deal.entry == mt5.DEAL_ENTRY_OUT and deal.magic == 20250101:
            last_out = deal
            break

    if last_out is None:
        return

    profit      = last_out.profit
    outcome     = "win" if profit >= 0 else "loss"
    close_price = last_out.price
    closed_at   = datetime.fromtimestamp(last_out.time, tz=timezone.utc)

    update_trade_outcome(
        ticket=ticket,
        outcome=outcome,
        close_price=close_price,
        closed_at=closed_at,
        profit=profit,
    )
    log.info(
        f"Poller: ticket={ticket} closed → {outcome} "
        f"@ {close_price}  profit={profit:.2f}"
    )


def get_known_tickets() -> set:
    """Return all tickets already in the trades table."""
    try:
        conn = get_conn()
        with conn.cursor() as cur:
            cur.execute("SELECT ticket FROM trades")
            rows = cur.fetchall()
        conn.close()
        return {r["ticket"] for r in rows}
    except Exception as e:
        log.error(f"poller.get_known_tickets failed: {e}")
        return set()


def sync_open_positions():
    """
    Scan all currently open MT5 positions.
    Any position not yet in the DB is recorded as a manual trade
    so the dashboard tracks it and detects when it closes.
    """
    all_positions = mt5.positions_get()
    if not all_positions:
        return

    known = get_known_tickets()
    for pos in all_positions:
        if pos.ticket not in known:
            symbol = pos.symbol.replace(MT5_SYMBOL_SUFFIX, "") if MT5_SYMBOL_SUFFIX else pos.symbol
            direction = "buy" if pos.type == 0 else "sell"
            ensure_manual_trade(
                ticket      = pos.ticket,
                symbol      = symbol,
                direction   = direction,
                lot         = pos.volume,
                entry_price = pos.price_open,
                sl          = pos.sl,
            )
            log.info(f"Poller: manual trade detected — ticket={pos.ticket} {symbol} {direction}")


def check_profit_lock():
    """
    Profit Lock: when a bot position is +PROFIT_LOCK_PIPS in profit,
    move SL to breakeven and push TP to +PROFIT_LOCK_TP_PIPS from entry.
    Called inside poll_loop() with MT5 session already open.
    """
    if not PROFIT_LOCK_ENABLED:
        return

    positions = mt5.positions_get()
    if not positions:
        return

    for pos in positions:
        if pos.magic != 20250101:   # only bot-placed positions
            continue

        entry  = pos.price_open
        is_buy = (pos.type == 0)

        # Calculate profit in pips
        profit_pts  = (pos.price_current - entry) if is_buy else (entry - pos.price_current)
        profit_pips = profit_pts / SL_PIP_SIZE

        if profit_pips < PROFIT_LOCK_PIPS:
            continue

        # Calculate target SL (breakeven) and TP (+100p from entry)
        new_sl = entry

        # Runner position (no TP set) — only move SL to breakeven, let it ride
        if pos.tp == 0.0:
            new_tp = 0.0
        else:
            if is_buy:
                new_tp = round(entry + PROFIT_LOCK_TP_PIPS * SL_PIP_SIZE, 2)
            else:
                new_tp = round(entry - PROFIT_LOCK_TP_PIPS * SL_PIP_SIZE, 2)

            # Never reduce TP (if original is already further, keep it)
            if is_buy and pos.tp > new_tp:
                new_tp = pos.tp
            elif not is_buy and pos.tp < new_tp:
                new_tp = pos.tp

        # Skip if already fully locked (SL at BE and TP at target)
        sl_at_be = (is_buy and pos.sl >= entry) or (not is_buy and pos.sl > 0 and pos.sl <= entry)
        tp_at_target = (pos.tp == new_tp)
        if sl_at_be and tp_at_target:
            continue

        # Modify position
        request = {
            "action":   mt5.TRADE_ACTION_SLTP,
            "position": pos.ticket,
            "symbol":   pos.symbol,
            "sl":       new_sl,
            "tp":       new_tp,
        }
        result = mt5.order_send(request)
        direction = "BUY" if is_buy else "SELL"
        if result.retcode == mt5.TRADE_RETCODE_DONE:
            log.info(
                f"ProfitLock: #{pos.ticket} {pos.symbol} {direction} "
                f"+{profit_pips:.0f}p → SL={new_sl} TP={new_tp}"
            )
        else:
            log.warning(
                f"ProfitLock: #{pos.ticket} modify failed: "
                f"{result.comment} (code {result.retcode})"
            )


def poll_loop():
    """Main loop — runs forever in a daemon thread."""
    while True:
        if mt5_connect():
            # Step 1: register any manually opened positions not yet in DB
            try:
                sync_open_positions()
            except Exception as e:
                log.error(f"poller.sync_open_positions failed: {e}")

            # Step 2: check outcome for all open (unclosed) trades in DB
            tickets = get_open_tickets()
            for ticket in tickets:
                try:
                    check_ticket(ticket)
                except Exception as e:
                    log.error(f"poller error ticket={ticket}: {e}")

            # Step 3: profit lock — auto-breakeven + TP override on profitable positions
            try:
                check_profit_lock()
            except Exception as e:
                log.error(f"poller.check_profit_lock failed: {e}")

            mt5.shutdown()
        else:
            log.warning("Poller: MT5 not available — will retry next cycle.")
        time.sleep(POLL_INTERVAL)


def start_poller():
    """Start the poller as a background daemon thread."""
    t = threading.Thread(target=poll_loop, daemon=True, name="outcome-poller")
    t.start()
    log.info("Win/Loss outcome poller started (every 60 sec).")
