"""Demo history: six weekly sessions showing gradual improvement, so the progress trend
and report have something to show before real practice data exists.

Every seeded session carries "seeded": true and is labelled as demo data in the UI.
Re-seeding replaces earlier demo sessions and never touches real ones.
"""

import random
from datetime import datetime, timedelta

from app import scoring, sessions

# How each of the story's four practice words went, oldest session first.
#   exact = said it; close = near miss (e.g. "brav"); missed = a different sound; quiet = no answer.
# Heard text is built from the real word and scored with the real score_response(), so demo
# accuracy numbers mean exactly what live ones do.
OUTCOMES_BY_SESSION = [
    ["exact", "missed", "quiet", "close"],
    ["exact", "close", "quiet", "close"],
    ["exact", "close", "missed", "exact"],
    ["exact", "exact", "missed", "exact"],
    ["exact", "exact", "close", "exact"],
    ["exact", "exact", "exact", "close"],
]

# Open-question answers: participation grows from one short answer to two full ones.
OPEN_ANSWERS_BY_SESSION = [
    ["", "happy"],
    ["help", "happy"],
    ["help the friend", "happy now"],
    ["share with the friend", "the friend is happy"],
    ["go and help the friend right away", "happy because they helped"],
    ["go over and help the friend who is alone", "happy and thankful, and they are friends now"],
]

PITCH_VARIATION = [1.6, 1.8, 2.1, 2.4, 2.7, 3.0]  # semitones, rising = more expressive
SPEAKING_RATE = [2.2, 2.3, 2.5, 2.6, 2.8, 2.9]    # syllables per second
PAUSES = [3, 3, 2, 2, 1, 1]                      # per targeted answer, on average
ATTEMPTS_BY_OUTCOME = {"exact": 1, "close": 2, "missed": 3, "quiet": 3}


def heard_for(word: str, outcome: str) -> str:
    if outcome == "exact":
        return word
    if outcome == "close":
        return word[:-1] if len(word) > 3 else word + "s"
    if outcome == "missed":
        return word[:2]
    return ""


def seed_demo_sessions(story: dict, weeks: int = 6) -> list:
    rng = random.Random(7)  # same demo data every time
    today = datetime.now().replace(hour=16, minute=30, second=0, microsecond=0)
    seeded = []
    for k in range(weeks):
        date = today - timedelta(weeks=weeks - k)
        outcomes = iter(OUTCOMES_BY_SESSION[k])
        open_answers = iter(OPEN_ANSWERS_BY_SESSION[k])
        beats = []
        for index, beat in enumerate(story["beats"]):
            if beat["type"] == "targeted":
                outcome = next(outcomes)
                said = heard_for(beat["target_response"], outcome)
                score = scoring.score_response(said, beat["target_response"])
                attempts_taken = ATTEMPTS_BY_OUTCOME[outcome]
                beats.append({
                    "index": index, "type": "targeted", "target": beat["target_response"],
                    "heard": said, "input": "voice", "accuracy": score["accuracy"], "match": score["match"],
                    "attempts_taken": attempts_taken,
                    "final_result": "match" if score["match"] in ("exact", "close") else "not_yet",
                    "features": None if not said else {
                        "mean_pitch": round(262 + rng.uniform(-12, 12), 1),
                        "pitch_variation": round(PITCH_VARIATION[k] + rng.uniform(-0.25, 0.25), 2),
                        "speaking_rate": round(SPEAKING_RATE[k] + rng.uniform(-0.15, 0.15), 2),
                        "pause_count": max(0, PAUSES[k] + rng.choice((-1, 0, 0, 1))),
                        "reliable": True,
                    },
                })
            else:
                said = next(open_answers, "")
                words = len(said.split())
                beats.append({
                    "index": index, "type": "open", "heard": said, "input": "voice",
                    "responded": bool(said), "word_count": words,
                    "duration_s": round(words * 0.45 + 0.4, 1) if said else 0.0,
                })
        seeded.append({
            "session_id": f"demo-{date:%Y%m%d}",
            "date": date.isoformat(timespec="seconds"),
            "story_id": story["id"],
            "mode": "voice",
            "seeded": True,
            "beats": beats,
            "summary": sessions.summarize(beats),
        })
    return seeded
