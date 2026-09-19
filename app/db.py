"""SQL storage for accounts and practice sessions (report history).

Uses SQLite by default (local / single-container deploys). Set DATABASE_URL to a
Postgres URL on Railway, Render, Fly, etc. Existing data/users.json and
data/sessions.json are imported once if the database is empty.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import datetime, timezone

from sqlalchemy import Boolean, String, JSON, create_engine, func, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from app import config


class Base(DeclarativeBase):
    pass


class UserRow(Base):
    __tablename__ = "users"

    username_key: Mapped[str] = mapped_column(String(24), primary_key=True)
    username: Mapped[str] = mapped_column(String(24), nullable=False)
    parent_email: Mapped[str | None] = mapped_column(String(120), nullable=True)
    salt: Mapped[str] = mapped_column(String(64), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    created: Mapped[str] = mapped_column(String(40), nullable=False)
    tokens: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)


class SessionRow(Base):
    __tablename__ = "sessions"

    session_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    date: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    story_id: Mapped[str] = mapped_column(String(80), nullable=False)
    mode: Mapped[str] = mapped_column(String(16), nullable=False, default="voice")
    seeded: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    username: Mapped[str | None] = mapped_column(String(24), nullable=True, index=True)
    beats: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    summary: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)


def _normalize_url(url: str) -> str:
    # Railway / Heroku style postgres:// → SQLAlchemy + psycopg
    if url.startswith("postgres://"):
        return "postgresql+psycopg://" + url[len("postgres://") :]
    if url.startswith("postgresql://") and "+psycopg" not in url:
        return "postgresql+psycopg://" + url[len("postgresql://") :]
    return url


ENGINE = create_engine(
    _normalize_url(config.DATABASE_URL),
    future=True,
    pool_pre_ping=True,
)
SessionLocal = sessionmaker(bind=ENGINE, autoflush=False, autocommit=False, future=True)


@contextmanager
def session_scope() -> Session:
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def init_db() -> None:
    """Create tables and import legacy JSON once if the database is empty."""
    Base.metadata.create_all(bind=ENGINE)
    _migrate_users_json()
    _migrate_sessions_json()


def storage_info() -> dict:
    url = config.DATABASE_URL
    if url.startswith("sqlite"):
        backend = "sqlite"
    elif "postgres" in url:
        backend = "postgres"
    else:
        backend = "sql"
    with SessionLocal() as db:
        users_n = db.scalar(select(func.count()).select_from(UserRow)) or 0
        sessions_n = db.scalar(select(func.count()).select_from(SessionRow)) or 0
    return {
        "backend": backend,
        "database_url_scheme": url.split(":", 1)[0],
        "users": users_n,
        "sessions": sessions_n,
    }


def _migrate_users_json() -> None:
    path = config.USERS_FILE
    if not path.exists():
        return
    with session_scope() as db:
        if (db.scalar(select(func.count()).select_from(UserRow)) or 0) > 0:
            return
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        users = raw.get("users") if isinstance(raw, dict) else None
        if not isinstance(users, dict):
            return
        for key, u in users.items():
            db.add(
                UserRow(
                    username_key=key.lower(),
                    username=u.get("username") or key,
                    parent_email=u.get("parent_email"),
                    salt=u["salt"],
                    password_hash=u["password_hash"],
                    created=u.get("created") or datetime.now(timezone.utc).isoformat(),
                    tokens=u.get("tokens") or {},
                )
            )


def _migrate_sessions_json() -> None:
    path = config.SESSIONS_FILE
    if not path.exists():
        return
    with session_scope() as db:
        if (db.scalar(select(func.count()).select_from(SessionRow)) or 0) > 0:
            return
        try:
            rows = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if not isinstance(rows, list):
            return
        for s in rows:
            if not s.get("session_id"):
                continue
            db.merge(
                SessionRow(
                    session_id=s["session_id"],
                    date=s.get("date") or "",
                    story_id=s.get("story_id") or "",
                    mode=s.get("mode") or "voice",
                    seeded=bool(s.get("seeded")),
                    username=s.get("username"),
                    beats=s.get("beats") or [],
                    summary=s.get("summary") or {},
                )
            )


# --- Users --------------------------------------------------------------------

def user_get(username_key: str) -> dict | None:
    with SessionLocal() as db:
        row = db.get(UserRow, username_key.lower())
        return _user_to_dict(row) if row else None


def user_exists(username_key: str) -> bool:
    with SessionLocal() as db:
        return db.get(UserRow, username_key.lower()) is not None


def user_create(record: dict) -> None:
    with session_scope() as db:
        key = record["username"].lower()
        if db.get(UserRow, key):
            raise ValueError("That username is already taken. Try logging in.")
        db.add(
            UserRow(
                username_key=key,
                username=record["username"],
                parent_email=record.get("parent_email"),
                salt=record["salt"],
                password_hash=record["password_hash"],
                created=record["created"],
                tokens=record.get("tokens") or {},
            )
        )


def user_save_tokens(username_key: str, tokens: dict) -> None:
    with session_scope() as db:
        row = db.get(UserRow, username_key.lower())
        if not row:
            return
        row.tokens = tokens


def user_find_by_token(token: str) -> dict | None:
    """Return a public user dict if the token is valid; drop expired tokens."""
    now_dt = datetime.now(timezone.utc)
    with session_scope() as db:
        for row in db.scalars(select(UserRow)).all():
            tokens = dict(row.tokens or {})
            exp = tokens.get(token)
            if not exp:
                continue
            try:
                exp_dt = datetime.fromisoformat(exp)
            except ValueError:
                del tokens[token]
                row.tokens = tokens
                return None
            if exp_dt.tzinfo is None:
                exp_dt = exp_dt.replace(tzinfo=timezone.utc)
            if exp_dt <= now_dt:
                del tokens[token]
                row.tokens = tokens
                return None
            return {"username": row.username}
    return None


def user_remove_token(token: str) -> None:
    with session_scope() as db:
        for row in db.scalars(select(UserRow)).all():
            tokens = dict(row.tokens or {})
            if token in tokens:
                del tokens[token]
                row.tokens = tokens
                return


def _user_to_dict(row: UserRow) -> dict:
    return {
        "username": row.username,
        "parent_email": row.parent_email,
        "salt": row.salt,
        "password_hash": row.password_hash,
        "created": row.created,
        "tokens": dict(row.tokens or {}),
    }


# --- Sessions -----------------------------------------------------------------

def session_to_dict(row: SessionRow) -> dict:
    out = {
        "session_id": row.session_id,
        "date": row.date,
        "story_id": row.story_id,
        "mode": row.mode,
        "seeded": bool(row.seeded),
        "beats": row.beats or [],
        "summary": row.summary or {},
    }
    if row.username:
        out["username"] = row.username
    return out


def sessions_load_all(username: str | None = None) -> list:
    """All saved sessions, oldest first.

    If username is set: that child's sessions plus shared seeded demo sessions.
    """
    with SessionLocal() as db:
        rows = list(db.scalars(select(SessionRow).order_by(SessionRow.date.asc())).all())
        if username:
            key = username.lower()
            rows = [
                r for r in rows
                if r.seeded or (r.username and r.username.lower() == key)
            ]
        return [session_to_dict(r) for r in rows]


def sessions_save_all(items: list) -> None:
    """Replace the entire sessions table (used when rewriting history)."""
    with session_scope() as db:
        for r in list(db.scalars(select(SessionRow)).all()):
            db.delete(r)
        db.flush()
        for s in items:
            db.add(
                SessionRow(
                    session_id=s["session_id"],
                    date=s["date"],
                    story_id=s["story_id"],
                    mode=s.get("mode") or "voice",
                    seeded=bool(s.get("seeded")),
                    username=s.get("username"),
                    beats=s.get("beats") or [],
                    summary=s.get("summary") or {},
                )
            )


def sessions_append(item: dict) -> None:
    with session_scope() as db:
        db.merge(
            SessionRow(
                session_id=item["session_id"],
                date=item["date"],
                story_id=item["story_id"],
                mode=item.get("mode") or "voice",
                seeded=bool(item.get("seeded")),
                username=item.get("username"),
                beats=item.get("beats") or [],
                summary=item.get("summary") or {},
            )
        )


def sessions_replace_seeded(seeded: list) -> list:
    with session_scope() as db:
        for r in list(db.scalars(select(SessionRow).where(SessionRow.seeded.is_(True))).all()):
            db.delete(r)
        db.flush()
        for s in seeded:
            db.add(
                SessionRow(
                    session_id=s["session_id"],
                    date=s["date"],
                    story_id=s["story_id"],
                    mode=s.get("mode") or "voice",
                    seeded=True,
                    username=s.get("username"),
                    beats=s.get("beats") or [],
                    summary=s.get("summary") or {},
                )
            )
        db.flush()
        rows = list(db.scalars(select(SessionRow).order_by(SessionRow.date.asc())).all())
        return [session_to_dict(r) for r in rows]
