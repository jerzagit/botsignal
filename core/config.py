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
SIGNAL_EXPIRY   = int(os.getenv("SIGNAL_EXPIRY", "300"))

# ── Trading agent schedule (Malaysia time = UTC+8) ────────────────────────────
# Agent runs between these hours MY time (e.g. 22:00 – 06:00 covers London+NY)
AGENT_START_HOUR_MY = int(os.getenv("AGENT_START_HOUR_MY", "22"))  # 10 PM
AGENT_END_HOUR_MY   = int(os.getenv("AGENT_END_HOUR_MY", "6"))     # 6 AM

# If True, agent auto-executes without asking for confirmation (CAREFUL!)
AGENT_AUTO_EXECUTE  = os.getenv("AGENT_AUTO_EXECUTE", "false").lower() == "true"
