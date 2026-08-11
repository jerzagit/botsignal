"""
Sync today's closed MT5 deals into the local dashboard database.

This runs at bot startup so daily P&L survives bot restarts and catches trades
that closed while the bot/dashboard poller was offline.
"""

import json
import logging
from datetime import datetime

import MetaTrader5 as mt5

from core.config import MT5_SYMBOL_SUFFIX
from core.db import get_conn

log = logging.getLogger(__name__)


def _deal_direction(in_deal, out_deal) -> str:
    """Return the original position direction, falling back from close deal type."""
    if in_deal is not None:
        return "buy" if in_deal.type == mt5.DEAL_TYPE_BUY else "sell"
    return "sell" if out_deal.type == mt5.DEAL_TYPE_BUY else "buy"


def _signal_symbol(symbol: str) -> str:
    if MT5_SYMBOL_SUFFIX and symbol.endswith(MT5_SYMBOL_SUFFIX):
        return symbol[: -len(MT5_SYMBOL_SUFFIX)]
    return symbol


def _ticket_exists(cur, ticket: int) -> bool:
    cur.execute("SELECT 1 FROM trades WHERE ticket = %s LIMIT 1", (ticket,))
    return cur.fetchone() is not None


def _upsert_history_trade(cur, position_id: int, out_deals: list, in_deal) -> str:
    last_out = sorted(out_deals, key=lambda d: d.time)[-1]
    ticket = int(position_id or last_out.ticket)
    signal_id = f"mt5day_{ticket}"
    symbol = _signal_symbol(last_out.symbol)
    direction = _deal_direction(in_deal, last_out)
    lot = float(in_deal.volume if in_deal is not None else sum(d.volume for d in out_deals))
    entry_price = float(in_deal.price if in_deal is not None else last_out.price)
    close_price = float(last_out.price)
    profit = sum(float(d.profit) + float(d.swap) + float(d.commission) for d in out_deals)
    outcome = "win" if profit >= 0 else "loss"
    closed_at = datetime.fromtimestamp(last_out.time)

    cur.execute(
        """
        INSERT INTO signals
            (signal_id, symbol, direction, entry_low, entry_high, sl, tps, raw_text,
             source_id, source_name, parser_profile, status)
        VALUES
            (%s, %s, %s, %s, %s, %s, %s, %s, 'mt5_history', 'MT5 History Sync',
             'history_sync', 'closed')
        ON DUPLICATE KEY UPDATE
            status = 'closed',
            updated_at = CURRENT_TIMESTAMP
        """,
        (
            signal_id,
            symbol,
            direction,
            entry_price,
            entry_price,
            0.0,
            json.dumps([]),
            f"MT5 startup daily history sync for position #{ticket}",
        ),
    )
    cur.execute(
        """
        INSERT INTO trades
            (signal_id, ticket, lot, entry_price, close_price, outcome, profit,
             closed_at, entry_mode)
        VALUES
            (%s, %s, %s, %s, %s, %s, %s, %s, 'history_sync')
        ON DUPLICATE KEY UPDATE
            close_price = VALUES(close_price),
            outcome = VALUES(outcome),
            profit = VALUES(profit),
            closed_at = VALUES(closed_at)
        """,
        (
            signal_id,
            ticket,
            lot,
            entry_price,
            close_price,
            outcome,
            profit,
            closed_at,
        ),
    )
    return outcome


def sync_today_closed_deals() -> dict:
    """
    Insert/update today's closed MT5 trade deals into MySQL.

    Only closed market trade deals are synced. Balance operations such as
    deposits and withdrawals have no symbol/OUT entry and are ignored.
    """
    start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    end = datetime.now()
    deals = mt5.history_deals_get(start, end) or []

    by_position = {}
    for deal in deals:
        if not getattr(deal, "symbol", ""):
            continue
        position_id = int(deal.position_id or 0)
        if position_id <= 0:
            continue
        by_position.setdefault(position_id, []).append(deal)

    synced = 0
    inserted = 0
    updated = 0
    pnl = 0.0
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            for position_id, position_deals in by_position.items():
                out_deals = [d for d in position_deals if d.entry == mt5.DEAL_ENTRY_OUT]
                if not out_deals:
                    continue

                in_deals = [d for d in position_deals if d.entry == mt5.DEAL_ENTRY_IN]
                in_deal = in_deals[0] if in_deals else None

                ticket = int(position_id)
                existed = _ticket_exists(cur, ticket)
                _upsert_history_trade(cur, position_id, out_deals, in_deal)
                synced += 1
                if existed:
                    updated += 1
                else:
                    inserted += 1
                pnl += sum(float(d.profit) + float(d.swap) + float(d.commission) for d in out_deals)
    finally:
        conn.close()

    summary = {
        "synced": synced,
        "inserted": inserted,
        "updated": updated,
        "pnl": round(pnl, 2),
    }
    log.info(
        "MT5 daily history sync complete: synced=%s inserted=%s updated=%s pnl=%.2f",
        synced,
        inserted,
        updated,
        summary["pnl"],
    )
    return summary
