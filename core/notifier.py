"""
core/notifier.py
Telegram bot — sends you confirmation messages with EXECUTE / SKIP buttons.
Also handles the button taps and routes to MT5 execution.
"""

import time
import uuid
import asyncio
import logging

from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes

from core.config import (
    BOT_TOKEN, YOUR_CHAT_ID, SIGNAL_EXPIRY, MAP_ENABLED,
    BREAKEVEN_KEEP_COUNT, LAYER_MODE, LAYER_COUNT, LAYER2_PIPS,
    ENTRY_MAX_DISTANCE_PIPS, WATCH_INTERVAL_SECS, SL_PIP_SIZE,
    MANUAL_SL_PIPS, MANUAL_TP1_PIPS, MANUAL_TP2_PIPS, MANUAL_SYMBOL,
    MT5_SYMBOL_SUFFIX, TREND_ENABLED, FIB_GUARD_ENABLED, FIB_SCANNER_ENABLED,
    MANUAL_RISK_PERCENT,
)
from core.signal import Signal
from core.state  import pending, pending_closes
from core.mt5    import execute_trade, close_position, set_breakeven, get_open_signal_groups
from core.layer_watcher import watch_layered_entry
from core.watcher import watch_and_execute
from core.db     import (
    upsert_signal, set_snr_levels, get_snr_levels, add_zone, get_today_zones,
    delete_zone, clear_zones,
)

log = logging.getLogger(__name__)

_app: Application = None   # shared app instance


def get_bot() -> Bot:
    return _app.bot


# ── Confirmation message ───────────────────────────────────────────────────────

async def send_confirmation(bot: Bot, signal: Signal, signal_id: str):
    direction_emoji = "🔴 SELL" if signal.direction == "sell" else "🟢 BUY"
    zone_str = (
        f"`{signal.entry_low}`"
        if signal.entry_low == signal.entry_high
        else f"`{signal.entry_low} – {signal.entry_high}`"
    )
    tps_str = "\n".join(f"  TP{i+1}: `{t}`" for i, t in enumerate(signal.tps))

    msg = (
        f"📡 *New Signal!*\n\n"
        f"*{signal.symbol}* {direction_emoji}\n"
        f"Entry Zone: {zone_str}\n"
        f"SL: `{signal.sl}`\n"
        f"{tps_str}\n\n"
        f"_Lot will be auto-calculated from your margin_\n"
        f"⏳ Expires in 5 min — tap fast!\n\n"
        f"Execute this trade?"
    )

    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ EXECUTE", callback_data=f"exec_{signal_id}"),
        InlineKeyboardButton("❌ SKIP",    callback_data=f"skip_{signal_id}"),
    ]])

    await bot.send_message(
        chat_id=YOUR_CHAT_ID,
        text=msg,
        parse_mode="Markdown",
        reply_markup=keyboard
    )
    log.info(f"Confirmation sent: {signal.symbol} {signal.direction} [{signal_id}]")


# ── Close alert confirmation ──────────────────────────────────────────────────

async def send_close_confirmation(bot, alert):
    """
    Route to the right confirmation flow based on alert reason:
      - setup_failed -> show groups, CLOSE per group or CLOSE ALL
      - early_tp     -> breakeven plan: keep top N, close rest of profitable, leave losses
    """
    groups = get_open_signal_groups(symbol=alert.symbol)

    if not groups:
        sym_label = alert.symbol or "all symbols"
        await bot.send_message(
            chat_id=YOUR_CHAT_ID,
            text=f"⚡ *{'Setup Failed' if alert.reason == 'setup_failed' else 'Early Profit'}*"
                 f" — no open positions found for {sym_label}. Nothing to do.",
            parse_mode="Markdown"
        )
        return

    if alert.reason == "collect_profit":
        await _send_collect_profit_plan(bot, alert, groups)
    elif alert.reason == "early_tp":
        await _send_early_tp_plan(bot, alert, groups)
    else:
        await _send_setup_failed_options(bot, alert, groups)


async def _send_setup_failed_options(bot, alert, groups):
    """Setup failed: show CLOSE button per signal group + CLOSE ALL."""
    buttons = []
    lines   = ["⚡ *Setup Failed Alert*\n", "Open positions by signal:\n"]

    all_tickets = []
    for g in groups:
        sid      = g["signal_id"]
        tickets  = [p.ticket for p in g["positions"]]
        pnl      = g["total_pnl"]
        pnl_str  = f"+${pnl:.2f}" if pnl >= 0 else f"-${abs(pnl):.2f}"
        time_str = g["received_at"].strftime("%H:%M") if g["received_at"] else "manual"
        entry    = (f"{g['entry_low']}" if g["entry_low"] == g["entry_high"]
                    else f"{g['entry_low']}–{g['entry_high']}")

        lines.append(
            f"📌 `{time_str}` | {g['direction'].upper()} @ `{entry}` | P&L: `{pnl_str}`"
        )
        pending_closes[sid] = tickets
        all_tickets.extend(tickets)
        buttons.append(InlineKeyboardButton(
            f"CLOSE {time_str}", callback_data=f"clsig_{sid}"
        ))

    all_id = f"all_{alert.symbol or 'ALL'}"
    pending_closes[all_id] = all_tickets
    buttons.append(InlineKeyboardButton("CLOSE ALL ⚠️", callback_data=f"clsig_{all_id}"))
    buttons.append(InlineKeyboardButton("SKIP ❌", callback_data="clskip"))

    await bot.send_message(
        chat_id=YOUR_CHAT_ID,
        text="\n".join(lines),
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[b] for b in buttons]),
    )
    log.info(f"Setup-failed confirmation sent — {len(groups)} group(s)")


async def _send_collect_profit_plan(bot, alert, groups):
    """
    Collect profit: 70% close (most profitable first), 30% breakeven (free ride).
    Losing positions untouched.
    """
    all_positions = [p for g in groups for p in g["positions"]]

    profitable = sorted(
        [p for p in all_positions if p.profit > 0],
        key=lambda p: p.profit, reverse=True   # most profitable first
    )
    losers = [p for p in all_positions if p.profit <= 0]

    if not profitable:
        sym_label = alert.symbol or "all symbols"
        await bot.send_message(
            chat_id=YOUR_CHAT_ID,
            text=(
                f"💰 *Collect Profit* — no profitable positions found for {sym_label}.\n"
                f"_Nothing to close._"
            ),
            parse_mode="Markdown"
        )
        return

    n          = len(profitable)
    keep_count = max(1, round(n * 0.3))   # 30% keep at breakeven (min 1)
    to_close   = profitable[:-keep_count] if keep_count < n else []
    keep_be    = profitable[-keep_count:]  # least profitable of the winners -> breakeven

    lines = [f"💰 *Collect Profit Plan* — {alert.symbol or 'all'}\n",
             f"_70% secure · 30% breakeven_\n"]

    if to_close:
        lines.append(f"💵 *CLOSE* — lock in profit ({len(to_close)} position{'s' if len(to_close)>1 else ''}):")
        for p in to_close:
            sign = "+" if p.profit >= 0 else ""
            lines.append(f"  `#{p.ticket}` | {p.symbol} | `{sign}${p.profit:.2f}`")

    if keep_be:
        lines.append(f"\n🔒 *BREAKEVEN* — free ride ({len(keep_be)} position{'s' if len(keep_be)>1 else ''}):")
        for p in keep_be:
            sign = "+" if p.profit >= 0 else ""
            lines.append(f"  `#{p.ticket}` | {p.symbol} | `{sign}${p.profit:.2f}` | Entry: `{p.price_open}`")

    if losers:
        lines.append(f"\n🔵 *UNTOUCHED* — in loss, original SL stays ({len(losers)}):")
        for p in losers:
            lines.append(f"  `#{p.ticket}` | {p.symbol} | `-${abs(p.profit):.2f}`")

    plan_id = f"cp_{alert.symbol or 'ALL'}"
    pending_closes[plan_id] = {
        "keep_be":  [p.ticket for p in keep_be],
        "to_close": [p.ticket for p in to_close],
    }

    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ EXECUTE PLAN", callback_data=f"clsig_{plan_id}"),
        InlineKeyboardButton("❌ SKIP",          callback_data="clskip"),
    ]])

    await bot.send_message(
        chat_id=YOUR_CHAT_ID,
        text="\n".join(lines),
        parse_mode="Markdown",
        reply_markup=keyboard,
    )
    log.info(
        f"Collect-profit plan sent: {len(to_close)} close, "
        f"{len(keep_be)} breakeven, {len(losers)} untouched"
    )


async def _send_early_tp_plan(bot, alert, groups):
    """
    Early TP: flatten all positions per group, sort profitable by P&L desc.
    Keep top BREAKEVEN_KEEP_COUNT at breakeven, close the rest that are profitable.
    Leave losing positions untouched.
    """
    # Flatten all positions across groups
    all_positions = [p for g in groups for p in g["positions"]]

    profitable = sorted(
        [p for p in all_positions if p.profit > 0],
        key=lambda p: p.profit, reverse=True
    )
    losers = [p for p in all_positions if p.profit <= 0]

    keep_be  = profitable[:BREAKEVEN_KEEP_COUNT]   # set to breakeven
    to_close = profitable[BREAKEVEN_KEEP_COUNT:]    # close

    lines = [f"⚡ *Early Profit Plan* — {alert.symbol or 'all'}\n"]

    if keep_be:
        lines.append(f"🔒 *KEEP at breakeven* (SL -> entry):")
        for p in keep_be:
            pnl_str = f"+${p.profit:.2f}"
            lines.append(f"  #{p.ticket} | {p.symbol} | P&L: `{pnl_str}` | Entry: `{p.price_open}`")

    if to_close:
        lines.append(f"\n💰 *CLOSE* (take profit):")
        for p in to_close:
            pnl_str = f"+${p.profit:.2f}"
            lines.append(f"  #{p.ticket} | {p.symbol} | P&L: `{pnl_str}`")

    if losers:
        lines.append(f"\n🔵 *UNTOUCHED* (in loss — original SL remains):")
        for p in losers:
            pnl_str = f"-${abs(p.profit):.2f}"
            lines.append(f"  #{p.ticket} | {p.symbol} | P&L: `{pnl_str}`")

    if not keep_be and not to_close:
        lines.append("\n_No profitable positions to act on._")
        await bot.send_message(
            chat_id=YOUR_CHAT_ID, text="\n".join(lines), parse_mode="Markdown"
        )
        return

    # Store the plan
    plan_id = f"etp_{alert.symbol or 'ALL'}"
    pending_closes[plan_id] = {
        "keep_be":  [p.ticket for p in keep_be],
        "to_close": [p.ticket for p in to_close],
    }

    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ EXECUTE PLAN", callback_data=f"clsig_{plan_id}"),
        InlineKeyboardButton("❌ SKIP",          callback_data="clskip"),
    ]])

    await bot.send_message(
        chat_id=YOUR_CHAT_ID,
        text="\n".join(lines),
        parse_mode="Markdown",
        reply_markup=keyboard,
    )
    log.info(
        f"Early-TP plan sent: {len(keep_be)} breakeven, "
        f"{len(to_close)} to close, {len(losers)} untouched"
    )


async def handle_close_callback(query, context):
    """Handle CLOSE / SKIP taps from the close alert message."""
    await query.answer()
    data = query.data

    if data == "clskip":
        await query.edit_message_text("❌ Close alert skipped.")
        pending_closes.clear()
        return

    close_id = data.replace("clsig_", "")
    plan     = pending_closes.pop(close_id, None)

    if plan is None:
        await query.edit_message_text("⚠️ Already handled or expired.")
        return

    # ── Early TP plan: dict with keep_be + to_close ───────────────────────────
    if isinstance(plan, dict):
        keep_be  = plan.get("keep_be", [])
        to_close = plan.get("to_close", [])
        total    = len(keep_be) + len(to_close)
        await query.edit_message_text(
            f"⏳ Executing plan: {len(keep_be)} breakeven, {len(to_close)} close..."
        )
        results = []
        for ticket in keep_be:
            r = await asyncio.get_event_loop().run_in_executor(None, set_breakeven, ticket)
            results.append(r)
        for ticket in to_close:
            r = await asyncio.get_event_loop().run_in_executor(None, close_position, ticket)
            results.append(r)
        log.info(f"Early-TP plan executed: {len(keep_be)} breakeven, {len(to_close)} closed")

    # ── Setup failed: flat list of tickets to close ───────────────────────────
    else:
        tickets = plan
        await query.edit_message_text(f"⏳ Closing {len(tickets)} position(s)...")
        results = []
        for ticket in tickets:
            r = await asyncio.get_event_loop().run_in_executor(None, close_position, ticket)
            results.append(r)
        log.info(f"Closed {len(tickets)} position(s) for close_id={close_id}")

    summary = "\n".join(results)
    await context.bot.send_message(
        chat_id=YOUR_CHAT_ID,
        text=f"*Results:*\n{summary}",
        parse_mode="Markdown"
    )


# ── Button handler ─────────────────────────────────────────────────────────────

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query

    # Route close alert callbacks separately
    if query.data.startswith("clsig_") or query.data == "clskip":
        await handle_close_callback(query, context)
        return

    # Route manual trade trend confirmation callbacks
    if query.data.startswith("manual_"):
        await handle_manual_trade_callback(query, context)
        return

    # Route Fib entry alert callbacks
    if query.data.startswith("fibalert_"):
        await handle_fib_alert_callback(query, context)
        return

    await query.answer()

    action, signal_id = query.data.split("_", 1)
    signal = pending.pop(signal_id, None)

    if signal is None:
        await query.edit_message_text("⚠️ Signal already handled or expired.")
        return

    if time.time() - signal.created_at > SIGNAL_EXPIRY:
        await query.edit_message_text(
            f"⏰ *Signal expired* — not safe to execute now.\n"
            f"`{signal.symbol} {signal.direction.upper()}`",
            parse_mode="Markdown"
        )
        upsert_signal(signal_id, signal, status="expired")
        return

    if action == "exec":
        await query.edit_message_text("⏳ Calculating lot size and placing trade...")
        result = await asyncio.get_event_loop().run_in_executor(
            None, execute_trade, signal, signal_id
        )
        upsert_signal(signal_id, signal, status="executed")
        await context.bot.send_message(
            chat_id=YOUR_CHAT_ID, text=result, parse_mode="Markdown"
        )
    else:
        direction_emoji = "🔴" if signal.direction == "sell" else "🟢"
        await query.edit_message_text(
            f"❌ Skipped {direction_emoji} `{signal.symbol} {signal.direction.upper()}`",
            parse_mode="Markdown"
        )
        upsert_signal(signal_id, signal, status="skipped")
        log.info(f"Skipped: {signal.symbol} {signal.direction} [{signal_id}]")


# ── AutoZone commands ─────────────────────────────────────────────────────────

async def cmd_snr(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/snr XAUUSD 5007 5014 5022 5035 5043 — set today's SNR levels."""
    args = context.args or []
    if len(args) < 2:
        await update.message.reply_text("Usage: /snr XAUUSD 5007 5014 5022 5035 5043")
        return

    symbol = args[0].upper()
    try:
        prices = sorted([float(p) for p in args[1:]])
    except ValueError:
        await update.message.reply_text("Invalid prices. Usage: /snr XAUUSD 5007 5014 5022")
        return

    set_snr_levels(symbol, prices)
    levels_str = ", ".join(str(int(p) if p == int(p) else p) for p in prices)
    await update.message.reply_text(
        f"\u2705 {len(prices)} SNR levels set for {symbol}: {levels_str}"
    )
    log.info(f"/snr {symbol}: {prices}")


async def cmd_map(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/map XAUUSD buy 5011-5014 — add a buy/sell zone with auto SL/TP from SNR."""
    if not MAP_ENABLED:
        await update.message.reply_text("\u274c AutoZone is disabled (MAP_ENABLED=false)")
        return

    args = context.args or []
    if len(args) < 3:
        await update.message.reply_text("Usage: /map XAUUSD buy 5011-5014")
        return

    symbol = args[0].upper()
    direction = args[1].lower()
    if direction not in ("buy", "sell"):
        await update.message.reply_text("Direction must be 'buy' or 'sell'.")
        return

    zone_str = args[2]
    parts = zone_str.replace("\u2013", "-").split("-")
    try:
        zone_low = float(parts[0])
        zone_high = float(parts[1]) if len(parts) > 1 else zone_low
    except (ValueError, IndexError):
        await update.message.reply_text("Invalid zone format. Use: 5011-5014")
        return

    if zone_low > zone_high:
        zone_low, zone_high = zone_high, zone_low

    # Auto-pick SL and TP from SNR levels
    snr = get_snr_levels(symbol)
    if not snr:
        await update.message.reply_text(
            f"\u274c No SNR levels found for {symbol}. Set them first with /snr"
        )
        return

    if direction == "buy":
        sl_candidates = [p for p in snr if p < zone_low]
        tp_candidates = [p for p in snr if p > zone_high]
        sl = max(sl_candidates) if sl_candidates else None
        tp = min(tp_candidates) if tp_candidates else None
    else:
        sl_candidates = [p for p in snr if p > zone_high]
        tp_candidates = [p for p in snr if p < zone_low]
        sl = min(sl_candidates) if sl_candidates else None
        tp = max(tp_candidates) if tp_candidates else None

    if sl is None or tp is None:
        missing = []
        if sl is None:
            missing.append("SL")
        if tp is None:
            missing.append("TP")
        await update.message.reply_text(
            f"\u274c No SNR level found for {', '.join(missing)}. "
            f"Add more levels with /snr"
        )
        return

    zone_id = add_zone(symbol, direction, zone_low, zone_high, sl, tp)
    dir_emoji = "\U0001f7e2" if direction == "buy" else "\U0001f534"

    def _fmt(v):
        return str(int(v)) if v == int(v) else str(v)

    await update.message.reply_text(
        f"\u2705 Zone #{zone_id} mapped!\n"
        f"{dir_emoji} {direction.upper()} {_fmt(zone_low)}-{_fmt(zone_high)}\n"
        f"SL: {_fmt(sl)} | TP: {_fmt(tp)}"
    )
    log.info(f"/map {symbol} {direction} {zone_low}-{zone_high} SL={sl} TP={tp}")


async def cmd_zones(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/zones — list today's zones + SNR levels."""
    zones = get_today_zones()
    # Gather unique symbols from zones to show their SNR
    symbols = sorted(set(z["symbol"] for z in zones)) if zones else ["XAUUSD"]

    lines = ["\U0001f5fa *AutoZone — Today's Zones*\n"]

    for sym in symbols:
        snr = get_snr_levels(sym)
        if snr:
            snr_str = " | ".join(str(int(p) if p == int(p) else p) for p in snr)
            lines.append(f"\U0001f4ca *{sym} SNR:* `{snr_str}`\n")

    if not zones:
        lines.append("_No zones mapped yet. Use /map to add zones._")
    else:
        for z in zones:
            zid = z["id"]
            d = z["direction"].upper()
            d_emoji = "\U0001f7e2" if z["direction"] == "buy" else "\U0001f534"
            zl = float(z["zone_low"])
            zh = float(z["zone_high"])
            sl = float(z["sl"])
            tp = float(z["tp"])
            fired = z["fired"]

            def _fmt(v):
                return str(int(v)) if v == int(v) else str(v)

            status = "\u2705 Fired" if fired else "\u23f3 Watching"
            lines.append(
                f"#{zid} {d_emoji} {d} `{_fmt(zl)}-{_fmt(zh)}` "
                f"SL:`{_fmt(sl)}` TP:`{_fmt(tp)}` — {status}"
            )

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def cmd_delzone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/delzone 3 — delete zone by ID."""
    args = context.args or []
    if not args:
        await update.message.reply_text("Usage: /delzone <zone_id>")
        return

    try:
        zone_id = int(args[0])
    except ValueError:
        await update.message.reply_text("Zone ID must be a number.")
        return

    if delete_zone(zone_id):
        await update.message.reply_text(f"\u2705 Zone #{zone_id} deleted.")
        log.info(f"/delzone {zone_id}")
    else:
        await update.message.reply_text(f"\u274c Zone #{zone_id} not found (today).")


async def cmd_clearmap(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/clearmap — clear all today's zones + SNR levels."""
    clear_zones()
    await update.message.reply_text("\u2705 All zones and SNR levels cleared for today.")
    log.info("/clearmap executed")


# ── Manual trade commands ──────────────────────────────────────────────────────

async def cmd_trade_now(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /buynow — instant buy at current market price with fixed SL/TP pips.
    /sellnow — instant sell at current market price with fixed SL/TP pips.
    Uses same DCA pipeline as Hafiz signals (all guards, layers, sub-splits).
    Checks H1+H4 trend before executing — warns if opposing direction.
    """
    command = update.message.text.split()[0].lstrip("/").lower()  # "buynow" or "sellnow"
    direction = "buy" if "buy" in command else "sell"

    # Get current market price from MT5
    import MetaTrader5 as mt5
    from core.mt5 import mt5_connect

    if not mt5_connect():
        await update.message.reply_text("MT5 not connected. Start MT5 and try again.")
        return

    symbol_mt5 = MANUAL_SYMBOL + MT5_SYMBOL_SUFFIX
    tick = mt5.symbol_info_tick(symbol_mt5)
    mt5.shutdown()

    if tick is None:
        await update.message.reply_text(f"Could not get price for {symbol_mt5}.")
        return

    price = tick.ask if direction == "buy" else tick.bid
    price = round(price, 2)

    # Calculate SL and TPs from fixed pip distances
    sl_dist  = MANUAL_SL_PIPS  * SL_PIP_SIZE
    tp1_dist = MANUAL_TP1_PIPS * SL_PIP_SIZE
    tp2_dist = MANUAL_TP2_PIPS * SL_PIP_SIZE

    if direction == "buy":
        sl  = round(price - sl_dist, 2)
        tp1 = round(price + tp1_dist, 2)
        tp2 = round(price + tp2_dist, 2)
    else:
        sl  = round(price + sl_dist, 2)
        tp1 = round(price - tp1_dist, 2)
        tp2 = round(price - tp2_dist, 2)

    # Build Signal object (same as Hafiz signal)
    signal = Signal(
        symbol=MANUAL_SYMBOL,
        direction=direction,
        entry_low=price,
        entry_high=price,
        sl=sl,
        tps=[tp1, tp2],
        raw_text=f"/{command} {MANUAL_SYMBOL} {direction} @{price} sl {sl} tp {tp1} tp {tp2}",
    )

    signal_id = uuid.uuid4().hex[:8]
    pending[signal_id] = signal
    upsert_signal(signal_id, signal, status="pending")

    direction_emoji = "\U0001f7e2 BUY" if direction == "buy" else "\U0001f534 SELL"
    tps_str = f"`{tp1}` | `{tp2}`"

    # ── Trend check (H1 + H4) ────────────────────────────────────────────────
    trend_line = ""
    trend_opposed = False
    if TREND_ENABLED:
        from core.trend_analyzer import check_trend_alignment
        trend = await asyncio.get_event_loop().run_in_executor(
            None, check_trend_alignment, direction, MANUAL_SYMBOL
        )
        h1_mark = "\u2705" if trend["h1"] != ("BEAR" if direction == "buy" else "BULL") else "\u274c"
        h4_mark = "\u2705" if trend["h4"] != ("BEAR" if direction == "buy" else "BULL") else "\u274c"
        trend_line = f"H1: `{trend['h1']}` {h1_mark} | H4: `{trend['h4']}` {h4_mark}"

        if not trend["aligned"]:
            trend_opposed = True

    # ── Fib retracement check (H1) ────────────────────────────────────────────
    fib_line = ""
    fib_opposed = False
    if FIB_GUARD_ENABLED:
        from core.trend_analyzer import check_fib_entry
        fib = await asyncio.get_event_loop().run_in_executor(
            None, check_fib_entry, direction, price, MANUAL_SYMBOL
        )
        fib_pct = fib["fib_pct"]
        if fib["in_zone"]:
            fib_line = f"Fib: `{fib_pct:.0f}%` \u2705"
        else:
            fib_opposed = True
            fib_line = (
                f"Fib: `{fib_pct:.0f}%` \u274c (above 38.2%)\n"
                f"     Last H1: `{fib['candle_high']}`\u2192`{fib['candle_low']}` | "
                f"38.2% = `{fib['fib_382']}`"
            )

    # ── If trend OR fib opposes: show warning with CONFIRM / CANCEL ───────────
    if trend_opposed or fib_opposed:
        warn_lines = [f"\u26a0\ufe0f *Entry Warning* \u2014 /{command}\n"]
        if trend_line:
            label = " \u2014 *opposing*" if trend_opposed else ""
            warn_lines.append(f"Trend: {trend_line}{label}")
        if fib_line:
            warn_lines.append(f"{fib_line}")
        warn_lines.append(
            f"\n*{MANUAL_SYMBOL}* {direction_emoji} @ `{price}`\n"
            f"SL: `{sl}` ({MANUAL_SL_PIPS}p) | TP: {tps_str}\n"
        )
        if fib_opposed and not trend_opposed:
            warn_lines.append("_Price hasn't pulled back enough. Wait for dip?_")
        elif trend_opposed and not fib_opposed:
            warn_lines.append("_Trade against trend? This increases risk._")
        else:
            warn_lines.append("_Trend opposing + bad pullback level. High risk entry._")

        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("\u2705 CONFIRM", callback_data=f"manual_exec_{signal_id}"),
            InlineKeyboardButton("\u274c CANCEL",  callback_data=f"manual_skip_{signal_id}"),
        ]])
        await update.message.reply_text(
            "\n".join(warn_lines),
            parse_mode="Markdown",
            reply_markup=keyboard,
        )
        reasons = []
        if trend_opposed:
            reasons.append(f"trend({trend.get('warning', '')})")
        if fib_opposed:
            reasons.append(f"fib({fib.get('warning', '')})")
        log.info(f"/{command}: {', '.join(reasons)} — waiting for confirmation [{signal_id}]")
        return

    # ── All checks passed: execute immediately ────────────────────────────────
    _start_manual_watcher(signal, signal_id, direction_emoji, price, sl, tps_str, trend_line)

    checks = trend_line
    if fib_line:
        checks = f"{checks} | {fib_line}" if checks else fib_line

    checks_info = f"\n{checks}" if checks else ""
    if LAYER_MODE:
        mode_line = (
            f"\U0001f522 DCA mode - up to `{LAYER_COUNT}` layers "
            f"(`{LAYER2_PIPS}p` apart)"
        )
    else:
        mode_line = "\U0001f3af Single entry mode"

    await update.message.reply_text(
        f"\U0001f3ae *Manual Trade*\n\n"
        f"*{MANUAL_SYMBOL}* {direction_emoji} @ `{price}`{checks_info}\n"
        f"SL: `{sl}` ({MANUAL_SL_PIPS}p) | TP: {tps_str}\n\n"
        f"{mode_line}\n"
        f"_All guards active - same pipeline as Hafiz signals_",
        parse_mode="Markdown"
    )
    log.info(f"/{command}: {MANUAL_SYMBOL} {direction} @ {price} SL={sl} TP1={tp1} TP2={tp2} [{signal_id}]")


def _start_manual_watcher(signal, signal_id, direction_emoji, price, sl, tps_str, trend_line):
    """Start the watcher task for a manual trade (shared by direct execute and confirm callback)."""
    if LAYER_MODE:
        watcher_task = watch_layered_entry(signal, signal_id, get_bot(), entry_mode="manual")
    else:
        watcher_task = watch_and_execute(signal, signal_id, get_bot())
    asyncio.create_task(watcher_task)


async def handle_manual_trade_callback(query, context):
    """Handle CONFIRM / CANCEL taps from trend warning on manual trades."""
    await query.answer()
    data = query.data

    action, signal_id = data.split("_", 1)
    # action is "manual", signal_id starts with "exec_xxx" or "skip_xxx"
    parts = data.replace("manual_", "").split("_", 1)
    action = parts[0]   # "exec" or "skip"
    signal_id = parts[1]

    signal = pending.get(signal_id)
    if signal is None:
        await query.edit_message_text("\u26a0\ufe0f Trade already handled or expired.")
        return

    if action == "exec":
        # Confirmed — start watcher
        pending.pop(signal_id, None)
        pending[signal_id] = signal  # re-add (watcher checks pending)

        direction_emoji = "\U0001f7e2 BUY" if signal.direction == "buy" else "\U0001f534 SELL"
        price = signal.entry_low
        tps_str = " | ".join(f"`{t}`" for t in signal.tps)

        _start_manual_watcher(signal, signal_id, direction_emoji, price, signal.sl, tps_str, "")

        if LAYER_MODE:
            mode_line = (
                f"\U0001f522 DCA mode - up to `{LAYER_COUNT}` layers "
                f"(`{LAYER2_PIPS}p` apart)"
            )
        else:
            mode_line = "\U0001f3af Single entry mode"

        await query.edit_message_text(
            f"\u2705 *Confirmed — executing against trend*\n\n"
            f"*{MANUAL_SYMBOL}* {direction_emoji} @ `{price}`\n"
            f"SL: `{signal.sl}` | TP: {tps_str}\n\n"
            f"{mode_line}\n"
            f"_All guards active_",
            parse_mode="Markdown"
        )
        log.info(f"Manual trade confirmed against trend [{signal_id}]")
    else:
        # Cancelled
        pending.pop(signal_id, None)
        upsert_signal(signal_id, signal, status="skipped")
        await query.edit_message_text(
            f"\u274c *Trade cancelled*\n"
            f"`{signal.symbol} {signal.direction.upper()}` — trend was opposing.",
            parse_mode="Markdown"
        )
        log.info(f"Manual trade cancelled (trend opposing) [{signal_id}]")


# ── Fib entry alert callback ─────────────────────────────────────────────────

async def handle_fib_alert_callback(query, context):
    """Handle BUY NOW / SELL NOW / DISMISS from Fib entry scanner alerts."""
    await query.answer()
    data = query.data  # e.g. "fibalert_buy_abc12345" or "fibalert_dismiss_abc12345"

    # Parse: fibalert_{action}_{alert_id}
    rest = data.replace("fibalert_", "")       # "buy_abc12345" or "dismiss_abc12345"
    action, alert_id = rest.split("_", 1)      # ("buy", "abc12345")

    if action == "dismiss":
        await query.edit_message_text("\u274c Alert dismissed.")
        log.info(f"Fib alert dismissed [{alert_id}]")
        return

    # action is "buy" or "sell" — execute the trade
    from core.trend_analyzer import fib_pending

    alert_data = fib_pending.pop(alert_id, None)
    if alert_data is None:
        await query.edit_message_text("\u26a0\ufe0f Alert expired. Use /buynow or /sellnow instead.")
        return

    direction = action  # "buy" or "sell"

    # Get fresh price from MT5
    import MetaTrader5 as mt5_mod
    from core.mt5 import mt5_connect

    if not mt5_connect():
        await query.edit_message_text("\u274c MT5 not connected. Start MT5 and try again.")
        return

    symbol_mt5 = MANUAL_SYMBOL + MT5_SYMBOL_SUFFIX
    tick = mt5_mod.symbol_info_tick(symbol_mt5)
    mt5_mod.shutdown()

    if tick is None:
        await query.edit_message_text(f"\u274c Could not get price for {symbol_mt5}.")
        return

    price = tick.ask if direction == "buy" else tick.bid
    price = round(price, 2)

    # Re-validate Fib zone at current price (price may have moved since alert)
    alert_price = alert_data["price"]
    price_moved_pips = abs(price - alert_price) / SL_PIP_SIZE
    if FIB_GUARD_ENABLED:
        from core.trend_analyzer import check_fib_entry
        fib = await asyncio.get_event_loop().run_in_executor(
            None, check_fib_entry, direction, price, MANUAL_SYMBOL
        )
        if not fib["in_zone"]:
            # Price moved out of Fib zone — show warning with CONFIRM/CANCEL
            signal_id = uuid.uuid4().hex[:8]

            # Build a temporary signal for the pending dict
            sl_dist_tmp  = MANUAL_SL_PIPS * SL_PIP_SIZE
            tp1_dist_tmp = MANUAL_TP1_PIPS * SL_PIP_SIZE
            tp2_dist_tmp = MANUAL_TP2_PIPS * SL_PIP_SIZE
            if direction == "buy":
                sl_tmp  = round(price - sl_dist_tmp, 2)
                tp1_tmp = round(price + tp1_dist_tmp, 2)
                tp2_tmp = round(price + tp2_dist_tmp, 2)
            else:
                sl_tmp  = round(price + sl_dist_tmp, 2)
                tp1_tmp = round(price - tp1_dist_tmp, 2)
                tp2_tmp = round(price - tp2_dist_tmp, 2)

            signal_tmp = Signal(
                symbol=MANUAL_SYMBOL, direction=direction,
                entry_low=price, entry_high=price, sl=sl_tmp,
                tps=[tp1_tmp, tp2_tmp],
                raw_text=f"/fibalert {MANUAL_SYMBOL} {direction} @{price}",
            )
            pending[signal_id] = signal_tmp
            upsert_signal(signal_id, signal_tmp, status="pending")

            dir_emoji = "\U0001f7e2 BUY" if direction == "buy" else "\U0001f534 SELL"
            keyboard = InlineKeyboardMarkup([[
                InlineKeyboardButton("\u2705 CONFIRM", callback_data=f"manual_exec_{signal_id}"),
                InlineKeyboardButton("\u274c CANCEL",  callback_data=f"manual_skip_{signal_id}"),
            ]])
            await query.edit_message_text(
                f"\u26a0\ufe0f *Price moved out of Fib zone*\n\n"
                f"Alert was @ `{alert_price}` \u2192 now @ `{price}` "
                f"({price_moved_pips:.0f}p moved)\n"
                f"Fib: `{fib['fib_pct']:.0f}%` \u274c (above 38.2%)\n\n"
                f"*{MANUAL_SYMBOL}* {dir_emoji} @ `{price}`\n"
                f"SL: `{sl_tmp}` ({MANUAL_SL_PIPS}p) | TP: `{tp1_tmp}` | `{tp2_tmp}`\n\n"
                f"_Price hasn\u2019t pulled back enough anymore. Still enter?_",
                parse_mode="Markdown",
                reply_markup=keyboard,
            )
            log.info(
                f"Fib alert: price moved out of zone ({alert_price}->{price}, "
                f"fib {fib['fib_pct']:.0f}%) — waiting for confirmation [{alert_id}]"
            )
            return

    # Build Signal (same as cmd_trade_now)
    sl_dist  = MANUAL_SL_PIPS  * SL_PIP_SIZE
    tp1_dist = MANUAL_TP1_PIPS * SL_PIP_SIZE
    tp2_dist = MANUAL_TP2_PIPS * SL_PIP_SIZE

    if direction == "buy":
        sl  = round(price - sl_dist, 2)
        tp1 = round(price + tp1_dist, 2)
        tp2 = round(price + tp2_dist, 2)
    else:
        sl  = round(price + sl_dist, 2)
        tp1 = round(price - tp1_dist, 2)
        tp2 = round(price - tp2_dist, 2)

    signal = Signal(
        symbol=MANUAL_SYMBOL,
        direction=direction,
        entry_low=price,
        entry_high=price,
        sl=sl,
        tps=[tp1, tp2],
        raw_text=f"/fibalert {MANUAL_SYMBOL} {direction} @{price} sl {sl} tp {tp1} tp {tp2}",
    )

    signal_id = uuid.uuid4().hex[:8]
    pending[signal_id] = signal
    upsert_signal(signal_id, signal, status="pending")

    # Start the manual watcher
    direction_emoji = "\U0001f7e2 BUY" if direction == "buy" else "\U0001f534 SELL"
    tps_str = f"`{tp1}` | `{tp2}`"
    _start_manual_watcher(signal, signal_id, direction_emoji, price, sl, tps_str, "")

    if LAYER_MODE:
        mode_line = (
            f"\U0001f522 DCA mode - up to `{LAYER_COUNT}` layers "
            f"(`{LAYER2_PIPS}p` apart)"
        )
    else:
        mode_line = "\U0001f3af Single entry mode"

    await query.edit_message_text(
        f"\u2705 *Fib Alert \u2192 Executing*\n\n"
        f"*{MANUAL_SYMBOL}* {direction_emoji} @ `{price}`\n"
        f"SL: `{sl}` ({MANUAL_SL_PIPS}p) | TP: {tps_str}\n\n"
        f"{mode_line}\n"
        f"_All guards active \u2014 same pipeline as manual trades_",
        parse_mode="Markdown"
    )
    log.info(f"Fib alert executed: {MANUAL_SYMBOL} {direction} @ {price} [{signal_id}]")


# ── Trend command ─────────────────────────────────────────────────────────────

async def cmd_trend(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/trend — show market direction across M5, M15, H1, H4."""
    from core.trend_analyzer import analyze_all_timeframes, format_trend_table

    await update.message.reply_text("Analyzing market direction...")

    results = await asyncio.get_event_loop().run_in_executor(
        None, analyze_all_timeframes, None
    )

    if not results:
        await update.message.reply_text("Could not connect to MT5. Start MT5 and try again.")
        return

    table = format_trend_table(results)
    await update.message.reply_text(table, parse_mode="Markdown")
    log.info("/trend command executed")


# ── Start notifier ─────────────────────────────────────────────────────────────

async def start_notifier():
    global _app
    _app = Application.builder().token(BOT_TOKEN).build()
    _app.add_handler(CallbackQueryHandler(handle_callback))
    _app.add_handler(CommandHandler("snr", cmd_snr))
    _app.add_handler(CommandHandler("map", cmd_map))
    _app.add_handler(CommandHandler("zones", cmd_zones))
    _app.add_handler(CommandHandler("delzone", cmd_delzone))
    _app.add_handler(CommandHandler("clearmap", cmd_clearmap))
    _app.add_handler(CommandHandler("buynow", cmd_trade_now))
    _app.add_handler(CommandHandler("sellnow", cmd_trade_now))
    if TREND_ENABLED:
        _app.add_handler(CommandHandler("trend", cmd_trend))
    await _app.initialize()
    await _app.start()
    await _app.updater.start_polling()
    log.info("Telegram notifier started.")
    # Keep running (listener runs in parallel via asyncio.gather)
    await asyncio.Event().wait()
