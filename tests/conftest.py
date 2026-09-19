"""Pytest fixtures: isolated SQLite DB per test."""

from __future__ import annotations

import os

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


@pytest.fixture(autouse=True)
def _fresh_db(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    url = f"sqlite:///{db_path.as_posix()}"
    monkeypatch.setenv("DATABASE_URL", url)

    import app.config as config
    import app.db as db

    config.DATABASE_URL = url
    db.ENGINE = create_engine(db._normalize_url(url), future=True, pool_pre_ping=True)
    db.SessionLocal = sessionmaker(bind=db.ENGINE, autoflush=False, autocommit=False, future=True)
    db.Base.metadata.drop_all(bind=db.ENGINE)
    db.init_db()
    yield
