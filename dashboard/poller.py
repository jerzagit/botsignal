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
from core.config import MT5_SYMBOL_SUFFIX

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
    """
    date_from = datetime.now(timezone.utc) - timedelta(days=90)
    date_to   = datetime.now(timezone.utc) + timedelta(days=1)

    deals = mt5.history_deals_get(date_from, date_to, position=ticket)
    if not deals:
        return  # Still open

    for deal in deals:
        if deal.entry == mt5.DEAL_ENTRY_OUT:
            profit      = deal.profit
            outcome     = "win" if profit >= 0 else "loss"
            close_price = deal.price
            closed_at   = datetime.fromtimestamp(deal.time, tz=timezone.utc)

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
            return


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

            mt5.shutdown()
        else:
            log.warning("Poller: MT5 not available — will retry next cycle.")
        time.sleep(POLL_INTERVAL)


def start_poller():
    """Start the poller as a background daemon thread."""
    t = threading.Thread(target=poll_loop, daemon=True, name="outcome-poller")
    t.start()
    log.info("Win/Loss outcome poller started (every 60 sec).")
