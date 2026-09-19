"""The companion's reaction to each answer: specific, warm words plus a sentiment.

The Groq LLM returns {"sentiment", "reaction_text"}. The sentiment picks a soft sound
(config.SOUND_FOR_SENTIMENT); the text is spoken by the narrator. If the reply isn't
valid JSON with an allowed sentiment, the sentiment falls back to "neutral" (soft_pop)
and a gentle built-in line is used, so the story never stalls in front of a child.
"""

import json
import random
import re

import httpx

from app import config, groq

SENTIMENTS = ("happy", "correct", "sad", "frustrated", "neutral")
MAX_RETRY_WAIT_S = 4.0  # free tier is 8K tokens/min; a short wait usually clears a 429

REACTION_SYSTEM_PROMPT = """You are StoryBuddy's warm companion, talking WITH a child (age 6-11) inside an interactive story. You receive: the child's transcribed reply, the current beat (narration + prompt + target word if any), and the story's title and moral.

Return ONLY valid JSON:
{ "sentiment": one of ["happy","correct","sad","frustrated","neutral"],
  "reaction_text": "1-3 short, warm sentences spoken back to the child" }

HOW TO REACT (be specific, never generic):
- ALWAYS reference something concrete from what the child said or the story moment. Reflect their actual words back.
  Bad: "Oh that's sad."
  Good: "You said the bunny felt lonely — that's such a kind thing to notice. I think the bunny is lucky you understand how it feels."
- happy/correct: celebrate the SPECIFIC thing they did well, then build the story forward with a little excitement.
- sad/negative: comfort warmly and specifically, naming the feeling they showed and validating it, THEN gently guide back. Never scold or sound disappointed.
- frustrated / "I don't know": lower the pressure, reassure, offer a tiny easy way back in ("That's okay — let's just imagine together...").
- If they struggled with the target word: stay positive, no correction pressure, warmly invite trying again another time.
- Near the story's end, gently explain WHY the character acted as they did, tying it to the moral in ONE simple, concrete sentence the child can grasp ("The bunny shared his carrot because being kind helps friends feel happy").

VOICE: warm, specific, calm, literal. Talk like a caring friend who was really listening — not a script. No idioms, no long speeches, no pressure. Vary your wording every time; never reuse a stock phrase.
sentiment must match the child's emotional tone so the app plays the right sound.
Never diagnose or correct harshly. Output ONLY the JSON."""

# Used only when the LLM can't be reached or returns something unusable.
FALLBACK_HIT = ["You said {target}! Well done.", "{Target}! You said it."]
FALLBACK_MISS = ["Thank you for trying. We can say {target} again another time.",
                 "Good trying. {Target} is a fun word to practise."]
FALLBACK_OPEN = ["Thank you for telling me your idea. Let's see what happens next.",
                 "I like hearing your idea. Let's keep going with the story."]
FALLBACK_SILENT = ["That's okay. We can listen to the story together.",
                   "That's okay. Let's see what happens next."]


def react(story: dict, beat_index: int, child_text: str, score: dict | None) -> dict:
    """Return {"sentiment", "reaction_text", "source": "groq"|"fallback", "fallback_reason"?}."""
    beat = story["beats"][beat_index]
    try:
        reply = _ask_groq(story, beat_index, child_text, score)
        parsed, problem = parse_reaction(reply)
        if parsed:
            return {**parsed, "source": "groq"}
        reason = problem
    except httpx.HTTPStatusError as exc:
        reason = f"Groq returned {exc.response.status_code}"
    except (httpx.HTTPError, KeyError) as exc:
        reason = f"Groq unavailable ({exc.__class__.__name__})"
    return {
        "sentiment": "neutral",
        "reaction_text": _fallback_text(beat, child_text, score),
        "source": "fallback",
        "fallback_reason": reason,
    }


def _ask_groq(story: dict, beat_index: int, child_text: str, score: dict | None) -> str:
    beat = story["beats"][beat_index]
    situation = {
        "story_title": story["title"],
        "story_moral": story["moral"],
        "beat_number": beat_index + 1,
        "total_beats": len(story["beats"]),
        "beat_type": beat["type"],
        "narration": beat["narration"],
        "prompt": beat["prompt"],
        "target_word": beat.get("target_response"),
        "child_reply": child_text or "(the child stayed quiet)",
        # Facts the model can't infer, so it follows the prompt's "near the end" and
        # "try again another time" guidance correctly.
        "near_story_end": beat_index >= len(story["beats"]) - 2,
        "after_your_reply": (
            "This is the last part: the story ends after your reply, so close it warmly."
            if beat_index == len(story["beats"]) - 1
            else "The story moves straight on to the next part."
        ) + " The child gets no second try at this word now, so do not ask them to say it again.",
    }
    if beat["type"] == "targeted":
        # From the scorer, so "correct" reflects what was actually heard.
        situation["target_word_heard"] = bool(score and score.get("said_target"))
    return groq.chat(
        [
            {"role": "system", "content": REACTION_SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(situation)},
        ],
        max_wait_s=MAX_RETRY_WAIT_S,
        timeout=20,
        temperature=0.8,  # the prompt asks for varied wording
        max_completion_tokens=600,
        reasoning_effort="low",
        response_format={"type": "json_object"},
    )


def parse_reaction(raw: str):
    """(clean reaction, None) if the LLM reply is usable, else (None, why not)."""
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return None, "reply was not valid JSON"
    if not isinstance(data, dict):
        return None, "reply was not a JSON object"
    sentiment = str(data.get("sentiment", "")).strip().lower()
    if sentiment not in SENTIMENTS:
        return None, f'sentiment "{sentiment}" is not one of {", ".join(SENTIMENTS)}'
    text = _clean(str(data.get("reaction_text") or ""))
    if not text:
        return None, "reaction_text was empty"
    return {"sentiment": sentiment, "reaction_text": text}, None


def _clean(text: str) -> str:
    text = " ".join(text.strip().strip('"').split())
    text = re.sub(r"[\U0001F300-\U0001FAFF☀-➿]", "", text).strip()
    # The next beat asks its own question; a trailing question here would stack two.
    sentences = re.split(r"(?<=[.!?])\s+", text)
    kept = [s for s in sentences if not s.rstrip().endswith("?")]
    return (" ".join(kept) if kept else text)[:400]


def _fallback_text(beat: dict, child_text: str, score: dict | None) -> str:
    if beat["type"] == "targeted":
        target = beat["target_response"]
        pool = FALLBACK_HIT if score and score.get("said_target") else FALLBACK_MISS
        return random.choice(pool).format(target=target, Target=target.capitalize())
    return random.choice(FALLBACK_OPEN if child_text.strip() else FALLBACK_SILENT)


def sound_for(sentiment: str, is_last_beat: bool, target_heard: bool) -> str:
    """Which sounds/ file to play for this reaction (name without extension)."""
    if is_last_beat and (target_heard or sentiment in ("happy", "correct")):
        return config.FINAL_SUCCESS_SOUND
    return config.SOUND_FOR_SENTIMENT.get(sentiment, config.DEFAULT_SOUND)
