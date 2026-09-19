"""Practice sessions: in-progress story runs, and saved history in SQL.

A saved session looks like:
{
  "session_id": "20260919-014012-ab12", "date": "2026-09-19T01:40:12",
  "story_id": "pip-and-the-kite", "mode": "voice" | "typed" | "mixed", "seeded": false,
  "username": "kid1",   # optional; set when the child is logged in
  "beats": [...],
  "summary": {see summarize()}
}
Typed answers are for testing without a mic: those sessions are saved but kept out of
the progress report, because they have no acoustics and aren't real speech.
"""

from __future__ import annotations

import secrets
import threading
from datetime import datetime

from app import db

FEATURE_KEYS = ("mean_pitch", "pitch_variation", "speaking_rate", "pause_count")

_lock = threading.Lock()
_active: dict = {}  # session_id -> in-progress session


# --- In-progress sessions ------------------------------------------------------

def start(story: dict, username: str | None = None) -> dict:
    now = datetime.now()
    session = {
        "session_id": f"{now:%Y%m%d-%H%M%S}-{secrets.token_hex(2)}",
        "date": now.isoformat(timespec="seconds"),
        "story_id": story["id"],
        "story": story,
        "beats": [],
        "username": username,
    }
    with _lock:
        _active[session["session_id"]] = session
    return session


def get_active(session_id: str) -> dict | None:
    with _lock:
        return _active.get(session_id)


def record_beat(session: dict, beat_record: dict) -> None:
    session["beats"].append(beat_record)
    session["pending_attempts"] = []


def pending_attempts(session: dict) -> list:
    """Tries so far on the current targeted beat (cleared when the beat is recorded)."""
    return session.setdefault("pending_attempts", [])


def targeted_record(index: int, target: str, attempts: list) -> dict:
    """The saved log for one practice word, once it's finished (matched, or out of tries).

    attempts: [{"attempt", "heard_text", "input", "match", "reason", "accuracy", "features"}]
    Acoustics come from the successful attempt only (None if the word wasn't matched).
    """
    success = next((a for a in attempts if a["match"]), None)
    best = success or max(attempts, key=lambda a: a["accuracy"])
    inputs = {a["input"] for a in attempts}
    return {
        "index": index,
        "type": "targeted",
        "target": target,
        "input": inputs.pop() if len(inputs) == 1 else "mixed",
        "attempts_taken": success["attempt"] if success else "not_yet",
        "final_result": "match" if success else "not_yet",
        "attempts": [{k: a[k] for k in ("attempt", "heard_text", "input", "match", "reason", "accuracy")}
                     for a in attempts],
        "heard": best["heard_text"],
        "accuracy": best["accuracy"],
        "features": success["features"] if success else None,
    }


def finish(session_id: str) -> dict:
    """Close an in-progress session and append it to SQL storage."""
    with _lock:
        session = _active.pop(session_id)
    inputs = {b["input"] for b in session["beats"]}
    saved = {
        "session_id": session["session_id"],
        "date": session["date"],
        "story_id": session["story_id"],
        "mode": inputs.pop() if len(inputs) == 1 else "mixed",
        "seeded": False,
        "beats": session["beats"],
        "summary": summarize(session["beats"]),
    }
    if session.get("username"):
        saved["username"] = session["username"]
    append(saved)
    return saved


# --- Saved history -------------------------------------------------------------

def load_all(username: str | None = None) -> list:
    return db.sessions_load_all(username=username)


def save_all(session_rows: list) -> None:
    db.sessions_save_all(session_rows)


def append(session: dict) -> None:
    with _lock:
        db.sessions_append(session)


def replace_seeded(seeded: list) -> list:
    """Swap out any earlier demo sessions for `seeded`; real sessions are kept."""
    with _lock:
        return db.sessions_replace_seeded(seeded)


def for_report(session_rows: list) -> list:
    """Sessions that count as progress: spoken sessions (demo ones included)."""
    return [s for s in session_rows if s.get("mode") == "voice"]


# --- Summaries -----------------------------------------------------------------

def _said(beat: dict) -> bool:
    """Was the practice word said? Retry-loop records have final_result; older ones match."""
    if "final_result" in beat:
        return beat["final_result"] == "match"
    return beat.get("match") in ("exact", "close")


def _attempts_for(beat: dict) -> int | None:
    taken = beat.get("attempts_taken")
    if isinstance(taken, int) and taken > 0:
        return taken
    if isinstance(beat.get("attempts"), list) and beat["attempts"]:
        return len(beat["attempts"])
    # Legacy beats without attempt logs: count a matched word as one try.
    if _said(beat):
        return 1
    return None


def avg_attempts_from_beats(beats: list) -> float | None:
    vals = [n for b in beats if b.get("type") == "targeted" and (n := _attempts_for(b)) is not None]
    return round(sum(vals) / len(vals), 2) if vals else None


def summarize(beats: list) -> dict:
    targeted = [b for b in beats if b["type"] == "targeted"]
    opened = [b for b in beats if b["type"] == "open"]
    measured = [b["features"] for b in targeted if b.get("features") and b["features"].get("mean_pitch") is not None]

    def mean(key):
        values = [f[key] for f in measured if f.get(key) is not None]
        return round(sum(values) / len(values), 2) if values else None

    answered = [b for b in opened if b.get("responded")]
    return {
        "targeted_accuracy": round(sum(b["accuracy"] for b in targeted) / len(targeted), 2) if targeted else None,
        "targets_said": sum(1 for b in targeted if _said(b)),
        "targets_total": len(targeted),
        "avg_attempts_per_word": avg_attempts_from_beats(beats),
        **{key: mean(key) for key in FEATURE_KEYS},
        "acoustic_beats": len(measured),
        "open_answered": len(answered),
        "open_total": len(opened),
        "open_avg_words": round(sum(b["word_count"] for b in answered) / len(answered), 1) if answered else 0,
    }
