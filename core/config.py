"""
core/config.py
All configuration loaded from .env — single source of truth.
"""

import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()

# ── Telegram user account (from my.telegram.org) ──────────────────────────────
TG_API_ID       = int(os.getenv("TG_API_ID", "0"))
TG_API_HASH     = os.getenv("TG_API_HASH", "")
TG_PHONE        = os.getenv("TG_PHONE", "")

# ── Your personal bot (from @BotFather) ───────────────────────────────────────
BOT_TOKEN       = os.getenv("BOT_TOKEN", "")
YOUR_CHAT_ID    = int(os.getenv("YOUR_CHAT_ID", "0"))

# ── Mentor's signal group ──────────────────────────────────────────────────────
SIGNAL_GROUP    = os.getenv("SIGNAL_GROUP", "AssistByHafizCarat")


@dataclass(frozen=True)
class SignalSource:
    """Telegram signal source configuration."""
    source_id: str
    chat: str
    risk_percent: float
    parser_profile: str = "hafiz"
    name: str = ""
    auto_execute: bool = True


SOURCE_RISK_DEFAULT = float(os.getenv("SOURCE_RISK_DEFAULT", "0.10"))
SOURCE_RISK_MODE = os.getenv("SOURCE_RISK_MODE", "reduce").lower()  # reduce | block
SOURCE_CONFLICT_MODE = os.getenv("SOURCE_CONFLICT_MODE", "allow").lower()
MAX_TOTAL_OPEN_RISK = float(os.getenv("MAX_TOTAL_OPEN_RISK", "0.20"))


def _parse_bool(value: str, default: bool = True) -> bool:
    if value is None or value == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _parse_signal_sources(raw: str) -> list[SignalSource]:
    """
    Parse SIGNAL_SOURCES.

    Format:
        source_id:chat:risk_percent:parser_profile:name:auto_execute

    Example:
        hafiz:-1002083967629:0.10:hafiz:PIPS FIGHTER:true

    Only source_id and chat are required. Missing risk uses SOURCE_RISK_DEFAULT.
    """
    sources: list[SignalSource] = []
    for item in (raw or "").split(","):
        item = item.strip()
        if not item:
            continue
        parts = [p.strip() for p in item.split(":")]
        if len(parts) < 2:
            continue
        source_id = parts[0].lower().replace(" ", "_")
        chat = parts[1]
        try:
            risk_percent = float(parts[2]) if len(parts) > 2 and parts[2] else SOURCE_RISK_DEFAULT
        except ValueError:
            risk_percent = SOURCE_RISK_DEFAULT
        parser_profile = (parts[3] if len(parts) > 3 and parts[3] else "hafiz").lower()
        name = parts[4] if len(parts) > 4 and parts[4] else source_id
        auto_execute = _parse_bool(parts[5], True) if len(parts) > 5 else True
        sources.append(SignalSource(source_id, chat, risk_percent, parser_profile, name, auto_execute))
    return sources


SIGNAL_SOURCES = _parse_signal_sources(os.getenv("SIGNAL_SOURCES", ""))
if not SIGNAL_SOURCES:
    SIGNAL_SOURCES = [
        SignalSource("default", SIGNAL_GROUP, SOURCE_RISK_DEFAULT, "hafiz", "Default", True)
    ]

# ── Environment mode ──────────────────────────────────────────────────────────
ENV_MODE = os.getenv("ENV_MODE", "demo").lower()
_P = "LIVE_" if ENV_MODE == "live" else "DEMO_"

# ── MT5 account (selected by ENV_MODE) ───────────────────────────────────────
MT5_PATH          = os.getenv("MT5_PATH", "")
MT5_LOGIN         = int(os.getenv(_P + "MT5_LOGIN", os.getenv("MT5_LOGIN", "0")))
MT5_PASSWORD      = os.getenv(_P + "MT5_PASSWORD", os.getenv("MT5_PASSWORD", ""))
MT5_SERVER        = os.getenv(_P + "MT5_SERVER", os.getenv("MT5_SERVER", ""))
MT5_SYMBOL_SUFFIX = os.getenv(_P + "MT5_SYMBOL_SUFFIX", os.getenv("MT5_SYMBOL_SUFFIX", ""))

# ── Risk management ───────────────────────────────────────────────────────────
# Symbol-specific risk benchmarks (10% of free margin = N pips of risk)
# Higher benchmark = larger lot size for same SL distance
RISK_PIPS_XAUUSD   = int(os.getenv("RISK_PIPS_XAUUSD", "1000"))   # Gold: 1000 pips benchmark
RISK_PIPS_DEFAULT  = int(os.getenv("RISK_PIPS", "500"))           # fallback for other symbols
RISK_PERCENT       = float(os.getenv("RISK_PERCENT", "0.005"))    # 0.5% equity risk by default

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

# ── Trade split ───────────────────────────────────────────────────────────────
# Split each signal into N equal positions so you can close them independently
# e.g. 5 = five tickets of lot/5 each → close 1 at TP1, 1 at TP2, let rest run
TRADE_SPLIT = int(os.getenv("TRADE_SPLIT", "1"))

# ── Entry price proximity guard ───────────────────────────────────────────────
# Max allowed distance (in pips) between current price and Hafiz's entry zone.
# If price is further away than this, the trade is blocked — signal came too early.
ENTRY_MAX_DISTANCE_PIPS = int(os.getenv("ENTRY_MAX_DISTANCE_PIPS", "50"))

# ── Margin level guard ─────────────────────────────────────────────────────────
# Minimum margin level (%) required before opening a new trade.
# margin_level = equity / used_margin * 100
# Professional floor: 300%. Below 200% = margin call danger zone.
MIN_MARGIN_LEVEL = float(os.getenv("MIN_MARGIN_LEVEL", "300"))

# ── Spread guard (env-specific default) ────────────────────────────────────────
# Max allowed broker spread in pips before entry is blocked.
# XAUUSD normal: 1–2 pips. Wide spread (news/off-hours) eats into your SL.
MAX_SPREAD_PIPS = float(os.getenv(_P + "MAX_SPREAD_PIPS",
                        os.getenv("MAX_SPREAD_PIPS", "3.0")))

# ── Reward:Risk ratio guard ────────────────────────────────────────────────────
# Minimum TP1:SL ratio required to allow the trade.
# 1.0 = break-even math. Professional standard is 1.5.
# If Hafiz's TP is smaller than the SL, skip — bad risk math.
MIN_RR_RATIO = float(os.getenv("MIN_RR_RATIO", "1.4"))

# ── Auto TP enforcement ────────────────────────────────────────────────────────
# If Hafiz's SL is tight (< SL_MIN_PIPS), auto-override TP to TP_ENFORCE_PIPS
# to ensure minimum reward. If SL >= SL_MIN_PIPS, trust Hafiz's TP.
SL_MIN_PIPS      = int(os.getenv("SL_MIN_PIPS",      "50"))   # pips threshold
TP_ENFORCE_PIPS  = int(os.getenv("TP_ENFORCE_PIPS",  "70"))   # override TP to this

# ── Same-direction stack guard ─────────────────────────────────────────────────
# Block opening a new trade if one already exists in the same direction
# on the same symbol. Prevents doubling exposure on a small account.
# Modes: "block" (default) | "reduce" (allow but reduce lot by existing exposure)
BLOCK_SAME_DIRECTION_STACK = os.getenv("BLOCK_SAME_DIRECTION_STACK", "true").lower() == "true"
STACK_MODE = os.getenv("STACK_MODE", "reduce").lower()

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

# Fail closed when MySQL is unavailable. This prevents MT5 entries that cannot
# be tracked in the dashboard/trade tables.
TRADE_REQUIRES_DB = os.getenv("TRADE_REQUIRES_DB", "true").lower() == "true"
MANUAL_TRADE_REQUIRES_DB = os.getenv("MANUAL_TRADE_REQUIRES_DB", "false").lower() == "true"
DB_CONNECT_TIMEOUT_SECS = int(os.getenv("DB_CONNECT_TIMEOUT_SECS", "3"))

# ── Profit Lock (auto-breakeven + TP override when in profit) ────────────────
PROFIT_LOCK_ENABLED = os.getenv("PROFIT_LOCK_ENABLED", "true").lower() == "true"
PROFIT_LOCK_PIPS    = int(os.getenv("PROFIT_LOCK_PIPS", "50"))      # trigger at +N pips profit
PROFIT_LOCK_TP_PIPS = int(os.getenv("PROFIT_LOCK_TP_PIPS", "100"))  # new TP = entry ± N pips

# ── Trailing stop (activates after profit lock fires) ─────────────────────────
# Trails SL every TRAIL_PIPS of further movement in profit direction.
TRAIL_ENABLED = os.getenv("TRAIL_ENABLED", "true").lower() == "true"
TRAIL_PIPS    = int(os.getenv("TRAIL_PIPS", "20"))   # pips of movement before SL is trailed

# ── Session filter (block new entries outside London/NY hours) ────────────────
# Uses UTC. London open=07:00, NY close=21:00. Set START > END for overnight wrap.
SESSION_FILTER_ENABLED  = os.getenv("SESSION_FILTER_ENABLED", "true").lower() == "true"
SESSION_START_HOUR_UTC  = int(os.getenv("SESSION_START_HOUR_UTC", "7"))   # 07:00 UTC = 3pm MYT
SESSION_END_HOUR_UTC    = int(os.getenv("SESSION_END_HOUR_UTC",   "21"))  # 21:00 UTC = 5am MYT

# ── Max loss per day circuit breaker ───────────────────────────────────────────
MAX_DAILY_LOSS_USD      = int(os.getenv("MAX_DAILY_LOSS_USD", "50"))   # stop trading if loss exceeds this
MAX_DCA_LAYERS_PER_SYMBOL = int(os.getenv("MAX_DCA_LAYERS_PER_SYMBOL", "3"))  # max DCA layers per symbol

# ── AutoZone (auto-entry from SNR/SND mapped zones) ─────────────────────────
MAP_ENABLED = os.getenv("MAP_ENABLED", "true").lower() == "true"

# ── Layered DCA entry ─────────────────────────────────────────────────────────
# When enabled, spreads the full position across N layers as price dips deeper.
# Layer count is DYNAMIC: min(LAYER_COUNT, int(total_lot / MIN_LOT))
# → small account → 3 layers, medium → 5, large → 7 (with LAYER_COUNT=7)
LAYER_MODE    = os.getenv("LAYER_MODE",  "false").lower() == "true"
LAYER_COUNT   = int(os.getenv("LAYER_COUNT",  "7"))    # max layers (dynamic floor: 1)
LAYER2_PIPS    = int(os.getenv("LAYER2_PIPS",     "35"))  # pips between each layer
MAX_SUB_SPLITS = int(os.getenv("MAX_SUB_SPLITS", "4"))   # max sub-orders per layer (auto-reduces for small accounts)

# ── Dynamic L2 gap + runway guard ────────────────────────────────────────────
# When L2_GAP_RATIO > 0, layer gap = sl_pips × ratio (replaces fixed LAYER2_PIPS)
# e.g. 50p SL × 0.40 = 20p gap → L2 has 30p runway to SL
# Set to 0 to fall back to fixed LAYER2_PIPS.
L2_GAP_RATIO       = float(os.getenv("L2_GAP_RATIO", "0.40"))
# Minimum runway (pips) between a layer trigger and SL. Skip layer if below this.
L2_MIN_RUNWAY_PIPS = int(os.getenv("L2_MIN_RUNWAY_PIPS", "25"))
# Fraction of total lot allocated to L1 (e.g. 0.30 = 30%). Remaining split equally across L2+.
L1_LOT_RATIO       = float(os.getenv("L1_LOT_RATIO", "0.30"))

# ── Manual trade (/goldbuynow, /goldsellnow) ─────────────────────────────────
MANUAL_SL_PIPS      = int(os.getenv("MANUAL_SL_PIPS",   "50"))       # SL distance from entry
MANUAL_TP1_PIPS     = int(os.getenv("MANUAL_TP1_PIPS",  "50"))      # TP1 distance from entry
MANUAL_TP2_PIPS     = int(os.getenv("MANUAL_TP2_PIPS",  "80"))      # TP2 distance from entry
MANUAL_SYMBOL       = os.getenv("MANUAL_SYMBOL", "XAUUSD").upper()  # default symbol
MANUAL_RISK_PERCENT = float(os.getenv("MANUAL_RISK_PERCENT", "0.10"))  # separate 10% risk
MANUAL_TRADE_DEDUPE_ENABLED = os.getenv("MANUAL_TRADE_DEDUPE_ENABLED", "true").lower() == "true"
MANUAL_TRADE_COOLDOWN_SECS = int(os.getenv("MANUAL_TRADE_COOLDOWN_SECS", "60"))

# Gold specific settings
GOLD_SL_PIPS   = int(os.getenv("GOLD_SL_PIPS",   "50"))
GOLD_TP1_PIPS = int(os.getenv("GOLD_TP1_PIPS",  "50"))
GOLD_TP2_PIPS = int(os.getenv("GOLD_TP2_PIPS",  "80"))


# ── Fib retracement guard (manual trades) ──────────────────────────────────
# Block/warn if price is outside the 0–38.2% pullback zone of the last opposite H1 candle
FIB_GUARD_ENABLED   = os.getenv("FIB_GUARD_ENABLED", "true").lower() == "true"
FIB_MAX_RETRACEMENT = float(os.getenv("FIB_MAX_RETRACEMENT", "0.382"))   # 38.2%

# ── Fib entry scanner (auto-alerts when price enters pullback zone) ───────
FIB_SCANNER_ENABLED  = os.getenv("FIB_SCANNER_ENABLED", "true").lower() == "true"
FIB_SCANNER_INTERVAL = int(os.getenv("FIB_SCANNER_INTERVAL", "60"))      # seconds

# ── Trend analyzer (/trend + auto-alerts) ───────────────────────────────────
TREND_ENABLED    = os.getenv("TREND_ENABLED", "true").lower() == "true"
TREND_INTERVAL   = int(os.getenv("TREND_INTERVAL", "60"))          # seconds between checks
TREND_EMA_SHORT  = int(os.getenv("TREND_EMA_SHORT", "9"))          # fast EMA period
TREND_EMA_LONG   = int(os.getenv("TREND_EMA_LONG", "21"))          # slow EMA period
TREND_RSI_PERIOD = int(os.getenv("TREND_RSI_PERIOD", "14"))        # RSI period

# ── Trading agent schedule (Malaysia time = UTC+8) ────────────────────────────
# Agent runs between these hours MY time (e.g. 22:00 – 06:00 covers London+NY)
AGENT_ENABLED       = os.getenv("AGENT_ENABLED", "false").lower() == "true"
AGENT_START_HOUR_MY = int(os.getenv("AGENT_START_HOUR_MY", "22"))  # 10 PM
AGENT_END_HOUR_MY   = int(os.getenv("AGENT_END_HOUR_MY", "6"))     # 6 AM

# If True, agent auto-executes without asking for confirmation (CAREFUL!)
AGENT_AUTO_EXECUTE  = os.getenv("AGENT_AUTO_EXECUTE", "false").lower() == "true"
AGENT_LIVE_UNLOCKED = os.getenv("AGENT_LIVE_UNLOCKED", "false").lower() == "true"

# Low-risk full-auto strategy mode. Runs beside the Telegram signal bot.
STRATEGY_ENABLED       = os.getenv("STRATEGY_ENABLED", "false").lower() == "true"
STRATEGY_SYMBOL        = os.getenv("STRATEGY_SYMBOL", "XAUUSD").upper()
STRATEGY_TIMEFRAME     = os.getenv("STRATEGY_TIMEFRAME", "M15").upper()
STRATEGY_SCAN_INTERVAL = int(os.getenv("STRATEGY_SCAN_INTERVAL", "60"))
STRATEGY_RISK_PERCENT  = float(os.getenv("STRATEGY_RISK_PERCENT", str(RISK_PERCENT)))
STRATEGY_DAILY_DRAWDOWN_PERCENT = float(os.getenv("STRATEGY_DAILY_DRAWDOWN_PERCENT", "3.0"))
STRATEGY_LIVE_UNLOCKED = os.getenv("STRATEGY_LIVE_UNLOCKED", "false").lower() == "true"
STRATEGY_MIN_RR        = float(os.getenv("STRATEGY_MIN_RR", "1.4"))
STRATEGY_BREAKOUT_LOOKBACK = int(os.getenv("STRATEGY_BREAKOUT_LOOKBACK", "20"))
STRATEGY_RETEST_TOLERANCE_PIPS = int(os.getenv("STRATEGY_RETEST_TOLERANCE_PIPS", "20"))
STRATEGY_CONFIRM_BODY_RATIO = float(os.getenv("STRATEGY_CONFIRM_BODY_RATIO", "0.45"))
STRATEGY_SWING_BUFFER_PIPS = int(os.getenv("STRATEGY_SWING_BUFFER_PIPS", "10"))
STRATEGY_TP_R_MULTIPLE = float(os.getenv("STRATEGY_TP_R_MULTIPLE", "1.5"))
STRATEGY_PARTIAL_CLOSE_R = float(os.getenv("STRATEGY_PARTIAL_CLOSE_R", "1.0"))
STRATEGY_PARTIAL_CLOSE_PERCENT = float(os.getenv("STRATEGY_PARTIAL_CLOSE_PERCENT", "50"))
