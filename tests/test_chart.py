"""Chart rendering."""

from __future__ import annotations

from pathlib import Path

import pytest

from omacap.analysis.audio import AudioBuffer
from omacap.analysis.report import analyse_buffer
from omacap.chart import (
    CHART_FORMATS,
    chart_lines,
    confidence_word,
    default_chart_path,
    format_clock,
    get_chart_format,
    render,
    render_markdown,
    render_text,
    summary_rows,
    write_chart,
)

from synth import SR, song

FOUR = [(0, ""), (7, ""), (9, "m"), (5, "")]


@pytest.fixture(scope="module")
def analysis():
    return analyse_buffer(AudioBuffer(song(FOUR, bars=16), SR), Path("My Song.wav"))


# -- formats --------------------------------------------------------------

@pytest.mark.parametrize(
    "given,expected",
    [("md", "md"), ("MD", "md"), (".md", "md"), ("markdown", "md"),
     ("txt", "txt"), (".txt", "txt"), ("text", "txt")],
)
def test_chart_format_lookup(given, expected):
    assert get_chart_format(given) == expected


def test_an_unknown_chart_format_is_rejected():
    with pytest.raises(ValueError, match="unknown chart format"):
        get_chart_format("pdf")


def test_the_chart_sits_beside_the_recording():
    assert default_chart_path(Path("/tmp/take.mp3")) == Path("/tmp/take.md")
    assert default_chart_path(Path("/tmp/take.mp3"), "txt") == Path("/tmp/take.txt")


@pytest.mark.parametrize(
    "seconds,expected",
    [(0, "0:00"), (5, "0:05"), (65, "1:05"), (600, "10:00"), (3725, "1:02:05")],
)
def test_clock_formatting(seconds, expected):
    assert format_clock(seconds) == expected


@pytest.mark.parametrize(
    "value,word", [(0.9, "high"), (0.75, "high"), (0.6, "medium"), (0.2, "low"), (0.0, "low")]
)
def test_confidence_wording(value, word):
    assert confidence_word(value) == word


# -- content --------------------------------------------------------------

def test_the_summary_covers_everything_that_was_asked_for(analysis):
    labels = [label for label, _ in summary_rows(analysis)]
    assert labels[:5] == ["Key", "Tempo", "Time signature", "Bars", "Length"]


def test_the_grid_has_one_line_per_four_bars(analysis):
    lines = chart_lines(analysis, bars_per_line=4)
    assert len(lines) == 4
    assert lines[0].startswith(" 1 |")
    assert lines[1].startswith(" 5 |")


def test_bars_per_line_is_configurable(analysis):
    assert len(chart_lines(analysis, bars_per_line=8)) == 2
    assert len(chart_lines(analysis, bars_per_line=2)) == 8


def test_grid_cells_line_up(analysis):
    lines = chart_lines(analysis, bars_per_line=4)
    assert len({len(line) for line in lines}) == 1


def test_every_bar_appears_in_the_grid(analysis):
    text = "\n".join(chart_lines(analysis))
    for bar in analysis.bars:
        assert bar.label in text


def test_a_song_with_no_bars_says_so():
    class Empty:
        bars: list = []

    assert "no bars" in chart_lines(Empty())[0]


# -- markdown -------------------------------------------------------------

def test_markdown_starts_with_the_song_title(analysis):
    assert render_markdown(analysis).splitlines()[0] == "# My Song"


def test_markdown_puts_the_facts_in_a_table(analysis):
    text = render_markdown(analysis)
    assert "| **Key** | C major" in text
    assert "| **Tempo** | 120 BPM |" in text
    assert "| **Time signature** | 4/4 |" in text
    assert "| **Bars** | 16 |" in text


def test_markdown_keeps_the_grid_in_a_code_block(analysis):
    text = render_markdown(analysis)
    assert text.count("```") == 2
    grid = text.split("```")[1]
    assert "C" in grid and "Am" in grid


def test_markdown_says_the_result_is_an_estimate(analysis):
    assert "estimate" in render_markdown(analysis)


# -- plain text -----------------------------------------------------------

def test_text_has_no_markdown_syntax(analysis):
    text = render_text(analysis)
    assert "```" not in text
    assert "**" not in text
    assert "|" in text            # the grid still uses bar lines


def test_text_starts_with_an_underlined_title(analysis):
    lines = render_text(analysis).splitlines()
    assert lines[0] == "My Song"
    assert lines[1] == "=" * len("My Song")


def test_text_lists_the_facts(analysis):
    text = render_text(analysis)
    assert "Tempo           120 BPM" in text
    assert "Time signature  4/4" in text


# -- dispatch and writing -------------------------------------------------

@pytest.mark.parametrize("chart_format", CHART_FORMATS)
def test_render_dispatches_on_format(analysis, chart_format):
    assert render(analysis, chart_format) == (
        render_markdown(analysis) if chart_format == "md" else render_text(analysis)
    )


def test_writing_creates_the_file_and_any_folders(analysis, tmp_path):
    target = tmp_path / "charts" / "song.md"
    written = write_chart(analysis, target)
    assert written == target
    assert target.read_text(encoding="utf-8").startswith("# My Song")


def test_writing_plain_text(analysis, tmp_path):
    target = tmp_path / "song.txt"
    write_chart(analysis, target, "txt")
    assert "```" not in target.read_text(encoding="utf-8")


# -- saying what was a close call -----------------------------------------

class _Stub:
    """Enough of an Analysis to render the summary."""

    def __init__(self, key_conf=1.0, meter_conf=1.0):
        from omacap.analysis.key import Key
        from omacap.analysis.meter import Meter

        self.key = Key(7, "major", 0.9, key_conf)
        self.meter = Meter(4, 0, "4/4", meter_conf)
        self.tempo = 120.0
        self.duration = 60.0
        self.bars = []
        self.bar_count = 0
        self.tempo_confidence = 1.0
        self.source = Path("take.wav")
        self.chord_vocabulary = []


def test_a_confident_key_is_stated_plainly():
    rows = dict(summary_rows(_Stub(key_conf=1.0)))
    assert rows["Key"] == "G major (1 sharp)"
    assert "relative" not in rows["Key"]


def test_an_uncertain_key_names_its_relative():
    """A key and its relative share every note, so naming one alone hides a coin flip."""
    rows = dict(summary_rows(_Stub(key_conf=0.5)))
    assert rows["Key"].startswith("G major (1 sharp)")
    assert "E minor" in rows["Key"]


def test_a_confident_time_signature_is_stated_plainly():
    rows = dict(summary_rows(_Stub(meter_conf=1.0)))
    assert rows["Time signature"] == "4/4"


def test_an_uncertain_time_signature_says_the_bars_may_be_wrong():
    rows = dict(summary_rows(_Stub(meter_conf=0.1)))
    assert rows["Time signature"].startswith("4/4")
    assert "grouped wrongly" in rows["Time signature"]


def test_the_threshold_is_where_the_wording_of_confidence_changes():
    from omacap.chart import CERTAIN, confidence_word

    assert confidence_word(CERTAIN) == "high"
    assert confidence_word(CERTAIN - 0.01) != "high"
