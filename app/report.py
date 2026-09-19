"""generate_report(): the parent/therapist practice summary, written by the Groq LLM.

It reports PRACTICE performance, not clinical progress: "practice-word accuracy", practice
scores that rose or changed, pace reported neutrally, and acoustic changes framed as
possible signals to discuss with the therapist.

The data sent with the prompt is compact (per-session summaries plus precomputed
first-vs-latest changes) so the model quotes real numbers instead of doing arithmetic, and
so it fits Groq's free-tier limits.

The reply is checked before it is shown: required sections present, none of the banned
words or phrases, closing line present. If the LLM fails the check twice, or Groq is
unavailable, a plain template report built directly from the numbers is returned instead.
"""

import json
import re

import httpx

from app import groq

REPORT_SYSTEM_PROMPT = """You are a speech-practice progress assistant. You receive a JSON time-series of a child's interactive-story practice sessions. Each session has practice-word accuracy (per practice word and overall) and acoustic features (mean_pitch, pitch_variation, speaking_rate, pause_count), plus participation on open questions.

You report PRACTICE performance, not clinical progress.

Wording rules:
- Call the accuracy metric "practice-word accuracy". Never call it a "clear-speech score" or use any clinical-clarity term.
- Say "practice scores rose" (or changed, dipped, stayed steady) across sessions. Never say the child's speech improved.
- Report pace (speaking_rate) neutrally. A faster pace is not good on its own.
- Frame acoustic changes (pitch, pitch variation, pauses) as possible signals to discuss with the therapist, using hedged language such as "may suggest".
- Note that patterns may reflect audio quality, word difficulty, or familiarity with the story, not only a change in skill.

Content rules:
1. Base progress on practice-word accuracy and the acoustic features. Treat open-question word counts as engagement, not accuracy.
2. Ground every claim in the actual numbers. Never invent data.
3. If there are fewer than 3 sessions, say plainly that the data is sparse.
4. Never diagnose. Never mention autism severity or clinical labels. Never say "cured", "normal", or "abnormal".

Output exactly these three parts:
**For the parent:** 2-3 warm sentences.
**For the therapist:** bullets, each starting with Improved, Stable or Watch.
**Practice suggestion:** one step.
End with: "This is a practice-tracking summary, not a clinical assessment. Please review with your speech therapist.\""""

CLOSING_LINE = (
    "This is a practice-tracking summary, not a clinical assessment. "
    "Please review with your speech therapist."
)
REQUIRED_HEADINGS = ("**For the parent:**", "**For the therapist:**", "**Practice suggestion:**")
BANNED = re.compile(
    # "typical"/"age-appropriate" are "normal" in disguise, and imply norms we don't have.
    r"\b(cured?|normal|abnormal|typical|atypical|age[- ]appropriate|autis\w*|asd|diagnos\w*"
    r"|disorder\w*|severity|deficit\w*|impair\w*"
    # Clinical-clarity and clinical-progress wording: this tracks practice, not speech.
    r"|clear[- ]?speech|clearer|intelligib\w*|articulat\w*|fluen\w*"
    r"|speech (?:has |is |have )?(?:improv\w*|gotten better|got better|progress\w*))\b",
    re.IGNORECASE,
)
MAX_SESSIONS = 12   # keeps the request well inside the 8K tokens/minute free tier
SPARSE_BELOW = 3    # fewer sessions than this = "sparse data"

UNITS = {
    "practice_word_accuracy": "percent, mean over the practice words in a session (100 = every practice word was recognised)",
    "mean_pitch": "Hz, average voice pitch during practice-word answers",
    "pitch_variation": "semitones, how much the voice rises and falls",
    "speaking_rate": "pace in syllables per second (report neutrally; faster is not better on its own)",
    "pause_count": "silent gaps of 0.3 s or more, average per practice-word answer",
    "open_answered": "open questions answered out of open_total (engagement, not accuracy)",
    "open_avg_words": "average words per answered open question (engagement, not accuracy)",
}


def build_report_input(sessions: list) -> dict:
    """The compact JSON the LLM sees: a time-series plus first-vs-latest changes."""
    recent = sessions[-MAX_SESSIONS:]
    series = []
    for s in recent:
        m = s["summary"]
        series.append({
            "date": s["date"][:10],
            "practice_words_pct": {b["target"]: round(b["accuracy"] * 100) for b in s["beats"] if b["type"] == "targeted"},
            "practice_word_accuracy": None if m["targeted_accuracy"] is None else round(m["targeted_accuracy"] * 100),
            "mean_pitch": m["mean_pitch"],
            "pitch_variation": m["pitch_variation"],
            "speaking_rate": m["speaking_rate"],
            "pause_count": m["pause_count"],
            "open_answered": m["open_answered"],
            "open_total": m["open_total"],
            "open_avg_words": m["open_avg_words"],
        })

    trend = {"sessions": len(series), "sparse_data": len(series) < SPARSE_BELOW}
    if series:
        trend["first_date"], trend["latest_date"] = series[0]["date"], series[-1]["date"]
    if len(series) >= 2:
        for key in ("practice_word_accuracy", "pitch_variation", "speaking_rate", "pause_count", "mean_pitch", "open_avg_words"):
            values = [row[key] for row in series if row[key] is not None]
            if len(values) >= 2:
                trend[key] = {"first": values[0], "latest": values[-1], "change": round(values[-1] - values[0], 2)}
    return {"units": UNITS, "sessions": series, "trend": trend}


def generate_report(sessions: list) -> dict:
    """Returns {"report": markdown, "source": "groq" | "template", "note"?: str, "input": dict}."""
    report_input = build_report_input(sessions)
    if not report_input["sessions"]:
        return {"report": None, "source": None, "note": "No spoken sessions yet.", "input": report_input}

    problem, text = None, None
    for _attempt in range(2):
        try:
            text = _ask_groq(report_input, previous=text, problem=problem)
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            problem = f"Groq unavailable ({exc.__class__.__name__})"
            break
        text = _ensure_closing(text)
        problem = _check(text, report_input)
        if problem is None:
            return {"report": text, "source": "groq", "input": report_input}

    return {
        "report": template_report(report_input),
        "source": "template",
        "note": f"Used the built-in template because the AI report {problem}.",
        "input": report_input,
    }


def _ask_groq(report_input: dict, previous: str | None = None, problem: str | None = None) -> str:
    messages = [
        {"role": "system", "content": REPORT_SYSTEM_PROMPT},
        {"role": "user", "content": _data_preface(report_input) + json.dumps(report_input)},
    ]
    if problem and previous:
        # Show the draft so the model fixes the one problem rather than starting over.
        messages += [
            {"role": "assistant", "content": previous},
            {"role": "user", "content": f"This report {problem}. Fix that, keep the rest, and check every rule again."},
        ]
    text = groq.chat(
        messages,
        max_wait_s=15,
        temperature=0.3,
        max_completion_tokens=3000,
        reasoning_effort="medium",
    )
    # The model likes typographic spaces/hyphens; keep the report plain text-friendly.
    return text.translate(_PLAIN).strip()


_PLAIN = str.maketrans({" ": " ", " ": " ", " ": " ", "‑": "-", "‐": "-"})


def _data_preface(report_input: dict) -> str:
    """Reading notes sent ahead of the data."""
    notes = [
        "Session data is below. Units are in \"units\"; \"trend\" has first-vs-latest values already computed, so quote those numbers.",
        "Describe only what the numbers show. Do not mention confidence, tiredness, mood, anxiety or motivation "
        "at all; the numbers cannot show them.",
        "Include the caveat that patterns may reflect audio quality, word difficulty, or familiarity with the story.",
        "Labels: use Improved only for practice-word accuracy or engagement. For pace, pitch, pitch variation and "
        "pauses use only Stable or Watch, since those are signals to discuss, not gains.",
    ]
    if report_input["trend"]["sparse_data"]:
        n = report_input["trend"]["sessions"]
        notes.append(
            f"Data is sparse: only {n} session{'s' if n != 1 else ''}. In the parent section, say in these words that "
            "the data is sparse. Do not label anything Improved, Stable or Watch as a trend; describe it as a "
            "starting point instead."
        )
    return "\n".join(notes) + "\n\n"


def _ensure_closing(text: str) -> str:
    if text and CLOSING_LINE not in " ".join(text.split()):
        text = f"{text.rstrip()}\n\n{CLOSING_LINE}"
    return text


ACOUSTIC_OR_PACE = re.compile(r"\b(pace|speaking rate|pitch|pauses?|pause count)\b", re.IGNORECASE)
# Personal causes the numbers can't show.
GUESSED_CAUSE = re.compile(r"\b(confiden\w*|tired\w*|fatigue\w*|mood\w*|anxi\w*|nervous\w*|motivat\w*)\b", re.IGNORECASE)
SAYS_SPARSE = re.compile(
    r"\bsparse\b|limited data|too early|not enough (?:data|sessions)|only (?:one|two|1|2) (?:practice )?sessions?",
    re.IGNORECASE,
)


def _check(text: str, report_input: dict | None = None) -> str | None:
    """Why the report can't be shown, or None if it passes.

    With report_input, also enforces the honesty rules that can be checked mechanically.
    """
    if not text:
        return "was empty"
    missing = [h for h in REQUIRED_HEADINGS if h not in text]
    if missing:
        return f"was missing the section {missing[0]}"
    body = text.replace(CLOSING_LINE, "")
    banned = BANNED.search(body)
    if banned:
        return f'used the word "{banned.group(0)}", which is not allowed'
    if report_input is None:
        return None
    for line in body.splitlines():
        if re.search(r"\bImproved\b", line) and ACOUSTIC_OR_PACE.search(line):
            return (f'labelled "{ACOUSTIC_OR_PACE.search(line).group(0)}" as Improved; pace and acoustic changes '
                    "must be Stable or Watch, framed as signals to discuss")
    cause = GUESSED_CAUSE.search(body)
    if cause:
        return f'guessed a personal cause ("{cause.group(0)}") that the numbers cannot show'
    if not (re.search(r"audio quality", body, re.IGNORECASE) and re.search(r"familiar", body, re.IGNORECASE)):
        return "left out the caveat that patterns may reflect audio quality, word difficulty, or familiarity"
    if report_input["trend"]["sparse_data"] and not SAYS_SPARSE.search(body):
        return 'did not say that the data is sparse (fewer than 3 sessions)'
    if report_input["trend"]["sessions"] == 1 and re.search(r"\bImproved\b", body):
        return 'used "Improved" with only one session, when there is nothing to compare against'
    return None


def template_report(report_input: dict) -> str:
    """Plain, number-grounded report used when the LLM can't be used."""
    t = report_input["trend"]
    rows = report_input["sessions"]
    latest = rows[-1]

    def pct(v):
        return f"{v}%"

    def went(c, unit="", fmt=lambda v: f"{v}"):
        return f"went from {fmt(c['first'])} to {fmt(c['latest'])}{unit}"

    def still(c, tolerance):
        return abs(c["change"]) <= tolerance

    if len(rows) < SPARSE_BELOW:
        parent = (
            f"There {'is' if len(rows) == 1 else 'are'} only {len(rows)} practice session"
            f"{'' if len(rows) == 1 else 's'} so far, so the data is sparse and it is too early to see a trend. "
            f"In the latest session, practice-word accuracy was {pct(latest['practice_word_accuracy'])}."
        )
    else:
        acc = t.get("practice_word_accuracy")
        direction = "rose" if acc and acc["change"] > 0 else "stayed steady" if acc and acc["change"] == 0 else "dipped"
        parent = (
            f"Across {len(rows)} practice sessions from {t['first_date']} to {t['latest_date']}, "
            f"practice scores {direction}"
            + (f": practice-word accuracy {went(acc, fmt=pct)}." if acc else ".")
            + f" In the latest session, {latest['open_answered']} of {latest['open_total']} story questions were answered."
        )

    bullets = []
    if c := t.get("practice_word_accuracy"):
        label = "Stable" if still(c, 2) else "Improved" if c["change"] > 0 else "Watch"
        bullets.append(f"- {label}: practice-word accuracy {went(c, fmt=pct)}.")
    if c := t.get("speaking_rate"):
        label = "Stable" if still(c, 0.1) else "Watch"
        bullets.append(f"- {label}: pace {went(c, ' syllables/second')}. Reported neutrally; a faster pace is not better on its own.")
    for key, what, unit, tolerance in (("pitch_variation", "pitch variation", " semitones", 0.2),
                                       ("pause_count", "pauses per practice-word answer", "", 0.25)):
        if c := t.get(key):
            if still(c, tolerance):
                bullets.append(f"- Stable: {what} {went(c, unit)}.")
            else:
                bullets.append(f"- Watch: {what} {went(c, unit)}. This may suggest a change worth discussing with the therapist.")
    if c := t.get("open_avg_words"):
        label = "Stable" if still(c, 0.5) else "Improved" if c["change"] > 0 else "Watch"
        bullets.append(f"- {label}: engagement, as words per open-question answer, {went(c)}.")
    if not bullets:
        bullets = ["- Stable: not enough sessions yet to compare; this session is a starting point."]
    bullets.append("- Patterns may reflect audio quality, word difficulty, or familiarity with the story, not only a change in skill.")
    weakest = min(latest["practice_words_pct"].items(), key=lambda kv: kv[1], default=None)
    suggestion = (
        f'Practise the word "{weakest[0]}" in a short, playful sentence before the next story.'
        if weakest and weakest[1] < 100 else "Keep the same story routine and try a new story next time."
    )
    return (
        f"**For the parent:** {parent}\n\n"
        f"**For the therapist:**\n" + "\n".join(bullets) + "\n\n"
        f"**Practice suggestion:** {suggestion}\n\n{CLOSING_LINE}"
    )
