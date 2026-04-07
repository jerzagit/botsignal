"""
btc_bot.py
BTC/USD Weekend Breakout Bot — standalone entry point.

Run: python btc_bot.py

Commands:
  /btcbuy  — force an immediate BTC buy entry (test / manual override)
"""

import asyncio
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv
load_dotenv()

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

from core.config import BOT_TOKEN, YOUR_CHAT_ID
from core.mt5 import mt5_connect
from core.btc_watcher import (
    start_btc_watcher, start_candle_saver, force_entry,
    BTC_SYMBOL, BTC_SYMBOL_SUFFIX, BTC_WEEKEND_ONLY, BTC_PROFIT_TARGET_USD,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("btc_bot")


async def cmd_btcbuy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /btcbuy — force an immediate BTC buy entry."""
    if update.effective_chat.id != YOUR_CHAT_ID:
        return
    await force_entry("buy", context.bot, skip_guards=True)


async def post_init(app: Application):
    """Runs after Application initialises — verify MT5, send startup message, start watcher."""
    if not mt5_connect():
        log.error("MT5 connection failed — ensure MT5 is open as Administrator with Algo Trading enabled")
        await app.bot.send_message(YOUR_CHAT_ID, "❌ BTC Bot: MT5 connection failed. Check terminal.")
        return

    symbol_display = BTC_SYMBOL + BTC_SYMBOL_SUFFIX
    mode = "weekends only" if BTC_WEEKEND_ONLY else "24/7"

    await app.bot.send_message(
        YOUR_CHAT_ID,
        f"🤖 *BTC Breakout Bot started*\n"
        f"Symbol: `{symbol_display}`\n"
        f"Strategy: H1 breakout → 0–25% Fib retrace → close at `${BTC_PROFIT_TARGET_USD:.0f}`\n"
        f"Active: {mode}\n"
        f"Commands: /btcbuy",
        parse_mode="Markdown",
    )

    log.info(f"MT5 connected. Watching {symbol_display} ({mode}) | /btcbuy available")

    # Start watcher + candle saver as background tasks
    asyncio.create_task(start_btc_watcher(app.bot))
    asyncio.create_task(start_candle_saver())


def main():
    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .build()
    )
    app.add_handler(CommandHandler("btcbuy", cmd_btcbuy))

    # run_polling() manages its own event loop — do not call inside asyncio.run()
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log.info("BTC bot stopped.")
