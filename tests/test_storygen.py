"""Offline tests for the story validator (no API calls)."""

import copy

import pytest

from app import settings, storygen

GOOD = {
    "title": "Tilly and the Blue Shell",
    "main_character": "Tilly",
    "helper": "Bo",
    "beats": [
        {"type": "targeted", "narration": "Tilly the turtle walked on the warm sand. Tilly found a small blue shell by the water.",
         "prompt": "Can you say the word shell?", "target_response": "shell"},
        {"type": "open", "narration": "The shell had a crack on one side. Tilly wanted to keep the shell safe and dry.",
         "prompt": "What do you think Tilly should do?", "target_response": None},
        {"type": "targeted", "narration": "Tilly found a soft green leaf. Tilly wrapped the leaf around the shell very gently.",
         "prompt": "Can you say the word leaf?", "target_response": "leaf"},
        {"type": "targeted", "narration": "Bo the crab came with a little box. Bo and Tilly put the shell in the box together.",
         "prompt": "Can you say the word box?", "target_response": "box"},
        {"type": "open", "narration": "The shell was safe in the box now. Tilly looked at the shell and smiled at Bo.",
         "prompt": "How do you think Tilly feels?", "target_response": None},
        {"type": "targeted", "narration": "Tilly and Bo sat in the sun. The sun was warm, and the two friends rested.",
         "prompt": "Can you say the word sun?", "target_response": "sun"},
    ],
}


def broken(changes):
    story = copy.deepcopy(GOOD)
    for (i, key), value in changes.items():
        story["beats"][i][key] = value
    return story


def test_good_story_passes_and_collects_targets():
    story, problem = storygen._validate(copy.deepcopy(GOOD), [])
    assert problem is None
    assert story["target_words"] == ["shell", "leaf", "box", "sun"]


def test_practice_words_must_match_in_order():
    assert storygen._validate(copy.deepcopy(GOOD), ["shell", "leaf", "box", "sun"])[1] is None
    assert "practice words" in storygen._validate(copy.deepcopy(GOOD), ["sun", "box", "leaf", "shell"])[1]


def test_wrong_beat_order_is_rejected():
    story = copy.deepcopy(GOOD)
    story["beats"][0], story["beats"][1] = story["beats"][1], story["beats"][0]
    assert "order" in storygen._validate(story, [])[1]


@pytest.mark.parametrize("changes, expected", [
    ({(0, "narration"): "Tilly the turtle walked on the warm sand. She found a small blue shell by the water."}, "repeat the character"),
    ({(0, "narration"): "Tilly the turtle walked on the sand. Tilly found a small blue stone by the water."}, "hears it first"),
    ({(0, "prompt"): "Can you say the word sand?"}, 'say "shell"'),
    ({(1, "prompt"): "Tilly should keep it."}, "open question"),
    ({(1, "narration"): "The shell had a crack. What should Tilly do with the shell today, friend?"}, "question inside"),
    ({(2, "narration"): "Tilly saw a leaf."}, "characters"),
    ({(3, "narration"): "A scary monster came with a box. Bo and Tilly put the shell in the box together."}, "not gentle"),
    ({(5, "target_response"): "shell", (5, "prompt"): "Can you say the word shell?",
      (5, "narration"): "Tilly and Bo sat with the shell. The shell was warm, and the two friends rested."}, "four different"),
])
def test_rule_breaks_are_caught(changes, expected):
    story, problem = storygen._validate(broken(changes), [])
    assert story is None and expected in problem


def test_bad_practice_words_raise():
    with pytest.raises(storygen.StoryError):
        storygen.generate_story(target_words=["one", "two"])


def test_demo_story_is_valid():
    demo = storygen.demo_story()
    assert demo["target_words"] == ["kite", "brave", "climb", "friend"]
    assert [b["type"] for b in demo["beats"]] == storygen.PATTERN


def test_voice_settings_are_validated(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "SETTINGS_FILE", tmp_path / "settings.json")
    assert settings.update(speed=0.75)["speed"] == 0.75
    assert settings.update(voice_id="Xb7hH8MSUJpSbSDYk0k2")["voice_id"] == "Xb7hH8MSUJpSbSDYk0k2"
    with pytest.raises(ValueError):
        settings.update(speed=0.5)
    with pytest.raises(ValueError):
        settings.update(voice_id="not-a-voice")
