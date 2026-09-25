"""Time signature and downbeat detection."""

from __future__ import annotations

import numpy as np
import pytest

from omacap.analysis.chords import synchronise
from omacap.analysis.features import HOP_LENGTH, chromagram, onset_strength
from omacap.analysis.meter import (
    Meter,
    bar_boundaries,
    beat_accents,
    detect_meter,
    harmonic_novelty,
    score_grouping,
)
from omacap.analysis.tempo import analyse_tempo

from synth import SR, song

FRAME_RATE = SR / HOP_LENGTH

COMPOUND_ACCENTS = [1.0, 0.3, 0.3, 0.6, 0.3, 0.3]


def meter_of(audio) -> Meter:
    onset = onset_strength(audio, SR)
    chroma = chromagram(audio, SR)
    grid = analyse_tempo(onset, FRAME_RATE)
    edges = np.append(grid.beats, grid.beats[-1] + 60.0 / grid.bpm)
    beat_chroma = synchronise(chroma, FRAME_RATE, edges)
    return detect_meter(grid.beats, onset, FRAME_RATE, beat_chroma)


FOUR = [(0, ""), (7, ""), (9, "m"), (5, "")]
THREE = [(0, ""), (5, ""), (7, "")]


@pytest.mark.parametrize(
    "expected,audio",
    [
        ("4/4", song(FOUR, beats_per_bar=4)),
        ("4/4", song(FOUR, beats_per_bar=4, bpm=90)),
        ("4/4", song([(0, ""), (7, "")], beats_per_bar=4, bars_per_chord=2)),
        ("3/4", song(THREE, beats_per_bar=3, bars=12)),
        ("3/4", song(THREE, beats_per_bar=3, bars=12, bars_per_chord=2)),
        ("6/8", song(FOUR, beats_per_bar=6, bars=12, accents=COMPOUND_ACCENTS)),
        ("5/4", song([(0, ""), (9, "m")], beats_per_bar=5, bars=12)),
        ("7/8", song([(0, ""), (5, "")], beats_per_bar=7, bars=12)),
    ],
)
def test_time_signatures_are_detected(expected, audio):
    assert meter_of(audio).name == expected


def test_three_four_is_not_reported_as_six_eight():
    """Six is a multiple of three; only a weaker mid-bar makes it compound."""
    assert meter_of(song(THREE, beats_per_bar=3, bars=12)).beats_per_bar == 3


def test_six_eight_is_marked_compound():
    meter = meter_of(song(FOUR, beats_per_bar=6, bars=12, accents=COMPOUND_ACCENTS))
    assert meter.is_compound
    assert not meter_of(song(FOUR, beats_per_bar=4)).is_compound


def test_confidence_is_reported():
    assert 0.0 <= meter_of(song(FOUR, beats_per_bar=4)).confidence <= 1.0


def test_too_few_beats_falls_back_to_four_four():
    meter = detect_meter(np.array([0.0, 0.5]), np.zeros(10), FRAME_RATE, np.zeros((2, 12)))
    assert meter.name == "4/4"
    assert meter.confidence == 0.0


# -- building blocks ------------------------------------------------------

def test_novelty_is_zero_when_the_chroma_holds_still():
    chroma = np.tile(np.array([1.0, 0, 0, 0, 1, 0, 0, 1, 0, 0, 0, 0]), (8, 1))
    novelty = harmonic_novelty(chroma)
    assert novelty[0] == 1.0          # nothing precedes the first beat
    assert np.allclose(novelty[1:], 0.0, atol=1e-6)


def test_novelty_rises_when_the_chroma_changes():
    first = np.array([1.0, 0, 0, 0, 1, 0, 0, 1, 0, 0, 0, 0])
    second = np.array([0.0, 1, 0, 0, 0, 1, 0, 0, 1, 0, 0, 0])
    novelty = harmonic_novelty(np.array([first, first, second, second]))
    assert novelty[2] > 0.5
    assert novelty[3] < 0.1


def test_novelty_of_nothing():
    assert harmonic_novelty(np.zeros((0, 12))).size == 0


def test_accents_are_scaled_to_one():
    onset = np.zeros(200)
    onset[::10] = np.linspace(0.2, 1.0, 20)
    accents = beat_accents(np.arange(20) * (10 / FRAME_RATE), onset, FRAME_RATE)
    assert accents.max() == pytest.approx(1.0)
    assert (accents >= 0).all()


def test_grouping_score_rewards_the_right_phase():
    strength = np.tile([1.0, 0.2, 0.2, 0.2], 8)
    assert score_grouping(strength, 4, 0) > score_grouping(strength, 4, 1)
    assert score_grouping(strength, 4, 0) > score_grouping(strength, 3, 0)


def test_grouping_score_of_too_little_data():
    assert score_grouping(np.ones(3), 4, 0) == 0.0


# -- bar grid -------------------------------------------------------------

def test_bars_are_one_per_group_of_beats():
    beats = np.arange(16) * 0.5
    boundaries = bar_boundaries(beats, Meter(4, 0, "4/4", 1.0))
    assert len(boundaries) == 5
    assert boundaries[0] == pytest.approx(0.0)
    assert np.allclose(np.diff(boundaries), 2.0)


def test_a_missed_first_beat_does_not_lose_bar_one():
    """The tracker often misses beat one; the grid must reach back for it."""
    beats = np.arange(1, 17) * 0.5          # beat at t=0 was not found
    boundaries = bar_boundaries(beats, Meter(4, 3, "4/4", 1.0))
    assert boundaries[0] == pytest.approx(0.0, abs=0.05)
    assert len(boundaries) >= 5


def test_a_mostly_empty_trailing_bar_is_dropped():
    beats = np.arange(17) * 0.5             # one beat into a fifth bar
    boundaries = bar_boundaries(beats, Meter(4, 0, "4/4", 1.0))
    assert len(boundaries) == 5             # four bars, not five


def test_the_grid_does_not_run_past_the_recording():
    beats = np.arange(8) * 0.5
    boundaries = bar_boundaries(beats, Meter(4, 0, "4/4", 1.0), duration=3.6)
    assert boundaries[-1] <= 3.6 + 1e-9


def test_too_few_beats_gives_no_bars():
    assert bar_boundaries(np.array([1.0]), Meter(4, 0, "4/4", 1.0)).size == 0
