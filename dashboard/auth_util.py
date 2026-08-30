"""
dashboard/auth_util.py
Portal session auth helpers.
"""

from __future__ import annotations

import os
import secrets
from functools import wraps
from pathlib import Path

from flask import redirect, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from core.env_editor import get_portal_username, parse_env_file, portal_needs_setup, update_env_values

ROOT = Path(__file__).resolve().parent.parent
SECRET_FILE = ROOT / "data" / "flask_secret.txt"


def get_or_create_secret_key() -> str:
    env_key = (os.getenv("FLASK_SECRET_KEY") or "").strip()
    if env_key:
        return env_key
    values = parse_env_file()
    if values.get("FLASK_SECRET_KEY"):
        return values["FLASK_SECRET_KEY"]
    SECRET_FILE.parent.mkdir(parents=True, exist_ok=True)
    if SECRET_FILE.is_file():
        return SECRET_FILE.read_text(encoding="utf-8").strip()
    key = secrets.token_hex(32)
    SECRET_FILE.write_text(key, encoding="utf-8")
    return key


def forwarded_prefix() -> str:
    """Base path when served behind Laragon PHP proxy, e.g. /jerzagit_botsignal/botsignal"""
    hdr = (request.headers.get("X-Forwarded-Prefix") or "").strip().rstrip("/")
    if hdr:
        return hdr
    cfg = (os.getenv("DASHBOARD_BASE_PATH") or "").strip().rstrip("/")
    return cfg


def path_for(path: str) -> str:
    """Build an app path that works behind a subdirectory proxy."""
    prefix = forwarded_prefix()
    if not path.startswith("/"):
        path = "/" + path
    return (prefix + path) if prefix else path


def verify_login(username: str, password: str) -> bool:
    values = parse_env_file()
    expected_user = values.get("PORTAL_USERNAME") or "admin"
    pw_hash = values.get("PORTAL_PASSWORD_HASH") or ""
    if not pw_hash:
        return False
    if username.strip() != expected_user:
        return False
    return check_password_hash(pw_hash, password)


def set_portal_password(username: str, password: str) -> None:
    update_env_values(
        {
            "PORTAL_USERNAME": username.strip() or "admin",
            "PORTAL_PASSWORD_HASH": generate_password_hash(password),
        },
        delete_keys={"PORTAL_PASSWORD"},
    )


def login_user(username: str) -> None:
    session.clear()
    session["portal_user"] = username
    session.permanent = True


def logout_user() -> None:
    session.clear()


def current_user() -> str | None:
    return session.get("portal_user")


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        from flask import jsonify

        if portal_needs_setup():
            if request.path.startswith("/api/"):
                return jsonify({"error": "setup_required"}), 403
            return redirect(path_for("/setup"))
        if not current_user():
            if request.path.startswith("/api/"):
                return jsonify({"error": "unauthorized"}), 401
            nxt = request.path
            return redirect(path_for("/login") + "?next=" + nxt)
        return view(*args, **kwargs)

    return wrapped
