"""Offline tests for sessions, demo seeding and the report's guardrails (no API calls)."""

import json

import pytest

from app import config, report, sessions
from app.seed import seed_demo_sessions

STORY = json.loads((config.DATA_DIR / "demo_story.json").read_text(encoding="utf-8"))


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
    assert acc == sorted(acc) and acc[-1] > acc[0] + 0.3
    assert pv[-1] > pv[0] + 1.0
    assert [s["date"] for s in seeded] == sorted(s["date"] for s in seeded)


def test_report_input_is_compact_and_precomputes_trend(seeded):
    data = report.build_report_input(seeded)
    assert len(json.dumps(data)) < 6000  # stays well inside Groq's free-tier token budget
    t = data["trend"]
    assert t["sessions"] == 6 and not t["sparse_data"]
    assert t["targeted_accuracy"]["first"] == 54 and t["targeted_accuracy"]["latest"] > 90
    assert "seeded" not in json.dumps(data["sessions"])


def test_sparse_flag_with_one_session(seeded):
    assert report.build_report_input(seeded[-1:])["trend"]["sparse_data"] is True


GOOD = (
    "**For the parent:** Accuracy rose from 54% to 98%.\n\n**For the therapist:**\n- Improved: accuracy.\n\n"
    "**Practice suggestion:** Practise friend.\n\n" + report.CLOSING_LINE
)


def test_check_accepts_a_good_report():
    assert report._check(GOOD) is None


@pytest.mark.parametrize("word", ["normal", "abnormal", "cured", "autism", "diagnosed", "typical", "age-appropriate"])
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
        assert report._check(text) is None
    assert "too early" in report.template_report(report.build_report_input(seeded[-1:]))


def test_typed_sessions_are_left_out_of_the_report(seeded):
    typed = {**seeded[0], "mode": "typed", "seeded": False}
    assert typed not in sessions.for_report(seeded + [typed])
