"""The companion's warm reaction to each answer, written by the Groq LLM.

If Groq is unreachable or returns nothing usable, a gentle built-in line is used so the
story never stalls in front of a child.
"""

import json
import random
import re

import httpx

from app import groq

REACTION_SYSTEM_PROMPT = """You are StoryBuddy, a warm, calm story companion for a verbal child aged 6-11 who is practising speaking during an interactive story. You have just asked the child something and they answered out loud. Write what you say back.

Rules:
- One or two short sentences, 20 words at most. Simple, concrete, literal words. No idioms, sarcasm, or figures of speech.
- NEVER ask a question. The next part of the story asks the next question, so your reply must end with a full stop or exclamation mark.
- Do not continue the story or introduce new events.
- If the child shares a feeling (for example "I'm scared"), kindly name that feeling first.
- Targeted word:
  - If target_word_heard is true: celebrate briefly and say the target word.
  - If false and they said something: never say "wrong", "no" or "try again". Thank them for trying, then say the target word once in a tiny sentence about the story so they hear it.
  - If they said nothing: say that is okay, then say the target word once in a tiny sentence.
- Open question: respond with interest to the child's actual idea, in your own words. Any idea is a good idea. If they said nothing, say that is okay and give one simple answer to question_you_asked yourself.
- Use the characters' names, not "he" or "she".
- Never mention scores, tests, therapy, diagnoses, or clinical words. No emojis. Output only the words to say aloud.

Examples (from a different story, to show the style only; never reuse these words):
{"beat_type":"targeted","target_word":"splash","target_word_heard":true,"child_said":"splash"} -> Yes! Splash! Mo made a big splash.
{"beat_type":"targeted","target_word":"splash","target_word_heard":false,"child_said":"um water"} -> Thank you for trying! Splash. Mo jumped in with a splash.
{"beat_type":"targeted","target_word":"splash","target_word_heard":false,"child_said":"(nothing - the child stayed quiet)"} -> That is okay. Splash! Mo loves to splash.
{"beat_type":"open","question_you_asked":"Where should Mo swim?","child_said":"to the island"} -> To the island! What a great place to swim.
{"beat_type":"open","question_you_asked":"How does Mo feel?","child_said":"(nothing - the child stayed quiet)"} -> That is okay. I think Mo feels happy in the warm water."""

FALLBACK_TARGETED_HIT = ["Wonderful! You said {target}!", "Yes! {target_cap}! Great job.", "You did it! {target_cap}!"]
FALLBACK_TARGETED_MISS = ["Thank you for trying! {target_cap}. That's the word.", "Good trying! {target_cap}. Let's keep going."]
FALLBACK_TARGETED_SILENT = ["That's okay. {target_cap}. That's the word.", "That's okay. {target_cap}! Let's keep going."]
FALLBACK_OPEN = ["What a great idea! Let's see what happens.", "I love your idea! Let's find out."]
FALLBACK_SILENT = ["That's okay. Let's see what happens next.", "That's okay. Let's keep going together."]


MAX_RETRY_WAIT_S = 4.0  # free tier is 8K tokens/min; a short wait usually clears a 429


def react(beat: dict, child_text: str, score: dict | None) -> dict:
    """Return {"text": what to say, "source": "groq" | "fallback", "fallback_reason"?}."""
    try:
        text = _groq_reaction(beat, child_text, score)
        if text:
            return {"text": text, "source": "groq"}
        reason = "reply failed the guardrails (question only, or target word missing)"
    except httpx.HTTPStatusError as exc:
        reason = f"Groq returned {exc.response.status_code}"
    except (httpx.HTTPError, KeyError, ValueError) as exc:
        reason = f"Groq unavailable ({exc.__class__.__name__})"
    return {"text": _fallback(beat, child_text, score), "source": "fallback", "fallback_reason": reason}


def _groq_reaction(beat: dict, child_text: str, score: dict | None) -> str:
    situation = {
        "beat_type": beat["type"],
        "story_so_far": beat["narration"],
        "question_you_asked": beat["prompt"],
        "child_said": child_text or "(nothing - the child stayed quiet)",
    }
    if beat["type"] == "targeted":
        situation["target_word"] = beat["target_response"]
        situation["target_word_heard"] = bool(score and score.get("said_target"))

    text = groq.chat(
        [
            {"role": "system", "content": REACTION_SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(situation)},
        ],
        max_wait_s=MAX_RETRY_WAIT_S,
        timeout=20,
        temperature=0.5,
        max_completion_tokens=400,
        reasoning_effort="low",
    )
    text = _clean(text)
    return text if _acceptable(text, beat) else ""


def _clean(text: str) -> str:
    text = " ".join(text.strip().strip('"').split())
    text = re.sub(r"[\U0001F300-\U0001FAFF☀-➿]", "", text).strip()
    # Drop any question: the next beat asks its own, and two in a row is confusing.
    sentences = re.split(r"(?<=[.!?])\s+", text)
    text = " ".join(s for s in sentences if not s.rstrip().endswith("?"))
    return text[:300]


def _acceptable(text: str, beat: dict) -> bool:
    """Guardrails the prompt alone can't guarantee: no stacked questions, target word modelled."""
    if not text:
        return False
    if beat["type"] == "targeted":
        target = beat["target_response"].lower()
        if not re.search(rf"\b{re.escape(target)}\b", text.lower()):
            return False
    return True


def _fallback(beat: dict, child_text: str, score: dict | None) -> str:
    if beat["type"] == "targeted":
        target = beat["target_response"]
        if score and score.get("said_target"):
            pool = FALLBACK_TARGETED_HIT
        else:
            pool = FALLBACK_TARGETED_MISS if child_text.strip() else FALLBACK_TARGETED_SILENT
        return random.choice(pool).format(target=target, target_cap=target.capitalize())
    return random.choice(FALLBACK_OPEN if child_text.strip() else FALLBACK_SILENT)
