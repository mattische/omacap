"""Feature extraction tests, checked against signals whose content is known."""

from __future__ import annotations

import numpy as np
import pytest

from omacap.analysis.features import (
    HOP_LENGTH,
    MAX_MIDI,
    MIN_MIDI,
    PITCH_NAMES,
    analyse_spectral,
    chromagram,
    midi_to_hz,
    onset_strength,
    semitone_filterbank,
    stft_magnitude,
)

SR = 22050


def tone(midi: int, seconds: float = 2.0, amplitude: float = 0.4) -> np.ndarray:
    t = np.arange(int(SR * seconds)) / SR
    return amplitude * np.sin(2 * np.pi * float(midi_to_hz(midi)) * t)


def chord(midis, seconds: float = 2.0) -> np.ndarray:
    return sum(tone(m, seconds) for m in midis)


def test_midi_to_hz_matches_concert_pitch():
    assert float(midi_to_hz(69)) == pytest.approx(440.0)
    assert float(midi_to_hz(57)) == pytest.approx(220.0)
    assert float(midi_to_hz(81)) == pytest.approx(880.0)


def test_stft_shape_follows_the_hop():
    magnitude = stft_magnitude(np.zeros(SR), n_fft=2048, hop_length=HOP_LENGTH)
    assert magnitude.shape[0] == 1025
    assert magnitude.shape[1] == pytest.approx(SR / HOP_LENGTH, abs=2)


def test_stft_finds_a_sine_at_the_right_bin():
    magnitude = stft_magnitude(tone(69), n_fft=8192)
    loudest = int(np.argmax(magnitude.mean(axis=1)))
    frequency = loudest * SR / 8192
    assert frequency == pytest.approx(440.0, abs=5.0)


def test_filterbank_rows_are_normalised():
    bank = semitone_filterbank(SR, 8192)
    assert bank.shape[0] == MAX_MIDI - MIN_MIDI
    assert np.allclose(bank.sum(axis=1), 1.0)
    assert (bank >= 0).all()


def test_filterbank_ignores_dc():
    assert (semitone_filterbank(SR, 8192)[:, 0] == 0).all()


@pytest.mark.parametrize("midi,name", [(60, "C"), (64, "E"), (69, "A"), (66, "F#"), (40, "E")])
def test_chroma_identifies_a_single_pitch(midi, name):
    values = chromagram(tone(midi), SR).mean(axis=1)
    assert PITCH_NAMES[int(np.argmax(values))] == name


@pytest.mark.parametrize(
    "midis,expected",
    [
        ((60, 64, 67), {"C", "E", "G"}),        # C major
        ((57, 60, 64), {"A", "C", "E"}),        # A minor
        ((55, 59, 62, 65), {"G", "B", "D", "F"}),  # G7
    ],
)
def test_chroma_identifies_the_notes_of_a_chord(midis, expected):
    values = chromagram(chord(midis), SR).mean(axis=1)
    top = {PITCH_NAMES[i] for i in np.argsort(values)[::-1][: len(expected)]}
    assert top == expected


def test_chroma_frames_are_scaled_to_one():
    values = chromagram(chord((60, 64, 67)), SR)
    assert values.shape[0] == 12
    assert np.allclose(values.max(axis=0), 1.0)


def test_chroma_of_silence_is_finite():
    values = chromagram(np.zeros(SR * 2), SR)
    assert np.isfinite(values).all()


def test_chroma_is_transposition_equivariant():
    """Transposing the audio by a semitone must rotate the chroma by one."""
    base = chromagram(chord((60, 64, 67)), SR).mean(axis=1)
    up = chromagram(chord((61, 65, 68)), SR).mean(axis=1)
    assert int(np.argmax(up)) == (int(np.argmax(base)) + 1) % 12


def test_onset_strength_is_normalised_and_positive():
    envelope = onset_strength(_clicks(120, 8.0), SR)
    assert envelope.min() >= 0.0
    assert envelope.max() == pytest.approx(1.0)


def test_onset_strength_peaks_on_the_clicks():
    bpm, duration = 120, 8.0
    envelope = onset_strength(_clicks(bpm, duration), SR)
    frame_rate = SR / HOP_LENGTH
    peaks = np.flatnonzero(
        (envelope[1:-1] > 0.3)
        & (envelope[1:-1] >= envelope[:-2])
        & (envelope[1:-1] > envelope[2:])
    )
    intervals = np.diff(peaks + 1) / frame_rate
    assert len(peaks) >= int(duration * bpm / 60) - 2
    assert np.median(intervals) == pytest.approx(60 / bpm, abs=0.03)


def test_steady_tone_produces_almost_no_onsets():
    envelope = onset_strength(tone(60, seconds=4.0), SR)
    # Only the note starting registers; the sustain is flat.
    assert float(np.mean(envelope > 0.3)) < 0.05


def test_spectral_features_share_a_time_axis():
    spectral = analyse_spectral(_clicks(120, 4.0), SR)
    assert spectral.chroma.shape[1] == spectral.n_frames
    assert spectral.onset.shape[0] == spectral.n_frames
    assert spectral.frame_rate == pytest.approx(SR / HOP_LENGTH)
    assert spectral.frame_times()[-1] == pytest.approx(4.0, abs=0.2)


def _clicks(bpm: float, duration: float) -> np.ndarray:
    signal = np.zeros(int(SR * duration))
    length = 1500
    decay = np.exp(-np.arange(length) / 100.0)
    click = decay * np.sin(2 * np.pi * 1000 * np.arange(length) / SR)
    for start in np.arange(0, duration - 0.1, 60 / bpm):
        index = int(start * SR)
        signal[index: index + length] += click
    return signal
