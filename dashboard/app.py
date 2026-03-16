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
                t.closed_at
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
        })

    return jsonify(result)


if __name__ == "__main__":
    start_poller()
    app.run(host="0.0.0.0", port=5000, debug=False)
