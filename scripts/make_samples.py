"""Generate test recordings in samples/ with the ElevenLabs narrator voice.

    python -m scripts.make_samples

Costs roughly 750 ElevenLabs characters. Existing files are skipped unless --force.
"""

import sys
import wave

import httpx

from app import config

SAMPLE_RATE = 16000

PASSAGE = [
    "Pip the little fox lived at the edge of a quiet green forest.",
    "Every morning, Pip hopped over the stream and counted the stones.",
    "One windy day, Pip saw a shiny red kite stuck high in an old oak tree.",
    "Pip took a deep breath and said, I can be brave.",
    "Step by step, Pip climbed up, and the kite came free.",
]

SAMPLES = {
    # Connected, fluent read-aloud speech.
    "read_aloud_fluent.wav": {"text": " ".join(PASSAGE), "voice_settings": {}},
    # Same passage read hesitantly: five mid-sentence stalls, like a child working
    # out a word. Expect ~5 more pauses and a lower speaking rate than the fluent read.
    "read_aloud_hesitant.wav": {
        "text": " ".join(PASSAGE)
        .replace("of a quiet", 'of a <break time="1.0s" /> quiet')
        .replace("over the stream", 'over the <break time="0.8s" /> stream')
        .replace("saw a shiny", 'saw a <break time="1.2s" /> shiny')
        .replace("in an old", 'in an <break time="0.9s" /> old')
        .replace("can be brave", 'can be <break time="1.0s" /> brave'),
        "voice_settings": {},
    },
    # A single target word, like a child's answer on a targeted beat.
    "word_brave.wav": {"text": "Brave!", "voice_settings": {}},
}


def synthesize_wav(text: str, voice_settings: dict, path) -> None:
    body = {"text": text, "model_id": config.ELEVENLABS_TTS_MODEL}
    if voice_settings:
        body["voice_settings"] = voice_settings
    response = httpx.post(
        f"{config.ELEVENLABS_BASE_URL}/text-to-speech/{config.ELEVENLABS_VOICE_ID}",
        params={"output_format": f"pcm_{SAMPLE_RATE}"},
        headers={"xi-api-key": config.ELEVENLABS_API_KEY},
        json=body,
        timeout=120,
    )
    if response.status_code != 200:
        raise SystemExit(f"ElevenLabs error {response.status_code}: {response.text[:300]}")
    # pcm_16000 is raw 16-bit little-endian mono; wrap it in a WAV header.
    with wave.open(str(path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(SAMPLE_RATE)
        out.writeframes(response.content)


def main() -> None:
    force = "--force" in sys.argv
    for name, spec in SAMPLES.items():
        path = config.SAMPLES_DIR / name
        if path.exists() and not force:
            print(f"skip  {name} (exists)")
            continue
        synthesize_wav(spec["text"], spec["voice_settings"], path)
        with wave.open(str(path)) as w:
            print(f"wrote {name}  {w.getnframes() / w.getframerate():.1f}s")


if __name__ == "__main__":
    main()
