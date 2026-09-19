"""score_response(): did the child say the target word?

Deliberately simple and explainable for a parent or therapist:
  exact        the target appears in what was heard           accuracy 1.0
  close        a heard word is a near match ("brav", "braver")  accuracy = similarity (0.75-0.99)
  missed       something was said, but not the target          accuracy = best similarity (< 0.75)
  no_response  nothing was heard                               accuracy 0.0

Note: transcription is biased towards the target word (Scribe keyterms), so a near
pronunciation is often transcribed as the word itself. The score measures "was the target
recognisable", not pronunciation detail.
"""

import math
import re
from difflib import SequenceMatcher

CLOSE_THRESHOLD = 0.75

_WORD = re.compile(r"[a-z0-9']+")


def _tokens(text: str) -> list:
    return _WORD.findall(text.lower().replace("’", "'"))


def score_response(transcript: str, target_word: str, words=None) -> dict:
    """Score one targeted answer. `words` (from transcribe()) adds recognition confidence."""
    target = _tokens(target_word)
    heard = _tokens(transcript or "")
    result = {"target": target_word, "heard": (transcript or "").strip()}

    if not heard:
        return {**result, "accuracy": 0.0, "match": "no_response", "said_target": False, "confidence": None}
    if not target:
        raise ValueError("target_word is empty")

    size = len(target)
    windows = [heard[i:i + size] for i in range(max(1, len(heard) - size + 1))]
    target_str = " ".join(target)
    best_window, best = None, 0.0
    for window in windows:
        ratio = SequenceMatcher(None, target_str, " ".join(window)).ratio()
        if ratio > best:
            best_window, best = window, ratio

    if best == 1.0:
        match = "exact"
    elif best >= CLOSE_THRESHOLD:
        match = "close"
    else:
        match = "missed"

    return {
        **result,
        "accuracy": round(best, 2),
        "match": match,
        "said_target": match in ("exact", "close"),
        "matched_text": " ".join(best_window) if best_window and match != "missed" else None,
        "confidence": _confidence(best_window, words) if match != "missed" else None,
    }


def _confidence(window, words):
    """Recognition confidence (0-1) of the matched word(s), from Scribe's log-probabilities."""
    if not window or not words:
        return None
    wanted = list(window)
    for i in range(len(words) - len(wanted) + 1):
        chunk = words[i:i + len(wanted)]
        if [t for w in chunk for t in _tokens(w["text"])] == wanted:
            logprobs = [w.get("logprob") for w in chunk if w.get("logprob") is not None]
            if logprobs:
                return round(math.exp(sum(logprobs) / len(logprobs)), 2)
    return None
