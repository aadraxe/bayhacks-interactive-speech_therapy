"""Acoustic engine: robust prosody features for connected read-aloud speech.

Only features that hold up on short, home-recorded child speech:

  mean_pitch       Hz. Mean F0 over voiced frames, with octave-jump errors removed.
  pitch_variation  Semitones. SD of F0 around the speaker's own median, so it measures
                   expressiveness and is comparable between a child's and an adult's voice.
  speaking_rate    Syllables per second across the speaking span (first sound to last,
                   pauses included). Syllables are counted from intensity peaks in voiced
                   speech (de Jong & Wempe, 2009), so no transcript is needed.
  pause_count      Silent gaps of at least MIN_PAUSE_S between stretches of speech.
                   Silence before the first word and after the last is ignored.
                   (pause_s, the total pause time, is reported alongside it.)

Jitter and shimmer are deliberately left out: they are unreliable on this kind of audio.
"""

from pathlib import Path

import numpy as np
import parselmouth
from parselmouth.praat import call

PITCH_FLOOR = 75.0      # Hz; low enough for adult voices
PITCH_CEILING = 600.0   # Hz; children's F0 regularly goes above 400
OCTAVE_JUMP_ST = 10.0   # frames further than this from the median are pitch-tracker errors
SILENCE_DB = -25.0      # silence = quieter than the loudest speech by this much
MIN_PAUSE_S = 0.30      # shorter gaps are normal breaks between words, not pauses
MIN_SOUNDING_S = 0.10
MIN_DIP_DB = 2.0        # intensity must dip this much between two syllables
MIN_VOICED_FRAMES = 20  # 0.2 s of voicing before pitch numbers mean anything
MIN_SPEECH_S = 1.5      # below this, rate and pause numbers are flagged as unreliable
MIN_DURATION_S = 0.25
DIGITAL_SILENCE = 1e-4  # peak amplitude below this = nothing was recorded


class AudioError(ValueError):
    """The file could not be read as audio."""


def extract_features(wav_path) -> dict:
    """Prosody features for one recording. See the module docstring for definitions."""
    return analyze(wav_path, include_tracks=False)


def analyze(wav_path, include_tracks: bool = False) -> dict:
    """extract_features() plus, optionally, the pitch/intensity tracks behind the numbers."""
    try:
        sound = parselmouth.Sound(str(Path(wav_path)))
    except parselmouth.PraatError as exc:
        raise AudioError("Could not read this file as audio. Try a WAV recording.") from exc
    if sound.n_channels > 1:
        sound = sound.convert_to_mono()

    duration = sound.get_total_duration()
    if duration < MIN_DURATION_S:
        return _no_speech(duration, "The recording is too short to analyse.")
    if np.max(np.abs(sound.values)) < DIGITAL_SILENCE:
        return _no_speech(duration, "No sound was picked up. Check the microphone.")

    pitch = sound.to_pitch_ac(
        time_step=0.01, pitch_floor=PITCH_FLOOR, pitch_ceiling=PITCH_CEILING
    )
    f0 = pitch.selected_array["frequency"]
    f0_times = pitch.xs()

    mean_pitch, pitch_variation, pitch_ok = _pitch_stats(f0)
    segments = _sounding_segments(sound)
    if not segments:
        return _no_speech(duration, "No speech was detected.")

    speech_start, speech_end = segments[0][0], segments[-1][1]
    span = speech_end - speech_start
    speech_s = sum(end - start for start, end in segments)
    pauses = [(a_end, b_start) for (_, a_end), (b_start, _) in zip(segments, segments[1:])]

    intensity = sound.to_intensity(minimum_pitch=50.0, time_step=0.01)
    int_values = intensity.values[0]
    int_times = intensity.xs()
    syllables = _syllable_nuclei(int_values, int_times, f0, f0_times)

    notes = []
    if not pitch_ok:
        notes.append("Too little voiced speech to measure pitch.")
    if speech_s < MIN_SPEECH_S:
        notes.append(
            f"Only {speech_s:.1f}s of speech: speaking rate and pauses need "
            f"at least {MIN_SPEECH_S:.1f}s to be meaningful."
        )

    result = {
        "mean_pitch": _round(mean_pitch, 1),
        "pitch_variation": _round(pitch_variation, 2),
        "speaking_rate": _round(len(syllables) / span, 2) if span > 0 else None,
        "pause_count": len(pauses),
        "pause_s": round(sum(b - a for a, b in pauses), 2),
        "duration_s": round(duration, 2),
        "speech_s": round(speech_s, 2),
        "syllable_count": len(syllables),
        "reliable": pitch_ok and speech_s >= MIN_SPEECH_S,
        "notes": notes,
    }
    if include_tracks:
        voiced = f0 > 0
        result["tracks"] = {
            "pitch": [[round(t, 3), round(v, 1)] for t, v in zip(f0_times[voiced], f0[voiced])],
            "intensity": [[round(t, 3), round(v, 1)] for t, v in zip(int_times[::2], int_values[::2])],
            "syllables": [round(t, 3) for t in syllables],
            "pauses": [[round(a, 3), round(b, 3)] for a, b in pauses],
            "speech_span": [round(speech_start, 3), round(speech_end, 3)],
        }
    return result


def _pitch_stats(f0: np.ndarray):
    """(mean Hz, SD in semitones, enough voicing?) with octave-jump frames removed."""
    hz = f0[f0 > 0]
    if len(hz) < MIN_VOICED_FRAMES:
        return None, None, False
    semitones = 12 * np.log2(hz / np.median(hz))
    keep = np.abs(semitones) <= OCTAVE_JUMP_ST
    if keep.sum() < MIN_VOICED_FRAMES:
        return None, None, False
    return float(hz[keep].mean()), float(semitones[keep].std()), True


def _sounding_segments(sound: parselmouth.Sound) -> list:
    """(start, end) of each stretch of speech, from Praat's silence detector."""
    textgrid = call(
        sound, "To TextGrid (silences)",
        100, 0.0, SILENCE_DB, MIN_PAUSE_S, MIN_SOUNDING_S, "silent", "sounding",
    )
    segments = []
    for i in range(1, call(textgrid, "Get number of intervals", 1) + 1):
        if call(textgrid, "Get label of interval", 1, i) == "sounding":
            segments.append((
                call(textgrid, "Get start time of interval", 1, i),
                call(textgrid, "Get end time of interval", 1, i),
            ))
    return segments


def _syllable_nuclei(values, times, f0, f0_times) -> list:
    """Times of syllable nuclei: voiced intensity peaks separated by a real dip."""
    threshold = max(np.quantile(values, 0.99) + SILENCE_DB, values.min())
    inner = values[1:-1]
    peaks = np.where((inner > values[:-2]) & (inner >= values[2:]) & (inner > threshold))[0] + 1

    nuclei = []
    for k, p in enumerate(peaks):
        stop = peaks[k + 1] if k + 1 < len(peaks) else len(values)
        dip = values[p:stop].min()
        if values[p] - dip <= MIN_DIP_DB:
            continue
        frame = int(np.argmin(np.abs(f0_times - times[p])))
        if f0[frame] > 0:
            nuclei.append(float(times[p]))
    return nuclei


def _no_speech(duration: float, note: str) -> dict:
    return {
        "mean_pitch": None,
        "pitch_variation": None,
        "speaking_rate": None,
        "pause_count": 0,
        "pause_s": 0.0,
        "duration_s": round(duration, 2),
        "speech_s": 0.0,
        "syllable_count": 0,
        "reliable": False,
        "notes": [note],
    }


def _round(value, digits):
    return None if value is None else round(value, digits)
