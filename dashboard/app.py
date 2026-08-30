"""
dashboard/app.py
SignalBot Web Dashboard — Flask app.
Behind Laragon: http://localhost/.../botsignal/ (PHP reverse proxy → :5000)
Direct: http://127.0.0.1:5000
"""

import json
import logging
import sys
import os
from datetime import timedelta

# Allow imports from project root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from flask import Flask, render_template, jsonify, request, redirect
from werkzeug.security import generate_password_hash

from core.db import get_conn, get_today_zones, get_snr_levels
from core.config import (
    MIN_MARGIN_LEVEL, MAX_SPREAD_PIPS, MIN_RR_RATIO,
    ENTRY_MAX_DISTANCE_PIPS, BLOCK_SAME_DIRECTION_STACK,
    SL_MIN_PIPS, TP_ENFORCE_PIPS, RISK_PERCENT, MIN_LOT, MAX_LOT,
    MT5_SYMBOL_SUFFIX, SL_PIP_SIZE, ENV_MODE, MAP_ENABLED,
    PROFIT_LOCK_ENABLED, PROFIT_LOCK_PIPS, PROFIT_LOCK_TP_PIPS,
    TRAIL_ENABLED, TRAIL_PIPS,
    SESSION_FILTER_ENABLED, SESSION_START_HOUR_UTC, SESSION_END_HOUR_UTC,
    LAYER_MODE, LAYER_COUNT, LAYER2_PIPS, MAX_SUB_SPLITS,
    L2_GAP_RATIO, L2_MIN_RUNWAY_PIPS, L1_LOT_RATIO,
    FIB_GUARD_ENABLED, FIB_MAX_RETRACEMENT,
    FIB_SCANNER_ENABLED, FIB_SCANNER_INTERVAL,
    TREND_ENABLED, TREND_INTERVAL,
    TREND_EMA_SHORT, TREND_EMA_LONG, TREND_RSI_PERIOD,
    MANUAL_SL_PIPS, MANUAL_TP1_PIPS, MANUAL_TP2_PIPS,
    MANUAL_SYMBOL, MANUAL_RISK_PERCENT,
    SIGNAL_SOURCES, SOURCE_RISK_MODE, SOURCE_CONFLICT_MODE, MAX_TOTAL_OPEN_RISK,
    DASHBOARD_POLLER_ENABLED, DASHBOARD_MT5_LIVE_ENABLED,
)
from core.env_editor import (
    SECTIONS,
    apply_settings_form,
    ensure_env_exists,
    list_runtime_options,
    load_env_for_ui,
    portal_needs_setup,
)
from dashboard.auth_util import (
    current_user,
    forwarded_prefix,
    get_or_create_secret_key,
    login_required,
    login_user,
    logout_user,
    path_for,
    set_portal_password,
    verify_login,
)
from dashboard.poller import start_poller

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)

ensure_env_exists()

app = Flask(__name__, template_folder="templates")
from dashboard.format_util import register_template_filters

register_template_filters(app)
app.secret_key = get_or_create_secret_key()
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=7)
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"


@app.before_request
def _configure_session_cookie_path():
    prefix = forwarded_prefix()
    app.config["SESSION_COOKIE_PATH"] = (prefix + "/") if prefix else "/"


@app.context_processor
def _inject_globals():
    prefix = forwarded_prefix()
    return {
        "base_path": prefix,
        "portal_user": current_user(),
        "env_mode": ENV_MODE,
    }


# ── Auth / setup ─────────────────────────────────────────────────────────────

@app.route("/setup", methods=["GET", "POST"])
def setup():
    if not portal_needs_setup():
        return redirect(path_for("/login"))
    error = None
    if request.method == "POST":
        username = (request.form.get("username") or "admin").strip()
        password = request.form.get("password") or ""
        confirm = request.form.get("confirm") or ""
        if len(password) < 8:
            error = "Password must be at least 8 characters."
        elif password != confirm:
            error = "Passwords do not match."
        else:
            set_portal_password(username, password)
            login_user(username)
            return redirect(path_for("/"))
    return render_template("setup.html", error=error)


@app.route("/login", methods=["GET", "POST"])
def login():
    if portal_needs_setup():
        return redirect(path_for("/setup"))
    if current_user():
        return redirect(path_for("/"))
    error = None
    if request.method == "POST":
        username = request.form.get("username") or ""
        password = request.form.get("password") or ""
        if verify_login(username, password):
            login_user(username.strip())
            nxt = request.args.get("next") or "/"
            if not nxt.startswith("/") or nxt.startswith("//"):
                nxt = "/"
            # nxt is Flask path; path_for adds prefix
            return redirect(path_for(nxt))
        error = "Invalid username or password."
    return render_template("login.html", error=error)


@app.route("/logout", methods=["POST", "GET"])
def logout():
    logout_user()
    return redirect(path_for("/login"))


@app.route("/settings", methods=["GET", "POST"])
@login_required
def settings():
    message = None
    error = None
    open_section = request.args.get("section") or ""
    if request.method == "POST":
        form = dict(request.form)
        section_id = (form.get("section_id") or "").strip() or None
        open_section = section_id or open_section
        for section in SECTIONS:
            if section_id and section.id != section_id:
                continue
            for field in section.fields:
                if field.type != "bool":
                    continue
                if field.key not in form:
                    form[field.key] = "false"
        ok, msg = apply_settings_form(
            form,
            password_hasher=generate_password_hash,
            section_id=section_id,
        )
        if ok:
            message = msg
            load_dotenv(override=True)
        else:
            error = msg
    return render_template(
        "settings.html",
        sections=SECTIONS,
        values=load_env_for_ui(),
        runtime_options=list_runtime_options(),
        open_section=open_section,
        message=message,
        error=error,
    )


def _configured_active_strategy() -> str:
    vals = load_env_for_ui()
    meta = vals.get("ACTIVE_STRATEGY") or {}
    raw = (meta.get("value") if isinstance(meta, dict) else meta) or ""
    from core.strategies.registry import resolve_strategy_name

    try:
        return resolve_strategy_name(str(raw).strip() or None)
    except Exception:
        return str(raw) or "breakout_retest_v1"


@app.route("/strategies", methods=["GET", "POST"])
@login_required
def strategies_page():
    from core.config import ACTIVE_STRATEGY as RUNTIME_STRATEGY
    from core.env_editor import update_env_values
    from core.strategies.registry import (
        get_strategy_info,
        list_strategy_info,
        resolve_strategy_name,
    )

    message = None
    error = None
    if request.method == "POST":
        chosen = (request.form.get("ACTIVE_STRATEGY") or "").strip()
        try:
            resolved = resolve_strategy_name(chosen)
            update_env_values({"ACTIVE_STRATEGY": resolved})
            load_dotenv(override=True)
            message = (
                f"Configured strategy saved as {resolved}. "
                "Restart the bot process to change the runtime strategy."
            )
        except Exception as exc:
            error = str(exc)

    infos = list_strategy_info()
    configured = _configured_active_strategy()
    try:
        runtime = resolve_strategy_name(RUNTIME_STRATEGY)
    except Exception:
        runtime = RUNTIME_STRATEGY
    detail_name = request.args.get("id") or configured
    try:
        detail = get_strategy_info(detail_name)
    except Exception:
        detail = infos[0] if infos else None

    from dashboard.service_control import bot_status

    return render_template(
        "strategies.html",
        strategies=infos,
        configured=configured,
        runtime=runtime,
        restart_required=(configured != runtime),
        detail=detail,
        message=message,
        error=error,
        bot_status=bot_status(),
        restart_result=None,
    )


@app.route("/system/restart-bot", methods=["POST"])
@login_required
def restart_bot_service():
    """Restart SignalBot (bot.py / Windows service). Does not kill MT5 or dashboard."""
    from dashboard.service_control import bot_status, restart_signalbot

    confirm = (request.form.get("confirm") or "").strip().lower()
    want_json = (
        request.accept_mimetypes.best == "application/json"
        or request.headers.get("X-Requested-With") == "XMLHttpRequest"
        or request.args.get("format") == "json"
    )
    if confirm not in ("1", "yes", "true", "restart"):
        msg = "Restart cancelled — confirmation required."
        if want_json:
            return jsonify({"ok": False, "message": msg}), 400
        return redirect(path_for("/strategies?restart_error=1"))

    result = restart_signalbot()
    # Prefer redirect back to strategies with flash-like query
    if want_json:
        return jsonify(result), (200 if result.get("ok") else 500)

    nxt = (request.form.get("next") or "").strip()
    if nxt.startswith("/") and not nxt.startswith("//"):
        q = "restart_ok=1" if result.get("ok") else "restart_error=1"
        return redirect(path_for(f"{nxt}?{q}"))

    # Re-render strategies with result
    from core.config import ACTIVE_STRATEGY as RUNTIME_STRATEGY
    from core.strategies.registry import (
        get_strategy_info,
        list_strategy_info,
        resolve_strategy_name,
    )

    infos = list_strategy_info()
    configured = _configured_active_strategy()
    try:
        runtime = resolve_strategy_name(RUNTIME_STRATEGY)
    except Exception:
        runtime = RUNTIME_STRATEGY
    detail_name = request.args.get("id") or configured
    try:
        detail = get_strategy_info(detail_name)
    except Exception:
        detail = infos[0] if infos else None

    return render_template(
        "strategies.html",
        strategies=infos,
        configured=configured,
        runtime=runtime,
        restart_required=(configured != runtime),
        detail=detail,
        message=result.get("message") if result.get("ok") else None,
        error=None if result.get("ok") else (result.get("message") or "Restart failed"),
        bot_status=bot_status(),
        restart_result=result,
    )


@app.route("/api/system/bot-status")
@login_required
def api_bot_status():
    from dashboard.service_control import bot_status

    return jsonify(bot_status())


@app.route("/backtests")
@login_required
def backtests_page():
    from backtest.catalog import list_run_dirs, summarize_run

    strategy_f = (request.args.get("strategy") or "").strip()
    symbol_f = (request.args.get("symbol") or "").strip().upper()
    run_type_f = (request.args.get("run_type") or "").strip()
    rows = [summarize_run(p) for p in list_run_dirs()]
    if strategy_f:
        rows = [r for r in rows if str(r.get("strategy")) == strategy_f]
    if symbol_f:
        rows = [r for r in rows if str(r.get("symbol")).upper() == symbol_f]
    if run_type_f:
        rows = [r for r in rows if str(r.get("run_type")) == run_type_f]
    strategies = sorted(
        {str(r.get("strategy")) for r in rows if r.get("strategy") and r.get("strategy") != "N/A"}
    )
    return render_template(
        "backtests.html",
        runs=rows,
        strategies=strategies,
        filter_strategy=strategy_f,
        filter_symbol=symbol_f,
        filter_run_type=run_type_f,
        launch_note="Backtest execution from dashboard is not implemented in this phase.",
    )


@app.route("/backtests/compare")
@login_required
def backtests_compare():
    from backtest.catalog import compare_runs, default_runs_root, list_run_dirs, summarize_run

    root = default_runs_root()
    a_id = (request.args.get("a") or "").strip()
    b_id = (request.args.get("b") or "").strip()
    runs = [summarize_run(p) for p in list_run_dirs()]
    comparison = None
    if a_id and b_id:
        pa, pb = root / a_id, root / b_id
        if pa.is_dir() and pb.is_dir():
            comparison = compare_runs(pa, pb)
    return render_template(
        "backtests_compare.html",
        runs=runs,
        a_id=a_id,
        b_id=b_id,
        comparison=comparison,
    )


@app.route("/backtests/<run_id>")
@login_required
def backtest_detail(run_id: str):
    from backtest.catalog import default_runs_root, load_run_detail

    run_dir = default_runs_root() / run_id
    if not run_dir.is_dir() or not (run_dir / "meta.json").is_file():
        return render_template("backtests_detail.html", missing=True, run_id=run_id), 404
    detail = load_run_detail(run_dir)
    return render_template("backtests_detail.html", missing=False, detail=detail)


# ── Dashboard pages / APIs ───────────────────────────────────────────────────

@app.route("/")
@login_required
def index():
    return render_template("index.html")


@app.route("/api/stats")
@login_required
def api_stats():
    date_from = request.args.get('date_from')
    date_to   = request.args.get('date_to')

    sig_cond, sig_params = [], []
    if date_from:
        sig_cond.append("received_at >= %s")
        sig_params.append(date_from + ' 00:00:00')
    if date_to:
        sig_cond.append("received_at <= %s")
        sig_params.append(date_to + ' 23:59:59')
    sig_df = ("AND " + " AND ".join(sig_cond)) if sig_cond else ""

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
@login_required
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
                s.source_id,
                s.source_name,
                s.parser_profile,
                s.source_risk_percent,
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
            "source_id":   r["source_id"],
            "source_name": r["source_name"],
            "parser_profile": r["parser_profile"],
            "source_risk_percent": float(r["source_risk_percent"]) if r["source_risk_percent"] is not None else None,
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
@login_required
def api_guards_config():
    start_myt = (SESSION_START_HOUR_UTC + 8) % 24
    end_myt   = (SESSION_END_HOUR_UTC   + 8) % 24

    return jsonify({
        "env_mode":  ENV_MODE,
        "session_filter": {
            "enabled":    SESSION_FILTER_ENABLED,
            "start_utc":  SESSION_START_HOUR_UTC,
            "end_utc":    SESSION_END_HOUR_UTC,
            "threshold":  f"{'ON' if SESSION_FILTER_ENABLED else 'OFF'} · {SESSION_START_HOUR_UTC:02d}:00–{SESSION_END_HOUR_UTC:02d}:00 UTC ({start_myt:02d}:00–{end_myt:02d}:00 MYT)",
        },
        "margin":    {"threshold": f"≥ {MIN_MARGIN_LEVEL:.0f}%",    "enabled": True},
        "stack":     {"threshold": "Block same-direction stack",     "enabled": BLOCK_SAME_DIRECTION_STACK},
        "rr_ratio":  {"threshold": f"≥ {MIN_RR_RATIO:.1f}:1",       "enabled": True},
        "spread":    {"threshold": f"≤ {MAX_SPREAD_PIPS:.0f} pips",  "enabled": True},
        "proximity": {"threshold": f"≤ {ENTRY_MAX_DISTANCE_PIPS} pips", "enabled": True},
        "lot_calc":  {"threshold": f"{MIN_LOT}–{MAX_LOT} lot",       "enabled": True},
        "risk":      {"threshold": "10% free margin @ 1000p benchmark"},
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
        "auto_tp":   {"threshold": f"SL < {SL_MIN_PIPS}p => TP set to {TP_ENFORCE_PIPS}p"},
        "profit_lock": {
            "threshold": f"+{PROFIT_LOCK_PIPS}p → BE + TP {PROFIT_LOCK_TP_PIPS}p",
            "enabled":   PROFIT_LOCK_ENABLED,
        },
        "trail": {
            "enabled":   TRAIL_ENABLED,
            "pips":      TRAIL_PIPS,
            "threshold": f"{'ON' if TRAIL_ENABLED else 'OFF'} · Trail SL every {TRAIL_PIPS}p",
        },
        "dca": {
            "enabled":        LAYER_MODE,
            "max_layers":     LAYER_COUNT,
            "layer_gap_pips": LAYER2_PIPS,
            "max_sub_splits": MAX_SUB_SPLITS,
            "min_lot":        MIN_LOT,
            "l1_lot_ratio":   L1_LOT_RATIO,
            "threshold":      f"{'ON' if LAYER_MODE else 'OFF'} · {LAYER_COUNT} layers · L1={int(L1_LOT_RATIO*100)}% / L2+={int((1-L1_LOT_RATIO)*100)}% · {MAX_SUB_SPLITS} splits",
        },
        "dynamic_gap": {
            "enabled":       L2_GAP_RATIO > 0,
            "gap_ratio":     L2_GAP_RATIO,
            "min_runway":    L2_MIN_RUNWAY_PIPS,
            "fallback_pips": LAYER2_PIPS,
            "threshold":     f"Gap = SL × {L2_GAP_RATIO} | Runway ≥ {L2_MIN_RUNWAY_PIPS}p" if L2_GAP_RATIO > 0 else f"Fixed {LAYER2_PIPS}p gap",
        },
        "trend": {
            "enabled":    TREND_ENABLED,
            "interval":   TREND_INTERVAL,
            "ema_short":  TREND_EMA_SHORT,
            "ema_long":   TREND_EMA_LONG,
            "rsi_period": TREND_RSI_PERIOD,
            "threshold":  f"{'ON' if TREND_ENABLED else 'OFF'} · EMA {TREND_EMA_SHORT}/{TREND_EMA_LONG} · RSI {TREND_RSI_PERIOD} · {TREND_INTERVAL}s",
        },
        "fib_guard": {
            "enabled":   FIB_GUARD_ENABLED,
            "max_retrace": f"{FIB_MAX_RETRACEMENT*100:.1f}%",
            "threshold": f"{'ON' if FIB_GUARD_ENABLED else 'OFF'} · 0–{FIB_MAX_RETRACEMENT*100:.0f}% H1 zone",
        },
        "fib_scanner": {
            "enabled":  FIB_SCANNER_ENABLED,
            "interval": FIB_SCANNER_INTERVAL,
            "threshold": f"{'ON' if FIB_SCANNER_ENABLED else 'OFF'} · scan every {FIB_SCANNER_INTERVAL}s",
        },
        "manual_trade": {
            "symbol":      MANUAL_SYMBOL,
            "sl_pips":     MANUAL_SL_PIPS,
            "tp1_pips":    MANUAL_TP1_PIPS,
            "tp2_pips":    MANUAL_TP2_PIPS,
            "risk_pct":    int(MANUAL_RISK_PERCENT * 100),
            "threshold":   f"{MANUAL_SYMBOL} · SL {MANUAL_SL_PIPS}p · TP {MANUAL_TP1_PIPS}/{MANUAL_TP2_PIPS}p · {int(MANUAL_RISK_PERCENT*100)}% risk",
        },
    })


@app.route("/api/guards/live")
@login_required
def api_guards_live():
    if not DASHBOARD_MT5_LIVE_ENABLED:
        return jsonify({
            "disabled": True,
            "error": "Dashboard MT5 live probe disabled",
            "margin_level": None,
            "margin_level_ok": None,
            "spread_pips": None,
            "spread_ok": None,
            "balance": None,
            "equity": None,
            "free_margin": None,
            "dca_estimate": None,
        })

    try:
        import MetaTrader5 as mt5
        from core.mt5 import mt5_connect

        if not mt5_connect():
            return jsonify({"error": "MT5 unavailable"}), 503
        acc  = mt5.account_info()
        symbol = "XAUUSD" + MT5_SYMBOL_SUFFIX
        tick = mt5.symbol_info_tick(symbol)

        spread_pips = round((tick.ask - tick.bid) / SL_PIP_SIZE, 2) if tick else None
        margin_level = round(acc.margin_level, 1) if acc and acc.margin > 0 else None

        dca_estimate = None
        if acc and LAYER_MODE:
            free = acc.margin_free
            est_total_lot = round(free * RISK_PERCENT / (50 * 10), 2)
            if est_total_lot >= MIN_LOT:
                sl_pips_est  = 50
                gap_est      = max(1, int(sl_pips_est * L2_GAP_RATIO))
                safe_steps   = int((sl_pips_est - 1) / gap_est)
                est_layers   = max(1, min(LAYER_COUNT, min(int(est_total_lot / MIN_LOT), 1 + safe_steps)))
                if est_layers == 1:
                    l1_lot = est_total_lot
                    ln_lot = est_total_lot
                else:
                    l1_lot = max(MIN_LOT, round(est_total_lot * L1_LOT_RATIO, 2))
                    ln_lot = max(MIN_LOT, round((est_total_lot - l1_lot) / (est_layers - 1), 2))
                l1_splits = min(max(1, int(l1_lot / MIN_LOT)), MAX_SUB_SPLITS)
                ln_splits = min(max(1, int(ln_lot / MIN_LOT)), MAX_SUB_SPLITS)
                dca_estimate = {
                    "total_lot":     est_total_lot,
                    "layers":        est_layers,
                    "l1_lot":        l1_lot,
                    "ln_lot":        ln_lot,
                    "l1_splits":     l1_splits,
                    "ln_splits":     ln_splits,
                    "splits":        l1_splits,
                    "sub_lot":       max(MIN_LOT, round(l1_lot / l1_splits, 2)),
                    "total_orders":  l1_splits + (est_layers - 1) * ln_splits if est_layers > 1 else l1_splits,
                }

        return jsonify({
            "margin_level":    margin_level,
            "margin_level_ok": margin_level is None or margin_level >= MIN_MARGIN_LEVEL,
            "spread_pips":     spread_pips,
            "spread_ok":       spread_pips is None or spread_pips <= MAX_SPREAD_PIPS,
            "balance":         round(acc.balance, 2) if acc else None,
            "equity":          round(acc.equity, 2)  if acc else None,
            "free_margin":     round(acc.margin_free, 2) if acc else None,
            "dca_estimate":    dca_estimate,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/zones")
@login_required
def api_zones():
    zones = get_today_zones()
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
@login_required
def api_guards_log():
    conn = get_conn()
    with conn.cursor() as cur:
        cur.execute("""
            SELECT id, fired_at, guard_name, signal_id,
                   symbol, direction, source_id, reason, value_actual, value_required
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
            "source_id":      r["source_id"],
            "reason":         r["reason"],
            "value_actual":   r["value_actual"],
            "value_required": r["value_required"],
        })
    return jsonify(result)


@app.route("/api/performance")
@login_required
def api_performance():
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

    daily = {}
    for r in rows:
        d = r["day"].strftime("%Y-%m-%d")
        daily[d] = daily.get(d, 0) + float(r["profit"])

    from datetime import datetime, timedelta
    days, pnl = [], []
    for i in range(29, -1, -1):
        dt = (datetime.now() - timedelta(days=i)).strftime("%Y-%m-%d")
        days.append(dt[5:])
        pnl.append(round(daily.get(dt, 0), 2))

    return jsonify({"labels": days, "pnl": pnl})


if __name__ == "__main__":
    if DASHBOARD_POLLER_ENABLED:
        start_poller()
    else:
        logging.info("Dashboard poller disabled; MT5 will not be touched by dashboard startup.")
    logging.info("Dashboard listening on http://127.0.0.1:5000 (use Laragon URL via PHP proxy)")
    app.run(host="0.0.0.0", port=5000, debug=False)
