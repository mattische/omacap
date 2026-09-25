"""Turning an analysis into a readable chord chart."""

from __future__ import annotations

from pathlib import Path

#: Bars per line. Four is how lead sheets are normally laid out.
BARS_PER_LINE = 4

CHART_FORMATS = ("md", "txt", "chordgrid")
DEFAULT_CHART_FORMAT = "md"

CHART_EXTENSIONS = {"md": ".md", "txt": ".txt", "chordgrid": ".md"}


def get_chart_format(name: str) -> str:
    """Normalise a chart format name, accepting a leading dot and some aliases."""
    key = name.strip().lower().lstrip(".")
    if key in ("markdown", "mkd"):
        key = "md"
    elif key == "text":
        key = "txt"
    elif key in ("grid", "obsidian"):
        key = "chordgrid"
    if key not in CHART_FORMATS:
        raise ValueError(
            f"unknown chart format {name!r}; choose one of: {', '.join(CHART_FORMATS)}"
        )
    return key


def format_clock(seconds: float) -> str:
    total = int(max(0.0, seconds))
    minutes, secs = divmod(total, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def confidence_word(value: float) -> str:
    """Plain-English confidence, so a reader knows how much to trust a line."""
    if value >= 0.75:
        return "high"
    if value >= 0.45:
        return "medium"
    return "low"


#: Appended to a bar the analysis was less sure of.
UNCERTAIN_MARK = "?"


def bar_text(bar, uncertain: set[int]) -> str:
    """A bar's chords, marked when the match was weaker than the song's usual."""
    return bar.label + (UNCERTAIN_MARK if bar.number in uncertain else "")


def _layout(analysis, bars_per_line: int, collapse: bool):
    """Group the bars into the rows a chart is written in.

    Each row is (bar number or None, the bars on it, the phrase letter, how many
    times to play it, the repeat mark). The letter and the count are passed on
    raw rather than formatted, because the plain chart and the chordgrid write
    them differently. A long phrase spans several rows, so the repeat marks open
    on the first and close on the last.
    """
    from .analysis.structure import find_phrases

    bars = analysis.bars
    flat = [
        (bars[i].number, bars[i: i + bars_per_line], "", 0, "")
        for i in range(0, len(bars), bars_per_line)
    ]
    if not collapse:
        return flat

    rows = []
    loose: list = []

    def flush():
        for i in range(0, len(loose), bars_per_line):
            chunk = loose[i: i + bars_per_line]
            rows.append((chunk[0].number, chunk, "", 0, ""))
        loose.clear()

    for phrase in find_phrases(bars):
        if not phrase.repeated:
            loose.extend(phrase.bars)
            continue
        flush()
        pieces = [
            phrase.bars[i: i + bars_per_line]
            for i in range(0, phrase.length, bars_per_line)
        ]
        for index, chunk in enumerate(pieces):
            first = index == 0
            last = index == len(pieces) - 1
            mark = ("both" if first and last
                    else "open" if first else "close" if last else "mid")
            rows.append((chunk[0].number if first else None, chunk,
                         phrase.letter if last else "",
                         phrase.repeats if last else 0, mark))
    flush()
    # A phrase boundary breaks the line, so a short phrase can cost more rows
    # than writing it out twice would. When that happens, write it out.
    return rows if len(rows) < len(flat) else flat


def _form_note(analysis, rows: list) -> list[str]:
    """One line naming the song's form, when the chart is written that way."""
    if not any(repeats for _, _, _, repeats, _ in rows):
        return []
    from .analysis.structure import find_phrases, form

    shape = form(find_phrases(analysis.bars))
    if not shape:
        return []
    return [f"Form: {shape}. A repeated phrase is written once, "
            f"with the number of times to play it."]


def chart_lines(analysis, bars_per_line: int = BARS_PER_LINE,
                collapse: bool = True) -> list[str]:
    """The chart grid: bar numbers down the left, chords across."""
    bars = analysis.bars
    if not bars:
        return ["(no bars were detected)"]

    uncertain = getattr(analysis, "uncertain_bars", set())
    texts = {bar.number: bar_text(bar, uncertain) for bar in bars}
    width = max(max((len(t) for t in texts.values()), default=4), 6)
    number_width = len(str(len(bars)))
    rows = _layout(analysis, bars_per_line, collapse)
    notes = [f"{letter}\u00d7{repeats}" if repeats else ""
             for _, _, letter, repeats, _ in rows]
    note_width = max((len(note) for note in notes), default=0)

    # A phrase boundary forces a line break, so some lines hold fewer bars than
    # others. They are padded with space rather than with empty cells, which would
    # read as bars that are not there.
    cell_width = width + 3
    full_row = cell_width * bars_per_line

    lines = []
    for (number, row, _, _, _), note in zip(rows, notes):
        cells = "".join(f" {texts[bar.number].ljust(width)} |" for bar in row)
        label = "" if number is None else str(number)
        tail = f"  {note.ljust(note_width)}" if note_width else ""
        lines.append(f"{label.rjust(number_width)} |{cells.ljust(full_row)}{tail}")
    return lines


def chordgrid_lines(analysis, bars_per_line: int = BARS_PER_LINE,
                    collapse: bool = True) -> list[str]:
    """The chart as a chordgrid block, which Obsidian renders as a chart.

    The format a working musician's notes are already in: a time signature, then
    bars between pipes. Writing this means omacap's output can sit beside charts
    written by hand instead of having to be copied across.
    """
    bars = analysis.bars
    if not bars:
        return ["```chordgrid", "4/4", "```"]

    uncertain = getattr(analysis, "uncertain_bars", set())
    lines = ["```chordgrid", "measure-num", "", analysis.meter.name, ""]
    for _, row, _, repeats, mark in _layout(analysis, bars_per_line, collapse):
        cells = " | ".join(bar_text(bar, uncertain) for bar in row)
        # Repeat marks are how a chart says this twice. The count goes on the
        # closing mark as "x3", with no space: that is the syntax the chordgrid
        # plugin parses, and anything else loses the count silently.
        left = "||:" if mark in ("open", "both") else "|"
        right = ":||" if mark in ("close", "both") else "|"
        if repeats > 1 and right == ":||":
            right += f"x{repeats}"
        lines.append(f"{left} {cells} {right}")
    lines.append("```")
    return lines


#: Below this, a detection is reported as a choice rather than a fact.
CERTAIN = 0.75


def summary_rows(analysis) -> list[tuple[str, str]]:
    """The facts that head the chart, as label/value pairs.

    Where a judgement was a close call, it is written as one. A chart that hides
    its uncertainty is worse than one that admits it: the reader cannot tell which
    lines to check.
    """
    key = analysis.key
    meter = analysis.meter

    key_text = f"{key.name} ({key.signature})"
    if key.confidence < CERTAIN:
        key_text += f" \u2014 or {key.relative.name}, its relative"

    meter_text = meter.name
    if meter.confidence < CERTAIN:
        meter_text += " \u2014 a close call, so the bars may be grouped wrongly"

    rows = [
        ("Key", key_text),
        ("Tempo", f"{analysis.tempo:.0f} BPM"),
        ("Time signature", meter_text),
        ("Bars", str(analysis.bar_count)),
        ("Length", format_clock(analysis.duration)),
        ("Chords used", ", ".join(analysis.chord_vocabulary) or "none"),
        (
            "Confidence",
            f"key {confidence_word(key.confidence)}, "
            f"tempo {confidence_word(analysis.tempo_confidence)}, "
            f"time signature {confidence_word(meter.confidence)}",
        ),
    ]
    feel = getattr(analysis, "rhythm", None)
    if feel is not None and feel.feel != "unclear":
        rows.insert(3, ("Feel", feel.description))
    return rows


FOOTER = (
    "Detected automatically by omacap. Chord, key and tempo detection is an "
    "estimate, not a transcription - check anything marked low confidence."
)


def render_markdown(analysis, bars_per_line: int = BARS_PER_LINE,
                    grid: bool = False, collapse: bool = True) -> str:
    """A Markdown chart, with the grid kept in a code block so it stays aligned."""
    rows = summary_rows(analysis)
    lines = [f"# {analysis.source.stem}", "", "| | |", "| --- | --- |"]
    for label, value in rows:
        lines.append(f"| **{label}** | {value} |")
    lines += ["", "## Chart", ""]
    lines.append(f"Bars read left to right, {bars_per_line} per line.")
    lines += _form_note(analysis, _layout(analysis, bars_per_line, collapse))
    if getattr(analysis, "uncertain_bars", set()):
        lines.append(
            f"A `{UNCERTAIN_MARK}` marks a bar the audio matched less well than "
            f"the rest of the song - worth a second listen."
        )
    lines.append("")
    if grid:
        lines += chordgrid_lines(analysis, bars_per_line, collapse)
    else:
        lines += ["```"] + chart_lines(analysis, bars_per_line, collapse) + ["```"]
    lines += ["", "---", "", FOOTER, ""]
    return "\n".join(lines)


def render_text(analysis, bars_per_line: int = BARS_PER_LINE,
                collapse: bool = True) -> str:
    """A plain-text chart."""
    title = analysis.source.stem
    rows = summary_rows(analysis)
    label_width = max(len(label) for label, _ in rows)
    lines = [title, "=" * len(title), ""]
    for label, value in rows:
        lines.append(f"{label.ljust(label_width)}  {value}")
    lines += ["", "CHART", "-----",
              f"Bars read left to right, {bars_per_line} per line."]
    lines += _form_note(analysis, _layout(analysis, bars_per_line, collapse)) + [""]
    lines += chart_lines(analysis, bars_per_line, collapse)
    lines += ["", FOOTER, ""]
    return "\n".join(lines)


def render(analysis, chart_format: str = DEFAULT_CHART_FORMAT,
           bars_per_line: int = BARS_PER_LINE, collapse: bool = True) -> str:
    """Render in the requested format."""
    wanted = get_chart_format(chart_format)
    if wanted == "txt":
        return render_text(analysis, bars_per_line, collapse)
    return render_markdown(analysis, bars_per_line,
                           grid=wanted == "chordgrid", collapse=collapse)


def write_chart(
    analysis,
    path: Path,
    chart_format: str = DEFAULT_CHART_FORMAT,
    bars_per_line: int = BARS_PER_LINE,
    collapse: bool = True,
) -> Path:
    """Write the chart to ``path``."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render(analysis, chart_format, bars_per_line, collapse),
                    encoding="utf-8")
    return path


def default_chart_path(audio_path: Path, chart_format: str = DEFAULT_CHART_FORMAT) -> Path:
    """The chart file that sits beside a recording."""
    audio_path = Path(audio_path)
    return audio_path.with_suffix(CHART_EXTENSIONS[get_chart_format(chart_format)])
