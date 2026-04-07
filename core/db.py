"""
core/db.py
MySQL write functions used by the bot to log signals and trades.
All dashboard reads go through dashboard/app.py directly.
"""

import json
import logging
from datetime import datetime, timedelta, timezone

import pymysql
import pymysql.cursors

from core.config import DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD

log = logging.getLogger(__name__)

_MY_TZ = timezone(timedelta(hours=8))

def _today_my() -> str:
    """Today's date in Malaysia time (UTC+8), as YYYY-MM-DD string."""
    return datetime.now(_MY_TZ).strftime("%Y-%m-%d")


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


def record_trade(signal_id: str, ticket: int, lot: float, entry_price: float,
                 entry_mode: str = None, layer_num: int = None):
    """Insert a trade row right after MT5 confirms execution."""
    sql = """
        INSERT INTO trades (signal_id, ticket, lot, entry_price, entry_mode, layer_num)
        VALUES (%s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE ticket = ticket
    """
    try:
        conn = get_conn()
        with conn.cursor() as cur:
            cur.execute(sql, (signal_id, ticket, lot, entry_price, entry_mode, layer_num))
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


# ── OHLC candles ──────────────────────────────────────────────────────────────

def upsert_candles(symbol: str, timeframe: str, candles: list[dict]) -> int:
    """
    Bulk upsert OHLC candles into the candles table.
    Each candle dict: {time (unix int), open, high, low, close, volume}
    Returns number of rows inserted/updated.
    """
    if not candles:
        return 0
    sql = """
        INSERT INTO candles (symbol, timeframe, candle_time, open, high, low, close, volume)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            open   = VALUES(open),
            high   = VALUES(high),
            low    = VALUES(low),
            close  = VALUES(close),
            volume = VALUES(volume)
    """
    try:
        conn = get_conn()
        with conn.cursor() as cur:
            rows = [
                (
                    symbol, timeframe,
                    datetime.utcfromtimestamp(c["time"]),
                    c["open"], c["high"], c["low"], c["close"],
                    c.get("volume", 0),
                )
                for c in candles
            ]
            cur.executemany(sql, rows)
            count = cur.rowcount
        conn.close()
        return count
    except Exception as e:
        log.error(f"db.upsert_candles failed: {e}")
        return 0


def get_candles(symbol: str, timeframe: str, limit: int = 100) -> list[dict]:
    """Return the most recent `limit` candles for a symbol/timeframe, oldest first."""
    sql = """
        SELECT candle_time, open, high, low, close, volume
        FROM candles
        WHERE symbol = %s AND timeframe = %s
        ORDER BY candle_time DESC
        LIMIT %s
    """
    try:
        conn = get_conn()
        with conn.cursor() as cur:
            cur.execute(sql, (symbol, timeframe, limit))
            rows = cur.fetchall()
        conn.close()
        return list(reversed(rows))   # oldest first
    except Exception as e:
        log.error(f"db.get_candles failed: {e}")
        return []


# ── SNR levels ────────────────────────────────────────────────────────────────

def set_snr_levels(symbol: str, prices: list[float]):
    """Replace today's SNR levels for a symbol with a new sorted list."""
    today = _today_my()
    try:
        conn = get_conn()
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM snr_levels WHERE symbol = %s AND valid_date = %s",
                (symbol, today),
            )
            for p in sorted(prices):
                cur.execute(
                    "INSERT INTO snr_levels (symbol, price, valid_date) VALUES (%s, %s, %s)",
                    (symbol, p, today),
                )
        conn.close()
    except Exception as e:
        log.error(f"db.set_snr_levels failed: {e}")


def get_snr_levels(symbol: str) -> list[float]:
    """Return today's SNR levels for a symbol, sorted ascending."""
    today = _today_my()
    try:
        conn = get_conn()
        with conn.cursor() as cur:
            cur.execute(
                "SELECT price FROM snr_levels WHERE symbol = %s AND valid_date = %s ORDER BY price",
                (symbol, today),
            )
            rows = cur.fetchall()
        conn.close()
        return [float(r["price"]) for r in rows]
    except Exception as e:
        log.error(f"db.get_snr_levels failed: {e}")
        return []


# ── Mapping zones ─────────────────────────────────────────────────────────────

def add_zone(symbol: str, direction: str, zone_low: float, zone_high: float,
             sl: float, tp: float) -> int | None:
    """
    Insert a new mapping zone for today. Returns the zone id.
    Auto-replaces any existing unfired zone with the same symbol+direction.
    """
    today = _today_my()
    try:
        conn = get_conn()
        with conn.cursor() as cur:
            # Remove existing unfired zones for same symbol+direction
            cur.execute(
                """DELETE FROM mapping_zones
                   WHERE symbol = %s AND direction = %s
                     AND valid_date = %s AND fired = FALSE""",
                (symbol, direction, today),
            )
            cur.execute(
                """INSERT INTO mapping_zones
                       (symbol, direction, zone_low, zone_high, sl, tp, valid_date)
                   VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                (symbol, direction, zone_low, zone_high, sl, tp, today),
            )
            zone_id = cur.lastrowid
        conn.close()
        return zone_id
    except Exception as e:
        log.error(f"db.add_zone failed: {e}")
        return None


def get_today_zones(symbol: str = None, direction: str = None,
                    active_only: bool = False) -> list[dict]:
    """Return today's mapping zones, optionally filtered."""
    today = _today_my()
    conds = ["valid_date = %s"]
    params: list = [today]
    if symbol:
        conds.append("symbol = %s")
        params.append(symbol)
    if direction:
        conds.append("direction = %s")
        params.append(direction)
    if active_only:
        conds.append("fired = FALSE")

    where = " AND ".join(conds)
    try:
        conn = get_conn()
        with conn.cursor() as cur:
            cur.execute(f"SELECT * FROM mapping_zones WHERE {where} ORDER BY id", params)
            rows = cur.fetchall()
        conn.close()
        return rows
    except Exception as e:
        log.error(f"db.get_today_zones failed: {e}")
        return []


def mark_zone_fired(zone_id: int, signal_id: str):
    """Mark a zone as fired (one-shot) and link it to a signal."""
    try:
        conn = get_conn()
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE mapping_zones SET fired = TRUE, signal_id = %s WHERE id = %s",
                (signal_id, zone_id),
            )
        conn.close()
    except Exception as e:
        log.error(f"db.mark_zone_fired failed: {e}")


def delete_zone(zone_id: int) -> bool:
    """Delete a zone by id (today only). Returns True if a row was deleted."""
    today = _today_my()
    try:
        conn = get_conn()
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM mapping_zones WHERE id = %s AND valid_date = %s",
                (zone_id, today),
            )
            deleted = cur.rowcount > 0
        conn.close()
        return deleted
    except Exception as e:
        log.error(f"db.delete_zone failed: {e}")
        return False


def clear_zones():
    """Clear all today's mapping zones AND SNR levels."""
    today = _today_my()
    try:
        conn = get_conn()
        with conn.cursor() as cur:
            cur.execute("DELETE FROM mapping_zones WHERE valid_date = %s", (today,))
            cur.execute("DELETE FROM snr_levels WHERE valid_date = %s", (today,))
        conn.close()
    except Exception as e:
        log.error(f"db.clear_zones failed: {e}")
