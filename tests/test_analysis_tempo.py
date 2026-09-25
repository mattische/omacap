"""Tempo and beat tracking, checked against click tracks of a known speed."""

from __future__ import annotations

import numpy as np
import pytest

from omacap.analysis.features import HOP_LENGTH, onset_strength
from omacap.analysis.tempo import (
    MAX_BPM,
    MIN_BPM,
    analyse_tempo,
    beat_confidence,
    estimate_tempo,
    onset_coverage,
    refine_tempo,
    tempo_autocorrelation,
    track_beats,
)

from synth import SR

FRAME_RATE = SR / HOP_LENGTH


def clicks(bpm: float, duration: float = 20.0, jitter: float = 0.0, seed: int = 0):
    rng = np.random.default_rng(seed)
    signal = np.zeros(int(SR * duration))
    length = 1500
    decay = np.exp(-np.arange(length) / 100.0)
    shape = decay * np.sin(2 * np.pi * 1000 * np.arange(length) / SR)
    position = 0.0
    while position < duration - 0.2:
        index = max(0, int((position + rng.normal(0, jitter)) * SR))
        signal[index: index + length] += shape
        position += 60.0 / bpm
    return signal


@pytest.mark.parametrize("bpm", [60, 75, 90, 100, 110, 120, 128, 140, 150, 160, 180, 200])
def test_tempo_is_found_exactly(bpm):
    grid = analyse_tempo(onset_strength(clicks(bpm), SR), FRAME_RATE)
    assert grid.bpm == pytest.approx(bpm, rel=0.02)


@pytest.mark.parametrize("bpm", [90, 120, 145, 175])
def test_tempo_survives_a_human_amount_of_jitter(bpm):
    grid = analyse_tempo(onset_strength(clicks(bpm, jitter=0.012), SR), FRAME_RATE)
    assert grid.bpm == pytest.approx(bpm, rel=0.03)


def test_fast_tempo_is_not_halved():
    """Autocorrelation peaks just as hard at half speed; coverage breaks the tie."""
    grid = analyse_tempo(onset_strength(clicks(180), SR), FRAME_RATE)
    assert grid.bpm == pytest.approx(180, rel=0.02)


def test_beats_span_the_recording():
    duration = 20.0
    grid = analyse_tempo(onset_strength(clicks(120, duration), SR), FRAME_RATE)
    assert len(grid) == pytest.approx(duration * 2, abs=3)
    assert grid.beats[0] < 1.0
    assert grid.beats[-1] > duration - 1.5
    assert (np.diff(grid.beats) > 0).all()


def test_beat_period_matches_the_tempo():
    grid = analyse_tempo(onset_strength(clicks(96), SR), FRAME_RATE)
    assert grid.beat_period == pytest.approx(60 / 96, rel=0.03)
    assert float(np.median(np.diff(grid.beats))) == pytest.approx(60 / 96, abs=0.03)


def test_confidence_is_high_for_a_metronome():
    grid = analyse_tempo(onset_strength(clicks(120), SR), FRAME_RATE)
    assert grid.confidence > 0.8


def test_candidate_tempi_cover_the_search_range():
    bpms, strengths = tempo_autocorrelation(onset_strength(clicks(120), SR), FRAME_RATE)
    assert bpms.size == strengths.size > 0
    # Lags are whole frames, so the end points land a few percent outside the
    # nominal limits rather than exactly on them.
    assert bpms.min() == pytest.approx(MIN_BPM, rel=0.05)
    assert bpms.max() == pytest.approx(MAX_BPM, rel=0.05)
    assert (np.diff(bpms) > 0).all()
    assert (strengths >= 0).all()


def test_silence_yields_no_tempo():
    grid = analyse_tempo(np.zeros(2000), FRAME_RATE)
    assert grid.bpm == 0.0
    assert len(grid) == 0
    assert grid.confidence == 0.0
    assert grid.beat_period == 0.0


def test_too_short_input_is_handled():
    assert estimate_tempo(np.zeros(2), FRAME_RATE) == 0.0
    assert track_beats(np.zeros(2), FRAME_RATE, 120).size == 0


def test_tracking_needs_a_tempo():
    assert track_beats(onset_strength(clicks(120), SR), FRAME_RATE, 0).size == 0


def test_refinement_beats_frame_quantisation():
    """Beat times land on frames; a straight-line fit recovers the true tempo."""
    period = 60 / 137.0
    exact = np.arange(40) * period
    quantised = np.round(exact * FRAME_RATE) / FRAME_RATE
    assert refine_tempo(quantised, 130.0) == pytest.approx(137.0, rel=0.005)


def test_refinement_tolerates_a_missed_beat():
    period = 60 / 120.0
    beats = np.delete(np.arange(40) * period, 17)
    assert refine_tempo(beats, 100.0) == pytest.approx(120.0, rel=0.01)


def test_refinement_falls_back_when_there_is_nothing_to_fit():
    assert refine_tempo(np.array([1.0, 2.0]), 111.0) == 111.0


def test_confidence_drops_for_erratic_beats():
    steady = np.arange(20) * 0.5
    erratic = np.cumsum(np.abs(np.random.default_rng(1).normal(0.5, 0.35, 20)))
    assert beat_confidence(steady, 120) > beat_confidence(erratic, 120)
    assert beat_confidence(np.array([1.0]), 120) == 0.0


def test_coverage_rewards_a_grid_that_explains_the_onsets():
    onset = onset_strength(clicks(120), SR)
    on_grid = track_beats(onset, FRAME_RATE, 120)
    half_speed = track_beats(onset, FRAME_RATE, 60)
    assert onset_coverage(on_grid, onset, FRAME_RATE) > onset_coverage(
        half_speed, onset, FRAME_RATE
    )
