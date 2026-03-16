"""
dashboard/app.py
SignalBot Web Dashboard — Flask app.
Run: python dashboard/app.py
Visit: http://localhost:5000
"""

import logging
import sys
import os

# Allow imports from project root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from flask import Flask, render_template, jsonify
from core.db import get_conn
from core.config import (
    MIN_MARGIN_LEVEL, MAX_SPREAD_PIPS, MIN_RR_RATIO,
    ENTRY_MAX_DISTANCE_PIPS, BLOCK_SAME_DIRECTION_STACK,
    SL_MIN_PIPS, TP_ENFORCE_PIPS, RISK_PERCENT, MIN_LOT, MAX_LOT,
    MT5_SYMBOL_SUFFIX, SL_PIP_SIZE,
)
from dashboard.poller import start_poller

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)

app = Flask(__name__, template_folder="templates")


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/stats")
def api_stats():
    conn = get_conn()
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) AS n FROM signals")
        total = cur.fetchone()["n"]

        cur.execute("SELECT COUNT(*) AS n FROM signals WHERE status = 'executed'")
        executed = cur.fetchone()["n"]

        cur.execute("SELECT COUNT(*) AS n FROM signals WHERE status = 'skipped'")
        skipped = cur.fetchone()["n"]

        cur.execute("SELECT COUNT(*) AS n FROM signals WHERE status = 'expired'")
        expired = cur.fetchone()["n"]

        cur.execute("SELECT COUNT(*) AS n FROM trades WHERE outcome = 'win'")
        wins = cur.fetchone()["n"]

        cur.execute("SELECT COUNT(*) AS n FROM trades WHERE outcome = 'loss'")
        losses = cur.fetchone()["n"]

        cur.execute("SELECT COUNT(*) AS n FROM trades WHERE outcome IS NULL")
        open_trades = cur.fetchone()["n"]

        cur.execute("SELECT COALESCE(SUM(profit), 0) AS total FROM trades WHERE outcome IS NOT NULL")
        total_profit = float(cur.fetchone()["total"])

    conn.close()
    win_rate = round(wins / (wins + losses) * 100, 1) if (wins + losses) > 0 else 0.0

    return jsonify({
        "total_signals": total,
        "executed":      executed,
        "skipped":       skipped,
        "expired":       expired,
        "wins":          wins,
        "losses":        losses,
        "open_trades":   open_trades,
        "win_rate":      win_rate,
        "total_profit":  total_profit,
    })


@app.route("/api/signals")
def api_signals():
    conn = get_conn()
    with conn.cursor() as cur:
        cur.execute("""
            SELECT
                s.signal_id,
                s.received_at,
                s.symbol,
                s.direction,
                s.entry_low,
                s.entry_high,
                s.sl,
                s.tps,
                s.status,
                t.ticket,
                t.lot,
                t.entry_price,
                t.outcome,
                t.profit,
                t.close_price,
                t.closed_at,
                t.entry_mode,
                t.layer_num
            FROM signals s
            LEFT JOIN trades t ON s.signal_id = t.signal_id
            ORDER BY s.received_at DESC
            LIMIT 200
        """)
        rows = cur.fetchall()
    conn.close()

    result = []
    for r in rows:
        result.append({
            "signal_id":   r["signal_id"],
            "received_at": r["received_at"].strftime("%Y-%m-%d %H:%M:%S") if r["received_at"] else None,
            "symbol":      r["symbol"],
            "direction":   r["direction"],
            "entry_low":   float(r["entry_low"]),
            "entry_high":  float(r["entry_high"]),
            "sl":          float(r["sl"]),
            "tps":         r["tps"] if isinstance(r["tps"], list) else [],
            "status":      r["status"],
            "ticket":      r["ticket"],
            "lot":         float(r["lot"]) if r["lot"] is not None else None,
            "entry_price": float(r["entry_price"]) if r["entry_price"] is not None else None,
            "outcome":     r["outcome"],
            "profit":      float(r["profit"]) if r["profit"] is not None else None,
            "close_price": float(r["close_price"]) if r["close_price"] is not None else None,
            "closed_at":   r["closed_at"].strftime("%Y-%m-%d %H:%M:%S") if r["closed_at"] else None,
            "entry_mode":  r["entry_mode"],
            "layer_num":   r["layer_num"],
        })

    return jsonify(result)


@app.route("/api/guards/config")
def api_guards_config():
    """Return guard thresholds from .env so the dashboard can display them."""
    return jsonify({
        "margin":    {"threshold": f"≥ {MIN_MARGIN_LEVEL:.0f}%",    "enabled": True},
        "stack":     {"threshold": "Block same-direction stack",     "enabled": BLOCK_SAME_DIRECTION_STACK},
        "rr_ratio":  {"threshold": f"≥ {MIN_RR_RATIO:.1f}:1",       "enabled": True},
        "spread":    {"threshold": f"≤ {MAX_SPREAD_PIPS:.0f} pips",  "enabled": True},
        "proximity": {"threshold": f"≤ {ENTRY_MAX_DISTANCE_PIPS} pips", "enabled": True},
        "lot_calc":  {"threshold": f"{MIN_LOT}–{MAX_LOT} lot",       "enabled": True},
        "risk":      {"threshold": f"{int(RISK_PERCENT*100)}% free margin"},
        "auto_tp":   {"threshold": f"SL < {SL_MIN_PIPS}p => TP set to {TP_ENFORCE_PIPS}p"},
    })


@app.route("/api/guards/live")
def api_guards_live():
    """Connect to MT5 briefly and return live account health values."""
    try:
        import sys, os
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        import MetaTrader5 as mt5
        from core.config import MT5_PATH, MT5_LOGIN, MT5_PASSWORD, MT5_SERVER

        kwargs = {"path": MT5_PATH} if MT5_PATH else {}
        if not mt5.initialize(**kwargs):
            return jsonify({"error": "MT5 not running"}), 503
        if not mt5.login(MT5_LOGIN, password=MT5_PASSWORD, server=MT5_SERVER):
            mt5.shutdown()
            return jsonify({"error": "MT5 login failed"}), 503

        acc  = mt5.account_info()
        symbol = "XAUUSD" + MT5_SYMBOL_SUFFIX
        tick = mt5.symbol_info_tick(symbol)
        mt5.shutdown()

        spread_pips = round((tick.ask - tick.bid) / SL_PIP_SIZE, 2) if tick else None
        margin_level = round(acc.margin_level, 1) if acc and acc.margin > 0 else None

        return jsonify({
            "margin_level":    margin_level,
            "margin_level_ok": margin_level is None or margin_level >= MIN_MARGIN_LEVEL,
            "spread_pips":     spread_pips,
            "spread_ok":       spread_pips is None or spread_pips <= MAX_SPREAD_PIPS,
            "balance":         round(acc.balance, 2) if acc else None,
            "equity":          round(acc.equity, 2)  if acc else None,
            "free_margin":     round(acc.margin_free, 2) if acc else None,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/guards/log")
def api_guards_log():
    """Return recent guard fire events."""
    conn = get_conn()
    with conn.cursor() as cur:
        cur.execute("""
            SELECT id, fired_at, guard_name, signal_id,
                   symbol, direction, reason, value_actual, value_required
            FROM guard_events
            ORDER BY fired_at DESC
            LIMIT 100
        """)
        rows = cur.fetchall()
    conn.close()

    result = []
    for r in rows:
        result.append({
            "id":             r["id"],
            "fired_at":       r["fired_at"].strftime("%Y-%m-%d %H:%M:%S") if r["fired_at"] else None,
            "guard_name":     r["guard_name"],
            "signal_id":      r["signal_id"],
            "symbol":         r["symbol"],
            "direction":      r["direction"],
            "reason":         r["reason"],
            "value_actual":   r["value_actual"],
            "value_required": r["value_required"],
        })
    return jsonify(result)


if __name__ == "__main__":
    start_poller()
    app.run(host="0.0.0.0", port=5000, debug=False)
