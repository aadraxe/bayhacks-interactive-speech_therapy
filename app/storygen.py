"""Story writer: the Groq LLM writes each 6-beat interactive story; stories.json keeps them.

Every generated story is validated before a child sees it (beat pattern, target words,
lengths, no pronouns, one question per beat, gentle content). A failing draft goes back to
the model with the problem named, up to MAX_ATTEMPTS tries; if none pass, or Groq is
unavailable, the built-in demo story is used instead.
"""

import json
import os
import re
import secrets
import threading
from datetime import datetime

import httpx

from app import config, groq

STORY_SYSTEM_PROMPT = """You write short interactive stories for StoryBuddy, a speech-practice companion for verbal, reading-age autistic children aged 6-11. A warm narrator reads each part of the story aloud, then pauses so the child can answer out loud.

Write exactly 6 beats, in this order: targeted, open, targeted, targeted, open, targeted.
- targeted beat: the narration uses the TARGET WORD at least once, so the child hears it first. Then the prompt asks the child to say that one word, for example: "Can you say the word shell?" Set target_response to the word in lowercase.
- open beat: the narration sets up a small choice or feeling, then the prompt asks one open question with many good answers, for example: "What do you think Tilly should do?" Set target_response to null.

Story shape (one connected story, each beat follows from the one before):
1. Meet the main character and the place. Something catches their eye.
2. A small, gentle problem appears (something is stuck, missing, or needs making). Ask what the character should do.
3. The character starts to fix the problem.
4. The friendly helper joins in and helps.
5. The problem is almost solved; the character feels something or makes a choice. Ask about it.
6. The problem is solved kindly. A calm, happy ending.

Language:
- Literal, concrete words. No idioms, sarcasm, or figures of speech.
- Never use a word with two meanings in the same story (for example "wave" the water and "wave" the hand).
- Short sentences of 12 words or fewer. Simple past tense.
- Each narration is 2 or 3 sentences, between 80 and 280 characters, with no questions in it. Each prompt is one sentence and is the only question in the beat.
- Never use he, she, him, her, his or hers. Repeat the character's name instead; it is easier to follow.

Characters:
- One main character (an animal or a child) with a simple name, and one friendly helper with a different simple name.
- Do not use any name listed in avoid_names.
- Calm and predictable. Nothing scary, sad, dangerous, violent or loud: no villains, monsters, getting lost, injuries or loss.

Target words:
- Four different words that are concrete and easy to picture, with 1 or 2 syllables. Not names.
- If target_words are given, use exactly those words, in that order, one per targeted beat.

Return only JSON in this shape:
{"title": "3 to 6 words", "main_character": "name", "helper": "name", "beats": [{"type": "targeted", "narration": "...", "prompt": "...", "target_response": "word"}, ...]}"""

PATTERN = ["targeted", "open", "targeted", "targeted", "open", "targeted"]
MAX_ATTEMPTS = 3      # low reasoning is ~1.2K tokens a try, so three fit the free tier
MIN_NARRATION = 60    # slack under the 80 asked for: a short happy last line is fine
MAX_NARRATION = 320   # and over the 280
MAX_PROMPT = 160
PRONOUNS = re.compile(r"\b(he|she|him|her|his|hers)\b", re.IGNORECASE)
UNSAFE = re.compile(
    r"\b(die[sd]?|dead|death|kill\w*|blood\w*|monsters?|scary|scared of the dark|ghosts?|guns?|weapons?"
    r"|hate|stupid|lost forever|hurt\w*|injur\w*|cry(ing)?|scream\w*|villains?)\b",
    re.IGNORECASE,
)
DEMO_STORY_FILE = config.DATA_DIR / "demo_story.json"

_lock = threading.Lock()


class StoryError(Exception):
    """A story could not be produced or found."""


# --- Generation ------------------------------------------------------------------

def generate_story(theme: str | None = None, target_words: list | None = None) -> dict:
    """Write, validate and save a new story. Falls back to the demo story on failure.

    Returns the story dict, with "source": "groq" or "fallback" and, on fallback, "note".
    """
    theme = " ".join((theme or "").split())[:80] or None
    words = [w.strip().lower() for w in (target_words or []) if w and w.strip()]
    if words and (len(words) != 4 or len(set(words)) != 4 or not all(re.fullmatch(r"[a-z]{2,15}", w) for w in words)):
        raise StoryError("Give exactly 4 different practice words (letters only), or leave them blank.")

    recent = load_stories()[-8:]
    request = {
        "theme": theme or "any gentle everyday adventure",
        "target_words": words or None,
        "avoid_titles": [s["title"] for s in recent],
        "avoid_names": sorted({n for s in recent for n in (s.get("main_character"), s.get("helper")) if n}),
    }
    problem, raw = None, None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            raw = _ask_groq(request, previous=raw if problem else None, problem=problem)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 400:  # Groq's JSON mode rejected a malformed reply
                problem, raw = "was not valid JSON", None
                continue
            problem = f"could not be written (Groq returned {exc.response.status_code})"
            break
        except (httpx.HTTPError, KeyError) as exc:
            problem = f"could not be written (Groq unavailable: {exc.__class__.__name__})"
            break
        except ValueError:
            problem, raw = "was not valid JSON", None
            continue
        story, problem = _validate(raw, words)
        if story:
            story.update({
                "id": _story_id(story["title"]),
                "created": datetime.now().isoformat(timespec="seconds"),
                "theme": theme,
                "source": "groq",
                "attempts": attempt,
            })
            save_story(story)
            return story

    demo = demo_story()
    demo.update(source="fallback", note=f"Used the built-in story because the new story {problem}.")
    return demo


def _ask_groq(request: dict, previous: dict | None, problem: str | None) -> dict:
    messages = [
        {"role": "system", "content": STORY_SYSTEM_PROMPT},
        {"role": "user", "content": json.dumps(request)},
    ]
    if problem and previous:
        # Show the draft so the model fixes the one problem instead of writing a new story
        # (which tends to bring new mistakes).
        messages += [
            {"role": "assistant", "content": json.dumps(previous)},
            {"role": "user", "content": f"This story {problem}. Fix that, keep everything else the same, and check every rule again."},
        ]
    elif problem:
        messages.append({"role": "user", "content": f"Your last answer {problem}. Return only the JSON story."})
    text = groq.chat(
        messages,
        max_wait_s=20,  # someone pressed "write a story"; a short wait beats a fallback
        temperature=0.9,
        max_completion_tokens=3000,
        reasoning_effort="low",  # medium spends ~2.4K tokens thinking and can truncate the JSON
        response_format={"type": "json_object"},
    )
    return json.loads(text)


def _validate(raw: dict, words: list):
    """(clean story, None) if it passes, else (None, what was wrong)."""
    if not isinstance(raw, dict):
        return None, "was not a JSON object"
    title = " ".join(str(raw.get("title", "")).split())
    beats = raw.get("beats")
    if not title or len(title) > 60:
        return None, "needs a short title"
    if not isinstance(beats, list) or [b.get("type") if isinstance(b, dict) else None for b in beats] != PATTERN:
        return None, "must have 6 beats in the order targeted, open, targeted, targeted, open, targeted"

    clean, targets = [], []
    for i, b in enumerate(beats, 1):
        narration = " ".join(str(b.get("narration", "")).split())
        prompt = " ".join(str(b.get("prompt", "")).split())
        if not MIN_NARRATION <= len(narration) <= MAX_NARRATION:
            return None, f"has a beat {i} narration of {len(narration)} characters; it must be 80 to 280"
        pronoun = PRONOUNS.search(f"{narration} {prompt}")
        if pronoun:
            return None, f'uses "{pronoun.group(0)}" in beat {i}; repeat the character\'s name instead'
        if not prompt or len(prompt) > MAX_PROMPT:
            return None, f"has a beat {i} prompt that is missing or too long"
        if "?" in narration:
            return None, f"asks a question inside the beat {i} narration; only the prompt may ask one question"
        if UNSAFE.search(f"{narration} {prompt}"):
            return None, f'uses the word "{UNSAFE.search(f"{narration} {prompt}").group(0)}", which is not gentle enough'
        if b["type"] == "targeted":
            word = str(b.get("target_response") or "").strip().lower().strip(".!?\"'")
            if not re.fullmatch(r"[a-z]{2,15}", word):
                return None, f"needs a single-word target_response on beat {i}"
            if not re.search(rf"\b{word}\b", prompt.lower()):
                return None, f'must ask the child to say "{word}" in the beat {i} prompt'
            if not re.search(rf"\b{word}\b", narration.lower()):
                return None, f'must use "{word}" in the beat {i} narration so the child hears it first'
            targets.append(word)
        else:
            word = None
            if not prompt.endswith("?"):
                return None, f"needs an open question on beat {i}"
        clean.append({"type": b["type"], "narration": narration, "prompt": prompt, "target_response": word})

    if len(set(targets)) != 4:
        return None, "must use four different target words"
    if words and targets != words:
        return None, f"must use the practice words {', '.join(words)} in that order"
    return {"title": title, "main_character": str(raw.get("main_character", ""))[:40],
            "helper": str(raw.get("helper", ""))[:40], "beats": clean, "target_words": targets}, None


def _story_id(title: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:40] or "story"
    return f"{slug}-{secrets.token_hex(2)}"


# --- Storage -------------------------------------------------------------------

def demo_story() -> dict:
    story = json.loads(DEMO_STORY_FILE.read_text(encoding="utf-8"))
    story.pop("note", None)
    story["target_words"] = [b["target_response"] for b in story["beats"] if b["type"] == "targeted"]
    return story


def load_stories() -> list:
    if not config.STORIES_FILE.exists():
        return []
    try:
        data = json.loads(config.STORIES_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return data if isinstance(data, list) else []


def save_story(story: dict) -> None:
    with _lock:
        stories = load_stories()
        stories.append(story)
        tmp = config.STORIES_FILE.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(stories, indent=2), encoding="utf-8")
        os.replace(tmp, config.STORIES_FILE)


def get_story(story_id: str | None) -> dict:
    """A saved story by id; the newest saved story if no id; the demo story if none saved."""
    stories = load_stories()
    if story_id:
        if story_id == demo_story()["id"]:
            return demo_story()
        for story in stories:
            if story["id"] == story_id:
                return story
        raise StoryError("That story doesn't exist.")
    return stories[-1] if stories else demo_story()
