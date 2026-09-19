"""Demo history: six weekly sessions showing gradual improvement, so the progress trend
and report have something to show before real practice data exists.

Every seeded session carries "seeded": true and is labelled as demo data in the UI.
Re-seeding replaces earlier demo sessions and never touches real ones.
"""

import random
from datetime import datetime, timedelta

from app import scoring, sessions

# What the "child" said for each target word, oldest session first. Scored with the real
# score_response(), so demo accuracy numbers mean exactly what live ones do.
HEARD_BY_SESSION = [
    {"kite": "kite", "brave": "bay", "climb": "", "friend": "fen"},
    {"kite": "kite", "brave": "bave", "climb": "", "friend": "fen"},
    {"kite": "kite", "brave": "bave", "climb": "kime", "friend": "friend"},
    {"kite": "kite", "brave": "brave", "climb": "kime", "friend": "friend"},
    {"kite": "kite", "brave": "brave", "climb": "clim", "friend": "friend"},
    {"kite": "kite", "brave": "brave", "climb": "climb", "friend": "frend"},
]

# Open-question answers: participation grows from one short answer to two full ones.
OPEN_ANSWERS_BY_SESSION = [
    ["", "happy"],
    ["climb", "happy"],
    ["climb the tree", "happy kite"],
    ["Pip should climb up", "the rabbit is happy"],
    ["Pip can climb up the tree slowly", "happy because the kite came back"],
    ["Pip should climb the tree and get the kite", "the rabbit feels happy and says thank you"],
]

PITCH_VARIATION = [1.6, 1.8, 2.1, 2.4, 2.7, 3.0]  # semitones, rising = more expressive
SPEAKING_RATE = [2.2, 2.3, 2.5, 2.6, 2.8, 2.9]    # syllables per second
PAUSES = [3, 3, 2, 2, 1, 1]                      # per targeted answer, on average


def seed_demo_sessions(story: dict, weeks: int = 6) -> list:
    rng = random.Random(7)  # same demo data every time
    today = datetime.now().replace(hour=16, minute=30, second=0, microsecond=0)
    seeded = []
    for k in range(weeks):
        date = today - timedelta(weeks=weeks - k)
        heard = HEARD_BY_SESSION[k]
        open_answers = iter(OPEN_ANSWERS_BY_SESSION[k])
        beats = []
        for index, beat in enumerate(story["beats"]):
            if beat["type"] == "targeted":
                said = heard.get(beat["target_response"], beat["target_response"])
                score = scoring.score_response(said, beat["target_response"])
                beats.append({
                    "index": index, "type": "targeted", "target": beat["target_response"],
                    "heard": said, "input": "voice", "accuracy": score["accuracy"], "match": score["match"],
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
