import pytest

from app.scoring import score_response
from app.voice import _clean_keyterms


@pytest.mark.parametrize("heard", ["brave", "Brave!", "I am brave.", "Pip is BRAVE"])
def test_exact_match_anywhere_in_answer(heard):
    s = score_response(heard, "brave")
    assert s["match"] == "exact" and s["accuracy"] == 1.0 and s["said_target"]


@pytest.mark.parametrize("heard", ["brav", "braver", "brayve"])
def test_near_match_counts_as_close(heard):
    s = score_response(heard, "brave")
    assert s["match"] == "close" and s["said_target"]
    assert 0.75 <= s["accuracy"] < 1.0


def test_different_word_is_missed():
    s = score_response("I like the tree", "brave")
    assert s["match"] == "missed" and not s["said_target"]
    assert s["accuracy"] < 0.75
    assert s["confidence"] is None


@pytest.mark.parametrize("heard", ["", "   ", None])
def test_silence_is_no_response(heard):
    s = score_response(heard, "brave")
    assert s["match"] == "no_response" and s["accuracy"] == 0.0


def test_multi_word_target():
    assert score_response("we flew the red kite today", "red kite")["match"] == "exact"


def test_curly_apostrophe_matches():
    assert score_response("Pip’s kite", "Pip's")["match"] == "exact"


def test_confidence_from_word_logprobs():
    words = [{"text": "I", "logprob": 0.0}, {"text": "am", "logprob": 0.0}, {"text": "brave.", "logprob": -0.105}]
    s = score_response("I am brave.", "brave", words)
    assert s["confidence"] == pytest.approx(0.9, abs=0.01)


def test_keyterms_are_cleaned_to_scribe_limits():
    assert _clean_keyterms(["brave", " brave ", "", "one two three four five six", "x" * 60, '["bad"]', "red kite"]) == [
        "brave",
        "red kite",
    ]
