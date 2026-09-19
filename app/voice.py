"""ElevenLabs voice: speak() for the narrator, transcribe() for the child's answers.

Request shapes checked against https://elevenlabs.io/docs (text-to-speech/convert and
speech-to-text/convert) and the live API. The key is sent only as the xi-api-key header.
"""

import hashlib
import re
from pathlib import Path

import httpx

from app import config, settings

STT_LANGUAGE = "en"
TTS_CACHE_DIR = config.DATA_DIR / "tts_cache"

# Scribe keyterm limits: < 50 characters, at most 5 words, plain word characters.
_KEYTERM_MAX_CHARS = 49
_KEYTERM_MAX_WORDS = 5
_KEYTERM_OK = re.compile(r"^[A-Za-z0-9' -]+$")


class VoiceError(Exception):
    """ElevenLabs rejected or failed a request. The message is safe to show."""


def speak(text: str, use_cache: bool = True, voice_id: str | None = None, speed: float | None = None) -> bytes:
    """Narrate `text` in the chosen narrator voice and speed. Returns MP3 bytes.

    Voice and speed default to the saved narrator settings. Repeated lines (story
    narration) are cached on disk so replaying a story doesn't spend credits again.
    """
    text = text.strip()
    if not text:
        raise VoiceError("Nothing to say.")
    current = settings.get()
    voice_id = voice_id or current["voice_id"]
    speed = speed if speed is not None else current["speed"]
    key = hashlib.sha256(
        f"{voice_id}|{config.ELEVENLABS_TTS_MODEL}|{speed}|{text}".encode()
    ).hexdigest()[:32]
    cached = TTS_CACHE_DIR / f"{key}.mp3"
    if use_cache and cached.exists():
        return cached.read_bytes()

    response = _post(
        f"/text-to-speech/{voice_id}",
        params={"output_format": "mp3_44100_128"},
        json={
            "text": text,
            "model_id": config.ELEVENLABS_TTS_MODEL,
            "voice_settings": {"speed": speed},
        },
    )
    audio = response.content
    if use_cache:
        TTS_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cached.write_bytes(audio)
    return audio


def transcribe(wav_path, target_words=()) -> dict:
    """Transcribe a recording with Scribe, biased towards `target_words` (keyterms).

    Returns {"text": str, "words": [{"text", "start", "end", "logprob"}], "language_code": str}.
    """
    audio = Path(wav_path).read_bytes()
    data = {"model_id": config.ELEVENLABS_STT_MODEL, "language_code": STT_LANGUAGE}
    keyterms = _clean_keyterms(target_words)
    if keyterms:
        data["keyterms"] = keyterms  # sent as repeated form fields, as the API expects

    response = _post(
        "/speech-to-text",
        data=data,
        files={"file": (Path(wav_path).name, audio, "audio/wav")},
        timeout=120,
    )
    body = response.json()
    words = [
        {
            "text": w["text"],
            "start": w.get("start"),
            "end": w.get("end"),
            "logprob": w.get("logprob"),
        }
        for w in body.get("words", [])
        if w.get("type") == "word"
    ]
    return {
        "text": (body.get("text") or "").strip(),
        "words": words,
        "language_code": body.get("language_code"),
    }


def _clean_keyterms(terms) -> list:
    cleaned = []
    for term in terms or ():
        term = " ".join(str(term).split())
        if (
            term
            and len(term) <= _KEYTERM_MAX_CHARS
            and len(term.split()) <= _KEYTERM_MAX_WORDS
            and _KEYTERM_OK.match(term)
            and term not in cleaned
        ):
            cleaned.append(term)
    return cleaned


def _post(path: str, timeout: float = 60, **kwargs) -> httpx.Response:
    try:
        response = httpx.post(
            f"{config.ELEVENLABS_BASE_URL}{path}",
            headers={"xi-api-key": config.ELEVENLABS_API_KEY},
            timeout=timeout,
            **kwargs,
        )
    except httpx.HTTPError as exc:
        raise VoiceError(f"Could not reach ElevenLabs: {exc.__class__.__name__}") from exc
    if response.status_code != 200:
        try:
            detail = response.json().get("detail")
            message = detail.get("message") if isinstance(detail, dict) else str(detail)
        except ValueError:
            message = response.text[:200]
        raise VoiceError(f"ElevenLabs error {response.status_code}: {message}")
    return response
