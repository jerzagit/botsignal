"""
core/env_editor.py
Read / update .env for the web settings portal.
"""

from __future__ import annotations

import os
import re
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = ROOT / ".env"
ENV_EXAMPLE_PATH = ROOT / ".env.example"
BACKUP_DIR = ROOT / "data" / "env_backups"

_LINE_RE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$")


@dataclass(frozen=True)
class Field:
    key: str
    label: str
    type: str  # string | password | int | float | bool | text | select
    help: str = ""
    placeholder: str = ""


@dataclass(frozen=True)
class Section:
    id: str
    title: str
    description: str
    fields: tuple[Field, ...]


def _live_account_fields(prefix: str, slot: int) -> tuple[Field, ...]:
    """prefix: LIVE | LIVE2 | LIVE3 — slot 1 keeps legacy LIVE_* keys."""
    return (
        Field(f"{prefix}_MT5_LABEL", "Display name", "string", f"Shown in Runtime dropdown (e.g. USC Live {slot})"),
        Field(f"{prefix}_MT5_LOGIN", "Login", "string"),
        Field(f"{prefix}_MT5_PASSWORD", "Password", "password"),
        Field(f"{prefix}_MT5_SERVER", "Server", "string"),
        Field(f"{prefix}_MT5_SYMBOL_SUFFIX", "Symbol suffix", "string", "e.g. -STD / -STDc"),
        Field(f"{prefix}_MAX_SPREAD_PIPS", "Max spread (pips)", "float"),
    )


# All configurable keys shown in the portal (grouped).
SECTIONS: tuple[Section, ...] = (
    Section(
        "portal",
        "Portal login",
        "Credentials for this web portal. Password fields leave blank to keep current value.",
        (
            Field("PORTAL_USERNAME", "Username", "string", "Login username"),
            Field("PORTAL_PASSWORD", "New password", "password", "Leave blank to keep current password"),
            Field("FLASK_SECRET_KEY", "Flask secret key", "password", "Session signing key — auto-generated if empty"),
        ),
    ),
    Section(
        "runtime",
        "Runtime mode",
        "Pick which configured MT5 account the bot should use. Options come from Demo + Live slots that have a login filled.",
        (
            Field("ENV_MODE", "Active account", "select", "demo | live | live2 | live3"),
        ),
    ),
    Section(
        "telegram",
        "Telegram",
        "User API + bot token + signal groups.",
        (
            Field("TG_API_ID", "API ID", "string", "from my.telegram.org"),
            Field("TG_API_HASH", "API hash", "password", "from my.telegram.org"),
            Field("TG_PHONE", "Phone (optional)", "string", "Leave blank to use saved Telethon session"),
            Field("BOT_TOKEN", "Bot token", "password", "from @BotFather"),
            Field("YOUR_CHAT_ID", "Your chat ID", "string"),
            Field("SIGNAL_GROUP", "Default signal group", "string", "Numeric ID or @username"),
            Field(
                "SIGNAL_SOURCES",
                "Signal sources",
                "text",
                "source_id:chat:risk:parser:name:auto_execute — comma-separated",
            ),
            Field("SOURCE_RISK_DEFAULT", "Default source risk", "float"),
            Field("SOURCE_RISK_MODE", "Source risk mode", "string", "reduce | block"),
            Field("SOURCE_CONFLICT_MODE", "Source conflict mode", "string", "allow | block"),
            Field("MAX_TOTAL_OPEN_RISK", "Max total open risk", "float"),
            Field("TELEGRAM_DROP_PENDING_UPDATES", "Drop pending bot updates on start", "bool"),
        ),
    ),
    Section(
        "mt5_common",
        "MT5 terminal",
        "Shared terminal behaviour (all accounts).",
        (
            Field("MT5_PATH", "MT5 terminal path", "string"),
            Field("MT5_ATTACH_EXISTING_FIRST", "Attach existing terminal first", "bool"),
            Field("MT5_ALLOW_TERMINAL_LAUNCH", "Allow launching MT5", "bool"),
            Field("MT5_LOCK_CONFIG", "Lock MT5 config", "bool"),
            Field("MT5_AUTO_TOGGLE_AUTOTRADE", "Auto-toggle Algo Trading", "bool"),
            Field("MT5_ALLOW_ACCOUNT_SWITCH", "Allow account switch", "bool"),
            Field("MT5_STARTUP_TIMEOUT_SECS", "Startup timeout (seconds)", "int"),
        ),
    ),
    Section(
        "mt5_demo",
        "MT5 demo account",
        "Demo / UAT credentials. Appears in Runtime when login is set.",
        (
            Field("DEMO_MT5_LABEL", "Display name", "string", "e.g. VT Demo"),
            Field("DEMO_MT5_LOGIN", "Demo login", "string"),
            Field("DEMO_MT5_PASSWORD", "Demo password", "password"),
            Field("DEMO_MT5_SERVER", "Demo server", "string"),
            Field("DEMO_MT5_SYMBOL_SUFFIX", "Demo symbol suffix", "string", "e.g. -VIP"),
            Field("DEMO_MAX_SPREAD_PIPS", "Demo max spread (pips)", "float"),
        ),
    ),
    Section(
        "mt5_live1",
        "MT5 live account 1",
        "Live slot 1 (max 3 live accounts). ENV_MODE=live. Uses legacy LIVE_* keys.",
        _live_account_fields("LIVE", 1),
    ),
    Section(
        "mt5_live2",
        "MT5 live account 2",
        "Live slot 2 — optional. ENV_MODE=live2.",
        _live_account_fields("LIVE2", 2),
    ),
    Section(
        "mt5_live3",
        "MT5 live account 3",
        "Live slot 3 — optional. ENV_MODE=live3.",
        _live_account_fields("LIVE3", 3),
    ),
    Section(
        "database",
        "MySQL database",
        "Dashboard and trade history.",
        (
            Field("DB_HOST", "Host", "string"),
            Field("DB_PORT", "Port", "int"),
            Field("DB_NAME", "Database name", "string"),
            Field("DB_USER", "User", "string"),
            Field("DB_PASSWORD", "Password", "password"),
            Field("DB_CONNECT_TIMEOUT_SECS", "Connect timeout (seconds)", "int"),
            Field("TRADE_REQUIRES_DB", "Block trades if DB down", "bool"),
            Field("MANUAL_TRADE_REQUIRES_DB", "Manual trades require DB", "bool"),
            Field("MANUAL_TRADE_DEDUPE_ENABLED", "Manual trade dedupe", "bool"),
            Field("MANUAL_TRADE_COOLDOWN_SECS", "Manual trade cooldown (seconds)", "int"),
        ),
    ),
    Section(
        "risk",
        "Risk & guards",
        "Lot sizing and pre-trade guards.",
        (
            Field("RISK_PERCENT", "Risk percent", "float", "e.g. 0.005 = 0.5%"),
            Field("RISK_PIPS", "Risk pips (default symbols)", "int"),
            Field("RISK_PIPS_XAUUSD", "Risk pips (XAUUSD)", "int"),
            Field("MIN_LOT", "Min lot", "float"),
            Field("MAX_LOT", "Max lot", "float"),
            Field("MIN_MARGIN_LEVEL", "Min margin level %", "float"),
            Field("MIN_RR_RATIO", "Min reward:risk", "float"),
            Field("ENTRY_MAX_DISTANCE_PIPS", "Entry max distance (pips)", "int"),
            Field("MAX_DAILY_LOSS_USD", "Max daily loss (USD)", "int"),
            Field("MAX_DCA_LAYERS_PER_SYMBOL", "Max DCA layers / symbol", "int"),
            Field("BLOCK_SAME_DIRECTION_STACK", "Block same-direction stack", "bool"),
            Field("STACK_MODE", "Stack mode", "string", "block | reduce"),
            Field("SL_MIN_PIPS", "Auto-TP if SL below (pips)", "int"),
            Field("TP_ENFORCE_PIPS", "Enforced TP (pips)", "int"),
            Field("SL_PIP_SIZE", "Pip size", "float"),
            Field("SL_WARN_MIN_PIPS", "SL warn min (pips)", "int"),
            Field("SL_WARN_MAX_PIPS", "SL warn max (pips)", "int"),
            Field("BREAKEVEN_KEEP_COUNT", "Early-TP keep count", "int"),
        ),
    ),
    Section(
        "timing",
        "Signal timing & session",
        "Watcher window and London/NY session filter.",
        (
            Field("SIGNAL_EXPIRY", "Signal expiry (seconds)", "int"),
            Field("WATCH_INTERVAL_SECS", "Watch interval (seconds)", "int"),
            Field("TRADE_SPLIT", "Trade split (standard mode)", "int"),
            Field("SESSION_FILTER_ENABLED", "Session filter enabled", "bool"),
            Field("SESSION_START_HOUR_UTC", "Session start hour (UTC)", "int"),
            Field("SESSION_END_HOUR_UTC", "Session end hour (UTC)", "int"),
        ),
    ),
    Section(
        "profit",
        "Profit lock & trailing",
        "Auto breakeven / trail behaviour.",
        (
            Field("PROFIT_LOCK_ENABLED", "Profit lock enabled", "bool"),
            Field("PROFIT_LOCK_PIPS", "Profit lock trigger (pips)", "int"),
            Field("PROFIT_LOCK_TP_PIPS", "Profit lock TP (pips)", "int"),
            Field("TRAIL_ENABLED", "Trailing stop enabled", "bool"),
            Field("TRAIL_PIPS", "Trail step (pips)", "int"),
        ),
    ),
    Section(
        "autozone_dca",
        "AutoZone & DCA layers",
        "Mapped zones and layered entries.",
        (
            Field("MAP_ENABLED", "AutoZone enabled", "bool"),
            Field("LAYER_MODE", "Layered DCA mode", "bool"),
            Field("LAYER_COUNT", "Max layers", "int"),
            Field("LAYER2_PIPS", "Layer gap (pips)", "int"),
            Field("MAX_SUB_SPLITS", "Max sub-splits / layer", "int"),
            Field("L2_GAP_RATIO", "Dynamic L2 gap ratio", "float", "0 = use fixed LAYER2_PIPS"),
            Field("L2_MIN_RUNWAY_PIPS", "Min runway to SL (pips)", "int"),
            Field("L1_LOT_RATIO", "L1 lot ratio", "float", "e.g. 0.30 = 30%"),
        ),
    ),
    Section(
        "manual",
        "Manual gold commands",
        "/goldbuynow and /goldsellnow defaults.",
        (
            Field("MANUAL_SYMBOL", "Symbol", "string"),
            Field("MANUAL_SL_PIPS", "SL pips", "int"),
            Field("MANUAL_TP1_PIPS", "TP1 pips", "int"),
            Field("MANUAL_TP2_PIPS", "TP2 pips", "int"),
            Field("MANUAL_RISK_PERCENT", "Manual risk percent", "float"),
            Field("GOLD_SL_PIPS", "Gold SL pips", "int"),
            Field("GOLD_TP1_PIPS", "Gold TP1 pips", "int"),
            Field("GOLD_TP2_PIPS", "Gold TP2 pips", "int"),
        ),
    ),
    Section(
        "fib_trend",
        "Fib & trend tools",
        "Pullback guard/scanner and trend analyzer.",
        (
            Field("FIB_GUARD_ENABLED", "Fib guard enabled", "bool"),
            Field("FIB_MAX_RETRACEMENT", "Fib max retracement", "float"),
            Field("FIB_SCANNER_ENABLED", "Fib scanner enabled", "bool"),
            Field("FIB_SCANNER_INTERVAL", "Fib scanner interval (s)", "int"),
            Field("TREND_ENABLED", "Trend analyzer enabled", "bool"),
            Field("TREND_INTERVAL", "Trend interval (s)", "int"),
            Field("TREND_EMA_SHORT", "EMA short", "int"),
            Field("TREND_EMA_LONG", "EMA long", "int"),
            Field("TREND_RSI_PERIOD", "RSI period", "int"),
        ),
    ),
    Section(
        "agent",
        "Night agent",
        "Scheduled agent window (Malaysia time).",
        (
            Field("AGENT_ENABLED", "Agent enabled", "bool"),
            Field("AGENT_START_HOUR_MY", "Start hour (MYT)", "int"),
            Field("AGENT_END_HOUR_MY", "End hour (MYT)", "int"),
            Field("AGENT_AUTO_EXECUTE", "Auto-execute", "bool"),
            Field("AGENT_LIVE_UNLOCKED", "Live unlock", "bool"),
        ),
    ),
    Section(
        "strategy",
        "Full-auto strategy",
        "Separate strategy engine beside signal bot.",
        (
            Field(
                "ACTIVE_STRATEGY",
                "Active strategy plugin",
                "string",
                help="Registered name, e.g. breakout_retest_v1 (alias: breakout_retest).",
            ),
            Field("STRATEGY_ENABLED", "Strategy enabled", "bool"),
            Field("STRATEGY_SYMBOL", "Symbol", "string"),
            Field("STRATEGY_TIMEFRAME", "Timeframe", "string"),
            Field("STRATEGY_SCAN_INTERVAL", "Scan interval (s)", "int"),
            Field("STRATEGY_RISK_PERCENT", "Risk percent", "float"),
            Field("STRATEGY_DAILY_DRAWDOWN_PERCENT", "Daily drawdown %", "float"),
            Field("STRATEGY_LIVE_UNLOCKED", "Live unlock", "bool"),
            Field("STRATEGY_MIN_RR", "Min RR", "float"),
            Field("STRATEGY_BREAKOUT_LOOKBACK", "Breakout lookback", "int"),
            Field("STRATEGY_RETEST_TOLERANCE_PIPS", "Retest tolerance (pips)", "int"),
            Field("STRATEGY_CONFIRM_BODY_RATIO", "Confirm body ratio", "float"),
            Field("STRATEGY_SWING_BUFFER_PIPS", "Swing buffer (pips)", "int"),
            Field("STRATEGY_TP_R_MULTIPLE", "TP R multiple", "float"),
            Field("STRATEGY_PARTIAL_CLOSE_R", "Partial close R", "float"),
            Field("STRATEGY_PARTIAL_CLOSE_PERCENT", "Partial close %", "float"),
        ),
    ),
    Section(
        "dashboard_safety",
        "Dashboard & safety",
        "Dashboard MT5 probes and live-inject confirmation.",
        (
            Field("DASHBOARD_POLLER_ENABLED", "Dashboard poller enabled", "bool"),
            Field("DASHBOARD_MT5_LIVE_ENABLED", "Dashboard MT5 live widgets", "bool"),
            Field("CONFIRM_LIVE_INJECT", "Confirm live inject", "string", "Set YES only to allow live inject tests"),
            Field("DASHBOARD_BASE_PATH", "Dashboard base path (optional)", "string", "Usually auto from Laragon proxy"),
        ),
    ),
)

SECRET_KEYS = {f.key for s in SECTIONS for f in s.fields if f.type == "password"}
ALL_KEYS = [f.key for s in SECTIONS for f in s.fields]
SECTION_BY_ID = {s.id: s for s in SECTIONS}
VALID_ENV_MODES = {"demo", "live", "live1", "live2", "live3"}


def list_runtime_options(raw_values: dict[str, str] | None = None) -> list[dict[str, str]]:
    """Dropdown options for ENV_MODE based on accounts that have a login set."""
    values = raw_values if raw_values is not None else parse_env_file()
    options: list[dict[str, str]] = []

    demo_login = (values.get("DEMO_MT5_LOGIN") or "").strip()
    if demo_login:
        name = (values.get("DEMO_MT5_LABEL") or "Demo").strip() or "Demo"
        server = (values.get("DEMO_MT5_SERVER") or "").strip()
        label = f"{name} — {demo_login}" + (f" @ {server}" if server else "")
        options.append({"value": "demo", "label": label})

    live_slots = (
        ("live", "LIVE", "Live 1"),
        ("live2", "LIVE2", "Live 2"),
        ("live3", "LIVE3", "Live 3"),
    )
    for mode, prefix, default_name in live_slots:
        login = (values.get(f"{prefix}_MT5_LOGIN") or "").strip()
        if not login:
            continue
        name = (values.get(f"{prefix}_MT5_LABEL") or default_name).strip() or default_name
        server = (values.get(f"{prefix}_MT5_SERVER") or "").strip()
        label = f"{name} — {login}" + (f" @ {server}" if server else "")
        options.append({"value": mode, "label": label})

    if not options:
        options.append({"value": "demo", "label": "Demo (configure an MT5 account first)"})
    return options


def _strip_value(raw: str) -> str:
    raw = raw.strip()
    if not raw:
        return ""
    if raw[0] in "\"'" and len(raw) >= 2 and raw[-1] == raw[0]:
        return raw[1:-1]
    # inline comment
    if " #" in raw:
        raw = raw.split(" #", 1)[0].rstrip()
    return raw


def _quote_value(value: str) -> str:
    if value == "":
        return ""
    if re.search(r'[\s#\'"]', value):
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    return value


def parse_env_file(path: Path | None = None) -> dict[str, str]:
    path = path or ENV_PATH
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        m = _LINE_RE.match(line)
        if not m:
            continue
        values[m.group(1)] = _strip_value(m.group(2))
    return values


def load_env_for_ui() -> dict[str, Any]:
    """Return values for the settings form (secrets masked)."""
    values = parse_env_file()
    # Prefer example defaults for missing display keys
    if ENV_EXAMPLE_PATH.is_file():
        for k, v in parse_env_file(ENV_EXAMPLE_PATH).items():
            values.setdefault(k, v)

    out: dict[str, Any] = {}
    for key in ALL_KEYS:
        val = values.get(key, "")
        if key in SECRET_KEYS:
            out[key] = {
                "set": bool(val),
                "value": "",  # never send secrets to the browser
                "placeholder": "(saved — leave blank to keep)" if val else "",
            }
        else:
            out[key] = {"set": True, "value": val, "placeholder": ""}
    # Portal password uses PORTAL_PASSWORD_HASH under the hood
    hash_set = bool(values.get("PORTAL_PASSWORD_HASH"))
    out["PORTAL_PASSWORD"] = {
        "set": hash_set,
        "value": "",
        "placeholder": "(saved — leave blank to keep)" if hash_set else "Set a password",
    }
    if not out.get("PORTAL_USERNAME", {}).get("value"):
        out["PORTAL_USERNAME"] = {"set": True, "value": "admin", "placeholder": ""}
    return out


def portal_needs_setup() -> bool:
    values = parse_env_file()
    return not bool(values.get("PORTAL_PASSWORD_HASH"))


def get_portal_username() -> str:
    return parse_env_file().get("PORTAL_USERNAME") or "admin"


def backup_env() -> Path | None:
    if not ENV_PATH.is_file():
        return None
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = BACKUP_DIR / f".env.{stamp}.bak"
    shutil.copy2(ENV_PATH, dest)
    return dest


def update_env_values(updates: dict[str, str], *, delete_keys: set[str] | None = None) -> Path | None:
    """
    Update keys in .env, preserving comments/unknown lines.
    Empty string for a secret key means 'leave unchanged' (caller should omit).
    """
    delete_keys = delete_keys or set()
    backup = backup_env()

    if ENV_PATH.is_file():
        lines = ENV_PATH.read_text(encoding="utf-8", errors="replace").splitlines()
    elif ENV_EXAMPLE_PATH.is_file():
        lines = ENV_EXAMPLE_PATH.read_text(encoding="utf-8", errors="replace").splitlines()
    else:
        lines = []

    seen: set[str] = set()
    new_lines: list[str] = []
    for line in lines:
        m = _LINE_RE.match(line)
        if not m:
            new_lines.append(line)
            continue
        key = m.group(1)
        if key in delete_keys:
            continue
        if key in updates:
            new_lines.append(f"{key}={_quote_value(updates[key])}")
            seen.add(key)
        else:
            new_lines.append(line)
            seen.add(key)

    for key, value in updates.items():
        if key not in seen and key not in delete_keys:
            new_lines.append(f"{key}={_quote_value(value)}")

    ENV_PATH.write_text("\n".join(new_lines).rstrip() + "\n", encoding="utf-8")
    return backup


def apply_settings_form(
    form: dict[str, Any],
    *,
    password_hasher,
    section_id: str | None = None,
) -> tuple[bool, str]:
    """
    Apply POSTed settings. password_hasher(plain) -> hash string.
    If section_id is set, only that section's fields are updated.
    Returns (ok, message).
    """
    updates: dict[str, str] = {}
    delete_keys: set[str] = set()

    if section_id:
        section = SECTION_BY_ID.get(section_id)
        if not section:
            return False, f"Unknown section: {section_id}"
        sections = (section,)
    else:
        sections = SECTIONS

    for section in sections:
        for field in section.fields:
            key = field.key
            if key == "PORTAL_PASSWORD":
                continue
            raw = form.get(key)
            if raw is None:
                continue
            if isinstance(raw, list):
                raw = raw[0]
            raw = str(raw)

            if field.type == "bool":
                updates[key] = "true" if raw.lower() in {"1", "true", "yes", "on"} else "false"
                continue

            if field.type == "password":
                if raw.strip() == "":
                    continue  # keep existing
                updates[key] = raw
                continue

            updates[key] = raw.strip()

    # Password change (portal section only)
    if section_id in (None, "portal"):
        new_password = form.get("PORTAL_PASSWORD")
        if isinstance(new_password, list):
            new_password = new_password[0]
        new_password = (new_password or "").strip()
        if new_password:
            if len(new_password) < 8:
                return False, "Portal password must be at least 8 characters."
            updates["PORTAL_PASSWORD_HASH"] = password_hasher(new_password)
            delete_keys.add("PORTAL_PASSWORD")

    if "PORTAL_USERNAME" in updates and not updates["PORTAL_USERNAME"]:
        updates["PORTAL_USERNAME"] = "admin"

    if "ENV_MODE" in updates:
        mode = updates["ENV_MODE"].strip().lower()
        if mode == "live1":
            mode = "live"
        if mode not in VALID_ENV_MODES - {"live1"}:
            return False, "ENV_MODE must be one of: demo, live, live2, live3."
        allowed = {o["value"] for o in list_runtime_options()}
        # Allow selecting a mode even if login temporarily empty after edit in another card
        # but prefer warning if not in allowed and not demo
        if mode not in allowed and mode != "demo":
            return False, f"Account '{mode}' has no login configured yet. Fill the MT5 live card first."
        updates["ENV_MODE"] = mode

    if not updates and not delete_keys:
        return False, "No changes submitted."

    update_env_values(updates, delete_keys=delete_keys)
    title = SECTION_BY_ID[section_id].title if section_id and section_id in SECTION_BY_ID else "Settings"
    return True, f"{title} saved to .env. Restart bot/dashboard for all changes to take effect."


def ensure_env_exists() -> None:
    if ENV_PATH.is_file():
        return
    if ENV_EXAMPLE_PATH.is_file():
        shutil.copy2(ENV_EXAMPLE_PATH, ENV_PATH)
