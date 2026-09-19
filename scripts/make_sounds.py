"""Generate StoryBuddy's sounds ONCE with the ElevenLabs Sound Effects API.

    python -m scripts.make_sounds           # creates any missing files
    python -m scripts.make_sounds --force   # regenerate all

Two sets, both committed to the repo so this normally never needs to run again:
  sounds/<name>.wav        reaction sounds, played after the child answers (picked by sentiment)
  sounds/story/<cue>.wav   story cues, played just before a beat's narration (the beat's
                           "sound_effect"); the cue list comes from the stories themselves

Sensory comfort is enforced after generation, not left to the prompt: every file is
trimmed to MAX_SECONDS, faded in (no sudden start) and out, and its peak is set well
below full scale. Story cues are quieter still and fade in more slowly, since they set a
scene rather than respond to the child. The app's volume setting sits on top of that.
"""

import sys
import wave
from dataclasses import dataclass

import httpx
import numpy as np

from app import config, stories

SAMPLE_RATE = 44100
MAX_SECONDS = 2.0
FADE_OUT_S = 0.25


@dataclass(frozen=True)
class Style:
    folder: object
    peak_dbfs: float
    fade_in_s: float


REACTION = Style(config.SOUNDS_DIR, peak_dbfs=-14.0, fade_in_s=0.04)
STORY_CUE = Style(config.SOUNDS_DIR / "story", peak_dbfs=-18.0, fade_in_s=0.15)

# name -> (prompt, requested duration in seconds)
REACTION_SOUNDS = {
    "happy_chime": (
        "A single soft, warm wind chime, two gentle bell notes, quiet and calm, for a young child. "
        "Soft start, no sudden attack.", 1.5),
    "gentle_encourage": (
        "A slow, soft, rising harp arpeggio of three notes, warm and comforting, very quiet and gentle.", 1.8),
    "soft_try_again": (
        "One soft, low, rounded marimba note, calm and reassuring, very quiet, no harshness.", 1.2),
    "soft_pop": (
        "A tiny, soft, gentle bubble pop, very quiet.", 0.6),
    "cheer": (
        "A soft, gentle sparkle of small twinkling bells rising upward, warm and happy but quiet. "
        "No crowd, no clapping, no loud sounds.", 2.0),
}

# One prompt per sound_effect cue used in stories/*.json.
STORY_CUES = {
    "birds": ("Soft, distant birdsong in a calm garden, two or three gentle chirps, quiet and peaceful.", 2.0),
    "rain": ("Gentle, light rain falling softly on leaves, calm and quiet, no thunder.", 2.0),
    "hop": ("Three soft, light hops of a small rabbit on grass, gentle and quiet.", 1.2),
    "crunch": ("A soft, gentle crunch of a rabbit nibbling a fresh carrot, quiet.", 1.2),
    "water": ("Calm pond water gently lapping at the shore, soft and quiet.", 2.0),
    "splash": ("A tiny, soft splash of a duckling stepping into a calm pond, gentle and quiet.", 1.2),
    "bump": ("A soft, muffled thump of a small rubber ball gently bumping a clay pot. Quiet. "
             "No crash, no breaking sound.", 1.0),
    "footsteps": ("Soft, slow footsteps of small paws on a wooden floor, quiet and calm.", 1.8),
    "sweep": ("Gentle, soft sweeping of a small broom on a wooden floor, quiet and calm.", 1.8),
}


def generate(prompt: str, seconds: float) -> np.ndarray:
    response = httpx.post(
        f"{config.ELEVENLABS_BASE_URL}/sound-generation",
        params={"output_format": f"pcm_{SAMPLE_RATE}"},
        headers={"xi-api-key": config.ELEVENLABS_API_KEY},
        json={"text": prompt, "duration_seconds": seconds, "prompt_influence": 0.6},
        timeout=120,
    )
    if response.status_code != 200:
        try:
            detail = response.json().get("detail")
            message = detail.get("message") if isinstance(detail, dict) else detail
        except ValueError:
            message = response.text[:300]
        raise SystemExit(f"ElevenLabs error {response.status_code}: {message}")
    return np.frombuffer(response.content, dtype="<i2").astype(np.float64) / 32768.0


def soften(samples: np.ndarray, style: Style) -> np.ndarray:
    samples = samples[: int(MAX_SECONDS * SAMPLE_RATE)].copy()
    fade_in, fade_out = int(style.fade_in_s * SAMPLE_RATE), int(FADE_OUT_S * SAMPLE_RATE)
    samples[:fade_in] *= np.linspace(0.0, 1.0, fade_in)
    samples[-fade_out:] *= np.linspace(1.0, 0.0, fade_out)
    peak = np.max(np.abs(samples)) or 1.0
    return samples * (10 ** (style.peak_dbfs / 20) / peak)


def save_wav(samples: np.ndarray, path) -> None:
    with wave.open(str(path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(SAMPLE_RATE)
        out.writeframes((np.clip(samples, -1, 1) * 32767).astype("<i2").tobytes())


def story_cues_needed() -> set:
    return {b["sound_effect"] for s in stories.load_all() for b in s["beats"] if b.get("sound_effect")}


def make(sounds: dict, style: Style, force: bool) -> None:
    style.folder.mkdir(parents=True, exist_ok=True)
    for name, (prompt, seconds) in sounds.items():
        path = style.folder / f"{name}.wav"
        label = path.relative_to(config.SOUNDS_DIR).as_posix()
        if path.exists() and not force:
            print(f"skip  {label} (exists)")
            continue
        samples = soften(generate(prompt, seconds), style)
        save_wav(samples, path)
        rms_db = 20 * np.log10(np.sqrt(np.mean(samples ** 2)) or 1e-9)
        print(f"wrote {label}  {len(samples) / SAMPLE_RATE:.2f}s  peak {style.peak_dbfs:.0f} dBFS  rms {rms_db:.1f} dBFS")


def main() -> None:
    missing = story_cues_needed() - STORY_CUES.keys()
    if missing:
        raise SystemExit(f"Add a prompt to STORY_CUES for: {', '.join(sorted(missing))}")
    force = "--force" in sys.argv
    make(REACTION_SOUNDS, REACTION, force)
    make({k: v for k, v in STORY_CUES.items() if k in story_cues_needed()}, STORY_CUE, force)


if __name__ == "__main__":
    main()
