"""generate_report(): the parent/therapist progress summary, written by the Groq LLM.

The system prompt is the one from the project spec, word for word. The data sent with it
is compact (per-session summaries plus precomputed first-vs-latest changes) so the model
quotes real numbers instead of doing arithmetic, and so it fits Groq's free-tier limits.

The reply is checked before it is shown: required sections present, none of the banned
words, closing line present. If the LLM fails the check twice, or Groq is unavailable,
a plain template report built directly from the numbers is returned instead.
"""

import json
import re

import httpx

from app import groq

REPORT_SYSTEM_PROMPT = """You are a speech-practice progress assistant. You receive a JSON time-series of a child's interactive-story sessions. Each session has per-targeted-word accuracy and acoustic features (mean_pitch, pitch_variation, speaking_rate, pause_count), plus participation on open questions.
1. Describe the TREND across sessions in warm, plain language for a parent.
2. Give a therapist summary: Improved / Stable / Watch (bullets).
3. Base progress on the TARGETED-word accuracy and acoustics. Treat open questions as engagement/participation, not accuracy.
4. Ground every claim in the actual numbers. Never invent data.
Hard rules: Do NOT diagnose. Never mention autism severity or clinical labels. Never say "cured", "normal", or "abnormal". Frame everything as practice progress. If data is sparse, say so. End with: "This is a practice-tracking summary, not a clinical assessment. Please review with your speech therapist."
Output: **For the parent:** (2-3 sentences) / **For the therapist:** (bullets) / **Practice suggestion:** (1 step)"""

CLOSING_LINE = (
    "This is a practice-tracking summary, not a clinical assessment. "
    "Please review with your speech therapist."
)
REQUIRED_HEADINGS = ("**For the parent:**", "**For the therapist:**", "**Practice suggestion:**")
BANNED = re.compile(
    # "typical"/"age-appropriate" are "normal" in disguise, and imply norms we don't have.
    r"\b(cured?|normal|abnormal|typical|atypical|age[- ]appropriate|autis\w*|asd|diagnos\w*"
    r"|disorder\w*|severity|deficit\w*|impair\w*)\b",
    re.IGNORECASE,
)
MAX_SESSIONS = 12   # keeps the request well inside the 8K tokens/minute free tier
SPARSE_BELOW = 3    # fewer sessions than this = "sparse data"

UNITS = {
    "targeted_accuracy": "percent, mean over the targeted words in a session (100 = every target word said clearly)",
    "mean_pitch": "Hz, average voice pitch during targeted answers",
    "pitch_variation": "semitones, how much the voice rises and falls (higher = more expressive)",
    "speaking_rate": "syllables per second",
    "pause_count": "silent gaps of 0.3 s or more, average per targeted answer",
    "open_answered": "open questions answered out of open_total (participation, not accuracy)",
    "open_avg_words": "average words per answered open question",
}


def build_report_input(sessions: list) -> dict:
    """The compact JSON the LLM sees: a time-series plus first-vs-latest changes."""
    recent = sessions[-MAX_SESSIONS:]
    series = []
    for s in recent:
        m = s["summary"]
        series.append({
            "date": s["date"][:10],
            "targeted_words_pct": {b["target"]: round(b["accuracy"] * 100) for b in s["beats"] if b["type"] == "targeted"},
            "targeted_accuracy": None if m["targeted_accuracy"] is None else round(m["targeted_accuracy"] * 100),
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
        for key in ("targeted_accuracy", "pitch_variation", "speaking_rate", "pause_count", "mean_pitch", "open_avg_words"):
            values = [row[key] for row in series if row[key] is not None]
            if len(values) >= 2:
                trend[key] = {"first": values[0], "latest": values[-1], "change": round(values[-1] - values[0], 2)}
    return {"units": UNITS, "sessions": series, "trend": trend}


def generate_report(sessions: list) -> dict:
    """Returns {"report": markdown, "source": "groq" | "template", "note"?: str, "input": dict}."""
    report_input = build_report_input(sessions)
    if not report_input["sessions"]:
        return {"report": None, "source": None, "note": "No spoken sessions yet.", "input": report_input}

    problem = None
    for _attempt in range(2):
        try:
            text = _ask_groq(report_input, retry_note=problem)
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            problem = f"Groq unavailable ({exc.__class__.__name__})"
            break
        text = _ensure_closing(text)
        problem = _check(text)
        if problem is None:
            return {"report": text, "source": "groq", "input": report_input}

    return {
        "report": template_report(report_input),
        "source": "template",
        "note": f"Used the built-in template because the AI report {problem}.",
        "input": report_input,
    }


def _ask_groq(report_input: dict, retry_note: str | None) -> str:
    messages = [
        {"role": "system", "content": REPORT_SYSTEM_PROMPT},
        {"role": "user", "content": _data_preface(report_input) + json.dumps(report_input)},
    ]
    if retry_note:
        messages.append({"role": "user", "content": f"Your previous answer {retry_note}. Rewrite it following every rule."})
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
    """Reading notes sent ahead of the data (the system prompt itself stays verbatim)."""
    notes = [
        "Session data is below. Units are in \"units\"; \"trend\" has first-vs-latest values already computed, so quote those numbers.",
        "Describe only what the numbers show. Do not guess causes (such as tiredness, mood or confidence).",
    ]
    if report_input["trend"]["sparse_data"]:
        n = report_input["trend"]["sessions"]
        notes.append(
            f"Data is sparse: only {n} session{'s' if n != 1 else ''}. Say so plainly, and do not label anything "
            "Improved, Stable or Watch as a trend; describe it as a starting point instead."
        )
    return "\n".join(notes) + "\n\n"


def _ensure_closing(text: str) -> str:
    if text and CLOSING_LINE not in " ".join(text.split()):
        text = f"{text.rstrip()}\n\n{CLOSING_LINE}"
    return text


def _check(text: str) -> str | None:
    """Why the report can't be shown, or None if it passes."""
    if not text:
        return "was empty"
    missing = [h for h in REQUIRED_HEADINGS if h not in text]
    if missing:
        return f"was missing the section {missing[0]}"
    banned = BANNED.search(text.replace(CLOSING_LINE, ""))
    if banned:
        return f'used the word "{banned.group(0)}", which is not allowed'
    return None


def template_report(report_input: dict) -> str:
    """Plain, number-grounded report used when the LLM can't be used."""
    t = report_input["trend"]
    rows = report_input["sessions"]
    latest = rows[-1]

    def pct(v):
        return f"{v}%"

    def change_line(key, label, unit, fmt=lambda v: f"{v}", higher_is_better=True):
        c = t.get(key)
        if not c:
            return None
        if abs(c["change"]) < 1e-9:
            verdict = "Stable"
        else:
            verdict = "Improved" if (c["change"] > 0) == higher_is_better else "Watch"
        return f"- {verdict}: {label} went from {fmt(c['first'])} to {fmt(c['latest'])}{unit}."

    if len(rows) < SPARSE_BELOW:
        parent = (
            f"There {'is' if len(rows) == 1 else 'are'} only {len(rows)} practice session"
            f"{'' if len(rows) == 1 else 's'} so far, so it is too early to see a trend. "
            f"In the latest session, target words were said with {pct(latest['targeted_accuracy'])} accuracy."
        )
    else:
        acc = t.get("targeted_accuracy")
        direction = "risen" if acc and acc["change"] > 0 else "stayed steady" if acc and acc["change"] == 0 else "dipped"
        parent = (
            f"Across {len(rows)} practice sessions from {t['first_date']} to {t['latest_date']}, "
            f"target-word accuracy has {direction}"
            + (f" from {pct(acc['first'])} to {pct(acc['latest'])}." if acc else ".")
            + f" In the latest session, {latest['open_answered']} of {latest['open_total']} story questions were answered."
        )

    bullets = [
        change_line("targeted_accuracy", "Target-word accuracy", "", pct),
        change_line("pitch_variation", "Pitch variation", " semitones"),
        change_line("speaking_rate", "Speaking rate", " syllables/second"),
        change_line("pause_count", "Pauses per targeted answer", "", higher_is_better=False),
        change_line("open_avg_words", "Words per open answer (participation)", ""),
    ]
    bullets = [b for b in bullets if b] or ["- Not enough sessions yet to compare."]
    weakest = min(latest["targeted_words_pct"].items(), key=lambda kv: kv[1], default=None)
    suggestion = (
        f'Practise the word "{weakest[0]}" in a short, playful sentence before the next story.'
        if weakest and weakest[1] < 100 else "Keep the same story routine and try a new story next time."
    )
    return (
        f"**For the parent:** {parent}\n\n"
        f"**For the therapist:**\n" + "\n".join(bullets) + "\n\n"
        f"**Practice suggestion:** {suggestion}\n\n{CLOSING_LINE}"
    )
