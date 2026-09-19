"""generate_report(): the parent/therapist practice summary, written by the Groq LLM.

It reports PRACTICE performance, not clinical progress: "practice-word accuracy", practice
scores that rose or changed, pace reported neutrally, and acoustic changes framed as
possible signals to discuss with the therapist.

The data sent with the prompt is compact (per-session summaries plus precomputed
first-vs-latest changes) so the model quotes real numbers instead of doing arithmetic, and
so it fits Groq's free-tier limits.

The reply is checked before it is shown: required sections present, none of the banned
words or phrases, closing line present. Charts JSON is extracted for the frontend. If the
LLM fails the check twice, or Groq is unavailable, a plain template report built directly
from the numbers is returned instead.
"""

import json
import re

import httpx

from app import groq, sessions

REPORT_SYSTEM_PROMPT = """You are a speech-practice progress assistant for StoryBuddy. You receive a JSON
time-series of a child's interactive-story practice sessions. Each session has:
per-targeted-word accuracy, average attempts per word, acoustic features
(mean_pitch, pitch_variation, speaking_rate, pause_count), and open-question
participation (answered count, average words per answer).

Write a warm, honest progress report in the format at the bottom, then append a
"charts" list.

=== MEASUREMENT HONESTY (follow exactly) ===
- Call the accuracy metric "practice-word accuracy". NEVER "clear-speech score"
  or anything implying a validated clinical clarity measure.
- Say "practice scores rose/changed across sessions", NOT "the child's speech
  improved". You report practice performance, not clinical progress.
- Report speaking rate and pitch as NEUTRAL observations. Never frame faster/
  slower or higher/lower as good or bad on their own.
- Treat acoustic changes as possible signals to discuss with the therapist,
  using hedged language ("may suggest", "worth revisiting").
- Values measured across different words are approximate; say so where relevant.
- Base progress on targeted-word accuracy and average attempts. Treat open-
  question answers as engagement/participation, not accuracy.

=== SPARSE DATA — CRITICAL ===
- If there are FEWER THAN 3 sessions: treat every metric as a BASELINE reading,
  NOT a trend. Do NOT label individual metrics "Improved" or "Watch". State
  plainly that trends need more sessions. Keep the whole report calm and short.
- Only use "Improved" or "Watch" when there are 3+ sessions AND the change is a
  direction that genuinely matters for practice. A single session-to-session
  difference is never a "Watch" item.
- Never produce a wall of "Watch" flags — that reads as alarming and is
  dishonest when the real message is "not enough data yet". Aim for a calm,
  balanced tone.

=== GROUNDING & SAFETY ===
- Every statement must cite the actual numbers. Never invent data.
- Do NOT diagnose. Never mention autism severity, disorders, or clinical labels.
  Never say "cured", "normal", or "abnormal".
- Note that patterns may reflect audio quality, word difficulty, or familiarity,
  not only skill change.

=== CHART GUIDANCE ===
After the text report, output a "charts" JSON list telling the frontend which
trends to plot from sessions.json. Rules:
- Set "chartable": true for a line chart ONLY if there are 3+ sessions;
  otherwise "chartable": false (frontend shows a "trends appear after a few more
  sessions" placeholder).
- Line charts for: practice_word_accuracy, avg_attempts_per_word, pitch_variation.
- speaking_rate and pause_count: line charts but mark "neutral": true — no good/
  bad framing, no colored zones.
- Do NOT chart mean_pitch as a trend (values across different words aren't
  cleanly comparable) — leave it as a noted number only.
- open_engagement: a simple bar/stat, not a trend line.
- Never imply clinical thresholds: no red/green danger zones, just the line.

=== OUTPUT FORMAT ===
**For the parent:** (2-3 warm, plain sentences, honest framing)

**For the therapist:**
(With 3+ sessions: bullets under Improved / Stable / Watch.
With fewer than 3: a short "Baseline readings" list stated neutrally, plus one
line that trends need more sessions.)

**Practice suggestion:** (1 simple next step)

Then append:
"charts": [
  { "metric": "practice_word_accuracy", "type": "line", "chartable": <bool> },
  { "metric": "avg_attempts_per_word", "type": "line", "chartable": <bool> },
  { "metric": "pitch_variation", "type": "line", "chartable": <bool> },
  { "metric": "speaking_rate", "type": "line", "chartable": <bool>, "neutral": true },
  { "metric": "pause_count", "type": "line", "chartable": <bool>, "neutral": true },
  { "metric": "open_engagement", "type": "bar", "chartable": true }
]

End the text report with exactly: "This is a practice-tracking summary, not a
clinical assessment. Please review with your speech therapist.\""""

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
SPARSE_BELOW = 3    # fewer sessions than this = "sparse data" / baseline only

CHART_LEAD = ("practice_word_accuracy", "avg_attempts_per_word")

UNITS = {
    "practice_word_accuracy": "percent, mean over the practice words in a session (100 = every practice word was recognised)",
    "avg_attempts_per_word": "average tries needed per practice word in the session (lower often means easier recognition that day)",
    "mean_pitch": "Hz, average voice pitch during practice-word answers (not charted as a trend; approximate across words)",
    "pitch_variation": "semitones, how much the voice rises and falls",
    "speaking_rate": "pace in syllables per second (report neutrally; faster is not better on its own)",
    "pause_count": "silent gaps of 0.3 s or more, average per practice-word answer",
    "open_answered": "open questions answered out of open_total (engagement, not accuracy)",
    "open_avg_words": "average words per answered open question (engagement, not accuracy)",
    "open_engagement": "engagement snapshot: answered / total open questions, plus average words",
}


def default_charts(session_count: int) -> list:
    chartable = session_count >= SPARSE_BELOW
    return [
        {"metric": "practice_word_accuracy", "type": "line", "chartable": chartable},
        {"metric": "avg_attempts_per_word", "type": "line", "chartable": chartable},
        {"metric": "pitch_variation", "type": "line", "chartable": chartable},
        {"metric": "speaking_rate", "type": "line", "chartable": chartable, "neutral": True},
        {"metric": "pause_count", "type": "line", "chartable": chartable, "neutral": True},
        {"metric": "open_engagement", "type": "bar", "chartable": True},
    ]


def build_report_input(session_rows: list) -> dict:
    """The compact JSON the LLM sees: a time-series plus first-vs-latest changes."""
    recent = session_rows[-MAX_SESSIONS:]
    series = []
    for s in recent:
        m = s["summary"]
        avg_attempts = m.get("avg_attempts_per_word")
        if avg_attempts is None:
            avg_attempts = sessions.avg_attempts_from_beats(s.get("beats") or [])
        series.append({
            "date": s["date"][:10],
            "practice_words_pct": {b["target"]: round(b["accuracy"] * 100) for b in s["beats"] if b["type"] == "targeted"},
            "practice_word_accuracy": None if m["targeted_accuracy"] is None else round(m["targeted_accuracy"] * 100),
            "avg_attempts_per_word": avg_attempts,
            "mean_pitch": m["mean_pitch"],
            "pitch_variation": m["pitch_variation"],
            "speaking_rate": m["speaking_rate"],
            "pause_count": m["pause_count"],
            "open_answered": m["open_answered"],
            "open_total": m["open_total"],
            "open_avg_words": m["open_avg_words"],
            "open_engagement": {
                "answered": m["open_answered"],
                "total": m["open_total"],
                "avg_words": m["open_avg_words"],
            },
        })

    trend = {"sessions": len(series), "sparse_data": len(series) < SPARSE_BELOW}
    if series:
        trend["first_date"], trend["latest_date"] = series[0]["date"], series[-1]["date"]
    if len(series) >= 2:
        for key in (
            "practice_word_accuracy", "avg_attempts_per_word", "pitch_variation",
            "speaking_rate", "pause_count", "mean_pitch", "open_avg_words",
        ):
            values = [row[key] for row in series if row.get(key) is not None]
            if len(values) >= 2:
                trend[key] = {"first": values[0], "latest": values[-1], "change": round(values[-1] - values[0], 2)}
    return {"units": UNITS, "sessions": series, "trend": trend}


def generate_report(session_rows: list) -> dict:
    """Returns {"report", "charts", "source": "groq"|"template", "note"?: str, "input": dict}."""
    report_input = build_report_input(session_rows)
    if not report_input["sessions"]:
        return {
            "report": None,
            "charts": [],
            "source": None,
            "note": "No spoken sessions yet.",
            "input": report_input,
        }

    problem, text, charts = None, None, None
    for _attempt in range(2):
        try:
            text = _ask_groq(report_input, previous=text, problem=problem)
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            problem = f"Groq unavailable ({exc.__class__.__name__})"
            break
        text, charts = _extract_charts(text)
        text = _ensure_closing(text)
        problem = _check(text, report_input)
        if problem is None:
            return {
                "report": text,
                "charts": charts or default_charts(report_input["trend"]["sessions"]),
                "source": "groq",
                "input": report_input,
            }

    return {
        "report": template_report(report_input),
        "charts": default_charts(report_input["trend"]["sessions"]),
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
        "Labels: use Improved only for practice-word accuracy, average attempts, or engagement. For pace, pitch, "
        "pitch variation and pauses use only Stable or Watch, since those are signals to discuss, not gains.",
        "After the three report sections and the closing line, append the charts JSON list exactly as specified.",
    ]
    if report_input["trend"]["sparse_data"]:
        n = report_input["trend"]["sessions"]
        notes.append(
            f"Data is sparse: only {n} session{'s' if n != 1 else ''}. Treat readings as baseline, not trends. "
            "Do NOT use Improved or Watch labels. Use a short Baseline readings list, say that trends need more "
            "sessions, and set chartable:false for line charts."
        )
    return "\n".join(notes) + "\n\n"


def _ensure_closing(text: str) -> str:
    if text and CLOSING_LINE not in " ".join(text.split()):
        text = f"{text.rstrip()}\n\n{CLOSING_LINE}"
    return text


_CHARTS_BLOCK = re.compile(
    r'(?:"charts"|charts)\s*:\s*(\[[\s\S]*?\])\s*(?=(?:This is a practice-tracking)|$)',
    re.IGNORECASE,
)


def _extract_charts(text: str) -> tuple[str, list | None]:
    """Pull the charts JSON out of the model reply so the markdown stays clean."""
    if not text:
        return text, None
    match = _CHARTS_BLOCK.search(text)
    if not match:
        # Try a looser trailing JSON array after a charts key.
        loose = re.search(r'"charts"\s*:\s*(\[[\s\S]*)', text, re.IGNORECASE)
        if not loose:
            return text, None
        raw = loose.group(1)
        # Trim to the first complete JSON array.
        try:
            charts, end = json.JSONDecoder().raw_decode(raw)
        except json.JSONDecodeError:
            return text, None
        cleaned = (text[:loose.start()] + text[loose.start() + loose.end(1) - len(raw) + end:]).strip()
        return cleaned, charts if isinstance(charts, list) else None

    try:
        charts = json.loads(match.group(1))
    except json.JSONDecodeError:
        return text, None
    cleaned = (text[:match.start()] + text[match.end():]).strip()
    # Drop a trailing orphaned "charts": left behind.
    cleaned = re.sub(r'(?:"charts"|charts)\s*:\s*$', "", cleaned, flags=re.IGNORECASE).strip()
    return cleaned, charts if isinstance(charts, list) else None


ACOUSTIC_OR_PACE = re.compile(r"\b(pace|speaking rate|pitch|pauses?|pause count)\b", re.IGNORECASE)
# Personal causes the numbers can't show.
GUESSED_CAUSE = re.compile(r"\b(confiden\w*|tired\w*|fatigue\w*|mood\w*|anxi\w*|nervous\w*|motivat\w*)\b", re.IGNORECASE)
SAYS_SPARSE = re.compile(
    r"\bsparse\b|limited data|too early|not enough (?:data|sessions)|only (?:one|two|1|2) (?:practice )?sessions?"
    r"|trends? need more|baseline readings?|more sessions",
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
    if report_input["trend"]["sparse_data"]:
        if not SAYS_SPARSE.search(body):
            return 'did not say that the data is sparse / trends need more sessions (fewer than 3 sessions)'
        if re.search(r"(?m)^\s*[-*•]+\s*\**\s*(Improved|Watch)\b", body):
            return 'used Improved/Watch with fewer than 3 sessions; use baseline readings instead'
    elif report_input["trend"]["sessions"] == 1 and re.search(r"\bImproved\b", body):
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
        baseline = [
            f"- Practice-word accuracy: {pct(latest['practice_word_accuracy'])}.",
        ]
        if latest.get("avg_attempts_per_word") is not None:
            baseline.append(f"- Average attempts per word: {latest['avg_attempts_per_word']}.")
        if latest.get("speaking_rate") is not None:
            baseline.append(f"- Speaking rate: {latest['speaking_rate']} syllables/second (neutral observation).")
        if latest.get("mean_pitch") is not None:
            baseline.append(
                f"- Mean pitch: {latest['mean_pitch']} Hz (approximate across different words; not a trend)."
            )
        baseline.append("- Trends need more sessions before labelling change as a pattern.")
        baseline.append(
            "- Patterns may reflect audio quality, word difficulty, or familiarity with the story, not only a change in skill."
        )
        therapist = "Baseline readings:\n" + "\n".join(baseline)
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
        if c := t.get("avg_attempts_per_word"):
            # Fewer attempts is usually better recognition that day.
            label = "Stable" if still(c, 0.25) else "Improved" if c["change"] < 0 else "Watch"
            bullets.append(f"- {label}: average attempts per word {went(c)}.")
        if c := t.get("speaking_rate"):
            label = "Stable" if still(c, 0.1) else "Watch"
            bullets.append(
                f"- {label}: pace {went(c, ' syllables/second')}. "
                "Reported neutrally; a faster pace is not better on its own."
            )
        for key, what, unit, tolerance in (
            ("pitch_variation", "pitch variation", " semitones", 0.2),
            ("pause_count", "pauses per practice-word answer", "", 0.25),
        ):
            if c := t.get(key):
                if still(c, tolerance):
                    bullets.append(f"- Stable: {what} {went(c, unit)}.")
                else:
                    bullets.append(
                        f"- Watch: {what} {went(c, unit)}. "
                        "This may suggest a change worth discussing with the therapist."
                    )
        if c := t.get("open_avg_words"):
            label = "Stable" if still(c, 0.5) else "Improved" if c["change"] > 0 else "Watch"
            bullets.append(f"- {label}: engagement, as words per open-question answer, {went(c)}.")
        if not bullets:
            bullets = ["- Stable: not enough change between sessions to highlight yet."]
        bullets.append(
            "- Patterns may reflect audio quality, word difficulty, or familiarity with the story, not only a change in skill."
        )
        therapist = "\n".join(bullets)

    weakest = min(latest["practice_words_pct"].items(), key=lambda kv: kv[1], default=None)
    suggestion = (
        f'Practise the word "{weakest[0]}" in a short, playful sentence before the next story.'
        if weakest and weakest[1] < 100 else "Keep the same story routine and try a new story next time."
    )
    return (
        f"**For the parent:** {parent}\n\n"
        f"**For the therapist:**\n{therapist}\n\n"
        f"**Practice suggestion:** {suggestion}\n\n{CLOSING_LINE}"
    )
