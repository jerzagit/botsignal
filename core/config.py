"""
core/config.py
All configuration loaded from .env — single source of truth.
"""

import os
from dotenv import load_dotenv

load_dotenv()

# ── Telegram user account (from my.telegram.org) ──────────────────────────────
TG_API_ID       = int(os.getenv("TG_API_ID", "0"))
TG_API_HASH     = os.getenv("TG_API_HASH", "")

# ── Your personal bot (from @BotFather) ───────────────────────────────────────
BOT_TOKEN       = os.getenv("BOT_TOKEN", "")
YOUR_CHAT_ID    = int(os.getenv("YOUR_CHAT_ID", "0"))

# ── Mentor's signal group ──────────────────────────────────────────────────────
SIGNAL_GROUP    = os.getenv("SIGNAL_GROUP", "AssistByHafizCarat")

# ── MT5 account ───────────────────────────────────────────────────────────────
MT5_PATH          = os.getenv("MT5_PATH", "")
MT5_SYMBOL_SUFFIX = os.getenv("MT5_SYMBOL_SUFFIX", "")
MT5_LOGIN       = int(os.getenv("MT5_LOGIN", "0"))
MT5_PASSWORD    = os.getenv("MT5_PASSWORD", "")
MT5_SERVER      = os.getenv("MT5_SERVER", "")

# ── Risk management ───────────────────────────────────────────────────────────
# % of FREE MARGIN to risk per trade (e.g. 0.05 = 5%)
RISK_PERCENT    = float(os.getenv("RISK_PERCENT", "0.05"))

# Min/max allowed lot sizes (safety guardrails)
MIN_LOT         = float(os.getenv("MIN_LOT", "0.01"))
MAX_LOT         = float(os.getenv("MAX_LOT", "0.50"))

# ── Signal confirmation ───────────────────────────────────────────────────────
# Seconds before a pending confirmation expires (default 5 min)
SIGNAL_EXPIRY        = int(os.getenv("SIGNAL_EXPIRY",        "1800"))  # 30 min — gives price time to reach entry zone
WATCH_INTERVAL_SECS  = int(os.getenv("WATCH_INTERVAL_SECS",  "30"))   # how often watcher checks price (seconds)

# ── Early TP / breakeven settings ─────────────────────────────────────────────
# Number of profitable positions to keep running at breakeven when early TP fires
BREAKEVEN_KEEP_COUNT = int(os.getenv("BREAKEVEN_KEEP_COUNT", "2"))

# ── Entry price proximity guard ───────────────────────────────────────────────
# Max allowed distance (in pips) between current price and Hafiz's entry zone.
# If price is further away than this, the trade is blocked — signal came too early.
ENTRY_MAX_DISTANCE_PIPS = int(os.getenv("ENTRY_MAX_DISTANCE_PIPS", "50"))

# ── Margin level guard ─────────────────────────────────────────────────────────
# Minimum margin level (%) required before opening a new trade.
# margin_level = equity / used_margin * 100
# Professional floor: 300%. Below 200% = margin call danger zone.
MIN_MARGIN_LEVEL = float(os.getenv("MIN_MARGIN_LEVEL", "300"))

# ── Spread guard ───────────────────────────────────────────────────────────────
# Max allowed broker spread in pips before entry is blocked.
# XAUUSD normal: 1–2 pips. Wide spread (news/off-hours) eats into your SL.
MAX_SPREAD_PIPS = float(os.getenv("MAX_SPREAD_PIPS", "3.0"))

# ── Reward:Risk ratio guard ────────────────────────────────────────────────────
# Minimum TP1:SL ratio required to allow the trade.
# 1.0 = break-even math. Professional standard is 1.5.
# If Hafiz's TP is smaller than the SL, skip — bad risk math.
MIN_RR_RATIO = float(os.getenv("MIN_RR_RATIO", "1.0"))

# ── Same-direction stack guard ─────────────────────────────────────────────────
# Block opening a new trade if one already exists in the same direction
# on the same symbol. Prevents doubling exposure on a small account.
BLOCK_SAME_DIRECTION_STACK = os.getenv("BLOCK_SAME_DIRECTION_STACK", "true").lower() == "true"

# ── SL sanity check (warns if Hafiz's SL is outside normal range) ─────────────
# For XAUUSD: 1 pip = 0.1 price units  →  50 pips = 5.0 pts, 70 pips = 7.0 pts
SL_PIP_SIZE     = float(os.getenv("SL_PIP_SIZE",     "0.1"))   # price units per pip
SL_WARN_MIN_PIPS = int(os.getenv("SL_WARN_MIN_PIPS", "50"))    # warn if SL < this
SL_WARN_MAX_PIPS = int(os.getenv("SL_WARN_MAX_PIPS", "70"))    # warn if SL > this

# ── MySQL database (dashboard) ────────────────────────────────────────────────
DB_HOST     = os.getenv("DB_HOST",     "localhost")
DB_PORT     = int(os.getenv("DB_PORT", "3307"))
DB_NAME     = os.getenv("DB_NAME",     "botsignal")
DB_USER     = os.getenv("DB_USER",     "root")
DB_PASSWORD = os.getenv("DB_PASSWORD", "")

# ── Trading agent schedule (Malaysia time = UTC+8) ────────────────────────────
# Agent runs between these hours MY time (e.g. 22:00 – 06:00 covers London+NY)
AGENT_START_HOUR_MY = int(os.getenv("AGENT_START_HOUR_MY", "22"))  # 10 PM
AGENT_END_HOUR_MY   = int(os.getenv("AGENT_END_HOUR_MY", "6"))     # 6 AM

# If True, agent auto-executes without asking for confirmation (CAREFUL!)
AGENT_AUTO_EXECUTE  = os.getenv("AGENT_AUTO_EXECUTE", "false").lower() == "true"
