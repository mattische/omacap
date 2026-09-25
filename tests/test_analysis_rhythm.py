"""What happens inside a beat, checked against patterns played on purpose."""

from __future__ import annotations

import numpy as np
import pytest

from omacap.analysis.features import HOP_LENGTH, onset_strength
from omacap.analysis.rhythm import (
    STRAIGHT_BELOW,
    SUBDIVISIONS,
    SWUNG_BELOW,
    Rhythm,
    analyse_rhythm,
    beat_profile,
    profile_peaks,
)
from omacap.analysis.tempo import analyse_tempo

SR = 22050


def played(offsets, bpm: float = 120.0, seconds: float = 24.0) -> np.ndarray:
    """Clicks at the given fractions of every beat."""
    signal = np.zeros(int(SR * seconds))
    beat = 60.0 / bpm
    length = 900
    decay = np.exp(-np.arange(length) / 70.0)
    position = 0.0
    while position < seconds - beat:
        for fraction in offsets:
            index = int((position + fraction * beat) * SR)
            amplitude = 1.0 if fraction == 0.0 else 0.55
            hertz = 1500 if fraction == 0.0 else 1000
            if 0 <= index < len(signal) - length:
                signal[index:index + length] += (
                    amplitude * decay * np.sin(2 * np.pi * hertz * np.arange(length) / SR)
                )
        position += beat
    return signal


def analyse(offsets, **kwargs) -> Rhythm:
    onset = onset_strength(played(offsets, **kwargs), SR)
    rate = SR / HOP_LENGTH
    grid = analyse_tempo(onset, rate)
    return analyse_rhythm(onset, rate, grid.beats)


# -- where the off-beat sits ----------------------------------------------

@pytest.mark.parametrize(
    "position", [0.50, 0.60, 2 / 3, 0.75]
)
def test_the_offbeat_is_found_where_it_was_played(position):
    rhythm = analyse([0.0, position])
    assert rhythm.offbeat == pytest.approx(position, abs=0.03)


def test_nothing_between_the_beats_is_reported_as_such():
    rhythm = analyse([0.0])
    assert rhythm.offbeat is None
    assert "on the beat" in rhythm.feel


def test_straight_and_swung_are_told_apart():
    straight = analyse([0.0, 0.5])
    swung = analyse([0.0, 2 / 3])
    assert straight.feel == "straight eighths"
    assert "swung" in swung.feel
    assert straight.offbeat < STRAIGHT_BELOW <= swung.offbeat


def test_a_heavy_swing_is_named():
    assert analyse([0.0, 0.78]).feel == "heavily swung"
    assert SWUNG_BELOW < 0.78


def test_a_busy_beat_claims_no_placement():
    """With several hits in a beat there is no single off-beat to name."""
    rhythm = analyse([0.0, 0.25, 0.5, 0.75])
    assert rhythm.busy
    assert rhythm.feel == "busier than eighths"
    assert "off-beat at" not in rhythm.description


def test_the_description_carries_the_number_when_it_means_something():
    rhythm = analyse([0.0, 0.5])
    assert "off-beat at 0.5" in rhythm.description


def test_the_feel_survives_a_different_tempo():
    for bpm in (90.0, 150.0):
        assert analyse([0.0, 2 / 3], bpm=bpm).offbeat == pytest.approx(2 / 3, abs=0.04)


# -- the pieces ------------------------------------------------------------

def test_the_profile_has_one_value_per_position():
    onset = onset_strength(played([0.0, 0.5]), SR)
    rate = SR / HOP_LENGTH
    grid = analyse_tempo(onset, rate)
    profile = beat_profile(onset, rate, grid.beats)
    assert profile.shape == (SUBDIVISIONS,)
    assert (profile >= 0).all()


def test_the_profile_peaks_on_the_beat():
    onset = onset_strength(played([0.0, 0.5]), SR)
    rate = SR / HOP_LENGTH
    grid = analyse_tempo(onset, rate)
    profile = beat_profile(onset, rate, grid.beats)
    assert int(np.argmax(profile)) == 0


def test_peaks_are_counted_not_guessed():
    assert len(profile_peaks(np.array([1.0, 0.0, 0.0, 0.0]))) == 1
    assert len(profile_peaks(np.array([1.0, 0.0, 0.8, 0.0]))) == 2
    assert profile_peaks(np.zeros(8)) == []


def test_the_beat_wraps_when_counting_peaks():
    """The position before the first is the last one, not the first itself.

    Getting that wrong made position zero always look like a peak, because it was
    being compared against its own value.
    """
    # The last position is louder than the first, and they are neighbours, so the
    # first is on a downward slope and is not a peak of its own.
    assert profile_peaks(np.array([0.9, 0.3, 0.0, 1.0])) == [0.75]
    assert profile_peaks(np.array([0.5, 0.0, 0.0, 1.0])) == [0.75]
    # Genuinely separated in the circle, both count.
    assert profile_peaks(np.array([1.0, 0.0, 0.8, 0.0])) == [0.0, 0.5]


def test_too_little_to_go_on():
    empty = analyse_rhythm(np.zeros(10), 43.0, np.array([0.0, 0.5]))
    assert empty.offbeat is None
    assert empty.feel == "unclear"


def test_silence_says_nothing():
    rhythm = analyse_rhythm(np.zeros(500), 43.0, np.arange(10) * 0.5)
    assert rhythm.offbeat is None
    assert rhythm.peaks == []
