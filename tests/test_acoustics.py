"""Tests for the acoustic engine.

Synthetic signals have a known pitch, syllable rate and pause layout, so the numbers
can be checked exactly. The samples/ tests run on real narrator recordings when present
(create them with: python -m scripts.make_samples).
"""

import numpy as np
import parselmouth
import pytest

from app import config
from app.acoustics import AudioError, extract_features

SR = 16000


def _syllable_train(f0_fn, seconds, rate=4.0):
    """Voiced 'syllables' at `rate` per second: harmonic tone with a Hann envelope each."""
    t = np.arange(int(seconds * SR)) / SR
    phase = 2 * np.pi * np.cumsum(f0_fn(t)) / SR
    tone = sum(np.sin(k * phase) / k for k in range(1, 6))
    syllable_len = int(SR / rate)
    envelope = np.tile(np.hanning(syllable_len), int(np.ceil(len(t) / syllable_len)))[: len(t)]
    return 0.3 * tone * envelope


def _write(tmp_path, samples, name="test.wav", channels=1):
    values = np.vstack([samples] * channels)
    path = tmp_path / name
    parselmouth.Sound(values, sampling_frequency=SR).save(str(path), "WAV")
    return path


def _speech_with_gaps(f0_fn, segments=3, segment_s=2.0, gap_s=0.6):
    silence = np.zeros(int(gap_s * SR))
    parts = [silence]  # leading silence must not count as a pause
    for i in range(segments):
        parts.append(_syllable_train(f0_fn, segment_s))
        parts.append(silence)
    return np.concatenate(parts)


def test_flat_pitch_known_pauses_and_rate(tmp_path):
    audio = _speech_with_gaps(lambda t: np.full_like(t, 200.0))
    f = extract_features(_write(tmp_path, audio))

    assert f["mean_pitch"] == pytest.approx(200, abs=5)
    assert f["pitch_variation"] < 0.5
    assert f["pause_count"] == 2  # gaps between 3 segments; edges ignored
    # 24 syllables over a 7.2 s speaking span (6 s speech + 2 x 0.6 s pauses) = 3.3/s
    assert 2.8 <= f["speaking_rate"] <= 3.9
    assert f["reliable"] is True


def test_expressive_pitch_scores_higher_variation(tmp_path):
    flat = extract_features(_write(tmp_path, _speech_with_gaps(lambda t: np.full_like(t, 220.0)), "flat.wav"))
    lively = extract_features(_write(
        tmp_path, _speech_with_gaps(lambda t: 220 * 2 ** (4 * np.sin(2 * np.pi * 0.7 * t) / 12)), "lively.wav"
    ))
    assert lively["pitch_variation"] > 2.0
    assert lively["pitch_variation"] > flat["pitch_variation"] + 1.5


def test_child_pitch_range_is_tracked(tmp_path):
    f = extract_features(_write(tmp_path, _speech_with_gaps(lambda t: np.full_like(t, 380.0))))
    assert f["mean_pitch"] == pytest.approx(380, abs=10)


def test_stereo_file_is_accepted(tmp_path):
    audio = _speech_with_gaps(lambda t: np.full_like(t, 200.0))
    f = extract_features(_write(tmp_path, audio, channels=2))
    assert f["pause_count"] == 2


def test_silence_returns_no_features_without_crashing(tmp_path):
    f = extract_features(_write(tmp_path, np.zeros(SR * 2)))
    assert f["reliable"] is False
    assert f["mean_pitch"] is None and f["speaking_rate"] is None
    assert f["notes"]


def test_too_short_recording(tmp_path):
    f = extract_features(_write(tmp_path, _syllable_train(lambda t: np.full_like(t, 200.0), 0.2)))
    assert f["reliable"] is False
    assert f["mean_pitch"] is None


def test_single_word_is_flagged_unreliable(tmp_path):
    word = np.concatenate([np.zeros(SR // 4), _syllable_train(lambda t: np.full_like(t, 200.0), 0.5), np.zeros(SR // 4)])
    f = extract_features(_write(tmp_path, word))
    assert f["reliable"] is False
    assert f["mean_pitch"] == pytest.approx(200, abs=5)  # pitch is still measured


def test_unreadable_file_raises_audio_error(tmp_path):
    bad = tmp_path / "not_audio.wav"
    bad.write_bytes(b"this is not a wav file")
    with pytest.raises(AudioError):
        extract_features(bad)


# --- Real narrator recordings ----------------------------------------------

FLUENT = config.SAMPLES_DIR / "read_aloud_fluent.wav"
HESITANT = config.SAMPLES_DIR / "read_aloud_hesitant.wav"
needs_samples = pytest.mark.skipif(
    not (FLUENT.exists() and HESITANT.exists()), reason="run python -m scripts.make_samples"
)


@needs_samples
def test_fluent_reading_is_plausible():
    f = extract_features(FLUENT)
    assert f["reliable"] is True
    assert 150 <= f["mean_pitch"] <= 300       # adult female narrator
    assert 1.0 <= f["pitch_variation"] <= 6.0  # normal read-aloud range
    assert 2.0 <= f["speaking_rate"] <= 6.0    # syllables/s for read speech
    assert 55 <= f["syllable_count"] <= 85     # passage has ~72 syllables


@needs_samples
def test_hesitant_reading_has_more_pausing_than_fluent():
    fluent, hesitant = extract_features(FLUENT), extract_features(HESITANT)
    assert hesitant["pause_count"] >= fluent["pause_count"] + 3
    assert hesitant["pause_s"] > fluent["pause_s"] + 3
    assert hesitant["speaking_rate"] < fluent["speaking_rate"]
