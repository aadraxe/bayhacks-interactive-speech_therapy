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

# --- Storage --------------------------------------------------------------
SESSIONS_FILE = DATA_DIR / "sessions.json"
STORIES_FILE = DATA_DIR / "stories.json"

SAFETY_BANNER = (
    "StoryBuddy supports speech-therapy practice. It does not diagnose any "
    "condition and does not replace a speech therapist."
)

for _d in (DATA_DIR, RECORDINGS_DIR, SAMPLES_DIR):
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
