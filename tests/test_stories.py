"""Offline tests for the pre-written stories and the reaction/sound wiring (no API calls)."""

import copy
import json

import pytest

from app import companion, config, settings, stories


def test_all_shipped_stories_are_valid():
    files = sorted(stories.STORIES_DIR.glob("*.json"))
    assert len(files) == 3
    for path in files:
        story = json.loads(path.read_text(encoding="utf-8"))
        assert stories.validate(story) == [], path.name
    assert len(stories.load_all()) == 3


def test_each_story_has_a_moral_and_a_sound_cue_per_beat():
    for story in stories.load_all():
        assert story["moral"].endswith(".")
        assert all(b["sound_effect"] for b in story["beats"])
        assert [b["id"] for b in story["beats"]] == [1, 2, 3, 4, 5, 6]


def test_validator_catches_rule_breaks():
    story = copy.deepcopy(stories.get("rosie-shares"))
    story["beats"][0]["prompt"] = "Can you say the word: apple?"
    story["beats"][1]["narration"] = "Ferdy looked cold. What should Rosie do?"
    story["beats"][2]["narration"] = "She hopped over with a red umbrella."
    story["beats"][3].pop("sound_effect")
    problems = " | ".join(stories.validate(story))
    assert 'ask for "carrot"' in problems
    assert "only the prompt may ask" in problems
    assert 'names instead of "She"' in problems
    assert "sound_effect" in problems


def test_target_sounds_must_appear_in_practice_words():
    story = copy.deepcopy(stories.get("sammy-swims"))
    story["target_sounds"] = ["r"]
    assert any('target sound "r"' in p for p in stories.validate(story))


def test_rotation_picks_the_least_recently_used_story():
    ids = [s["id"] for s in stories.load_all()]
    assert stories.next_in_rotation([])["id"] == ids[0]
    history = [
        {"story_id": ids[0], "date": "2026-09-01T10:00:00"},
        {"story_id": ids[1], "date": "2026-09-02T10:00:00"},
        {"story_id": ids[2], "date": "2026-09-03T10:00:00"},
        {"story_id": ids[0], "date": "2026-09-04T10:00:00"},
    ]
    assert stories.next_in_rotation(history)["id"] == ids[1]
    # Demo sessions don't count as having been read.
    assert stories.next_in_rotation([{**h, "seeded": True} for h in history])["id"] == ids[0]


def test_unknown_story_raises():
    with pytest.raises(stories.StoryError):
        stories.get("nope")


# --- Reaction JSON and sounds ------------------------------------------------------

@pytest.mark.parametrize("raw, sentiment", [
    ('{"sentiment": "happy", "reaction_text": "You said carrot so clearly!"}', "happy"),
    ('{"sentiment": "Frustrated", "reaction_text": "That is okay."}', "frustrated"),
])
def test_valid_reaction_is_parsed(raw, sentiment):
    parsed, problem = companion.parse_reaction(raw)
    assert problem is None and parsed["sentiment"] == sentiment


@pytest.mark.parametrize("raw, why", [
    ("not json", "not valid JSON"),
    ('["happy"]', "not a JSON object"),
    ('{"sentiment": "angry", "reaction_text": "Hi."}', "is not one of"),
    ('{"sentiment": "happy", "reaction_text": ""}', "empty"),
])
def test_invalid_reaction_is_rejected(raw, why):
    parsed, problem = companion.parse_reaction(raw)
    assert parsed is None and why in problem


def test_trailing_question_is_dropped_but_text_kept():
    parsed, _ = companion.parse_reaction('{"sentiment": "happy", "reaction_text": "Yes, carrot! What will Rosie do next?"}')
    assert parsed["reaction_text"] == "Yes, carrot!"


def test_open_beat_sentiment_maps_to_sound():
    assert companion.sound_for("happy", False) == "happy_chime"
    assert companion.sound_for("correct", False) == "happy_chime"
    assert companion.sound_for("sad", False) == "gentle_encourage"
    assert companion.sound_for("frustrated", False) == "soft_try_again"
    assert companion.sound_for("neutral", False) == "soft_pop"
    assert companion.sound_for("mystery", False) == config.DEFAULT_SOUND
    assert companion.sound_for("happy", True) == "cheer"
    assert companion.sound_for("sad", True) == "gentle_encourage"


def test_targeted_outcome_maps_to_sound():
    assert companion.sound_for_outcome("match", False) == "happy_chime"
    assert companion.sound_for_outcome("match", True) == "cheer"
    assert companion.sound_for_outcome("retry", False) == "soft_try_again"
    assert companion.sound_for_outcome("retry", True) == "soft_try_again"
    assert companion.sound_for_outcome("not_yet", True) == "gentle_encourage"


def test_every_mapped_sound_file_exists():
    names = (set(config.SOUND_FOR_SENTIMENT.values()) | set(config.SOUND_FOR_OUTCOME.values())
             | {config.DEFAULT_SOUND, config.FINAL_SUCCESS_SOUND})
    for name in names:
        assert (config.SOUNDS_DIR / f"{name}.wav").exists(), name


def test_every_story_cue_has_a_sound_file():
    for story in stories.load_all():
        for beat in story["beats"]:
            path = config.SOUNDS_DIR / "story" / f"{beat['sound_effect']}.wav"
            assert path.exists(), f"{story['id']} beat {beat['id']}: missing {path.name} (run python -m scripts.make_sounds)"


def test_unusable_llm_reply_falls_back_to_neutral(monkeypatch):
    monkeypatch.setattr(companion, "_ask_groq", lambda *a, **k: "not json")
    r = companion.react(stories.get("rosie-shares"), 0, "carrot", outcome="match")
    assert r["sentiment"] == "neutral" and r["source"] == "fallback" and "carrot" in r["reaction_text"].lower()


def test_sound_volume_setting(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "SETTINGS_FILE", tmp_path / "settings.json")
    assert settings.update(sound_volume=0.3)["sound_volume"] == 0.3
    with pytest.raises(ValueError):
        settings.update(sound_volume=1.5)
