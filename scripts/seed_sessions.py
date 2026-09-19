"""Add (or refresh) six weeks of labelled demo sessions in data/sessions.json.

    python -m scripts.seed_sessions          # add / refresh demo sessions
    python -m scripts.seed_sessions --remove # remove them again

Real sessions are never touched. Demo sessions carry "seeded": true.
"""

import sys

from app import sessions, storygen
from app.seed import seed_demo_sessions


def main() -> None:
    if "--remove" in sys.argv:
        remaining = sessions.replace_seeded([])
        print(f"Removed demo sessions. {len(remaining)} real session(s) remain.")
        return
    all_sessions = sessions.replace_seeded(seed_demo_sessions(storygen.demo_story()))
    for s in all_sessions:
        m = s["summary"]
        tag = "demo" if s.get("seeded") else s.get("mode", "")
        print(f"{s['date'][:10]}  {tag:6}  accuracy {m['targeted_accuracy']}  pitch_variation {m['pitch_variation']}")


if __name__ == "__main__":
    main()
