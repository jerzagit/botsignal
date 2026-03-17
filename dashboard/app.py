"""
dashboard/app.py
SignalBot Web Dashboard — Flask app.
Run: python dashboard/app.py
Visit: http://localhost:5000
"""

import json
import logging
import sys
import os

# Allow imports from project root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from flask import Flask, render_template, jsonify, request
from core.db import get_conn, get_today_zones, get_snr_levels
from core.config import (
    MIN_MARGIN_LEVEL, MAX_SPREAD_PIPS, MIN_RR_RATIO,
    ENTRY_MAX_DISTANCE_PIPS, BLOCK_SAME_DIRECTION_STACK,
    SL_MIN_PIPS, TP_ENFORCE_PIPS, RISK_PERCENT, MIN_LOT, MAX_LOT,
    MT5_SYMBOL_SUFFIX, SL_PIP_SIZE, ENV_MODE, MAP_ENABLED,
    PROFIT_LOCK_ENABLED, PROFIT_LOCK_PIPS, PROFIT_LOCK_TP_PIPS,
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
    date_from = request.args.get('date_from')
    date_to   = request.args.get('date_to')

    # Signal date filter (received_at)
    sig_cond, sig_params = [], []
    if date_from:
        sig_cond.append("received_at >= %s")
        sig_params.append(date_from + ' 00:00:00')
    if date_to:
        sig_cond.append("received_at <= %s")
        sig_params.append(date_to + ' 23:59:59')
    sig_df = ("AND " + " AND ".join(sig_cond)) if sig_cond else ""

    # Trade date filter — join signals so we can filter by received_at
    trd_join   = "JOIN signals s ON t.signal_id = s.signal_id" if sig_cond else ""
    trd_cond   = [c.replace("received_at", "s.received_at") for c in sig_cond]
    trd_df     = ("AND " + " AND ".join(trd_cond)) if trd_cond else ""
    trd_params = list(sig_params)

    conn = get_conn()
    with conn.cursor() as cur:
        cur.execute(f"SELECT COUNT(*) AS n FROM signals WHERE 1=1 {sig_df}", sig_params)
        total = cur.fetchone()["n"]

        cur.execute(f"SELECT COUNT(*) AS n FROM signals WHERE status = 'executed' {sig_df}", sig_params)
        executed = cur.fetchone()["n"]

        cur.execute(f"SELECT COUNT(*) AS n FROM signals WHERE status = 'skipped' {sig_df}", sig_params)
        skipped = cur.fetchone()["n"]

        cur.execute(f"SELECT COUNT(*) AS n FROM signals WHERE status = 'expired' {sig_df}", sig_params)
        expired = cur.fetchone()["n"]

        cur.execute(f"SELECT COUNT(*) AS n FROM trades t {trd_join} WHERE t.outcome = 'win' {trd_df}", trd_params)
        wins = cur.fetchone()["n"]

        cur.execute(f"SELECT COUNT(*) AS n FROM trades t {trd_join} WHERE t.outcome = 'loss' {trd_df}", trd_params)
        losses = cur.fetchone()["n"]

        cur.execute(f"SELECT COUNT(*) AS n FROM trades t {trd_join} WHERE t.outcome IS NULL {trd_df}", trd_params)
        open_trades = cur.fetchone()["n"]

        cur.execute(f"SELECT COALESCE(SUM(t.profit), 0) AS total FROM trades t {trd_join} WHERE t.outcome IS NOT NULL {trd_df}", trd_params)
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
    date_from = request.args.get('date_from')
    date_to   = request.args.get('date_to')

    cond, params = [], []
    if date_from:
        cond.append("s.received_at >= %s")
        params.append(date_from + ' 00:00:00')
    if date_to:
        cond.append("s.received_at <= %s")
        params.append(date_to + ' 23:59:59')
    where = ("WHERE " + " AND ".join(cond)) if cond else ""

    conn = get_conn()
    with conn.cursor() as cur:
        cur.execute(f"""
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
            {where}
            ORDER BY s.received_at DESC
            LIMIT 500
        """, params)
        rows = cur.fetchall()
    conn.close()

    result = []
    for r in rows:
        result.append({
            "signal_id":   r["signal_id"],
            "received_at": r["received_at"].strftime("%Y-%m-%d %H:%M") if r["received_at"] else None,
            "symbol":      r["symbol"],
            "direction":   r["direction"],
            "entry_low":   round(float(r["entry_low"]),  2),
            "entry_high":  round(float(r["entry_high"]), 2),
            "sl":          round(float(r["sl"]), 2),
            "tps":         [round(t, 2) for t in json.loads(r["tps"])] if r["tps"] else [],
            "status":      r["status"],
            "ticket":      r["ticket"],
            "lot":         round(float(r["lot"]), 2) if r["lot"] is not None else None,
            "entry_price": round(float(r["entry_price"]), 2) if r["entry_price"] is not None else None,
            "outcome":     r["outcome"],
            "profit":      round(float(r["profit"]), 2) if r["profit"] is not None else None,
            "close_price": round(float(r["close_price"]), 2) if r["close_price"] is not None else None,
            "closed_at":   r["closed_at"].strftime("%Y-%m-%d %H:%M") if r["closed_at"] else None,
            "entry_mode":  r["entry_mode"],
            "layer_num":   r["layer_num"],
        })

    return jsonify(result)


@app.route("/api/guards/config")
def api_guards_config():
    """Return guard thresholds from .env so the dashboard can display them."""
    return jsonify({
        "env_mode":  ENV_MODE,
        "margin":    {"threshold": f"≥ {MIN_MARGIN_LEVEL:.0f}%",    "enabled": True},
        "stack":     {"threshold": "Block same-direction stack",     "enabled": BLOCK_SAME_DIRECTION_STACK},
        "rr_ratio":  {"threshold": f"≥ {MIN_RR_RATIO:.1f}:1",       "enabled": True},
        "spread":    {"threshold": f"≤ {MAX_SPREAD_PIPS:.0f} pips",  "enabled": True},
        "proximity": {"threshold": f"≤ {ENTRY_MAX_DISTANCE_PIPS} pips", "enabled": True},
        "lot_calc":  {"threshold": f"{MIN_LOT}–{MAX_LOT} lot",       "enabled": True},
        "risk":      {"threshold": f"{int(RISK_PERCENT*100)}% free margin"},
        "auto_tp":   {"threshold": f"SL < {SL_MIN_PIPS}p => TP set to {TP_ENFORCE_PIPS}p"},
        "profit_lock": {
            "threshold": f"+{PROFIT_LOCK_PIPS}p → BE + TP {PROFIT_LOCK_TP_PIPS}p",
            "enabled": PROFIT_LOCK_ENABLED,
        },
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


@app.route("/api/zones")
def api_zones():
    """Return today's mapping zones + SNR levels."""
    zones = get_today_zones()
    # Collect unique symbols
    symbols = sorted(set(z["symbol"] for z in zones)) if zones else ["XAUUSD"]

    snr_data = {}
    for sym in symbols:
        snr_data[sym] = get_snr_levels(sym)

    zone_list = []
    for z in zones:
        zone_list.append({
            "id":        z["id"],
            "symbol":    z["symbol"],
            "direction": z["direction"],
            "zone_low":  round(float(z["zone_low"]), 2),
            "zone_high": round(float(z["zone_high"]), 2),
            "sl":        round(float(z["sl"]), 2),
            "tp":        round(float(z["tp"]), 2),
            "fired":     bool(z["fired"]),
            "signal_id": z["signal_id"],
        })

    return jsonify({
        "enabled":    MAP_ENABLED,
        "zones":      zone_list,
        "snr_levels": snr_data,
    })


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
            "fired_at":       r["fired_at"].strftime("%Y-%m-%d %H:%M") if r["fired_at"] else None,
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
