"""Key detection."""

from __future__ import annotations

import numpy as np
import pytest

from omacap.analysis.chords import ChordSpan, decode, merge_adjacent
from omacap.analysis.features import HOP_LENGTH, chromagram
from omacap.analysis.key import (
    MAJOR_PROFILE,
    MINOR_PROFILE,
    Key,
    chord_evidence,
    detect_key,
    detect_key_with_chords,
    key_scores,
    pitch_name,
)

from synth import SR, chord_audio

FRAME_RATE = SR / HOP_LENGTH
BEAT = 0.6


def render(progression, repeats: int = 4):
    sequence = progression * repeats
    audio = np.concatenate([chord_audio(r, q, BEAT) for r, q in sequence]).astype(np.float32)
    boundaries = np.arange(len(sequence) + 1) * BEAT
    return audio, boundaries


def analyse(progression, repeats: int = 4) -> Key:
    audio, boundaries = render(progression, repeats)
    chroma = chromagram(audio, SR)
    spans = merge_adjacent(decode(chroma, FRAME_RATE, boundaries))
    return detect_key_with_chords(chroma, spans)


# -- naming ---------------------------------------------------------------

@pytest.mark.parametrize(
    "tonic,mode,name,short",
    [(0, "major", "C major", "C"), (9, "minor", "A minor", "Am"),
     (3, "major", "Eb major", "Eb"), (6, "major", "F# major", "F#"),
     (2, "minor", "D minor", "Dm")],
)
def test_keys_are_spelled_conventionally(tonic, mode, name, short):
    key = Key(tonic=tonic, mode=mode, correlation=0.9, confidence=0.9)
    assert key.name == name
    assert key.short_name == short


@pytest.mark.parametrize(
    "tonic,mode,signature",
    [(0, "major", "no sharps or flats"), (7, "major", "1 sharp"),
     (2, "major", "2 sharps"), (5, "major", "1 flat"), (3, "major", "3 flats"),
     (9, "minor", "no sharps or flats"), (4, "minor", "1 sharp"),
     (2, "minor", "1 flat")],
)
def test_key_signatures(tonic, mode, signature):
    assert Key(tonic, mode, 0.9, 0.9).signature == signature


def test_flat_keys_are_spelled_with_flats():
    flat_key = Key(3, "major", 0.9, 0.9)          # Eb major
    sharp_key = Key(2, "major", 0.9, 0.9)         # D major
    assert pitch_name(10, flat_key) == "Bb"
    assert pitch_name(10, sharp_key) == "A#"
    assert pitch_name(10) == "A#"


# -- detection ------------------------------------------------------------

def test_profiles_are_twelve_long_and_peak_on_the_tonic():
    for profile in (MAJOR_PROFILE, MINOR_PROFILE):
        assert len(profile) == 12
        assert int(np.argmax(profile)) == 0


def test_all_twenty_four_keys_are_scored():
    scores = key_scores(np.ones(12) / 12)
    assert len(scores) == 24
    assert {mode for _, mode, _ in scores} == {"major", "minor"}


def test_scores_come_back_sorted():
    values = [score for _, _, score in key_scores(np.random.default_rng(0).random(12))]
    assert values == sorted(values, reverse=True)


@pytest.mark.parametrize(
    "name,progression",
    [
        ("C major", [(0, ""), (5, ""), (7, ""), (0, "")]),
        ("G major", [(7, ""), (0, ""), (2, ""), (7, "")]),
        ("Eb major", [(3, ""), (8, ""), (10, ""), (3, "")]),
        ("D minor", [(2, "m"), (7, "m"), (9, "7"), (2, "m")]),
        ("A minor", [(9, "m"), (5, ""), (0, ""), (9, "m")]),
        ("B minor", [(11, "m"), (7, ""), (2, ""), (11, "m")]),
    ],
)
def test_keys_are_detected(name, progression):
    assert analyse(progression).name == name


@pytest.mark.parametrize(
    "name,progression",
    [
        ("A minor", [(9, "m"), (5, ""), (0, ""), (7, "")]),
        ("E minor", [(4, "m"), (0, ""), (7, ""), (2, "")]),
        ("D minor", [(2, "m"), (10, ""), (5, ""), (0, "")]),
    ],
)
def test_relative_minors_are_not_mistaken_for_their_major(name, progression):
    """Chroma alone cannot tell A minor from C major; the chords decide it."""
    audio, boundaries = render(progression)
    chroma = chromagram(audio, SR)
    spans = merge_adjacent(decode(chroma, FRAME_RATE, boundaries))
    assert detect_key_with_chords(chroma, spans).name == name


def test_silence_has_no_key_and_no_confidence():
    key = detect_key(np.zeros((12, 50)))
    assert key.confidence == 0.0
    assert key.correlation == 0.0


def test_detection_falls_back_to_chroma_without_chords():
    audio, _ = render([(0, ""), (5, ""), (7, ""), (0, "")])
    chroma = chromagram(audio, SR)
    assert detect_key_with_chords(chroma, []).name == detect_key(chroma).name


def test_a_single_chroma_vector_is_accepted():
    audio, _ = render([(0, ""), (5, ""), (7, ""), (0, "")])
    chroma = chromagram(audio, SR)
    assert detect_key(chroma.mean(axis=1)).name == detect_key(chroma).name


# -- chord evidence -------------------------------------------------------

def spans_for(labels):
    roots = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}
    made = []
    for index, label in enumerate(labels):
        quality = "m" if label.endswith("m") else ""
        made.append(
            ChordSpan(label, index * 1.0, index + 1.0, roots[label[0]], quality, 0.9)
        )
    return made


def test_diatonic_chords_support_their_key():
    spans = spans_for(["C", "F", "G", "C"])
    assert chord_evidence(spans, 0, "major") > chord_evidence(spans, 1, "major")


def test_ending_on_the_tonic_counts_for_the_key():
    ending_on_c = chord_evidence(spans_for(["Am", "F", "G", "C"]), 0, "major")
    ending_on_am = chord_evidence(spans_for(["C", "F", "G", "Am"]), 0, "major")
    assert ending_on_c > ending_on_am


def test_out_of_key_chords_count_against_it():
    in_key = chord_evidence(spans_for(["C", "F", "G", "C"]), 0, "major")
    out_of_key = chord_evidence(spans_for(["C", "F", "B", "C"]), 0, "major")
    assert out_of_key < in_key


def test_evidence_without_chords_is_zero():
    assert chord_evidence([], 0, "major") == 0.0


# -- the relative key ------------------------------------------------------

@pytest.mark.parametrize(
    "tonic,mode,expected",
    [(7, "major", "E minor"), (4, "minor", "G major"), (0, "major", "A minor"),
     (9, "minor", "C major"), (3, "major", "C minor"), (2, "minor", "F major")],
)
def test_the_relative_key(tonic, mode, expected):
    assert Key(tonic, mode, 0.9, 0.5).relative.name == expected


def test_a_key_and_its_relative_share_a_signature():
    """That is exactly why they cannot be told apart by pitch content."""
    for tonic in range(12):
        for mode in ("major", "minor"):
            key = Key(tonic, mode, 0.9, 0.5)
            assert key.signature == key.relative.signature


def test_the_relative_of_the_relative_is_the_original():
    for tonic in range(12):
        key = Key(tonic, "major", 0.9, 0.5)
        assert key.relative.relative.name == key.name


# -- relative keys --------------------------------------------------------

def test_a_key_and_its_relative_are_a_pair():
    from omacap.analysis.key import are_relatives

    assert are_relatives((0, "major"), (9, "minor"))      # C major / A minor
    assert are_relatives((9, "minor"), (0, "major"))      # and the other way
    assert are_relatives((7, "major"), (4, "minor"))      # G major / E minor


def test_keys_that_are_not_a_relative_pair():
    from omacap.analysis.key import are_relatives

    assert not are_relatives((0, "major"), (7, "major"))  # same mode
    assert not are_relatives((0, "minor"), (9, "minor"))
    assert not are_relatives((0, "major"), (0, "minor"))  # parallel, not relative
    assert not are_relatives((0, "major"), (2, "minor"))


def test_the_chords_decide_between_relatives(monkeypatch):
    """The chroma cannot tell C major from A minor, so it must not get a vote.

    Both keys hold the same notes, so whatever correlation the chroma gives one
    it gives the other. Here the chroma is made to prefer C major while the
    chords behave like A minor; A minor has to win.
    """
    import numpy as np

    from omacap.analysis import key as key_module
    from omacap.analysis.chords import ChordSpan

    # An A minor progression: Am - Dm - E - Am, ending at home.
    spans = [
        ChordSpan("Am", 0.0, 2.0, 9, "m", 0.9),
        ChordSpan("Dm", 2.0, 4.0, 2, "m", 0.9),
        ChordSpan("E", 4.0, 6.0, 4, "", 0.9),
        ChordSpan("Am", 6.0, 8.0, 9, "m", 0.9),
    ]
    # A chroma holding the white notes, weighted towards C.
    chroma = np.zeros(12)
    for pitch, level in {0: 5.0, 2: 3.0, 4: 4.0, 5: 3.0, 7: 4.0, 9: 3.5, 11: 2.5}.items():
        chroma[pitch] = level

    detected = key_module.detect_key_with_chords(chroma.reshape(12, 1), spans)
    assert detected.name == "A minor", detected.name


def test_a_non_relative_runner_up_still_uses_the_combined_score():
    """The rule fires only where the chroma is blind, not everywhere."""
    import numpy as np

    from omacap.analysis import key as key_module
    from omacap.analysis.chords import ChordSpan

    spans = [
        ChordSpan("C", 0.0, 2.0, 0, "", 0.9),
        ChordSpan("F", 2.0, 4.0, 5, "", 0.9),
        ChordSpan("G", 4.0, 6.0, 7, "", 0.9),
        ChordSpan("C", 6.0, 8.0, 0, "", 0.9),
    ]
    chroma = np.zeros(12)
    for pitch, level in {0: 6.0, 2: 2.5, 4: 4.0, 5: 4.0, 7: 5.0, 9: 3.0, 11: 2.5}.items():
        chroma[pitch] = level
    detected = key_module.detect_key_with_chords(chroma.reshape(12, 1), spans)
    assert detected.name == "C major", detected.name
