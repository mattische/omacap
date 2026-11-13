"""The play-along page: the chords bar by bar, beside the recording."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from omacap.analysis.audio import AudioBuffer
from omacap.analysis.report import analyse_buffer
from omacap.player import beats_text, notes_text, player_data, render_player, write_player

from synth import SR, song


@dataclass
class Meter:
    beats_per_bar: int = 4
    name: str = "4/4"

    @property
    def is_compound(self) -> bool:
        return False


@dataclass
class Span:
    label: str
    start: float
    end: float


@dataclass
class Bar:
    chords: list = field(default_factory=list)
    start: float = 0.0
    end: float = 4.0

    @property
    def duration(self) -> float:
        return self.end - self.start


def spans(*beats: str) -> list[Span]:
    return [Span(label, i, i + 1) for i, label in enumerate(beats)]


def test_a_chord_held_all_bar_is_written_once():
    bar = Bar(["C"])
    assert beats_text(bar, spans("C", "C", "C", "C"), Meter()) == "C"
    assert notes_text(bar, spans("C", "C", "C", "C"), Meter()) == "C"


def test_a_change_inside_the_bar_shows_where_it_falls():
    bar = Bar(["D", "G"])
    played = spans("D", "D", "D", "G")
    assert beats_text(bar, played, Meter()) == "D · · G"
    assert notes_text(bar, played, Meter()) == "D[2.] G[4]"


def test_an_even_split_reads_the_same_both_ways():
    bar = Bar(["C", "G"])
    played = spans("C", "C", "G", "G")
    assert beats_text(bar, played, Meter()) == "C · G ·"
    assert notes_text(bar, played, Meter()) == "C / G"


def test_a_bar_with_no_chord_says_so_rather_than_writing_rests():
    bar = Bar(["N.C."])
    assert beats_text(bar, spans(*["N.C."] * 4), Meter()) == "N.C."
    assert notes_text(bar, spans(*["N.C."] * 4), Meter()) == "N.C."


def test_the_page_carries_every_bar_and_plays_the_recording_by_name(tmp_path):
    analysis = analyse_buffer(AudioBuffer(song([(0, ""), (7, ""), (9, "m"), (5, "")], bars=16), SR),
                              tmp_path / "Take.wav")
    data = player_data(analysis, "Take.wav")
    assert [b["n"] for b in data["bars"]] == [b.number for b in analysis.bars]
    assert data["audio"] == "Take.wav" and data["metre"] == "4/4"
    # The progression repeats, so there is at least one section to loop.
    assert data["sections"] and all(s["first"] <= s["last"] for s in data["sections"])

    page = write_player(analysis, tmp_path / "Take.wav")
    assert page == tmp_path / "Take.html"
    embedded = re.search(r"let D=(.*?);const au=", page.read_text(encoding="utf-8"), re.S)
    assert json.loads(embedded.group(1)) == json.loads(json.dumps(data))


def test_a_name_cannot_close_the_script_early(tmp_path):
    analysis = analyse_buffer(AudioBuffer(song([(0, "")], bars=4), SR), Path("a</script>b.wav"))
    html = render_player(analysis, "a</script>b.wav")
    assert html.count("</script>") == 1
