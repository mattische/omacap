"""Writing bars the Obsidian chordgrid plugin can read.

The plugin reads a bar as chords only if the whole bar matches its chord
grammar, and as rhythm otherwise, so an invalid bar is not ignored - it is drawn
as something else. These tests hold omacap's output to that grammar.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import pytest

from omacap.chordgrid import (
    CHORDS_ONLY,
    SEPARATOR,
    bar_source,
    is_chords,
    no_chord_bar,
    note_value,
    quarters_per_beat,
    rest,
)

NO_CHORD = "N.C."


@dataclass
class FakeMeter:
    beats_per_bar: int = 4
    name: str = "4/4"

    @property
    def is_compound(self) -> bool:
        return self.name.endswith("/8")


@dataclass
class FakeSpan:
    label: str
    start: float
    end: float


@dataclass
class FakeBar:
    number: int = 1
    start: float = 0.0
    end: float = 4.0
    chords: list = field(default_factory=list)

    @property
    def duration(self) -> float:
        return self.end - self.start


def spans_from_beats(labels: list[str], bar: FakeBar, beats: int):
    """One span per beat, so a bar can be described beat by beat in a test."""
    step = bar.duration / beats
    return [FakeSpan(label, bar.start + i * step, bar.start + (i + 1) * step)
            for i, label in enumerate(labels)]


def written(labels: list[str], meter=None, chords=None) -> str:
    """Write a bar described one label per beat."""
    meter = meter or FakeMeter()
    bar = FakeBar(chords=chords if chords is not None
                  else list(dict.fromkeys(labels)))
    return bar_source(bar, spans_from_beats(labels, bar, meter.beats_per_bar),
                      meter, NO_CHORD)


# -- the grammar ----------------------------------------------------------

@pytest.mark.parametrize("text", [
    "C", "Am", "F#m", "Bb7", "Gmaj7", "Csus4", "Dadd9", "C/E", "Ebm7",
    "C / G", "Em / G / Am / D",
])
def test_the_plugin_reads_these_as_chords(text):
    assert is_chords(text)


@pytest.mark.parametrize("text", [
    "N.C.", "C?", "Em?", "C G", "C/", "H", "", "-1", "C[4 4]", "%",
])
def test_the_plugin_does_not_read_these_as_chords(text):
    # Each of these would be parsed as rhythm instead, so omacap must not write
    # any of them where a chord belongs.
    assert not is_chords(text)


def test_two_chords_in_a_bar_need_spaces_round_the_slash():
    # "C/E" is one chord with a bass note; "C / E" is two chords.
    assert SEPARATOR == " / "
    assert is_chords("C/E") and is_chords("C / E")


# -- note values ----------------------------------------------------------

@pytest.mark.parametrize("quarters,value", [
    (4.0, "1"), (3.0, "2."), (2.0, "2"), (1.5, "4."), (1.0, "4"), (0.5, "8"),
])
def test_note_values_are_the_plugin_s(quarters, value):
    assert note_value(quarters) == value


def test_a_length_with_no_notation_is_refused():
    assert note_value(3.5) is None
    assert rest(3.5) is None


def test_a_rest_is_a_note_value_with_a_minus():
    assert rest(4.0) == "-1"


def test_a_compound_metre_counts_in_eighths():
    assert quarters_per_beat(FakeMeter(6, "6/8")) == 0.5
    assert quarters_per_beat(FakeMeter(4, "4/4")) == 1.0


# -- whole bars -----------------------------------------------------------

def test_one_chord_is_written_alone():
    assert written(["C", "C", "C", "C"]) == "C"


def test_an_even_split_is_written_as_chords():
    assert written(["C", "C", "G", "G"]) == "C / G"


def test_four_chords_in_four_four_are_written_as_chords():
    assert written(["C", "Em", "D", "G"],
                   chords=["C", "Em", "D", "G"]) == "C / Em / D / G"


def test_an_uneven_split_is_written_as_rhythm():
    # Three beats of C and one of G is not "C / G", which would be drawn as two
    # against two.
    assert written(["C", "C", "C", "G"]) == "C[2.] G[4]"


def test_a_chord_that_comes_back_gets_its_own_run():
    assert written(["Dm", "Dm", "Am", "Dm"],
                   chords=["Dm", "Am"]) == "Dm[2] Am[4] Dm[4]"


def test_three_beats_in_three_four_cannot_split_evenly_in_two():
    meter = FakeMeter(3, "3/4")
    assert written(["Am", "D", "D"], meter) == "Am[4] D[2]"


def test_a_bar_with_no_chord_is_written_as_rests():
    assert written([NO_CHORD] * 4) == "-1"
    assert no_chord_bar(FakeMeter(3, "3/4")) == "-2."


def test_no_chord_is_dropped_when_a_real_chord_shares_the_bar():
    assert written(["D", "D", "D", NO_CHORD], chords=["D", NO_CHORD]) == "D"


def test_a_bar_is_never_written_as_something_the_plugin_cannot_read():
    rhythm = re.compile(r"^(?:[A-G][^\s\[]*\[[0-9]+\.?\](?: |$))+$")
    rests = re.compile(r"^-[0-9]+\.?(?: -[0-9]+\.?)*$")
    for labels in [
        ["C", "C", "C", "C"], ["C", "C", "G", "G"], ["C", "C", "C", "G"],
        ["C", "G", "C", "G"], [NO_CHORD] * 4, ["C", NO_CHORD, NO_CHORD, "G"],
        ["C", "Em", "D", "G"],
    ]:
        text = written(labels)
        assert (CHORDS_ONLY.match(text) or rhythm.match(text)
                or rests.match(text)), (labels, text)


# -- against the real analysis -------------------------------------------

@pytest.fixture(scope="module")
def analysis_song():
    from pathlib import Path

    from omacap.analysis.audio import AudioBuffer
    from omacap.analysis.report import analyse_buffer
    from synth import SR, song

    progression = [(0, ""), (7, ""), (9, "m"), (5, "")]
    return analyse_buffer(AudioBuffer(song(progression, bars=16), SR),
                          Path("Grid.wav"))


def test_every_bar_of_a_real_song_is_readable(analysis_song):
    """The property that matters: nothing omacap writes falls through."""
    rhythm = re.compile(r"^(?:[A-G][^\s\[]*\[[0-9]+\.?\](?: |$))+$")
    rests = re.compile(r"^-[0-9]+\.?(?: -[0-9]+\.?)*$")
    analysis = analysis_song
    for bar in analysis.bars:
        text = bar_source(bar, analysis.chords, analysis.meter)
        assert (CHORDS_ONLY.match(text) or rhythm.match(text)
                or rests.match(text)), (bar.number, text)


def test_rhythm_in_a_bar_adds_up_to_the_bar(analysis_song):
    """A bar whose note values do not fill it is a bar the plugin draws wrong."""
    analysis = analysis_song
    lengths = {"1": 4.0, "2.": 3.0, "2": 2.0, "4.": 1.5, "4": 1.0,
               "8.": 0.75, "8": 0.5}
    per_bar = analysis.meter.beats_per_bar * quarters_per_beat(analysis.meter)
    for bar in analysis.bars:
        text = bar_source(bar, analysis.chords, analysis.meter)
        values = re.findall(r"\[([0-9]+\.?)\]", text)
        if not values:
            continue
        assert sum(lengths[v] for v in values) == pytest.approx(per_bar), \
            (bar.number, text)
