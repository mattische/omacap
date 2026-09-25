"""End-to-end analysis over synthetic songs with a known tempo, key and chords."""

from __future__ import annotations

import subprocess
import wave
from pathlib import Path

import numpy as np
import pytest

from omacap.analysis.audio import AudioBuffer, DecodeError, load_audio, trim_silence
from omacap.analysis.chords import ChordSpan
from omacap.analysis.report import (
    AnalysisError,
    analyse_buffer,
    analyse_file,
    bar_chords,
    chord_family,
)

from synth import SR, song

FOUR = [(0, ""), (7, ""), (9, "m"), (5, "")]


def analyse(progression=FOUR, vocabulary=None, **kwargs):
    audio = song(progression, **kwargs)
    return analyse_buffer(AudioBuffer(audio, SR), Path("Test Song.wav"),
                          **({"vocabulary": vocabulary} if vocabulary else {}))


@pytest.fixture(scope="module")
def analysis():
    return analyse(bars=16)


# -- the headline facts ---------------------------------------------------

def test_tempo_is_detected(analysis):
    assert analysis.tempo == pytest.approx(120, rel=0.02)
    assert analysis.tempo_confidence > 0.7


def test_time_signature_is_detected(analysis):
    assert analysis.meter.name == "4/4"
    assert analysis.meter.beats_per_bar == 4


def test_key_is_detected(analysis):
    assert analysis.key.name == "C major"


def test_bar_count_is_exact(analysis):
    assert analysis.bar_count == 16
    assert analysis.beat_count == pytest.approx(64, abs=2)


def test_duration_is_reported(analysis):
    assert analysis.duration == pytest.approx(32.0, abs=0.5)


def test_bars_are_numbered_and_tile_the_song(analysis):
    assert [bar.number for bar in analysis.bars] == list(range(1, 17))
    for previous, current in zip(analysis.bars, analysis.bars[1:]):
        assert previous.end == pytest.approx(current.start)
        assert current.duration > 0
    assert analysis.bars[0].start == pytest.approx(0.0, abs=0.3)


def test_chords_follow_the_progression(analysis):
    labels = [bar.label for bar in analysis.bars]
    assert labels[:4] == ["C", "G", "Am", "F"]
    matches = sum(
        label == ["C", "G", "Am", "F"][index % 4] for index, label in enumerate(labels)
    )
    assert matches / len(labels) >= 0.9


def test_the_vocabulary_lists_what_the_chart_shows(analysis):
    assert set(analysis.chord_vocabulary) >= {"C", "G", "Am", "F"}
    assert set(analysis.chord_vocabulary) <= {bar.label for bar in analysis.bars}


@pytest.mark.parametrize(
    "progression,beats_per_bar,bpm,bars,expected,vocabulary",
    [
        ([(9, "m"), (5, ""), (0, ""), (7, "")], 4, 100, 12,
         ["Am", "F", "C", "G"], None),
        ([(0, ""), (5, ""), (7, "")], 3, 140, 12, ["C", "F", "G"], None),
        # Sevenths are not in the default vocabulary, which writes the plain
        # triad on purpose, so this asks for the one that has them.
        ([(2, "m7"), (7, "7"), (0, ""), (0, "")], 4, 90, 16,
         ["Dm7", "G7", "C", "C"], "standard"),
    ],
)
def test_other_songs(progression, beats_per_bar, bpm, bars, expected, vocabulary):
    result = analyse(progression, beats_per_bar=beats_per_bar, bpm=bpm, bars=bars,
                     vocabulary=vocabulary)
    assert result.tempo == pytest.approx(bpm, rel=0.03)
    assert result.bar_count == bars
    assert [bar.label for bar in result.bars[: len(expected)]] == expected


def test_a_different_tempo_is_followed():
    assert analyse(bpm=96, bars=12).tempo == pytest.approx(96, rel=0.03)


# -- refusals -------------------------------------------------------------

def test_a_clip_that_is_too_short_is_refused():
    buffer = AudioBuffer(np.zeros(SR * 2, dtype=np.float32), SR)
    with pytest.raises(AnalysisError, match="at least"):
        analyse_buffer(buffer, Path("short.wav"))


def test_material_without_a_beat_is_refused():
    buffer = AudioBuffer(np.zeros(SR * 20, dtype=np.float32), SR)
    with pytest.raises(AnalysisError, match="no steady beat"):
        analyse_buffer(buffer, Path("silence.wav"))


# -- bar summarising ------------------------------------------------------

def span(label, start, end, root=0, quality=""):
    return ChordSpan(label, start, end, root, quality, 0.9)


def test_a_bar_held_by_one_chord_lists_only_that_chord():
    spans = [span("C", 0.0, 1.6), span("G", 1.6, 2.0, 7)]
    assert bar_chords(spans, 0.0, 2.0) == ["C"]


def test_a_bar_split_between_two_chords_lists_both_in_order():
    spans = [span("C", 0.0, 1.0), span("G", 1.0, 2.0, 7)]
    assert bar_chords(spans, 0.0, 2.0) == ["C", "G"]


def test_a_bar_never_lists_more_than_two_chords():
    spans = [span("C", 0.0, 0.5), span("G", 0.5, 1.0, 7),
             span("Am", 1.0, 1.5, 9, "m"), span("F", 1.5, 2.0, 5)]
    assert len(bar_chords(spans, 0.0, 2.0)) <= 2


def test_a_chord_and_its_extension_count_as_one():
    """D and Dmaj7 are the same chord on a chart."""
    spans = [span("D", 0.0, 1.2, 2, ""), span("Dmaj7", 1.2, 2.0, 2, "maj7")]
    assert bar_chords(spans, 0.0, 2.0) == ["D"]


def test_the_best_supported_variant_wins_its_family():
    spans = [span("Dmaj7", 0.0, 1.5, 2, "maj7"), span("D", 1.5, 2.0, 2, "")]
    assert bar_chords(spans, 0.0, 2.0) == ["Dmaj7"]


def test_a_bar_with_no_chords():
    assert bar_chords([], 0.0, 2.0) == []
    assert bar_chords([span("C", 0.0, 1.0)], 2.0, 2.0) == []


@pytest.mark.parametrize(
    "quality,family",
    [("", "major"), ("maj7", "major"), ("7", "major"), ("sus4", "major"),
     ("m", "minor"), ("m7", "minor"), ("dim", "dim")],
)
def test_chord_families(quality, family):
    assert chord_family(span("X", 0, 1, 4, quality)) == (4, family)


def test_no_chord_has_its_own_family():
    assert chord_family(span("N.C.", 0, 1, -1, "N.C."))[0] == -1


# -- decoding -------------------------------------------------------------

def test_a_wav_file_can_be_analysed(tmp_path):
    path = tmp_path / "Loop Take.wav"
    _write_wav(path, song(FOUR, bars=12))
    result = analyse_file(path)
    assert result.source.stem == "Loop Take"
    assert result.tempo == pytest.approx(120, rel=0.03)
    assert result.bar_count == 12


def test_a_missing_file_is_reported(tmp_path):
    with pytest.raises(DecodeError, match="no such file"):
        load_audio(tmp_path / "absent.wav")


def test_a_file_that_is_not_audio_is_reported(tmp_path):
    path = tmp_path / "notes.txt"
    path.write_text("this is not audio")
    with pytest.raises(DecodeError, match="could not decode"):
        load_audio(path)


def test_decoding_resamples_to_the_analysis_rate(tmp_path):
    path = tmp_path / "take.wav"
    _write_wav(path, song(FOUR, bars=4))
    buffer = load_audio(path, sample_rate=SR)
    assert buffer.sample_rate == SR
    assert buffer.duration == pytest.approx(8.0, abs=0.2)
    assert buffer.samples.ndim == 1


def test_leading_and_trailing_silence_is_trimmed():
    audio = song(FOUR, bars=4)
    padded = np.concatenate([np.zeros(SR * 3, dtype=np.float32), audio,
                             np.zeros(SR * 3, dtype=np.float32)])
    trimmed = trim_silence(AudioBuffer(padded, SR))
    assert trimmed.duration == pytest.approx(len(audio) / SR, abs=0.3)


def test_trimming_silence_only():
    buffer = AudioBuffer(np.zeros(SR, dtype=np.float32), SR)
    assert trim_silence(buffer).duration == pytest.approx(1.0)


def _write_wav(path: Path, samples: np.ndarray) -> None:
    peak = float(np.abs(samples).max()) or 1.0
    pcm = (samples / peak * 32000).astype("<i2")
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(SR)
        handle.writeframes(pcm.tobytes())


# -- which bars are worth a second listen ----------------------------------

def test_a_bar_carries_how_well_it_matched(analysis):
    assert all(bar.strength > 0 for bar in analysis.bars)
    assert all(0.0 <= bar.strength <= 1.0 for bar in analysis.bars)


def test_bar_strength_averages_the_spans_inside_it():
    from omacap.analysis.report import bar_strength

    spans = [span("C", 0.0, 1.0), span("C", 1.0, 2.0)]
    spans[0] = ChordSpan("C", 0.0, 1.0, 0, "", 0.8)
    spans[1] = ChordSpan("C", 1.0, 2.0, 0, "", 0.4)
    assert bar_strength(spans, 0.0, 2.0) == pytest.approx(0.6)


def test_a_bar_with_nothing_in_it_scores_zero():
    from omacap.analysis.report import bar_strength

    assert bar_strength([], 0.0, 2.0) == 0.0


def test_an_evenly_matched_song_marks_nothing():
    """The mark is relative, so a song the analysis handled well gets no marks."""
    from omacap.analysis.report import Analysis, Bar
    from omacap.analysis.key import Key
    from omacap.analysis.meter import Meter

    bars = [Bar(number=i + 1, start=i, end=i + 1, chords=["C"], strength=0.8)
            for i in range(16)]
    result = Analysis(source=Path("x.wav"), duration=16.0, tempo=120.0,
                      meter=Meter(4, 0, "4/4", 1.0), key=Key(0, "major", 0.9, 0.9),
                      bars=bars, chords=[], beat_count=64, tempo_confidence=1.0)
    assert result.uncertain_bars == set()


def test_the_weakest_bars_are_marked():
    from omacap.analysis.report import Analysis, Bar
    from omacap.analysis.key import Key
    from omacap.analysis.meter import Meter

    strengths = [0.8] * 14 + [0.2, 0.25]
    bars = [Bar(number=i + 1, start=i, end=i + 1, chords=["C"], strength=s)
            for i, s in enumerate(strengths)]
    result = Analysis(source=Path("x.wav"), duration=16.0, tempo=120.0,
                      meter=Meter(4, 0, "4/4", 1.0), key=Key(0, "major", 0.9, 0.9),
                      bars=bars, chords=[], beat_count=64, tempo_confidence=1.0)
    assert result.uncertain_bars == {15, 16}


def test_too_few_bars_to_judge_marks_nothing():
    from omacap.analysis.report import Analysis, Bar
    from omacap.analysis.key import Key
    from omacap.analysis.meter import Meter

    bars = [Bar(number=1, start=0, end=1, chords=["C"], strength=0.1)]
    result = Analysis(source=Path("x.wav"), duration=1.0, tempo=120.0,
                      meter=Meter(4, 0, "4/4", 1.0), key=Key(0, "major", 0.9, 0.9),
                      bars=bars, chords=[], beat_count=4, tempo_confidence=1.0)
    assert result.uncertain_bars == set()
