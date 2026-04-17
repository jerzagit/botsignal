"""
core/mt5.py
MT5 connection management and trade execution.
"""

import logging
import json
import time
import datetime
from pathlib import Path

import MetaTrader5 as mt5

from core.config import MT5_PATH, MT5_LOGIN, MT5_PASSWORD, MT5_SERVER, MT5_SYMBOL_SUFFIX, \
                       SL_PIP_SIZE, ENTRY_MAX_DISTANCE_PIPS, MIN_MARGIN_LEVEL, \
                       MAX_SPREAD_PIPS, MIN_RR_RATIO, BLOCK_SAME_DIRECTION_STACK, \
                       TRADE_SPLIT, MIN_LOT, SL_MIN_PIPS, TP_ENFORCE_PIPS, \
                       SESSION_FILTER_ENABLED, SESSION_START_HOUR_UTC, SESSION_END_HOUR_UTC
from core.signal import Signal
from core.risk   import calculate_lot

log = logging.getLogger(__name__)
TRADE_LOG = Path("data/trades.json")


def _fire_guard(guard_name: str, signal: Signal, signal_id: str,
                reason: str, actual: str = "", required: str = ""):
    """Write a guard block event to the DB (best-effort, never raises)."""
    try:
        from core.db import record_guard_event
        record_guard_event(guard_name, signal_id or "",
                           signal.symbol, signal.direction,
                           reason, actual, required)
    except Exception as e:
        log.warning(f"_fire_guard log failed: {e}")


# ── Connection ────────────────────────────────────────────────────────────────

def mt5_connect() -> bool:
    """Connect and login to MT5. Returns True on success."""
    kwargs = {"path": MT5_PATH} if MT5_PATH else {}
    if not mt5.initialize(**kwargs):
        log.error(f"MT5 initialize() failed: {mt5.last_error()}")
        return False
    if not mt5.login(MT5_LOGIN, password=MT5_PASSWORD, server=MT5_SERVER):
        log.error(f"MT5 login failed: {mt5.last_error()}")
        mt5.shutdown()
        return False
    return True


def mt5_connect_test() -> tuple[bool, str]:
    """Startup sanity check — returns (ok, message)."""
    if not mt5_connect():
        return False, f"Login failed: {mt5.last_error()}"
    info = mt5.account_info()
    msg = (
        f"Connected as #{info.login} | "
        f"Balance: ${info.balance:,.2f} | "
        f"Free margin: ${info.margin_free:,.2f}"
    )
    mt5.shutdown()
    return True, msg


# ── Trade execution ───────────────────────────────────────────────────────────

def execute_trade(signal: Signal, signal_id: str = None,
                  lot_override: float = None,
                  own_tickets: list = None,
                  tp_override: float = None,
                  skip_proximity: bool = False,
                  entry_mode: str = None,
                  layer_num: int = None,
                  skip_rr_check: bool = False,
                  skip_session: bool = False) -> str:
    """
    Place a market order on MT5 for the given signal.

    Extra params (all optional, for layered DCA mode):
      lot_override   – use this lot instead of calculate_lot()
      own_tickets    – exempt these tickets from the stack guard (own session layers)
      tp_override    – place a single order with this TP (skips TRADE_SPLIT loop)
      skip_proximity – skip proximity guard (L2+ are intentionally outside zone)
      entry_mode     – 'layered_dca' | 'direct' | None (stored in DB for dashboard)
      layer_num      – 1/2/3… for DCA layers; None for direct trades
      skip_session   – skip session filter (manual /buynow /sellnow)

    Returns a human-readable result message.
    """
    # ── GUARD 0: Session filter — block new entries outside London/NY ─────────
    if SESSION_FILTER_ENABLED and not skip_session:
        h = datetime.datetime.utcnow().hour
        if SESSION_START_HOUR_UTC <= SESSION_END_HOUR_UTC:
            in_session = SESSION_START_HOUR_UTC <= h < SESSION_END_HOUR_UTC
        else:   # wraps midnight e.g. 22:00–06:00
            in_session = h >= SESSION_START_HOUR_UTC or h < SESSION_END_HOUR_UTC
        if not in_session:
            utc_now = datetime.datetime.utcnow().strftime("%H:%M UTC")
            return (
                f"⏰ *Trade blocked — outside trading session*\n"
                f"Current time: `{utc_now}` | Session: "
                f"`{SESSION_START_HOUR_UTC:02d}:00–{SESSION_END_HOUR_UTC:02d}:00 UTC`\n"
                f"_3:00 PM–5:00 AM MYT (London + NY only)_"
            )

    # ── GUARD X: Max daily loss circuit breaker ───────────────────────────────
    if MAX_DAILY_LOSS_USD > 0:
        from core.state import get_daily_loss
        if get_daily_loss() >= MAX_DAILY_LOSS_USD:
            return (
                f"🛑 *Trading blocked — daily loss limit reached*\n"
                f"Today's loss: `${get_daily_loss():.2f}` | Limit: `$MAX_DAILY_LOSS_USD`\n"
                f"_Wait for reset at midnight or close some positions._"
            )

    if not mt5_connect():
        return "❌ Could not connect to MT5."

    symbol = signal.symbol + MT5_SYMBOL_SUFFIX

    # ── GUARD 1: Account info ─────────────────────────────────────────────────
    account = mt5.account_info()
    if account is None:
        mt5.shutdown()
        return "❌ Could not retrieve account info from MT5."

    # ── GUARD 2: Margin level — must be above MIN_MARGIN_LEVEL ───────────────
    # Skipped when margin=0 (no open trades yet — first trade always allowed)
    if account.margin > 0 and account.margin_level < MIN_MARGIN_LEVEL:
        _fire_guard("margin", signal, signal_id,
                    "Margin level too low",
                    f"{account.margin_level:.1f}%", f"≥{MIN_MARGIN_LEVEL:.0f}%")
        mt5.shutdown()
        return (
            f"❌ *Trade blocked — margin level too low*\n"
            f"Current: `{account.margin_level:.1f}%` | Required: `≥ {MIN_MARGIN_LEVEL:.0f}%`\n"
            f"_Close some open positions to free up margin._"
        )

    # ── GUARD 3: Same-direction stack — don't double up on small account ─────
    # Exception: positions already at breakeven (SL == entry price) are free
    # trades — no capital at risk, so new entries are allowed alongside them.
    if BLOCK_SAME_DIRECTION_STACK:
        existing  = mt5.positions_get(symbol=symbol) or []
        same_type = mt5.ORDER_TYPE_BUY if signal.direction == "buy" else mt5.ORDER_TYPE_SELL
        stacked   = [p for p in existing if p.type == same_type]
        # Exempt our own layer positions from the stack check
        if own_tickets:
            stacked = [p for p in stacked if p.ticket not in own_tickets]
        # Filter out breakeven positions (SL moved to entry price)
        at_risk   = [p for p in stacked if round(p.sl, 2) != round(p.price_open, 2)]
        if at_risk:
            _fire_guard("stack", signal, signal_id,
                        f"{len(at_risk)} same-direction position(s) at risk",
                        f"{len(at_risk)} at risk", "0 at risk")
            mt5.shutdown()
            return (
                f"⚠️ *Trade blocked — already have {len(at_risk)} "
                f"{signal.direction.upper()} position(s) at risk on `{symbol}`*\n"
                f"Stacking same direction doubles your exposure.\n"
                f"_Close existing trades or move them to breakeven first._"
            )
        # Check own DCA layers count limit
        if own_tickets and MAX_DCA_LAYERS_PER_SYMBOL > 0:
            own_layers = len(own_tickets)
            if own_layers >= MAX_DCA_LAYERS_PER_SYMBOL:
                mt5.shutdown()
                return (
                    f"⚠️ *Trade blocked — DCA layer limit reached*\n"
                    f"Already have {own_layers} layer(s) for this signal. "
                    f"Max: {MAX_DCA_LAYERS_PER_SYMBOL}\n"
                    f"_Wait for some layers to close before adding more._"
                )

    # ── GUARD 4: Auto-adjust TP + RR ratio check ─────────────────────────────
    sl_distance  = abs(signal.entry_mid - signal.sl)
    sl_pips_calc = sl_distance / SL_PIP_SIZE

    # Build effective TP list — override if Hafiz's SL is tight (< SL_MIN_PIPS)
    effective_tps = list(signal.tps)
    tp_override_note = ""
    if sl_pips_calc < SL_MIN_PIPS:
        min_tp_pts = TP_ENFORCE_PIPS * SL_PIP_SIZE
        new_tps = []
        for t in effective_tps:
            tp_dist = abs(t - signal.entry_mid)
            if tp_dist < min_tp_pts:
                adjusted = round(
                    signal.entry_mid + min_tp_pts if signal.direction == "buy"
                    else signal.entry_mid - min_tp_pts, 2
                )
                new_tps.append(adjusted)
            else:
                new_tps.append(t)
        if new_tps != effective_tps:
            tp_override_note = (
                f"\n⚙️ _SL tight ({sl_pips_calc:.0f} pips) — "
                f"TP auto-adjusted to {TP_ENFORCE_PIPS} pips_"
            )
        effective_tps = new_tps

    tp_distance = abs(effective_tps[0] - signal.entry_mid) if effective_tps else 0
    rr_ratio    = tp_distance / sl_distance if sl_distance > 0 else 0
    if not skip_rr_check and rr_ratio < MIN_RR_RATIO:
        sl_pips = sl_distance / SL_PIP_SIZE
        tp_pips = tp_distance / SL_PIP_SIZE
        _fire_guard("rr_ratio", signal, signal_id,
                    f"RR ratio too low — SL {sl_pips:.0f}p / TP {tp_pips:.0f}p",
                    f"{rr_ratio:.2f}:1", f"≥{MIN_RR_RATIO:.1f}:1")
        mt5.shutdown()
        return (
            f"⚠️ *Trade blocked — poor reward:risk ratio*\n"
            f"SL: `{sl_pips:.0f} pips` | TP1: `{tp_pips:.0f} pips` | "
            f"Ratio: `{rr_ratio:.2f}:1`\n"
            f"_Minimum required: `{MIN_RR_RATIO:.1f}:1` — not worth the risk._"
        )

    # Ensure symbol is visible in Market Watch
    info = mt5.symbol_info(symbol)
    if info is None:
        mt5.shutdown()
        return f"❌ Symbol `{symbol}` not found in MT5."
    if not info.visible:
        mt5.symbol_select(symbol, True)

    # Current market price
    tick = mt5.symbol_info_tick(symbol)
    if tick is None:
        mt5.shutdown()
        return "❌ Could not fetch current price from MT5."

    order_type = mt5.ORDER_TYPE_BUY if signal.direction == "buy" else mt5.ORDER_TYPE_SELL
    price      = tick.ask if signal.direction == "buy" else tick.bid
    tp         = signal.tps[0]

    # ── GUARD 5: Spread — block if broker spread is unusually wide ───────────
    spread_pips = (tick.ask - tick.bid) / SL_PIP_SIZE
    if spread_pips > MAX_SPREAD_PIPS:
        _fire_guard("spread", signal, signal_id,
                    "Spread too wide",
                    f"{spread_pips:.1f} pips", f"≤{MAX_SPREAD_PIPS:.0f} pips")
        mt5.shutdown()
        return (
            f"⏳ *Trade blocked — spread too wide*\n"
            f"Current spread: `{spread_pips:.1f} pips` | Max allowed: `{MAX_SPREAD_PIPS:.0f} pips`\n"
            f"_Likely news event or off-hours. Wait for spread to normalise._"
        )

    # ── GUARD 6: Entry proximity — price must be near Hafiz's entry zone ─────
    # Skipped for L2+ layers (they are intentionally outside zone by design)
    if not skip_proximity:
        distance_pts  = max(0.0, max(signal.entry_low - price, price - signal.entry_high))
        distance_pips = distance_pts / SL_PIP_SIZE
        if distance_pips > ENTRY_MAX_DISTANCE_PIPS:
            _fire_guard("proximity", signal, signal_id,
                        f"Price {price} too far from zone {signal.entry_low}–{signal.entry_high}",
                        f"{distance_pips:.0f} pips away", f"≤{ENTRY_MAX_DISTANCE_PIPS} pips")
            mt5.shutdown()
            zone_str = (
                f"{signal.entry_low}"
                if signal.entry_low == signal.entry_high
                else f"{signal.entry_low}–{signal.entry_high}"
            )
            return (
                f"⏳ *Trade skipped — price too far from entry zone*\n"
                f"Current price: `{price}` | Entry zone: `{zone_str}`\n"
                f"Distance: `{distance_pips:.0f} pips` (max: `{ENTRY_MAX_DISTANCE_PIPS} pips`)\n"
                f"_Signal came early — tap again when price is closer._"
            )

    # Auto lot size from risk % (or use pre-calculated layer lot)
    if lot_override is not None:
        lot = lot_override
        lot_explanation = f"📦 Lot: `{lot}` (layer)"
    else:
        lot, lot_explanation = calculate_lot(signal)
    if lot == 0.0:
        _fire_guard("lot_calc", signal, signal_id,
                    lot_explanation, "0.00 lot", f"≥{MIN_LOT}")
        mt5.shutdown()
        return f"❌ Lot calculation failed:\n{lot_explanation}"

    direction_emoji = "🔴" if signal.direction == "sell" else "🟢"

    # ── Manual trade: re-anchor SL/TP to actual fill price ───────────────────
    # /buynow and /sellnow fix SL/TP at command time. If price moves before the
    # 30-sec watcher fires, SL can end up on the wrong side → MT5 rejects it.
    # Re-anchor using the same pip distance, relative to actual execution price.
    actual_sl = signal.sl
    actual_tp_override = tp_override
    if entry_mode == "manual" and signal.entry_mid:
        sl_pts = abs(signal.entry_mid - signal.sl)
        if signal.direction == "sell":
            actual_sl = round(price + sl_pts, 2)
            if tp_override and tp_override != 0.0:
                tp_pts = abs(signal.entry_mid - tp_override)
                actual_tp_override = round(price - tp_pts, 2)
        else:
            actual_sl = round(price - sl_pts, 2)
            if tp_override and tp_override != 0.0:
                tp_pts = abs(tp_override - signal.entry_mid)
                actual_tp_override = round(price + tp_pts, 2)

    # ── Single-order mode (used by layer watcher — tp_override provided) ─────
    if tp_override is not None:
        req = {
            "action":       mt5.TRADE_ACTION_DEAL,
            "symbol":       symbol,
            "volume":       lot,
            "type":         order_type,
            "price":        price,
            "sl":           actual_sl,
            "tp":           actual_tp_override,
            "deviation":    20,
            "magic":        20250101,
            "comment":      "SignalBot Layer",
            "type_time":    mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }
        r = mt5.order_send(req)
        mt5.shutdown()
        if r.retcode != mt5.TRADE_RETCODE_DONE:
            log.error(
                f"Layer order FAILED | {symbol} {signal.direction.upper()} "
                f"price={price} sl={actual_sl} tp={actual_tp_override} lot={lot} "
                f"retcode={r.retcode} comment={r.comment!r} "
                f"entry_mode={entry_mode}"
            )
            return f"❌ Layer order failed: `{r.comment}` (code `{r.retcode}`)"
        from core.db import record_trade
        _log_trade(signal, lot, price, r.order)
        if signal_id:
            record_trade(signal_id, r.order, lot, price, entry_mode, layer_num)
        return (
            f"✅ *Trade Executed!*\n\n"
            f"{direction_emoji} `{symbol} {signal.direction.upper()}`\n"
            f"Entry: `{price}` | SL: `{actual_sl}` | TP: `{actual_tp_override}`\n"
            f"Lot: `{lot}` | Ticket: `#{r.order}`"
            f"{tp_override_note}"
        )

    # ── Split lot into TRADE_SPLIT equal positions ────────────────────────────
    # Cap splits so total risk never exceeds the calculated lot.
    # e.g. lot=0.01, TRADE_SPLIT=5 ->only 1 split (can't split below MIN_LOT)
    vol_step      = info.volume_step
    actual_splits = max(1, min(TRADE_SPLIT, int(lot / MIN_LOT)))
    split_lot     = round(round(lot / actual_splits / vol_step) * vol_step, 2)
    split_lot     = max(MIN_LOT, split_lot)

    tickets = []
    failed  = []

    for i in range(actual_splits):
        # Assign TPs across splits — use effective_tps (may be auto-adjusted)
        split_tp = effective_tps[i % len(effective_tps)] if effective_tps else tp
        # For manual trades, re-anchor TP to actual fill price
        if entry_mode == "manual" and signal.entry_mid and split_tp:
            tp_pts = abs(split_tp - signal.entry_mid)
            split_tp = round(
                price + tp_pts if signal.direction == "buy" else price - tp_pts, 2
            )
        req = {
            "action":       mt5.TRADE_ACTION_DEAL,
            "symbol":       symbol,
            "volume":       split_lot,
            "type":         order_type,
            "price":        price,
            "sl":           actual_sl,
            "tp":           split_tp,
            "deviation":    20,
            "magic":        20250101,
            "comment":      f"SignalBot {i+1}/{TRADE_SPLIT}",
            "type_time":    mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }
        r = mt5.order_send(req)
        if r.retcode == mt5.TRADE_RETCODE_DONE:
            tickets.append((r.order, split_tp))
        else:
            failed.append(f"#{i+1}: {r.comment}")

    mt5.shutdown()

    if not tickets:
        return (
            f"❌ *All {TRADE_SPLIT} orders failed*\n"
            + "\n".join(failed)
        )

    # Log all filled tickets
    from core.db import record_trade
    for ticket, _ in tickets:
        _log_trade(signal, split_lot, price, ticket)
        if signal_id:
            record_trade(signal_id, ticket, split_lot, price, entry_mode, layer_num)

    tps_str  = " ->".join(str(t) for t in signal.tps)
    tick_lines = "\n".join(
        f"  `#{t}` ->TP `{tp_val}`" for t, tp_val in tickets
    )
    failed_str = ("\n⚠️ *Failed:* " + ", ".join(failed)) if failed else ""

    return (
        f"✅ *Trade Executed! ({len(tickets)}/{TRADE_SPLIT} filled)*\n\n"
        f"{direction_emoji} `{symbol} {signal.direction.upper()}`\n"
        f"Entry: `{price}` | SL: `{signal.sl}`\n"
        f"TPs: `{tps_str}`\n\n"
        f"{lot_explanation}\n"
        f"📦 Split: `{actual_splits} × {split_lot} lot`"
        + (f" _(reduced from {TRADE_SPLIT} — margin too small to split further)_" if actual_splits < TRADE_SPLIT else "")
        + "\n\n"
        f"🎫 Tickets:\n{tick_lines}{failed_str}{tp_override_note}"
    )


# ── Close position ────────────────────────────────────────────────────────────

def close_position(ticket: int) -> str:
    """Close an open position by ticket number at market price."""
    if not mt5_connect():
        return "❌ Could not connect to MT5."

    positions = mt5.positions_get(ticket=ticket)
    if not positions:
        mt5.shutdown()
        return f"❌ Position #{ticket} not found (may already be closed)."

    pos   = positions[0]
    tick  = mt5.symbol_info_tick(pos.symbol)
    if tick is None:
        mt5.shutdown()
        return f"❌ Could not get price for {pos.symbol}."

    # To close: BUY position ->sell; SELL position ->buy
    order_type = mt5.ORDER_TYPE_SELL if pos.type == 0 else mt5.ORDER_TYPE_BUY
    price      = tick.bid if pos.type == 0 else tick.ask

    request = {
        "action":       mt5.TRADE_ACTION_DEAL,
        "symbol":       pos.symbol,
        "volume":       pos.volume,
        "type":         order_type,
        "position":     ticket,
        "price":        price,
        "deviation":    20,
        "magic":        20250101,
        "comment":      "SignalBot Close",
        "type_time":    mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,
    }

    result = mt5.order_send(request)
    mt5.shutdown()

    if result.retcode != mt5.TRADE_RETCODE_DONE:
        return f"❌ Close failed: `{result.comment}` (code `{result.retcode}`)"

    direction = "BUY" if pos.type == 0 else "SELL"
    return (
        f"✅ *Position Closed*\n"
        f"`{pos.symbol} {direction}` | Ticket: `#{ticket}`\n"
        f"Closed @ `{price}` | Lot: `{pos.volume}`"
    )


def set_breakeven(ticket: int) -> str:
    """Move a position's SL to its entry price (breakeven)."""
    if not mt5_connect():
        return "❌ Could not connect to MT5."

    positions = mt5.positions_get(ticket=ticket)
    if not positions:
        mt5.shutdown()
        return f"❌ Position #{ticket} not found."

    pos = positions[0]
    entry = pos.price_open

    request = {
        "action":   mt5.TRADE_ACTION_SLTP,
        "position": ticket,
        "sl":       entry,
        "tp":       pos.tp,
    }

    result = mt5.order_send(request)
    mt5.shutdown()

    if result.retcode != mt5.TRADE_RETCODE_DONE:
        return f"❌ Breakeven failed #{ticket}: `{result.comment}`"

    direction = "BUY" if pos.type == 0 else "SELL"
    return f"🔒 Breakeven set — `{pos.symbol} {direction}` #{ticket} SL ->`{entry}`"


def modify_sl_tp(ticket: int, new_sl: float = None, new_tp: float = None) -> str:
    """Modify SL and/or TP on an open position. Keeps existing value for any param not provided."""
    if not mt5_connect():
        return "❌ Could not connect to MT5."

    positions = mt5.positions_get(ticket=ticket)
    if not positions:
        mt5.shutdown()
        return f"❌ Position #{ticket} not found."

    pos = positions[0]
    request = {
        "action":   mt5.TRADE_ACTION_SLTP,
        "position": ticket,
        "symbol":   pos.symbol,
        "sl":       new_sl if new_sl is not None else pos.sl,
        "tp":       new_tp if new_tp is not None else pos.tp,
    }

    result = mt5.order_send(request)
    mt5.shutdown()

    if result.retcode != mt5.TRADE_RETCODE_DONE:
        return f"❌ Modify failed #{ticket}: {result.comment}"

    direction = "BUY" if pos.type == 0 else "SELL"
    return f"✅ Modified {pos.symbol} {direction} #{ticket} SL->{request['sl']} TP->{request['tp']}"


def get_open_signal_groups(symbol: str = None) -> list:
    """
    Return open MT5 positions grouped by their signal_id from MySQL.
    Each group is a dict with signal info + list of open positions + total P&L.

    If symbol is given, filter to that symbol only.
    Positions not in MySQL (manual trades) are grouped under signal_id=None.
    """
    if not mt5_connect():
        return []

    # Get all open positions from MT5
    all_positions = mt5.positions_get()
    mt5.shutdown()

    if not all_positions:
        return []

    if symbol:
        suffix = MT5_SYMBOL_SUFFIX
        target = symbol.upper() + suffix
        all_positions = [p for p in all_positions if p.symbol == target]

    if not all_positions:
        return []

    # Build ticket ->position map
    ticket_map = {p.ticket: p for p in all_positions}
    open_tickets = list(ticket_map.keys())

    # Look up these tickets in MySQL to get signal groupings
    groups = {}   # signal_id ->{signal_info, positions[]}

    try:
        from core.db import get_conn
        conn = get_conn()
        placeholders = ",".join(["%s"] * len(open_tickets))
        with conn.cursor() as cur:
            cur.execute(f"""
                SELECT t.ticket, t.signal_id, t.lot, t.entry_price,
                       s.received_at, s.symbol, s.direction,
                       s.entry_low, s.entry_high, s.sl, s.tps
                FROM trades t
                JOIN signals s ON t.signal_id = s.signal_id
                WHERE t.ticket IN ({placeholders}) AND t.outcome IS NULL
            """, open_tickets)
            rows = cur.fetchall()
        conn.close()

        for row in rows:
            sid = row["signal_id"]
            if sid not in groups:
                groups[sid] = {
                    "signal_id":  sid,
                    "received_at": row["received_at"],
                    "symbol":     row["symbol"],
                    "direction":  row["direction"],
                    "entry_low":  float(row["entry_low"]),
                    "entry_high": float(row["entry_high"]),
                    "sl":         float(row["sl"]),
                    "positions":  [],
                    "total_pnl":  0.0,
                }
            pos = ticket_map[row["ticket"]]
            groups[sid]["positions"].append(pos)
            groups[sid]["total_pnl"] = round(
                groups[sid]["total_pnl"] + pos.profit, 2
            )
            del ticket_map[row["ticket"]]

    except Exception as e:
        log.error(f"get_open_signal_groups DB error: {e}")

    # Any remaining tickets (not in MySQL = manual trades)
    for ticket, pos in ticket_map.items():
        sid = f"manual_{ticket}"
        groups[sid] = {
            "signal_id":  sid,
            "received_at": None,
            "symbol":     pos.symbol,
            "direction":  "buy" if pos.type == 0 else "sell",
            "entry_low":  pos.price_open,
            "entry_high": pos.price_open,
            "sl":         pos.sl,
            "positions":  [pos],
            "total_pnl":  round(pos.profit, 2),
        }

    return list(groups.values())


# ── Trade log (feeds dashboard later) ────────────────────────────────────────

def _log_trade(signal: Signal, lot: float, entry: float, ticket: int):
    TRADE_LOG.parent.mkdir(exist_ok=True)
    trades = []
    if TRADE_LOG.exists():
        try:
            trades = json.loads(TRADE_LOG.read_text())
        except Exception:
            trades = []

    trades.append({
        "ticket":    ticket,
        "symbol":    signal.symbol,
        "direction": signal.direction,
        "entry":     entry,
        "sl":        signal.sl,
        "tps":       signal.tps,
        "lot":       lot,
        "time":      time.strftime("%Y-%m-%d %H:%M:%S"),
        "status":    "open",
    })

    TRADE_LOG.write_text(json.dumps(trades, indent=2))
    log.info(f"Trade logged: ticket={ticket} {signal.symbol} {signal.direction} lot={lot}")
