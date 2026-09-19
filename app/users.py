"""Simple local accounts: username + password stored in data/users.json.

Passwords are salted PBKDF2 hashes (stdlib only). Login returns a bearer token
kept in the browser; tokens are listed on the user record until logout/expiry.
"""

from __future__ import annotations

import hashlib
import json
import re
import secrets
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app import config

USERS_FILE = config.DATA_DIR / "users.json"
TOKEN_DAYS = 30
PBKDF2_ROUNDS = 120_000
USERNAME_RE = re.compile(r"^[a-zA-Z0-9_]{3,24}$")

_lock = threading.Lock()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _load() -> dict:
    if not USERS_FILE.exists():
        return {"users": {}}
    try:
        return json.loads(USERS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"users": {}}


def _save(data: dict) -> None:
    USERS_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = USERS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tmp.replace(USERS_FILE)


def _hash_password(password: str, salt: bytes | None = None) -> tuple[str, str]:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ROUNDS)
    return salt.hex(), digest.hex()


def _verify(password: str, salt_hex: str, hash_hex: str) -> bool:
    try:
        salt = bytes.fromhex(salt_hex)
    except ValueError:
        return False
    _, check = _hash_password(password, salt)
    return secrets.compare_digest(check, hash_hex)


def validate_username(username: str) -> str | None:
    if not USERNAME_RE.match(username or ""):
        return "Username must be 3–24 letters, numbers, or underscores."
    return None


def validate_password(password: str) -> str | None:
    if not password or len(password) < 4:
        return "Password must be at least 4 characters."
    if len(password) > 72:
        return "Password is too long."
    return None


def signup(username: str, password: str) -> dict:
    username = (username or "").strip()
    if err := validate_username(username):
        raise ValueError(err)
    if err := validate_password(password):
        raise ValueError(err)

    with _lock:
        data = _load()
        key = username.lower()
        if key in data["users"]:
            raise ValueError("That username is already taken. Try logging in.")
        salt, pw_hash = _hash_password(password)
        token = secrets.token_urlsafe(32)
        expires = (_now() + timedelta(days=TOKEN_DAYS)).isoformat()
        data["users"][key] = {
            "username": username,
            "salt": salt,
            "password_hash": pw_hash,
            "created": _now().isoformat(),
            "tokens": {token: expires},
        }
        _save(data)
    return {"username": username, "token": token}


def login(username: str, password: str) -> dict:
    username = (username or "").strip()
    with _lock:
        data = _load()
        user = data["users"].get(username.lower())
        if not user or not _verify(password, user["salt"], user["password_hash"]):
            raise ValueError("Wrong username or password.")
        token = secrets.token_urlsafe(32)
        expires = (_now() + timedelta(days=TOKEN_DAYS)).isoformat()
        user.setdefault("tokens", {})[token] = expires
        # Drop expired tokens
        user["tokens"] = {
            t: exp for t, exp in user["tokens"].items()
            if datetime.fromisoformat(exp) > _now()
        }
        _save(data)
        return {"username": user["username"], "token": token}


def logout(token: str | None) -> None:
    if not token:
        return
    with _lock:
        data = _load()
        for user in data["users"].values():
            if token in user.get("tokens", {}):
                del user["tokens"][token]
                _save(data)
                return


def user_from_token(token: str | None) -> dict | None:
    if not token:
        return None
    with _lock:
        data = _load()
        changed = False
        found = None
        for user in data["users"].values():
            tokens = user.get("tokens", {})
            exp = tokens.get(token)
            if not exp:
                continue
            if datetime.fromisoformat(exp) <= _now():
                del tokens[token]
                changed = True
                break
            found = {"username": user["username"]}
            break
        if changed:
            _save(data)
        return found
