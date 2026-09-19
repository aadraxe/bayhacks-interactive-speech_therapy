"""Did the child say the target word?

is_close_match()  decides a targeted beat's retry loop: match -> move on, no match -> try again
                  (up to 3). Deliberately LENIENT, so an STT mishear never forces an unfair retry.
score_response()  the 0-1 accuracy number that goes into sessions and the progress report.

score_response() is deliberately simple and explainable for a parent or therapist:
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


INFLECTIONS = ("s", "es", "ed", "d", "ing", "er", "y", "ies")


def is_close_match(transcript: str, target_word: str) -> dict:
    """Lenient check for the retry loop: did the child (probably) say the target word?

    Case, punctuation and extra words are ignored ("I think it's a carrot!" matches).
    MATCH when any word (or two words joined, e.g. "rain bow") is:
      exact        the target itself
      inflection   the target plus an ending: "carrots", "smiled", "swimming"
      stretched    the target with drawn-out letters: "rrred", "caaarrot"
      close        within a small spelling distance: 1 edit up to 4 letters, 2 up to 8, 3 beyond
      phonetic     sounds the same once spelling is normalised: "kerrot" ~ "carrot", "flour" ~ "flower"
    One guard: for short words (4 letters or fewer) the first sound must agree, because
    swapping it makes a different word ("wed" for "red", "fun" for "sun"). That is usually
    the practice sound itself, not a transcription slip.

    Otherwise NO MATCH, with the reason kept for the log:
      started_word  said the start of the word ("car" for "carrot": the ending was dropped)
      dropped_start said the end of the word ("wim" for "swim": the beginning was dropped)
      different     said something else
      no_response   nothing was heard

    Returns {"match", "reason", "heard_text", "closest", "distance"}.
    """
    heard_text = " ".join((transcript or "").split())
    target = _collapse(_letters(target_word))
    if not target:
        raise ValueError("target_word is empty")
    tokens = [_letters(t) for t in _tokens(heard_text)]
    tokens = [t for t in tokens if t]
    result = {"heard_text": heard_text, "closest": None, "distance": None}
    if not tokens:
        return {**result, "match": False, "reason": "no_response"}

    # Every word, plus each pair of neighbouring words joined together.
    candidates = tokens + [a + b for a, b in zip(tokens, tokens[1:])]
    best = None  # (reason rank, distance, candidate, reason)
    for word in candidates:
        reason = _match_reason(word, target)
        distance = _edit_distance(_collapse(word), target)
        rank = 0 if reason else 1
        key = (rank, distance, word, reason)
        if best is None or key < best:
            best = key

    _, distance, closest, reason = best
    result.update(closest=closest, distance=distance)
    if reason:
        return {**result, "match": True, "reason": reason}
    if any(len(t) >= 2 and target.startswith(t) for t in tokens):
        reason = "started_word"
    elif any(len(t) >= 2 and target.endswith(t) for t in tokens):
        reason = "dropped_start"
    else:
        reason = "different"
    return {**result, "match": False, "reason": reason}


def _match_reason(word: str, target: str) -> str | None:
    if word == target:
        return "exact"
    stems = {target, target + target[-1]}  # "swim" -> "swimming" doubles the last letter
    if target.endswith("e"):
        stems.add(target[:-1])              # "smile" -> "smiling"
    if target.endswith("y"):
        stems.add(target[:-1] + "i")        # "bunny" -> "bunnies"
    if any(word == stem + ending for stem in stems for ending in INFLECTIONS):
        return "inflection"
    if _collapse(word) == target:
        return "stretched"
    short = len(target) <= 4
    if short and _phonetic(word)[:1] != _phonetic(target)[:1]:
        return None
    allowed = 1 if short else 2 if len(target) <= 8 else 3
    if _edit_distance(_collapse(word), target) <= allowed:
        return "close"
    if _edit_distance(_phonetic(word), _phonetic(target)) <= (0 if short else 1):
        return "phonetic"
    return None


def _letters(text: str) -> str:
    return re.sub(r"[^a-z]", "", text.lower())


def _collapse(word: str) -> str:
    """Squash letters repeated 3+ times ("rrred" -> "red") but keep real doubles ("carrot")."""
    return re.sub(r"(.)\1{2,}", r"\1", word)


_PHONETIC_RULES = [
    (r"^kn", "n"), (r"^wr", "r"), (r"^wh", "w"), (r"ph", "f"), (r"ck", "k"), (r"qu", "kw"),
    (r"gh", ""), (r"c(?=[eiy])", "s"), (r"c", "k"), (r"x", "ks"), (r"z", "s"),
    (r"(ou|ow)", "au"), (r"(.)\1+", r"\1"), (r"(?<=.{3})e$", ""), (r"y$", "i"),
]


def _phonetic(word: str) -> str:
    """A rough sound-alike key: spelling variants that sound the same map to one form."""
    key = _collapse(word)
    for pattern, replacement in _PHONETIC_RULES:
        key = re.sub(pattern, replacement, key)
    return key


def _edit_distance(a: str, b: str) -> int:
    """Levenshtein distance (insertions, deletions, substitutions)."""
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        current = [i]
        for j, cb in enumerate(b, 1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (ca != cb)))
        previous = current
    return previous[-1]


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
