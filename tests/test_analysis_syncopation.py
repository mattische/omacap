"""Syncopation: a note that displaces the strong position after it.

The measure carries no threshold - a position quieter than the stronger one
after it simply contributes nothing - because a fixed floor made dense material
unreadable, where every position cleared it. These tests hold the anchors the
scale is read against.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from omacap.analysis.audio import AudioBuffer
from omacap.analysis.features import HOP_LENGTH, onset_strengths
from omacap.analysis.report import analyse_buffer
from omacap.analysis.rhythm import (
    BAR_SUBDIVISIONS,
    SYNCOPATED,
    bar_profile,
    metrical_weights,
    syncopation,
    syncopation_word,
)

from synth import SR, click, song

POSITIONS = 16
WEIGHTS = metrical_weights(4)


# -- the metrical hierarchy -----------------------------------------------

def test_the_downbeat_is_the_strongest_position():
    weights = metrical_weights(4)
    assert weights[0] == weights.max()
    assert len(weights) == 4 * BAR_SUBDIVISIONS


def test_four_four_has_a_strong_third_beat():
    # The half-bar makes beat 3 stronger than beats 2 and 4, as it is played.
    weights = metrical_weights(4)
    assert weights[8] > weights[4] == weights[12]


def test_three_four_has_no_half_bar_to_be_strong_on():
    weights = metrical_weights(3)
    assert weights[4] == weights[8]          # beats 2 and 3 are equals
    assert weights[0] > weights[4]


def test_a_compound_metre_is_strong_where_its_second_pulse_falls():
    # 6/8 counts in eighths but is felt in two, so position 12 - the fourth
    # eighth - is the second pulse and stronger than the eighths round it.
    weights = metrical_weights(6)
    assert weights[12] > weights[4]
    assert weights[0] > weights[12]


def test_beats_are_stronger_than_the_notes_between_them():
    weights = metrical_weights(4)
    assert weights[4] > weights[2] > weights[1]


# -- the measure ----------------------------------------------------------

def profile_from(positions: list[int]):
    """A profile with these positions at full strength and the rest silent."""
    profile = np.zeros(POSITIONS)
    profile[positions] = 1.0
    return profile


def test_notes_on_the_beat_are_not_syncopated():
    assert syncopation(profile_from([0, 4, 8, 12]), WEIGHTS) == 0.0


def test_straight_eighths_are_not_syncopated():
    """Every off-beat here is followed by a beat that is played, so nothing moved."""
    assert syncopation(profile_from([0, 2, 4, 6, 8, 10, 12, 14]), WEIGHTS) == 0.0


def test_an_offbeat_before_a_silent_beat_is_syncopated():
    assert syncopation(profile_from([2, 6, 10, 14]), WEIGHTS) > 0.0


def test_the_anticipated_downbeat_scores_highest():
    """A note just before an empty bar line displaces the strongest position."""
    before_the_bar = syncopation(profile_from([15]), WEIGHTS)
    before_a_beat = syncopation(profile_from([3]), WEIGHTS)
    assert before_the_bar > before_a_beat > 0.0


def test_a_quiet_offbeat_is_not_judged():
    """No threshold decides what counts as played; being quieter is enough."""
    loud_beat = np.zeros(POSITIONS)
    loud_beat[[0, 4, 8, 12]] = 1.0
    loud_beat[[2, 6, 10, 14]] = 0.3       # off-beats present but weaker
    assert syncopation(loud_beat, WEIGHTS) == 0.0


def test_a_flat_profile_is_not_syncopated():
    """Dense material used to break the measure; now it scores zero, not noise."""
    assert syncopation(np.ones(POSITIONS), WEIGHTS) == 0.0


def test_the_words_follow_the_anchored_scale():
    assert syncopation_word(0.0) == "played straight"
    assert syncopation_word(0.05) == "played straight"
    assert syncopation_word(0.2) == "slightly syncopated"
    assert syncopation_word(SYNCOPATED) == "syncopated"
    assert syncopation_word(0.9) == "syncopated"


# -- against audio --------------------------------------------------------

def rendered(hits: list[int], bars: int = 16, bpm: float = 120.0):
    """A click pattern at these sixteenth positions of every bar."""
    beat = 60.0 / bpm
    bar_length = 4 * beat
    samples = np.zeros(int(bars * bar_length * SR) + 4000)
    for bar in range(bars):
        for hit in hits:
            start = int((bar * bar_length + hit * bar_length / POSITIONS) * SR)
            sound = click(0.5, accent=(hit == 0))
            samples[start:start + len(sound)] += sound
    downbeats = [index * bar_length for index in range(bars)]
    onset, _ = onset_strengths(samples, SR)

    class FakeBar:
        def __init__(self, start):
            self.start = start
            self.duration = bar_length

    profile = bar_profile(onset, SR / HOP_LENGTH,
                          [FakeBar(d) for d in downbeats], POSITIONS)
    return syncopation(profile, WEIGHTS)


@pytest.mark.parametrize("name,hits", [
    ("four on the floor", [0, 4, 8, 12]),
    ("straight eighths", [0, 2, 4, 6, 8, 10, 12, 14]),
])
def test_a_straight_pattern_played_for_real_scores_nothing(name, hits):
    assert rendered(hits) < 0.05, name


@pytest.mark.parametrize("name,hits", [
    ("offbeat eighths only", [2, 6, 10, 14]),
    ("classic syncopation", [0, 3, 6, 10, 13]),
    ("anticipated downbeat", [3, 4, 7, 11, 15]),
])
def test_a_displaced_pattern_played_for_real_reaches_the_threshold(name, hits):
    assert rendered(hits) >= SYNCOPATED, name


def test_the_scale_separates_straight_from_displaced():
    straight = rendered([0, 2, 4, 6, 8, 10, 12, 14])
    displaced = rendered([3, 4, 7, 11, 15])
    assert displaced > straight
    assert straight < SYNCOPATED <= displaced


# -- through the whole analysis -------------------------------------------

def test_a_straight_song_is_reported_as_straight():
    audio = song([(0, ""), (7, ""), (9, "m"), (5, "")], bars=16)
    analysis = analyse_buffer(AudioBuffer(audio, SR), Path("Straight.wav"))
    assert analysis.syncopation_feel == "played straight"
    assert analysis.syncopated_sections == {}


def test_every_section_gets_a_score():
    audio = song([(0, ""), (7, ""), (9, "m"), (5, "")], bars=16)
    analysis = analyse_buffer(AudioBuffer(audio, SR), Path("Sections.wav"))
    assert analysis.section_syncopation, "expected at least one section scored"
    assert all(0.0 <= score < 1.0
               for score in analysis.section_syncopation.values())


def test_a_syncopated_section_is_named_in_the_chart():
    """The reporting path, driven by a score rather than by real audio.

    No recording to hand reaches the threshold, so the flag would otherwise
    never be exercised. This checks the chart says where it is.
    """
    from omacap.chart import Section, section_heading, summary_rows

    audio = song([(0, ""), (7, ""), (9, "m"), (5, "")], bars=16)
    analysis = analyse_buffer(AudioBuffer(audio, SR), Path("Flagged.wav"))
    first = min(analysis.section_syncopation)
    analysis.section_syncopation[first] = 0.55
    analysis.syncopation = 0.55

    rows = dict(summary_rows(analysis))
    assert "syncopated" in rows["Syncopation"]
    assert f"bar {first}" in rows["Syncopation"]

    section = Section(rows=[], letter="A", repeats=2, start=first, end=first + 7)
    heading = section_heading(section, first=False, last=False,
                              syncopated={first})
    assert "syncopated" in heading
    assert "syncopated" not in section_heading(section, first=False, last=False,
                                               syncopated=set())
