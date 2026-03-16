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
from core.state  import pending
from core.mt5    import execute_trade

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


# ── Button handler ─────────────────────────────────────────────────────────────

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
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
        return

    if action == "exec":
        await query.edit_message_text("⏳ Calculating lot size and placing trade...")
        result = await asyncio.get_event_loop().run_in_executor(None, execute_trade, signal)
        await context.bot.send_message(
            chat_id=YOUR_CHAT_ID, text=result, parse_mode="Markdown"
        )
    else:
        direction_emoji = "🔴" if signal.direction == "sell" else "🟢"
        await query.edit_message_text(
            f"❌ Skipped {direction_emoji} `{signal.symbol} {signal.direction.upper()}`",
            parse_mode="Markdown"
        )
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
