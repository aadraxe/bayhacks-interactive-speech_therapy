"""Offline tests for sessions, demo seeding and the report's guardrails (no API calls)."""

import json

import pytest

from app import report, sessions, stories
from app.seed import seed_demo_sessions

STORY = stories.get("rosie-shares")


@pytest.fixture
def seeded():
    return seed_demo_sessions(STORY)


def test_summary_separates_targeted_from_open():
    beats = [
        {"type": "targeted", "target": "kite", "accuracy": 1.0, "match": "exact",
         "features": {"mean_pitch": 250.0, "pitch_variation": 2.0, "speaking_rate": 2.5, "pause_count": 1}},
        {"type": "targeted", "target": "brave", "accuracy": 0.5, "match": "missed", "features": None},
        {"type": "open", "responded": True, "word_count": 4},
        {"type": "open", "responded": False, "word_count": 0},
    ]
    s = sessions.summarize(beats)
    assert s["targeted_accuracy"] == 0.75 and s["targets_said"] == 1 and s["targets_total"] == 2
    assert s["pitch_variation"] == 2.0 and s["acoustic_beats"] == 1  # typed/silent beats don't dilute acoustics
    assert s["open_answered"] == 1 and s["open_total"] == 2 and s["open_avg_words"] == 4.0


def test_seeded_history_improves_and_is_labelled(seeded):
    assert len(seeded) == 6
    assert all(s["seeded"] and s["mode"] == "voice" for s in seeded)
    acc = [s["summary"]["targeted_accuracy"] for s in seeded]
    pv = [s["summary"]["pitch_variation"] for s in seeded]
    assert acc == sorted(acc) and acc[-1] > acc[0] + 0.25
    assert pv[-1] > pv[0] + 1.0
    assert [s["date"] for s in seeded] == sorted(s["date"] for s in seeded)


def test_report_input_is_compact_and_precomputes_trend(seeded):
    data = report.build_report_input(seeded)
    assert len(json.dumps(data)) < 6000  # stays well inside Groq's free-tier token budget
    t = data["trend"]
    assert t["sessions"] == 6 and not t["sparse_data"]
    assert t["practice_word_accuracy"]["first"] < 75 and t["practice_word_accuracy"]["latest"] > 90
    assert "seeded" not in json.dumps(data["sessions"])


def test_sparse_flag_with_one_session(seeded):
    assert report.build_report_input(seeded[-1:])["trend"]["sparse_data"] is True


GOOD = (
    "**For the parent:** Accuracy rose from 54% to 98%.\n\n**For the therapist:**\n- Improved: accuracy.\n\n"
    "**Practice suggestion:** Practise friend.\n\n" + report.CLOSING_LINE
)


def test_check_accepts_a_good_report():
    assert report._check(GOOD) is None


@pytest.mark.parametrize("word", ["normal", "abnormal", "cured", "autism", "diagnosed", "typical", "age-appropriate",
                                  "clear-speech", "clearer", "fluency", "articulation", "speech has improved", "speech improved"])
def test_check_rejects_banned_words(word):
    assert "not allowed" in report._check(GOOD.replace("Accuracy rose", f"Speech is {word}; accuracy rose"))


def test_check_rejects_missing_section():
    assert "missing" in report._check(GOOD.replace("**Practice suggestion:**", "Suggestion:"))


def test_closing_line_is_added_if_missing():
    text = report._ensure_closing(GOOD.replace(report.CLOSING_LINE, ""))
    assert text.rstrip().endswith(report.CLOSING_LINE)


def test_template_report_passes_its_own_checks(seeded):
    for subset in (seeded, seeded[-1:], seeded[-2:]):
        text = report.template_report(report.build_report_input(subset))
        assert report._check(text, report.build_report_input(subset)) is None
    assert "too early" in report.template_report(report.build_report_input(seeded[-1:]))


def test_template_uses_honest_practice_wording(seeded):
    text = report.template_report(report.build_report_input(seeded))
    assert "practice scores rose" in text and "practice-word accuracy" in text
    # Pace is never labelled a gain, and acoustic changes are hedged.
    pace = next(line for line in text.splitlines() if "pace" in line)
    assert not pace.startswith("- Improved") and "not better on its own" in pace
    assert "may suggest" in text
    assert "audio quality, word difficulty, or familiarity" in text
    assert "sparse" in report.template_report(report.build_report_input(seeded[-1:]))


HONEST = (
    "**For the parent:** Practice scores rose from 68% to 98%.\n\n**For the therapist:**\n"
    "- Improved: practice-word accuracy went from 68% to 98%.\n"
    "- Watch: pace went from 2.2 to 2.9 syllables/second.\n"
    "- Patterns may reflect audio quality, word difficulty, or familiarity with the story.\n\n"
    "**Practice suggestion:** Practise share.\n\n" + report.CLOSING_LINE
)


def test_honest_report_passes_strict_checks(seeded):
    assert report._check(HONEST, report.build_report_input(seeded)) is None


@pytest.mark.parametrize("line", [
    "- Improved: pace went from 2.2 to 2.9 syllables/second.",
    "- Improved: speaking rate rose from 2.2 to 2.9.",
    "- **Improved:** pitch variation grew from 1.5 to 3.0 semitones.",
    "- Improved: pause count dropped from 2.3 to 1.3.",
])
def test_pace_and_acoustics_cannot_be_labelled_improved(seeded, line):
    text = HONEST.replace("- Watch: pace went from 2.2 to 2.9 syllables/second.", line)
    assert "must be Stable or Watch" in report._check(text, report.build_report_input(seeded))


def test_caveat_is_required(seeded):
    text = HONEST.replace("- Patterns may reflect audio quality, word difficulty, or familiarity with the story.\n", "")
    assert "caveat" in report._check(text, report.build_report_input(seeded))


@pytest.mark.parametrize("phrase", ["(sparse data)", "(only one session, so it is too early to say)", "(limited data)"])
def test_sparse_must_be_said_with_few_sessions(seeded, phrase):
    one = report.build_report_input(seeded[-1:])
    single = HONEST.replace("- Improved: practice-word accuracy went from 68% to 98%.", "- Stable: practice-word accuracy was 68%.")
    assert "sparse" in report._check(single, one)
    assert report._check(single.replace("rose from", f"{phrase} rose from"), one) is None


def test_improved_needs_more_than_one_session(seeded):
    text = HONEST.replace("rose from", "(sparse data) rose from")
    assert "only one session" in report._check(text, report.build_report_input(seeded[-1:]))
    assert report._check(text, report.build_report_input(seeded[-2:])) is None


@pytest.mark.parametrize("cause", ["growing confidence", "less tired", "a better mood"])
def test_guessed_personal_causes_are_rejected(seeded, cause):
    text = HONEST.replace("Practice scores rose", f"Thanks to {cause}, practice scores rose")
    assert "personal cause" in report._check(text, report.build_report_input(seeded))


def test_report_data_uses_practice_word_naming(seeded):
    data = json.dumps(report.build_report_input(seeded))
    assert "practice_word_accuracy" in data and "targeted_accuracy" not in data


def test_typed_sessions_are_left_out_of_the_report(seeded):
    typed = {**seeded[0], "mode": "typed", "seeded": False}
    assert typed not in sessions.for_report(seeded + [typed])
