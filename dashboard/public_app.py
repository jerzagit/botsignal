"""
dashboard/public_app.py
Public-facing dashboard — deploy this on a VPS/cloud.
No MT5 credentials exposed. Token-protected.

Usage:
    set PUBLIC_ACCESS_TOKEN=mysecrettoken
    set PUBLIC_PORT=8080
    python dashboard/public_app.py

Then visit: http://your-vps:8080/?token=mysecrettoken
"""

import os
import json
import sys
import logging
from functools import wraps
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from flask import Flask, render_template, jsonify, request, redirect, url_for, session

from core.db import get_conn
from core.config import (
    MIN_MARGIN_LEVEL, MAX_SPREAD_PIPS, MIN_RR_RATIO,
    ENTRY_MAX_DISTANCE_PIPS, BLOCK_SAME_DIRECTION_STACK,
    SL_MIN_PIPS, TP_ENFORCE_PIPS, RISK_PERCENT, MIN_LOT, MAX_LOT,
    MT5_SYMBOL_SUFFIX, SL_PIP_SIZE, ENV_MODE, MAP_ENABLED,
    PROFIT_LOCK_ENABLED, PROFIT_LOCK_PIPS, PROFIT_LOCK_TP_PIPS,
    TRAIL_ENABLED, TRAIL_PIPS,
    SESSION_FILTER_ENABLED, SESSION_START_HOUR_UTC, SESSION_END_HOUR_UTC,
    LAYER_MODE, LAYER_COUNT,
    SIGNAL_SOURCES, SOURCE_RISK_MODE, SOURCE_CONFLICT_MODE, MAX_TOTAL_OPEN_RISK,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

app = Flask(__name__, template_folder="templates")
app.secret_key = os.getenv("FLASK_SECRET_KEY", os.urandom(24).hex())

ACCESS_TOKEN = os.getenv("PUBLIC_ACCESS_TOKEN", "")
if not ACCESS_TOKEN:
    log.warning("PUBLIC_ACCESS_TOKEN not set — using random token")
    ACCESS_TOKEN = os.urandom(16).hex()
    log.warning(f"Generated token: {ACCESS_TOKEN}")

PUBLIC_PORT = int(os.getenv("PUBLIC_PORT", "8080"))


def require_token(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        token = request.args.get("token") or session.get("token")
        if token != ACCESS_TOKEN:
            if request.path.startswith("/api/"):
                return jsonify({"error": "Unauthorized"}), 401
            return render_template("public_login.html", error=None)
        session["token"] = token
        return f(*args, **kwargs)
    return decorated



# ── Auth pages ────────────────────────────────────────────────────────────

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        token = request.form.get("token", "")
        if token == ACCESS_TOKEN:
            session["token"] = token
            return redirect(url_for("index"))
        return render_template("public_login.html", error="Invalid token")
    return render_template("public_login.html", error=None)


# ── Main page ─────────────────────────────────────────────────────────────

@app.route("/")
@require_token
def index():
    return render_template("index.html")


# ── API: Stats ────────────────────────────────────────────────────────────

@app.route("/api/stats")
@require_token
def api_stats():
    date_from = request.args.get("date_from")
    date_to   = request.args.get("date_to")

    cond, params = [], []
    if date_from:
        cond.append("received_at >= %s"); params.append(date_from + " 00:00:00")
    if date_to:
        cond.append("received_at <= %s"); params.append(date_to + " 23:59:59")
    df = ("AND " + " AND ".join(cond)) if cond else ""

    trd_cond = [c.replace("received_at", "s.received_at") for c in cond]
    trd_df = ("AND " + " AND ".join(trd_cond)) if trd_cond else ""
    trd_join = "JOIN signals s ON t.signal_id = s.signal_id" if cond else ""

    try:
        conn = get_conn()
        with conn.cursor() as cur:
            cur.execute(f"SELECT COUNT(*) AS n FROM signals WHERE 1=1 {df}", params)
            total = cur.fetchone()["n"]
            cur.execute(f"SELECT COUNT(*) AS n FROM signals WHERE status = 'executed' {df}", params)
            executed = cur.fetchone()["n"]
            cur.execute(f"SELECT COUNT(*) AS n FROM signals WHERE status = 'skipped' {df}", params)
            skipped = cur.fetchone()["n"]
            cur.execute(f"SELECT COUNT(*) AS n FROM signals WHERE status = 'expired' {df}", params)
            expired = cur.fetchone()["n"]
            cur.execute(f"SELECT COUNT(*) AS n FROM trades t {trd_join} WHERE t.outcome = 'win' {trd_df}", params)
            wins = cur.fetchone()["n"]
            cur.execute(f"SELECT COUNT(*) AS n FROM trades t {trd_join} WHERE t.outcome = 'loss' {trd_df}", params)
            losses = cur.fetchone()["n"]
            cur.execute(f"SELECT COUNT(*) AS n FROM trades t {trd_join} WHERE t.outcome IS NULL {trd_df}", params)
            open_trades = cur.fetchone()["n"]
            cur.execute(f"SELECT COALESCE(SUM(t.profit), 0) AS total FROM trades t {trd_join} WHERE t.outcome IS NOT NULL {trd_df}", params)
            total_profit = float(cur.fetchone()["total"])
        conn.close()
    except Exception as e:
        log.error(f"api_stats error: {e}")
        return jsonify({"error": str(e)}), 500

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


# ── API: Signals ──────────────────────────────────────────────────────────

@app.route("/api/signals")
@require_token
def api_signals():
    date_from = request.args.get("date_from")
    date_to   = request.args.get("date_to")

    cond, params = [], []
    if date_from:
        cond.append("s.received_at >= %s"); params.append(date_from + " 00:00:00")
    if date_to:
        cond.append("s.received_at <= %s"); params.append(date_to + " 23:59:59")
    where = ("WHERE " + " AND ".join(cond)) if cond else ""

    try:
        conn = get_conn()
        with conn.cursor() as cur:
            cur.execute(f"""
                SELECT s.signal_id, s.received_at, s.symbol, s.direction,
                       s.entry_low, s.entry_high, s.sl, s.tps, s.status,
                       s.source_id, s.source_name, s.parser_profile, s.source_risk_percent,
                       t.outcome, t.entry_mode, t.layer_num
                FROM signals s
                LEFT JOIN trades t ON s.signal_id = t.signal_id
                {where}
                ORDER BY s.received_at DESC
                LIMIT 500
            """, params)
            rows = cur.fetchall()
        conn.close()
    except Exception as e:
        log.error(f"api_signals error: {e}")
        return jsonify({"error": str(e)}), 500

    result = []
    for r in rows:
        signal_id = r["signal_id"]
        if signal_id and signal_id.startswith("manual_"):
            continue
        result.append({
            "signal_id":   signal_id,
            "received_at": r["received_at"].strftime("%Y-%m-%d %H:%M") if r["received_at"] else None,
            "symbol":      r["symbol"],
            "direction":   r["direction"],
            "entry_low":   round(float(r["entry_low"]),  2),
            "entry_high":  round(float(r["entry_high"]), 2),
            "sl":          round(float(r["sl"]), 2),
            "tps":         [round(t, 2) for t in json.loads(r["tps"])] if r["tps"] else [],
            "status":      r["status"],
            "source_id":   r["source_id"],
            "source_name": r["source_name"],
            "parser_profile": r["parser_profile"],
            "source_risk_percent": float(r["source_risk_percent"]) if r["source_risk_percent"] is not None else None,
            "outcome":     r["outcome"],
            "entry_mode":  r["entry_mode"],
            "layer_num":   r["layer_num"],
        })

    return jsonify(result)


# ── API: Guard config ─────────────────────────────────────────────────────

@app.route("/api/guards/config")
@require_token
def api_guards_config():
    start_myt = (SESSION_START_HOUR_UTC + 8) % 24
    end_myt   = (SESSION_END_HOUR_UTC   + 8) % 24

    return jsonify({
        "env_mode":  ENV_MODE,
        "session_filter": {
            "enabled":   SESSION_FILTER_ENABLED,
            "start_utc": SESSION_START_HOUR_UTC,
            "end_utc":   SESSION_END_HOUR_UTC,
            "threshold": f"{'ON' if SESSION_FILTER_ENABLED else 'OFF'} · {SESSION_START_HOUR_UTC:02d}:00–{SESSION_END_HOUR_UTC:02d}:00 UTC ({start_myt:02d}:00–{end_myt:02d}:00 MYT)",
        },
        "margin":    {"threshold": f"≥ {MIN_MARGIN_LEVEL:.0f}%",    "enabled": True},
        "stack":     {"threshold": "Block same-direction stack",     "enabled": BLOCK_SAME_DIRECTION_STACK},
        "rr_ratio":  {"threshold": f"≥ {MIN_RR_RATIO:.1f}:1",       "enabled": True},
        "spread":    {"threshold": f"≤ {MAX_SPREAD_PIPS:.0f} pips",  "enabled": True},
        "proximity": {"threshold": f"≤ {ENTRY_MAX_DISTANCE_PIPS} pips", "enabled": True},
        "lot_calc":  {"threshold": f"{MIN_LOT}–{MAX_LOT} lot",       "enabled": True},
        "source_risk": {
            "enabled": True,
            "mode": SOURCE_RISK_MODE,
            "conflict_mode": SOURCE_CONFLICT_MODE,
            "max_total_open_risk": MAX_TOTAL_OPEN_RISK,
            "sources": [
                {
                    "source_id": s.source_id,
                    "name": s.name,
                    "risk_percent": s.risk_percent,
                    "parser_profile": s.parser_profile,
                    "auto_execute": s.auto_execute,
                }
                for s in SIGNAL_SOURCES
            ],
            "threshold": f"{len(SIGNAL_SOURCES)} source(s) | global cap {MAX_TOTAL_OPEN_RISK*100:.1f}%",
        },
        "profit_lock": {
            "threshold": f"+{PROFIT_LOCK_PIPS}p → BE + TP {PROFIT_LOCK_TP_PIPS}p",
            "enabled":   PROFIT_LOCK_ENABLED,
        },
        "trail": {
            "enabled": TRAIL_ENABLED,
            "pips":    TRAIL_PIPS,
            "threshold": f"{'ON' if TRAIL_ENABLED else 'OFF'} · Trail SL every {TRAIL_PIPS}p",
        },
        "dca": {
            "enabled":    LAYER_MODE,
            "max_layers": LAYER_COUNT,
            "threshold":  f"{'ON' if LAYER_MODE else 'OFF'} · {LAYER_COUNT} layers",
        },
    })


# ── API: Guard log ────────────────────────────────────────────────────────

@app.route("/api/guards/log")
@require_token
def api_guards_log():
    try:
        conn = get_conn()
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, fired_at, guard_name, signal_id,
                       symbol, direction, source_id, reason, value_actual, value_required
                FROM guard_events ORDER BY fired_at DESC LIMIT 100
            """)
            rows = cur.fetchall()
        conn.close()
    except Exception as e:
        log.error(f"api_guards_log error: {e}")
        return jsonify({"error": str(e)}), 500

    result = []
    for r in rows:
        result.append({
            "id":             r["id"],
            "fired_at":       r["fired_at"].strftime("%Y-%m-%d %H:%M") if r["fired_at"] else None,
            "guard_name":     r["guard_name"],
            "signal_id":      r["signal_id"],
            "symbol":         r["symbol"],
            "direction":      r["direction"],
            "source_id":      r["source_id"],
            "reason":         r["reason"],
            "value_actual":   r["value_actual"],
            "value_required": r["value_required"],
        })
    return jsonify(result)


# ── API: Performance ──────────────────────────────────────────────────────

@app.route("/api/performance")
@require_token
def api_performance():
    try:
        conn = get_conn()
        with conn.cursor() as cur:
            cur.execute("""
                SELECT DATE(closed_at) AS day, profit
                FROM trades
                WHERE outcome IN ('win', 'loss')
                  AND closed_at >= DATE_SUB(NOW(), INTERVAL 30 DAY)
                ORDER BY day
            """)
            rows = cur.fetchall()
        conn.close()
    except Exception as e:
        log.error(f"api_performance error: {e}")
        return jsonify({"error": str(e)}), 500

    daily = {}
    for r in rows:
        d = r["day"].strftime("%Y-%m-%d")
        daily[d] = daily.get(d, 0) + float(r["profit"])

    days, pnl = [], []
    for i in range(29, -1, -1):
        dt = (datetime.now() - timedelta(days=i)).strftime("%Y-%m-%d")
        days.append(dt[5:])
        pnl.append(round(daily.get(dt, 0), 2))

    return jsonify({"labels": days, "pnl": pnl})


if __name__ == "__main__":
    log.info(f"Public dashboard starting on port {PUBLIC_PORT}")
    log.info(f"Access token: {ACCESS_TOKEN}")
    log.info(f"URL: http://localhost:{PUBLIC_PORT}/?token={ACCESS_TOKEN}")
    app.run(host="0.0.0.0", port=PUBLIC_PORT, debug=False)
