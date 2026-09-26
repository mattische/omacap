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
    for wanted in ("Key", "Tempo", "Time signature", "Bars", "Length"):
        assert wanted in labels
    # Musical judgements first, then the counts.
    assert labels.index("Key") < labels.index("Bars")
    assert labels.index("Time signature") < labels.index("Length")


def test_the_grid_has_one_line_per_four_bars(analysis):
    lines = chart_lines(analysis, bars_per_line=4, collapse=False)
    assert len(lines) == 4
    assert lines[0].startswith(" 1 |")
    assert lines[1].startswith(" 5 |")


def test_bars_per_line_is_configurable(analysis):
    assert len(chart_lines(analysis, bars_per_line=8, collapse=False)) == 2
    assert len(chart_lines(analysis, bars_per_line=2, collapse=False)) == 8


def test_grid_cells_line_up(analysis):
    lines = chart_lines(analysis, bars_per_line=4, collapse=False)
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

def test_markdown_heads_the_document_with_the_song_title(analysis):
    # The frontmatter comes first, because that is where Obsidian reads it; the
    # title is the first thing a reader sees.
    body = render_markdown(analysis).split("---\n", 2)[-1].lstrip("\n")
    assert body.splitlines()[0] == "# My Song"
    assert render_markdown(analysis, frontmatter=False).splitlines()[0] == "# My Song"


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
    expected = {
        "md": lambda: render_markdown(analysis),
        "txt": lambda: render_text(analysis),
        "chordgrid": lambda: render_markdown(analysis, grid=True),
    }[chart_format]
    assert render(analysis, chart_format) == expected()


def test_writing_creates_the_file_and_any_folders(analysis, tmp_path):
    target = tmp_path / "charts" / "song.md"
    written = write_chart(analysis, target)
    assert written == target
    assert "# My Song" in target.read_text(encoding="utf-8")


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


# -- marking the bars worth checking --------------------------------------

def test_an_uncertain_bar_is_marked(analysis):
    from omacap.chart import UNCERTAIN_MARK, bar_text

    bar = analysis.bars[0]
    assert bar_text(bar, set()) == bar.label
    assert bar_text(bar, {bar.number}) == bar.label + UNCERTAIN_MARK


def test_the_grid_marks_them_and_says_what_it_means(analysis, monkeypatch):
    marked = {bar.number for bar in analysis.bars[:2]}
    monkeypatch.setattr(type(analysis), "uncertain_bars", property(lambda self: marked))
    grid = "\n".join(chart_lines(analysis))
    assert "?" in grid
    assert "second listen" in render_markdown(analysis)


def test_nothing_is_said_when_nothing_is_marked(analysis, monkeypatch):
    monkeypatch.setattr(type(analysis), "uncertain_bars", property(lambda self: set()))
    assert "second listen" not in render_markdown(analysis)
    assert "?" not in "\n".join(chart_lines(analysis))


def test_the_columns_still_line_up_with_marks(analysis, monkeypatch):
    marked = {bar.number for bar in analysis.bars[::3]}
    monkeypatch.setattr(type(analysis), "uncertain_bars", property(lambda self: marked))
    lines = chart_lines(analysis)
    assert len({len(line) for line in lines}) == 1


# -- the chordgrid format --------------------------------------------------

@pytest.mark.parametrize("given", ["chordgrid", "grid", "obsidian", "OBSIDIAN"])
def test_the_chordgrid_aliases(given):
    assert get_chart_format(given) == "chordgrid"


def test_a_chordgrid_block_is_written(analysis):
    from omacap.chart import chordgrid_lines

    lines = chordgrid_lines(analysis)
    assert lines[0] == "```chordgrid"
    assert lines[-1] == "```"
    assert analysis.meter.name in lines


def test_chordgrid_bars_are_written_between_pipes(analysis):
    from omacap.chart import chordgrid_lines

    rows = [l for l in chordgrid_lines(analysis, collapse=False)
            if l.startswith("| ")]
    assert rows, "expected some bar rows"
    for row in rows:
        assert row.startswith("| ") and row.endswith(" |")
    assert rows[0].count("|") == 5          # four bars a line


def test_chordgrid_renders_through_the_dispatcher(analysis):
    text = render(analysis, "chordgrid")
    assert "```chordgrid" in text
    assert "\n# " in text                   # still a markdown document


def test_a_chordgrid_chart_is_written_as_markdown(analysis, tmp_path):
    assert default_chart_path(Path("/tmp/take.mp3"), "chordgrid") == Path("/tmp/take.md")
    target = write_chart(analysis, tmp_path / "chart.md", "chordgrid")
    assert "```chordgrid" in target.read_text(encoding="utf-8")


def test_a_song_with_no_bars_still_makes_a_block():
    from omacap.chart import chordgrid_lines

    class Empty:
        bars: list = []
        class meter:
            name = "4/4"

    lines = chordgrid_lines(Empty())
    assert lines[0] == "```chordgrid" and lines[-1] == "```"


# -- collapsing repeats ---------------------------------------------------

def test_a_repeated_phrase_is_written_once(analysis):
    # The fixture is one four-bar loop over and over, so it collapses to a
    # single row however long the song is.
    collapsed = chart_lines(analysis)
    assert len(collapsed) < len(chart_lines(analysis, collapse=False))


def test_the_collapsed_row_says_how_many_times(analysis):
    text = "\n".join(chart_lines(analysis))
    assert "×" in text


def test_collapsing_never_makes_a_chart_longer(analysis):
    assert len(chart_lines(analysis)) <= len(chart_lines(analysis, collapse=False))


def test_collapsed_lines_still_line_up(analysis):
    assert len({len(line) for line in chart_lines(analysis)}) == 1


def test_no_collapse_writes_every_bar(analysis):
    text = "\n".join(chart_lines(analysis, collapse=False))
    assert "×" not in text
    assert len(chart_lines(analysis, collapse=False)) == (len(analysis.bars) + 3) // 4


def test_a_collapsed_chordgrid_brackets_the_phrase_with_repeat_marks(analysis):
    from omacap.chart import chordgrid_lines

    rows = [l for l in chordgrid_lines(analysis) if "|" in l and "chordgrid" not in l]
    assert any(l.startswith("||:") for l in rows)
    assert any(":||" in l for l in rows)


def test_the_repeat_count_is_written_the_way_the_plugin_parses_it(analysis):
    r"""The chordgrid plugin reads a count with ``/^(:?\|\|)x(\d+)/``.

    Anchored, lowercase x, no space. A count written any other way is dropped
    silently and the chart renders as if the phrase were played once, so the
    syntax is pinned here rather than trusted.
    """
    import re

    from omacap.chart import chordgrid_lines

    plugin = re.compile(r"^(:?\|\|)x(\d+)")
    closes = [line.split()[-1] for line in chordgrid_lines(analysis)
              if ":||" in line]
    assert closes, "expected a collapsed phrase"
    counts = [plugin.match(close) for close in closes]
    assert all(counts), closes
    assert all(int(m.group(2)) > 1 for m in counts)


def test_a_chordgrid_never_writes_a_letter_inside_the_block(analysis):
    # The plugin has no syntax for a section label, so the letters stay in the
    # form line above the block.
    from omacap.chart import chordgrid_lines

    assert "\u00d7" not in "\n".join(chordgrid_lines(analysis))


def test_the_form_is_named_above_the_chart(analysis):
    assert "Form: A" in render_markdown(analysis)
    assert "Form: A" in render_text(analysis)


def test_no_collapse_leaves_the_form_out(analysis):
    assert "Form:" not in render(analysis, "md", collapse=False)


def test_no_chord_is_not_listed_as_a_chord_the_song_uses():
    from omacap.chart import _played_chords

    class Stub:
        chord_vocabulary = ["C", "N.C.", "Am"]

    assert _played_chords(Stub()) == ["C", "Am"]


@pytest.fixture(scope="module")
def two_part_song():
    """A song with two different repeated phrases, so it has two sections."""
    import numpy as np

    first = song([(0, ""), (7, ""), (9, "m"), (5, "")], bars=8)
    second = song([(4, "m"), (0, ""), (2, ""), (2, "")], bars=8)
    joined = np.concatenate([first, second, first, second])
    return analyse_buffer(AudioBuffer(joined, SR), Path("Two Parts.wav"))


def test_bars_per_line_is_not_claimed_once_each_section_has_its_own_grid(
        two_part_song, analysis):
    from omacap.chart import _group_rows, _layout

    sections = len(_group_rows(_layout(two_part_song, 4, True), 4))
    assert sections > 1, "expected the fixture to have several sections"
    assert "per line" not in render(two_part_song, "chordgrid")
    # A chart that is not split into sections still says how it is laid out.
    assert "per line" in render(two_part_song, "md")
    assert "per line" in render(two_part_song, "chordgrid", sections=False)


# -- frontmatter ----------------------------------------------------------

def test_a_markdown_chart_starts_with_frontmatter(analysis):
    text = render(analysis, "md")
    assert text.startswith("---\n")
    assert text.split("---")[1].strip().startswith("title:")


def test_frontmatter_carries_the_song_s_facts(analysis):
    from omacap.chart import frontmatter_lines

    fields = dict(line.split(": ", 1) for line in frontmatter_lines(analysis)
                  if ": " in line)
    assert fields["bars"] == str(analysis.bar_count)
    assert fields["tempo"] == str(round(analysis.tempo))
    assert analysis.key.name in fields["key"]
    assert analysis.meter.name in fields["time_signature"]
    assert fields["source"] == f'"{analysis.source.name}"'


def test_a_text_chart_has_no_frontmatter(analysis):
    assert not render(analysis, "txt").startswith("---")


def test_frontmatter_can_be_left_out(analysis):
    assert not render(analysis, "md", frontmatter=False).startswith("---")
    assert not render(analysis, "chordgrid", frontmatter=False).startswith("---")


def test_a_sharp_chord_cannot_comment_out_the_rest_of_the_line():
    """`C#` written bare in YAML loses everything after the hash."""
    from omacap.chart import yaml_list, yaml_scalar

    assert yaml_scalar("C#") == '"C#"'
    assert yaml_list(["C#", "Bb", "F#m7"]) == '["C#", "Bb", "F#m7"]'


def test_a_title_with_quotes_or_colons_survives():
    from omacap.chart import yaml_scalar

    assert yaml_scalar('He said "no": take 2') == '"He said \\"no\\": take 2"'
    assert yaml_scalar("4:30") == '"4:30"'
    assert yaml_scalar("back\\slash") == '"back\\\\slash"'


def test_numbers_are_written_as_numbers():
    from omacap.chart import yaml_scalar

    assert yaml_scalar(139) == "139"
    assert yaml_scalar(124.4) == "124.4"
    assert yaml_scalar(True) == "true"


def test_frontmatter_does_not_change_between_runs(analysis):
    """These files live in a synced vault; a timestamp would churn every one."""
    from omacap.chart import frontmatter_lines

    assert frontmatter_lines(analysis) == frontmatter_lines(analysis)


def test_the_frontmatter_block_is_closed(analysis):
    from omacap.chart import frontmatter_lines

    lines = frontmatter_lines(analysis)
    assert lines[0] == "---"
    assert lines.count("---") == 2
    assert lines[-2] == "---"


def test_the_frontmatter_parses_as_yaml(analysis):
    """Checked with a real parser where one is installed.

    omacap depends on nothing, so PyYAML is not required - but when it happens to
    be available there is no reason to trust a hand-rolled writer on faith.
    """
    yaml = pytest.importorskip("yaml")

    from omacap.chart import frontmatter_lines

    block = "\n".join(frontmatter_lines(analysis)[1:-2])
    fields = yaml.safe_load(block)
    assert isinstance(fields, dict)
    assert fields["bars"] == analysis.bar_count      # a number, not a string
    assert fields["title"] == analysis.source.stem
    assert isinstance(fields["chords"], list)


@pytest.mark.parametrize("value", [
    "C#", "Bb", 'He said "no": take 2', "4:30", "- leading dash",
    "&anchor *alias", "~", "", "#hash", "a\\backslash", "line\twith\ttabs",
    "En psalm till vänner", "100%", "{braces}", "[brackets]",
])
def test_any_value_round_trips_through_yaml(value):
    yaml = pytest.importorskip("yaml")

    from omacap.chart import yaml_scalar

    assert yaml.safe_load(f"field: {yaml_scalar(value)}")["field"] == value


def test_a_chord_list_round_trips_through_yaml():
    yaml = pytest.importorskip("yaml")

    from omacap.chart import yaml_list

    chords = ["C#", "Bb", "F#m7", "Ab", "D#dim", "N.C."]
    assert yaml.safe_load(f"chords: {yaml_list(chords)}")["chords"] == chords


def test_no_field_name_is_a_yaml_boolean(analysis):
    """`yes:` and `on:` are parsed as booleans by YAML 1.1, so no key may be one."""
    from omacap.chart import frontmatter_lines

    reserved = {"y", "yes", "n", "no", "true", "false", "on", "off", "null"}
    names = [line.split(":", 1)[0] for line in frontmatter_lines(analysis)
             if ":" in line]
    assert names, "expected some fields"
    assert not reserved & {name.lower() for name in names}


def test_the_default_format_is_the_one_obsidian_renders(analysis):
    """A chart nobody can read as a chart is the wrong thing to write by default."""
    from omacap.chart import CHART_FORMATS, DEFAULT_CHART_FORMAT

    assert DEFAULT_CHART_FORMAT == "chordgrid"
    assert CHART_FORMATS[0] == DEFAULT_CHART_FORMAT   # what the interface starts on
    assert "```chordgrid" in render(analysis)


def test_the_default_chart_is_still_a_markdown_file(tmp_path, analysis):
    from omacap.chart import default_chart_path, write_chart

    target = default_chart_path(tmp_path / "take.wav")
    assert target.suffix == ".md"
    written = write_chart(analysis, target)
    assert "```chordgrid" in written.read_text(encoding="utf-8")


def test_the_plain_grid_is_still_available(analysis):
    # -t md keeps the aligned grid with bar numbers down the left, for reading
    # outside Obsidian.
    text = render(analysis, "md")
    assert "```chordgrid" not in text
    assert "| C" in text


def test_only_the_frontmatter_uses_a_triple_dash(analysis):
    """Two `---` fences and a third for a rule reads as duplicated frontmatter."""
    lines = render(analysis, "md").splitlines()
    fences = [n for n, line in enumerate(lines) if line.strip() == "---"]
    assert len(fences) == 2, "exactly the frontmatter, opened and closed"
    assert fences[0] == 0, "and at the very top, or Obsidian will not read it"
    assert "***" in lines          # the rule above the footer instead
