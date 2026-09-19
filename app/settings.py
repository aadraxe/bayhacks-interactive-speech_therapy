"""Narrator settings a parent/therapist can change from the app: voice and speed.

Stored in data/settings.json; defaults come from .env (config). Only ElevenLabs'
premade voices are offered, because the free plan can't use library voices via the API.
"""

import json
import os
import threading

from app import config

SETTINGS_FILE = config.DATA_DIR / "settings.json"
SPEED_MIN, SPEED_MAX = 0.7, 1.2  # ElevenLabs' accepted range for voice_settings.speed

# Premade voices auditioned for StoryBuddy (same children's passage at speed 0.85).
# Measured with our own engine: clarity = Scribe transcribed 35/35 words for all of them.
NARRATOR_VOICES = [
    {"id": "XrExE9yKIg1WjnnlVkGX", "name": "Matilda", "accent": "American", "gender": "female",
     "pitch_variation": 4.85, "speaking_rate": 3.19, "note": "Warm and expressive, clear pace. Recommended."},
    {"id": "Xb7hH8MSUJpSbSDYk0k2", "name": "Alice", "accent": "British", "gender": "female",
     "pitch_variation": 5.3, "speaking_rate": 3.49, "note": "Most expressive; a classic children's-book voice."},
    {"id": "pFZP5JQG7iQjIQuC4Bku", "name": "Lily", "accent": "British", "gender": "female",
     "pitch_variation": 4.44, "speaking_rate": 2.82, "note": "Slowest and softest of the set."},
    {"id": "JBFqnCBsd6RMkjVDRZzb", "name": "George", "accent": "British", "gender": "male",
     "pitch_variation": 3.62, "speaking_rate": 3.42, "note": "Warm storyteller, deeper voice."},
    {"id": "EXAVITQu4vr4xnSDxMaL", "name": "Sarah", "accent": "American", "gender": "female",
     "pitch_variation": 3.59, "speaking_rate": 2.95, "note": "Calm and reassuring (the original voice)."},
    {"id": "cgSgspJ2msm6clMCkdW9", "name": "Jessica", "accent": "American", "gender": "female",
     "pitch_variation": 3.37, "speaking_rate": 3.74, "note": "Bright and playful, but the fastest."},
    {"id": "hpp4J3VqNfWAUOO0d1Us", "name": "Bella", "accent": "American", "gender": "female",
     "pitch_variation": 3.35, "speaking_rate": 3.10, "note": "Bright and warm, a little flatter."},
    {"id": "bIHbv24MWmeRgasZH58o", "name": "Will", "accent": "American", "gender": "male",
     "pitch_variation": 2.77, "speaking_rate": 3.61, "note": "Relaxed; least expressive of the set."},
]

_lock = threading.Lock()


def get() -> dict:
    """Current settings: {"voice_id", "speed", "sound_volume"}."""
    current = {
        "voice_id": config.ELEVENLABS_VOICE_ID,
        "speed": config.ELEVENLABS_SPEED,
        "sound_volume": config.SOUND_VOLUME,
    }
    if SETTINGS_FILE.exists():
        try:
            stored = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
            current.update({k: stored[k] for k in current if k in stored})
        except (OSError, ValueError):
            pass
    return current


def update(voice_id: str | None = None, speed: float | None = None, sound_volume: float | None = None) -> dict:
    with _lock:
        current = get()
        if sound_volume is not None:
            if not 0.0 <= sound_volume <= 1.0:
                raise ValueError("Sound volume must be between 0 and 1.")
            current["sound_volume"] = round(sound_volume, 2)
        if voice_id is not None:
            if voice_id not in {v["id"] for v in NARRATOR_VOICES}:
                raise ValueError("Unknown voice.")
            current["voice_id"] = voice_id
        if speed is not None:
            if not SPEED_MIN <= speed <= SPEED_MAX:
                raise ValueError(f"Speed must be between {SPEED_MIN} and {SPEED_MAX}.")
            current["speed"] = round(speed, 2)
        tmp = SETTINGS_FILE.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(current, indent=2), encoding="utf-8")
        os.replace(tmp, SETTINGS_FILE)
    return current


def voice_name(voice_id: str) -> str:
    return next((v["name"] for v in NARRATOR_VOICES if v["id"] == voice_id), voice_id)
