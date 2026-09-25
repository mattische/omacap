"""Chord recognition, checked against progressions whose chords are known."""

from __future__ import annotations

import numpy as np
import pytest

from omacap.analysis.chords import (
    CHANGE_PENALTY,
    NO_CHORD,
    QUALITIES,
    ChordSpan,
    chord_labels,
    chord_templates,
    decode,
    get_vocabulary,
    match_templates,
    merge_adjacent,
    synchronise,
    viterbi,
)
from omacap.analysis.features import HOP_LENGTH, chromagram

from synth import SR, chord_audio, expected_labels

FRAME_RATE = SR / HOP_LENGTH
BEAT = 0.6

PROGRESSIONS = {
    "I-V-vi-IV": [(0, ""), (7, ""), (9, "m"), (5, "")],
    "ii-V-I": [(2, "m7"), (7, "7"), (0, "maj7"), (0, "maj7")],
    "minor loop": [(9, "m"), (5, ""), (0, ""), (7, "")],
    "suspensions": [(0, "sus4"), (0, ""), (5, "maj7"), (7, "7")],
    "quick changes": [(0, ""), (9, "m"), (5, ""), (7, ""), (2, "m"), (7, "7"), (0, ""), (0, "")],
}


def render(progression, repeats: int = 4, noise: float = 0.0, seed: int = 0):
    sequence = progression * repeats
    audio = np.concatenate([chord_audio(root, quality, BEAT) for root, quality in sequence])
    if noise:
        audio = audio + noise * np.random.default_rng(seed).standard_normal(len(audio))
    boundaries = np.arange(len(sequence) + 1) * BEAT
    return audio.astype(np.float32), boundaries, expected_labels(sequence)


def recognise(progression, **kwargs):
    audio, boundaries, wanted = render(progression, **kwargs)
    spans = decode(chromagram(audio, SR), FRAME_RATE, boundaries)
    return [span.label for span in spans], wanted


# -- templates ------------------------------------------------------------

def test_every_root_and_quality_has_a_label():
    labels = chord_labels()
    assert len(labels) == 12 * len(QUALITIES) + 1
    assert labels[-1] == NO_CHORD
    assert len(set(labels)) == len(labels)
    assert "C" in labels and "Am" in labels and "G7" in labels


def test_templates_are_unit_length_and_positive():
    templates = chord_templates()
    assert templates.shape == (12 * len(QUALITIES), 12)
    assert np.allclose(np.linalg.norm(templates, axis=1), 1.0)
    assert (templates >= 0).all()


def test_a_major_template_contains_exactly_its_triad():
    template = chord_templates()[0]           # C major
    assert set(np.flatnonzero(template)) == {0, 4, 7}


def test_a_minor_template_flattens_the_third():
    minor_start = 12 * [suffix for suffix, _ in QUALITIES].index("m")
    assert set(np.flatnonzero(chord_templates()[minor_start])) == {0, 3, 7}


# -- recognition ----------------------------------------------------------

@pytest.mark.parametrize("name", list(PROGRESSIONS))
def test_progressions_are_recognised(name):
    got, wanted = recognise(PROGRESSIONS[name])
    assert got == wanted


@pytest.mark.parametrize("noise", [0.02, 0.05, 0.1, 0.2])
def test_recognition_survives_noise(noise):
    got, wanted = recognise(PROGRESSIONS["I-V-vi-IV"], noise=noise)
    matches = sum(a == b for a, b in zip(got, wanted))
    assert matches / len(wanted) >= 0.9


def test_a_triad_is_not_reported_as_a_seventh():
    """A plain triad must beat the seventh chord that contains it."""
    got, _ = recognise([(0, ""), (5, ""), (7, ""), (0, "")])
    assert set(got) == {"C", "F", "G"}


def test_silence_is_reported_as_no_chord():
    audio = np.zeros(int(SR * 4), dtype=np.float32)
    spans = decode(chromagram(audio, SR), FRAME_RATE, np.arange(9) * 0.5)
    assert {span.label for span in spans} == {NO_CHORD}
    assert {span.root for span in spans} == {-1}


def test_spans_tile_the_timeline():
    audio, boundaries, _ = render(PROGRESSIONS["I-V-vi-IV"], repeats=2)
    spans = decode(chromagram(audio, SR), FRAME_RATE, boundaries)
    assert len(spans) == len(boundaries) - 1
    assert spans[0].start == pytest.approx(boundaries[0])
    assert spans[-1].end == pytest.approx(boundaries[-1])
    for previous, current in zip(spans, spans[1:]):
        assert previous.end == pytest.approx(current.start)
        assert current.duration > 0


def test_roots_and_qualities_agree_with_the_labels():
    audio, boundaries, _ = render(PROGRESSIONS["ii-V-I"], repeats=2)
    for span in decode(chromagram(audio, SR), FRAME_RATE, boundaries):
        if span.label == NO_CHORD:
            continue
        from omacap.analysis.features import PITCH_NAMES

        assert span.label == f"{PITCH_NAMES[span.root]}{span.quality}"
        assert 0.0 <= span.strength <= 1.0


# -- vocabularies ---------------------------------------------------------

def test_the_simple_vocabulary_writes_only_triads():
    audio, boundaries, _ = render(PROGRESSIONS["ii-V-I"], repeats=2)
    spans = decode(chromagram(audio, SR), FRAME_RATE, boundaries, get_vocabulary("simple"))
    assert {span.quality for span in spans} <= {"", "m", NO_CHORD}


def test_the_standard_vocabulary_allows_sevenths_but_not_suspensions():
    assert set(get_vocabulary("standard")) == {"", "m", "7", "m7"}


def test_the_full_vocabulary_allows_everything():
    assert set(get_vocabulary("full")) == {suffix for suffix, _ in QUALITIES}


def test_an_unknown_vocabulary_is_rejected():
    with pytest.raises(ValueError, match="unknown chord vocabulary"):
        get_vocabulary("jazz")


# -- segmentation and smoothing ------------------------------------------

def test_synchronise_produces_one_row_per_interval():
    chroma = np.random.default_rng(0).random((12, 400))
    segments = synchronise(chroma, 40.0, np.arange(6) * 1.0)
    assert segments.shape == (5, 12)


def test_synchronise_skips_the_edges_of_each_segment():
    """The chroma window straddles chord changes, so the edges are not used."""
    chroma = np.zeros((12, 400))
    # Pitch class C only in the middle half of the first second: what a chord
    # looks like once the neighbouring chords are smeared across its edges.
    chroma[0, 10:30] = 1.0
    inner = synchronise(chroma, 40.0, np.array([0.0, 1.0]), margin=0.25)
    edges = synchronise(chroma, 40.0, np.array([0.0, 1.0]), margin=0.0)
    assert inner[0, 0] == pytest.approx(1.0)
    assert edges[0, 0] == pytest.approx(0.5)


def test_synchronise_handles_an_empty_boundary_list():
    assert synchronise(np.zeros((12, 10)), 40.0, np.array([1.0])).shape == (0, 12)


def test_viterbi_holds_a_chord_through_a_single_weak_frame():
    scores = np.zeros((5, 3))
    scores[:, 0] = 0.9
    scores[2, 0] = 0.55          # one bad frame
    scores[2, 1] = 0.58          # a rival that is briefly better
    assert list(viterbi(scores, CHANGE_PENALTY)) == [0, 0, 0, 0, 0]


def test_viterbi_still_follows_a_real_change():
    scores = np.zeros((6, 3))
    scores[:3, 0] = 0.9
    scores[3:, 1] = 0.9
    assert list(viterbi(scores, CHANGE_PENALTY)) == [0, 0, 0, 1, 1, 1]


def test_viterbi_handles_no_segments():
    assert viterbi(np.zeros((0, 5))).size == 0


def test_match_templates_scores_every_chord_plus_no_chord():
    scores = match_templates(np.random.default_rng(0).random((7, 12)))
    assert scores.shape == (7, 12 * len(QUALITIES) + 1)


def test_restricting_the_vocabulary_rules_chords_out():
    scores = match_templates(
        np.random.default_rng(0).random((3, 12)), qualities=("", "m")
    )
    seventh_column = 12 * [suffix for suffix, _ in QUALITIES].index("7")
    assert np.isneginf(scores[:, seventh_column]).all()
    assert np.isfinite(scores[:, 0]).all()


def test_merging_joins_repeated_chords():
    spans = [
        ChordSpan("C", 0.0, 1.0, 0, "", 0.9),
        ChordSpan("C", 1.0, 2.0, 0, "", 0.8),
        ChordSpan("G", 2.0, 3.0, 7, "", 0.9),
    ]
    merged = merge_adjacent(spans)
    assert [span.label for span in merged] == ["C", "G"]
    assert merged[0].start == 0.0 and merged[0].end == 2.0
    assert merged[0].duration == 2.0
    assert merged[0].strength == 0.9


def test_merging_an_empty_list():
    assert merge_adjacent([]) == []


# -- how dearly a change costs --------------------------------------------

def test_a_suspension_may_resolve_onto_its_own_root():
    """Csus4 to C is the same chord voiced differently, not the harmony moving."""
    got, wanted = recognise([(0, "sus4"), (0, ""), (0, "sus4"), (0, "")])
    assert got == wanted


def test_moving_to_another_root_costs_more_than_changing_quality():
    """The same margin buys a quality change but not a root change.

    The rival sits on the final segment, so the path pays to switch once and never
    pays to come back.
    """
    from omacap.analysis.chords import CHANGE_PENALTY, SAME_ROOT_FRACTION, chord_labels

    names = chord_labels()
    margin = CHANGE_PENALTY * 0.5          # more than the same-root cost, less than a full change

    def final_choice(rival: str) -> str:
        scores = np.zeros((3, len(names)))
        scores[:, names.index("C")] = 0.90
        scores[-1, names.index(rival)] = 0.90 + margin
        return names[list(viterbi(scores))[-1]]

    assert final_choice("C7") == "C7", "a quality change on the same root should be taken"
    assert final_choice("F") == "C", "a root change needs the full margin"
    assert SAME_ROOT_FRACTION < 1.0


def test_no_chord_gets_no_same_root_discount():
    """No-chord shares a root with nothing, so reaching it costs the full change."""
    from omacap.analysis.chords import CHANGE_PENALTY, chord_labels

    names = chord_labels()
    scores = np.zeros((3, len(names)))
    scores[:, names.index("C")] = 0.90
    scores[1, -1] = 0.90 + CHANGE_PENALTY * 0.5      # no-chord, a modest margin
    assert names[list(viterbi(scores))[1]] == "C"
