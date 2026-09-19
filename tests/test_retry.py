"""The targeted-beat retry loop and is_close_match() (offline: the LLM is stubbed)."""

import json

import pytest
from fastapi.testclient import TestClient

from app import companion, config, main, sessions
from app.scoring import is_close_match

# --- is_close_match -------------------------------------------------------------------

@pytest.mark.parametrize("heard, target, reason", [
    ("carrot", "carrot", "exact"),
    ("I think it's a carrot!", "carrot", "exact"),     # extra words and punctuation ignored
    ("CARROT.", "carrot", "exact"),
    ("carrots", "carrot", "inflection"),
    ("swimming", "swim", "inflection"),                # doubled consonant
    ("smiling", "smile", "inflection"),
    ("rrred", "red", "stretched"),
    ("caaarrot", "carrot", "stretched"),
    ("rain bow", "rainbow", "exact"),                  # STT split one word in two
    ("carot", "carrot", "close"),
    ("kerrot", "carrot", "close"),
    ("son", "sun", "close"),                           # classic mishear, same first sound
    ("fower", "flower", "close"),
    ("flour", "flower", "close"),
])
def test_lenient_matches(heard, target, reason):
    r = is_close_match(heard, target)
    assert r["match"] and r["reason"] == reason and r["heard_text"] == heard


@pytest.mark.parametrize("heard, target, reason", [
    ("car", "carrot", "started_word"),                 # got the start, dropped the ending
    ("rain", "rainbow", "started_word"),
    ("wim", "swim", "dropped_start"),                  # dropped the /s/
    ("wed", "red", "different"),                       # first sound swapped on a short word
    ("fun", "sun", "different"),
    ("banana", "carrot", "different"),
    ("", "carrot", "no_response"),
    ("   ", "carrot", "no_response"),
])
def test_real_misses_are_retried_and_reason_kept(heard, target, reason):
    r = is_close_match(heard, target)
    assert not r["match"] and r["reason"] == reason
    assert r["heard_text"] == " ".join(heard.split())  # raw heard text is always kept


# --- the loop, end to end through the API -----------------------------------------------

@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SESSIONS_FILE", tmp_path / "sessions.json")
    replies = []

    def fake_groq(story, beat_index, child_text, outcome, attempt):
        replies.append(outcome)
        target = story["beats"][beat_index]["target_response"]
        text = {
            "retry": f"So close! Let's say it together. Can you say: {target}?",
            "match": f"You said {target}!",
            "not_yet": f"You're doing great. We'll practise {target} next time.",
            None: "What a kind idea.",
        }[outcome]
        return json.dumps({"sentiment": "happy" if outcome == "match" else "neutral", "reaction_text": text})

    monkeypatch.setattr(companion, "_ask_groq", fake_groq)
    c = TestClient(main.app)
    c.replies = replies
    return c


def start(c, story_id="rosie-shares"):
    return c.post("/api/session/start", json={"story_id": story_id}).json()["session_id"]


def say(c, sid, text):
    return c.post(f"/api/session/{sid}/respond", data={"text": text}).json()


def test_match_on_first_try(client):
    sid = start(client)
    r = say(client, sid, "carrot")
    assert r["outcome"] == "match" and r["attempt"] == 1 and r["beat_done"]
    assert r["sound"]["name"] == "happy_chime" and r["next_beat_index"] == 1
    rec = r["beat_record"]
    assert rec["attempts_taken"] == 1 and rec["final_result"] == "match" and rec["target"] == "carrot"


def test_retry_then_match_logs_every_try(client):
    sid = start(client)
    r1 = say(client, sid, "car")
    assert r1["outcome"] == "retry" and r1["attempt"] == 1 and not r1["beat_done"]
    assert r1["next_beat_index"] == 0 and r1["sound"]["name"] == "soft_try_again"
    assert r1["reaction"].endswith("Can you say: carrot?")  # the retry invitation is kept
    r2 = say(client, sid, "carrot")
    assert r2["outcome"] == "match" and r2["attempt"] == 2 and r2["beat_done"]
    rec = r2["beat_record"]
    assert rec["attempts_taken"] == 2
    assert [a["heard_text"] for a in rec["attempts"]] == ["car", "carrot"]
    assert [a["reason"] for a in rec["attempts"]] == ["started_word", "exact"]


def test_three_misses_move_on_warmly(client):
    sid = start(client)
    for n in (1, 2):
        r = say(client, sid, "banana")
        assert r["outcome"] == "retry" and r["attempt"] == n
    r = say(client, sid, "")
    assert r["outcome"] == "not_yet" and r["attempt"] == 3 and r["beat_done"]
    assert r["sound"]["name"] == "gentle_encourage" and r["next_beat_index"] == 1
    rec = r["beat_record"]
    assert rec["attempts_taken"] == "not_yet" and rec["final_result"] == "not_yet"
    assert rec["features"] is None and len(rec["attempts"]) == 3


def test_never_more_than_max_attempts(client):
    sid = start(client)
    beat_indexes = [say(client, sid, "banana")["beat_index"] for _ in range(4)]
    assert beat_indexes == [0, 0, 0, 1]  # the 4th answer already belongs to the next beat


def test_open_beats_are_not_retried(client):
    sid = start(client)
    say(client, sid, "carrot")
    r = say(client, sid, "")  # open beat, silence
    assert r["beat_type"] == "open" and r["outcome"] is None and r["beat_done"]
    assert r["beat_record"]["responded"] is False and r["next_beat_index"] == 2


def test_full_session_is_saved_with_attempt_logs(client):
    sid = start(client)
    answers = [["car", "carrot"], ["help Ferdy"], ["wed", "wed", "wed"], ["share"], ["happy"], ["rainbow"]]
    for tries in answers:
        for text in tries:
            r = say(client, sid, text)
    assert r["sound"]["name"] == "cheer"  # last beat matched
    saved = r["saved_session"]
    targeted = [b for b in saved["beats"] if b["type"] == "targeted"]
    assert [b["attempts_taken"] for b in targeted] == [2, "not_yet", 1, 1]
    assert saved["summary"]["targets_said"] == 3
    on_disk = sessions.load_all()
    assert on_disk[-1]["session_id"] == sid


def test_scolding_retry_line_is_replaced(client, monkeypatch):
    monkeypatch.setattr(companion, "_ask_groq",
                        lambda *a, **k: json.dumps({"sentiment": "neutral", "reaction_text": "That's wrong. Try again: carrot?"}))
    sid = start(client)
    r = say(client, sid, "banana")
    assert r["outcome"] == "retry" and r["reaction_source"] == "fallback"
    assert "wrong" not in r["reaction"].lower() and "carrot" in r["reaction"]


def test_retry_line_must_name_the_word(client, monkeypatch):
    monkeypatch.setattr(companion, "_ask_groq",
                        lambda *a, **k: json.dumps({"sentiment": "neutral", "reaction_text": "Let's have another go together!"}))
    sid = start(client)
    r = say(client, sid, "banana")
    assert r["reaction_source"] == "fallback" and "carrot" in r["reaction"]


def test_targeted_record_uses_successful_attempts_acoustics():
    feats = {"mean_pitch": 250.0, "pitch_variation": 2.0, "speaking_rate": 2.5, "pause_count": 1, "reliable": True}
    attempts = [
        {"attempt": 1, "heard_text": "car", "input": "voice", "match": False, "reason": "started_word", "accuracy": 0.67, "features": {**feats, "mean_pitch": 1.0}},
        {"attempt": 2, "heard_text": "carrot", "input": "voice", "match": True, "reason": "exact", "accuracy": 1.0, "features": feats},
    ]
    rec = sessions.targeted_record(0, "carrot", attempts)
    assert rec["features"] == feats and rec["attempts_taken"] == 2 and rec["heard"] == "carrot"
