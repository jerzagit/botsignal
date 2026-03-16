"""
core/db.py
MySQL write functions used by the bot to log signals and trades.
All dashboard reads go through dashboard/app.py directly.
"""

import json
import logging

import pymysql
import pymysql.cursors

from core.config import DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD

log = logging.getLogger(__name__)


def get_conn():
    """Return a new pymysql connection. Caller must close it."""
    return pymysql.connect(
        host=DB_HOST,
        port=DB_PORT,
        db=DB_NAME,
        user=DB_USER,
        password=DB_PASSWORD,
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=True,
    )


def upsert_signal(signal_id: str, signal, status: str = "pending"):
    """
    Insert a new signal or update its status.
    Called by listener.py on arrival (pending),
    and by notifier.py on EXECUTE/SKIP/expire.
    """
    sql = """
        INSERT INTO signals
            (signal_id, symbol, direction, entry_low, entry_high, sl, tps, raw_text, status)
        VALUES
            (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            status     = VALUES(status),
            updated_at = CURRENT_TIMESTAMP
    """
    try:
        conn = get_conn()
        with conn.cursor() as cur:
            cur.execute(sql, (
                signal_id,
                signal.symbol,
                signal.direction,
                signal.entry_low,
                signal.entry_high,
                signal.sl,
                json.dumps(signal.tps),
                signal.raw_text,
                status,
            ))
        conn.close()
    except Exception as e:
        log.error(f"db.upsert_signal failed: {e}")


def record_trade(signal_id: str, ticket: int, lot: float, entry_price: float):
    """Insert a trade row right after MT5 confirms execution."""
    sql = """
        INSERT INTO trades (signal_id, ticket, lot, entry_price)
        VALUES (%s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE ticket = ticket
    """
    try:
        conn = get_conn()
        with conn.cursor() as cur:
            cur.execute(sql, (signal_id, ticket, lot, entry_price))
        conn.close()
    except Exception as e:
        log.error(f"db.record_trade failed: {e}")


def ensure_manual_trade(ticket: int, symbol: str, direction: str,
                        lot: float, entry_price: float, sl: float):
    """
    Called by the poller when it finds an MT5 position not in the DB.
    Inserts a synthetic 'manual' signal + trade row so the dashboard shows it.
    Safe to call multiple times — uses ON DUPLICATE KEY UPDATE.
    """
    signal_id = f"manual_{ticket}"
    sig_sql = """
        INSERT INTO signals
            (signal_id, symbol, direction, entry_low, entry_high, sl, tps, raw_text, status)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'manual')
        ON DUPLICATE KEY UPDATE signal_id = signal_id
    """
    trade_sql = """
        INSERT INTO trades (signal_id, ticket, lot, entry_price)
        VALUES (%s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE ticket = ticket
    """
    try:
        conn = get_conn()
        with conn.cursor() as cur:
            cur.execute(sig_sql, (
                signal_id, symbol, direction,
                entry_price, entry_price, sl,
                json.dumps([]), "Manual trade",
            ))
            cur.execute(trade_sql, (signal_id, ticket, lot, entry_price))
        conn.close()
        log.info(f"db.ensure_manual_trade: ticket={ticket} {symbol} {direction} recorded")
    except Exception as e:
        log.error(f"db.ensure_manual_trade failed: {e}")


def record_guard_event(guard_name: str, signal_id: str, symbol: str,
                       direction: str, reason: str,
                       value_actual: str = "", value_required: str = ""):
    """Log a guard block event so the dashboard can show why a trade was rejected."""
    sql = """
        INSERT INTO guard_events
            (guard_name, signal_id, symbol, direction, reason, value_actual, value_required)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
    """
    try:
        conn = get_conn()
        with conn.cursor() as cur:
            cur.execute(sql, (guard_name, signal_id, symbol, direction,
                              reason, value_actual, value_required))
        conn.close()
    except Exception as e:
        log.error(f"db.record_guard_event failed: {e}")


def update_trade_outcome(ticket: int, outcome: str, close_price: float,
                         closed_at, profit: float):
    """Called by the win/loss poller when an MT5 position is found closed."""
    sql = """
        UPDATE trades
        SET outcome     = %s,
            close_price = %s,
            closed_at   = %s,
            profit      = %s
        WHERE ticket = %s
    """
    try:
        conn = get_conn()
        with conn.cursor() as cur:
            cur.execute(sql, (outcome, close_price, closed_at, profit, ticket))
        conn.close()
    except Exception as e:
        log.error(f"db.update_trade_outcome failed: {e}")
