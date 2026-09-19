"""Accounts: username + password (+ parent email on signup).

Stored in SQL (see app.db). Passwords are salted PBKDF2 hashes. Login returns a
bearer token kept in the browser until logout/expiry.
"""

from __future__ import annotations

import hashlib
import re
import secrets
import threading
from datetime import datetime, timedelta, timezone

from app import db

TOKEN_DAYS = 30
PBKDF2_ROUNDS = 120_000
USERNAME_RE = re.compile(r"^[a-zA-Z0-9_]{3,24}$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

_lock = threading.Lock()


def _now() -> datetime:
    return datetime.now(timezone.utc)


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


def validate_parent_email(email: str) -> str | None:
    email = (email or "").strip()
    if not email:
        return "Please enter a parent's email."
    if len(email) > 120:
        return "Email is too long."
    if not EMAIL_RE.match(email):
        return "Please enter a valid parent email."
    return None


def signup(username: str, password: str, parent_email: str = "") -> dict:
    username = (username or "").strip()
    parent_email = (parent_email or "").strip()
    if err := validate_username(username):
        raise ValueError(err)
    if err := validate_password(password):
        raise ValueError(err)
    if err := validate_parent_email(parent_email):
        raise ValueError(err)

    with _lock:
        salt, pw_hash = _hash_password(password)
        token = secrets.token_urlsafe(32)
        expires = (_now() + timedelta(days=TOKEN_DAYS)).isoformat()
        try:
            db.user_create({
                "username": username,
                "parent_email": parent_email.lower(),
                "salt": salt,
                "password_hash": pw_hash,
                "created": _now().isoformat(),
                "tokens": {token: expires},
            })
        except ValueError:
            raise
    return {"username": username, "token": token}


def login(username: str, password: str) -> dict:
    username = (username or "").strip()
    with _lock:
        user = db.user_get(username.lower())
        if not user or not _verify(password, user["salt"], user["password_hash"]):
            raise ValueError("Wrong username or password.")
        token = secrets.token_urlsafe(32)
        expires = (_now() + timedelta(days=TOKEN_DAYS)).isoformat()
        tokens = dict(user.get("tokens") or {})
        tokens[token] = expires
        tokens = {
            t: exp for t, exp in tokens.items()
            if datetime.fromisoformat(exp) > _now()
        }
        db.user_save_tokens(username.lower(), tokens)
        return {"username": user["username"], "token": token}


def logout(token: str | None) -> None:
    if not token:
        return
    with _lock:
        db.user_remove_token(token)


def user_from_token(token: str | None) -> dict | None:
    if not token:
        return None
    with _lock:
        return db.user_find_by_token(token)
