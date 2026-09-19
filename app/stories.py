"""Pre-written interactive stories, loaded from stories/*.json (no live generation).

Each story: id, title, moral, target_sounds, and 6 beats in the order
targeted, open, targeted, targeted, open, targeted. Every beat has id, type, narration,
prompt, target_response (the practice word, or null for open beats) and sound_effect.

Stories are checked when loaded; a file that breaks the rules is skipped (and the tests
fail), so a child never gets a half-broken story.
"""

import json
import re

from app import config

STORIES_DIR = config.BASE_DIR / "stories"
PATTERN = ["targeted", "open", "targeted", "targeted", "open", "targeted"]
PRONOUNS = re.compile(r"\b(he|she|him|her|his|hers)\b", re.IGNORECASE)  # names are easier to follow
UNSAFE = re.compile(
    r"\b(die[sd]?|dead|death|kill\w*|blood\w*|monsters?|scary|ghosts?|guns?|weapons?"
    r"|hate|stupid|hurt\w*|injur\w*|scream\w*|villains?)\b",
    re.IGNORECASE,
)


class StoryError(Exception):
    """A story could not be found or is invalid."""


def validate(story: dict) -> list:
    """Problems with a story (empty list = valid)."""
    problems = []
    for key in ("id", "title", "moral", "target_sounds", "beats"):
        if not story.get(key):
            problems.append(f"missing {key}")
    beats = story.get("beats") or []
    if [b.get("type") for b in beats] != PATTERN:
        return problems + ["beats must be targeted, open, targeted, targeted, open, targeted"]

    targets = []
    for n, b in enumerate(beats, 1):
        where = f"beat {n}"
        narration, prompt = b.get("narration", ""), b.get("prompt", "")
        if b.get("id") != n:
            problems.append(f"{where}: id should be {n}")
        if not narration or not prompt:
            problems.append(f"{where}: needs narration and prompt")
        if "?" in narration:
            problems.append(f"{where}: only the prompt may ask a question")
        if not prompt.endswith("?"):
            problems.append(f"{where}: prompt should be a question")
        if not re.fullmatch(r"[a-z_]+", str(b.get("sound_effect") or "")):
            problems.append(f"{where}: needs a sound_effect cue (lowercase name)")
        for pattern, label in ((PRONOUNS, "use names instead of"), (UNSAFE, "not gentle enough:")):
            hit = pattern.search(f"{narration} {prompt}")
            if hit:
                problems.append(f'{where}: {label} "{hit.group(0)}"')
        word = b.get("target_response")
        if b.get("type") == "targeted":
            if not word or not re.fullmatch(r"[a-z]+", word):
                problems.append(f"{where}: target_response must be one lowercase word")
                continue
            targets.append(word)
            if not re.search(rf"\b{word}\b", prompt.lower()):
                problems.append(f'{where}: prompt must ask for "{word}"')
            if not re.search(rf"\b{word}\b", narration.lower()):
                problems.append(f'{where}: narration should use "{word}" so the child hears it first')
        elif word is not None:
            problems.append(f"{where}: open beats have target_response null")

    if len(set(targets)) != 4:
        problems.append("needs four different practice words")
    for sound in story.get("target_sounds") or []:
        missing = [w for w in targets if sound not in w]
        if missing:
            problems.append(f'target sound "{sound}" is not in: {", ".join(missing)}')
    return problems


def load_all() -> list:
    """Every valid story, in a stable order (by id)."""
    stories = []
    for path in sorted(STORIES_DIR.glob("*.json")):
        try:
            story = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not validate(story):
            story["target_words"] = [b["target_response"] for b in story["beats"] if b["type"] == "targeted"]
            stories.append(story)
    return stories


def get(story_id: str) -> dict:
    for story in load_all():
        if story["id"] == story_id:
            return story
    raise StoryError("That story doesn't exist.")


def next_in_rotation(past_sessions: list) -> dict:
    """The story used least recently (never-used stories first), so a child sees variety."""
    stories = load_all()
    if not stories:
        raise StoryError("No stories found in the stories/ folder.")
    last_used = {}
    for s in past_sessions:
        if not s.get("seeded"):
            last_used[s["story_id"]] = max(last_used.get(s["story_id"], ""), s["date"])
    return min(stories, key=lambda st: last_used.get(st["id"], ""))
