"""Practice sessions: the in-progress story run, and the saved history in sessions.json.

A saved session looks like:
{
  "session_id": "20260919-014012-ab12", "date": "2026-09-19T01:40:12",
  "story_id": "pip-and-the-kite", "mode": "voice" | "typed" | "mixed", "seeded": false,
  "beats": [
    {"index": 0, "type": "targeted", "target": "kite", "heard": "kite", "input": "voice",
     "accuracy": 1.0, "match": "exact",
     "features": {"mean_pitch", "pitch_variation", "speaking_rate", "pause_count", "reliable"}},
    {"index": 1, "type": "open", "heard": "...", "input": "voice",
     "responded": true, "word_count": 7, "duration_s": 2.1}
  ],
  "summary": {see summarize()}
}
Typed answers are for testing without a mic: those sessions are saved but kept out of
the progress report, because they have no acoustics and aren't real speech.
"""

import json
import os
import secrets
import threading
from datetime import datetime

from app import config

FEATURE_KEYS = ("mean_pitch", "pitch_variation", "speaking_rate", "pause_count")

_lock = threading.Lock()
_active: dict = {}  # session_id -> in-progress session


# --- In-progress sessions ------------------------------------------------------

def start(story: dict) -> dict:
    now = datetime.now()
    session = {
        "session_id": f"{now:%Y%m%d-%H%M%S}-{secrets.token_hex(2)}",
        "date": now.isoformat(timespec="seconds"),
        "story_id": story["id"],
        "story": story,
        "beats": [],
    }
    with _lock:
        _active[session["session_id"]] = session
    return session


def get_active(session_id: str) -> dict | None:
    with _lock:
        return _active.get(session_id)


def record_beat(session: dict, beat_record: dict) -> None:
    session["beats"].append(beat_record)


def finish(session_id: str) -> dict:
    """Close an in-progress session and append it to sessions.json."""
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
    append(saved)
    return saved


# --- Saved history -------------------------------------------------------------

def load_all() -> list:
    if not config.SESSIONS_FILE.exists():
        return []
    try:
        data = json.loads(config.SESSIONS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return sorted(data, key=lambda s: s["date"]) if isinstance(data, list) else []


def save_all(sessions: list) -> None:
    """Atomic write, so a crash mid-save can't corrupt the history."""
    tmp = config.SESSIONS_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(sessions, indent=2), encoding="utf-8")
    os.replace(tmp, config.SESSIONS_FILE)


def append(session: dict) -> None:
    with _lock:
        sessions = load_all()
        sessions.append(session)
        save_all(sessions)


def replace_seeded(seeded: list) -> list:
    """Swap out any earlier demo sessions for `seeded`; real sessions are kept."""
    with _lock:
        sessions = [s for s in load_all() if not s.get("seeded")] + seeded
        sessions.sort(key=lambda s: s["date"])
        save_all(sessions)
    return sessions


def for_report(sessions: list) -> list:
    """Sessions that count as progress: spoken sessions (demo ones included)."""
    return [s for s in sessions if s.get("mode") == "voice"]


# --- Summaries -----------------------------------------------------------------

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
        "targets_said": sum(1 for b in targeted if b["match"] in ("exact", "close")),
        "targets_total": len(targeted),
        **{key: mean(key) for key in FEATURE_KEYS},
        "acoustic_beats": len(measured),
        "open_answered": len(answered),
        "open_total": len(opened),
        "open_avg_words": round(sum(b["word_count"] for b in answered) / len(answered), 1) if answered else 0,
    }
