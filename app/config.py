"""Central config. Every secret comes from the environment (.env), never from source."""

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
STATIC_DIR = BASE_DIR / "static"
SAMPLES_DIR = BASE_DIR / "samples"
RECORDINGS_DIR = DATA_DIR / "recordings"

load_dotenv(BASE_DIR / ".env")

# --- API keys -------------------------------------------------------------
ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY", "")
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")

# --- ElevenLabs -----------------------------------------------------------
ELEVENLABS_BASE_URL = "https://api.elevenlabs.io/v1"
# Default narrator: Matilda (warm, expressive, clear). Changeable in the app (data/settings.json).
ELEVENLABS_VOICE_ID = os.getenv("ELEVENLABS_VOICE_ID", "XrExE9yKIg1WjnnlVkGX")
# Slower than normal speech for young listeners. ElevenLabs accepts 0.7-1.2 (1.0 = normal).
ELEVENLABS_SPEED = float(os.getenv("ELEVENLABS_SPEED", "0.8"))
ELEVENLABS_TTS_MODEL = os.getenv("ELEVENLABS_TTS_MODEL", "eleven_multilingual_v2")
ELEVENLABS_STT_MODEL = os.getenv("ELEVENLABS_STT_MODEL", "scribe_v2")

# --- Groq (LLM: warm reactions + progress report) --------------------------
# OpenAI-compatible endpoint. gpt-oss-120b is Groq's strongest free-tier text model.
GROQ_BASE_URL = "https://api.groq.com/openai/v1"
GROQ_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")

# --- Reaction sounds ----------------------------------------------------------
# Soft sounds in sounds/ (made once by scripts/make_sounds.py), played before the spoken
# reaction. The companion's reply picks the sentiment; this map picks the sound.
SOUNDS_DIR = BASE_DIR / "sounds"
SOUND_FOR_SENTIMENT = {
    "happy": "happy_chime",
    "correct": "happy_chime",
    "sad": "gentle_encourage",
    "frustrated": "soft_try_again",
    "neutral": "soft_pop",
}
DEFAULT_SOUND = "soft_pop"        # when the reply is invalid or the sentiment is unknown
FINAL_SUCCESS_SOUND = "cheer"     # last beat, answered well

# Targeted beats: up to MAX_ATTEMPTS tries per practice word, then always move on warmly.
# On these beats the attempt's OUTCOME picks the sound (open beats still use the sentiment).
MAX_ATTEMPTS = 3
SOUND_FOR_OUTCOME = {
    "match": "happy_chime",        # said it (cheer instead on the story's last beat)
    "retry": "soft_try_again",     # not yet, another try coming
    "not_yet": "gentle_encourage", # 3 tries used: move on, practise it another time
}
# Playback volume for these sounds, 0.0-1.0 (they're already mastered quietly).
# Changeable in the app; this is the starting value.
SOUND_VOLUME = float(os.getenv("SOUND_VOLUME", "0.5"))

# --- Frontend (CORS) --------------------------------------------------------
# Origins allowed to call the API from a browser, comma-separated. Defaults cover the
# usual dev servers (Vite 5173, Create React App / Next.js 3000). Pages served by this
# backend itself (the Lab) don't need an entry.
CORS_ORIGINS = [
    o.strip()
    for o in os.getenv(
        "CORS_ORIGINS",
        "http://localhost:5173,http://127.0.0.1:5173,http://localhost:3000,http://127.0.0.1:3000",
    ).split(",")
    if o.strip()
]

# --- Storage --------------------------------------------------------------
SESSIONS_FILE = DATA_DIR / "sessions.json"

SAFETY_BANNER = (
    "StoryBuddy supports speech-therapy practice. It does not diagnose any "
    "condition and does not replace a speech therapist."
)

for _d in (DATA_DIR, RECORDINGS_DIR, SAMPLES_DIR, SOUNDS_DIR):
    _d.mkdir(parents=True, exist_ok=True)


def _is_placeholder(value: str) -> bool:
    """A key left at its .env.example default counts as not configured."""
    return not value or value.strip().lower().startswith("your_")


def missing_keys() -> list:
    """Names of keys that are not configured, for the health check."""
    missing = []
    if _is_placeholder(ELEVENLABS_API_KEY):
        missing.append("ELEVENLABS_API_KEY")
    if _is_placeholder(GROQ_API_KEY):
        missing.append("GROQ_API_KEY")
    return missing
