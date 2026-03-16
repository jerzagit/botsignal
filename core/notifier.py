"""
core/notifier.py
Telegram bot — sends you confirmation messages with EXECUTE / SKIP buttons.
Also handles the button taps and routes to MT5 execution.
"""

import time
import asyncio
import logging

from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, ContextTypes

from core.config import BOT_TOKEN, YOUR_CHAT_ID, SIGNAL_EXPIRY
from core.signal import Signal
from core.state  import pending, pending_closes
from core.mt5    import execute_trade, close_position, set_breakeven, get_open_signal_groups
from core.config import BREAKEVEN_KEEP_COUNT

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
      - setup_failed → show groups, CLOSE per group or CLOSE ALL
      - early_tp     → breakeven plan: keep top N, close rest of profitable, leave losses
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

    if alert.reason == "early_tp":
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
        lines.append(f"🔒 *KEEP at breakeven* (SL → entry):")
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

    await query.answer()

    action, signal_id = query.data.split("_", 1)
    signal = pending.pop(signal_id, None)

    if signal is None:
        await query.edit_message_text("⚠️ Signal already handled or expired.")
        return

    from core.db import upsert_signal, record_trade

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


# ── Start notifier ─────────────────────────────────────────────────────────────

async def start_notifier():
    global _app
    _app = Application.builder().token(BOT_TOKEN).build()
    _app.add_handler(CallbackQueryHandler(handle_callback))
    await _app.initialize()
    await _app.start()
    await _app.updater.start_polling()
    log.info("Telegram notifier started.")
    # Keep running (listener runs in parallel via asyncio.gather)
    await asyncio.Event().wait()
