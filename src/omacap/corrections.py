"""Chords a person corrected, and keeping them through every later analysis.

The analysis is an estimate and somebody listening is the authority, so a
correction always wins. Corrections live beside the recording in
``<name>.chords.json``, keyed by bar: they travel with the song, survive a
re-analysis, and are laid over whatever a later, better analysis finds - it fills
in the bars nobody touched.

A chart can also be corrected by hand, in Obsidian. Before a chart is rewritten,
the copy on disk is compared with what omacap last wrote there; if it differs it
was edited, and the edited bars are read back into corrections first. A chart that
was edited but cannot be read back is **not rewritten** - somebody's work is not
ours to discard, the same rule ``apply_update`` follows for a dirty checkout.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import replace
from pathlib import Path

from .analysis.chords import NO_CHORD, ChordSpan
from .analysis.features import FLAT_NAMES, PITCH_NAMES
from .chordgrid import CHORD, NOTE_VALUES, bar_source, quarters_per_beat

#: How a held beat is written on the play-along page: ``D · · G``.
HOLDS = frozenset({"·", "."})

_CHORD = re.compile(rf"^(?:{CHORD}|N\.C\.)$")
_NOTE = re.compile(rf"^({CHORD}|N\.C\.)\[(\d+\.?)\]$")
_REST = re.compile(r"^-(\d+\.?)$")
_QUARTERS = {token: length for length, token in NOTE_VALUES.items()}

_BLOCK = re.compile(r"^```chordgrid[^\n]*\n(.*?)^```", re.S | re.M)
_FIRST_BAR = re.compile(r"measure-num(?::\s*(\d+))?", re.I)
_SIGNATURE = re.compile(r"^\d+/\d+$")
_CLOSE = re.compile(r":\|\|(?:x(\d+))?\s*$")


class CorrectionError(ValueError):
    """A bar, or a chart, that cannot be read as chords."""


# -- one bar --------------------------------------------------------------

def parse_bar(text: str, meter, previous: list[str] | None = None) -> list[str]:
    """Which chord holds each beat of a bar written by a person.

    Takes every way a bar is written here: one chord (``C``), chords split evenly
    (``C / G``), a chord or a hold per beat (``D · · G``), note values as the
    chordgrid chart writes them (``D[2.] G[4]``), rests (``-1``) for no chord, and
    ``%`` for the bar before.
    """
    beats = max(1, meter.beats_per_bar)
    tokens = text.split()
    if not tokens:
        raise CorrectionError("the bar is empty")
    if tokens == ["%"]:
        if previous is None:
            raise CorrectionError("% repeats the bar before, and there is none")
        return list(previous)

    if any(token in HOLDS for token in tokens):
        if len(tokens) != beats:
            raise CorrectionError(
                f"write one chord or · per beat: {beats}, not {len(tokens)}")
        out: list[str] = []
        for token in tokens:
            if token in HOLDS:
                if not out:
                    raise CorrectionError("a bar cannot start with ·")
                out.append(out[-1])
            else:
                out.append(_chord(token))
        return out

    if any("[" in token or _REST.match(token) for token in tokens):
        per_beat = quarters_per_beat(meter)
        out = []
        for token in tokens:
            note, rest = _NOTE.match(token), _REST.match(token)
            if not (note or rest):
                raise CorrectionError(f"{token!r} is not a note value like C[2.] or -4")
            label, value = (note.group(1), note.group(2)) if note else (NO_CHORD, rest.group(1))
            if value not in _QUARTERS:
                raise CorrectionError(f"{value!r} is not a note value")
            count = _QUARTERS[value] / per_beat
            if abs(count - round(count)) > 1e-6 or round(count) < 1:
                raise CorrectionError(f"{token} is not a whole number of beats")
            out += [label] * round(count)
        if len(out) != beats:
            raise CorrectionError(
                f"those note values add up to {len(out)} beats; the bar has {beats}")
        return out

    chords = [_chord(token) for token in tokens if token != "/"]
    if beats % len(chords):
        raise CorrectionError(
            f"{len(chords)} chords do not split {beats} beats evenly; "
            f"write them per beat (C · G ·) or with note values")
    each = beats // len(chords)
    return [chord for chord in chords for _ in range(each)]


def _chord(token: str) -> str:
    if token.upper() in ("N.C.", "NC"):
        return NO_CHORD
    if not _CHORD.match(token):
        raise CorrectionError(f"{token!r} is not a chord")
    return token


def runs(beats: list[str]) -> list[tuple[str, int]]:
    out: list[list] = []
    for label in beats:
        if out and out[-1][0] == label:
            out[-1][1] += 1
        else:
            out.append([label, 1])
    return [(label, count) for label, count in out]


def written_beats(bar, spans, meter) -> list[str]:
    """The beats of a bar as the chart writes it - which is what a reader sees."""
    return parse_bar(bar_source(bar, spans, meter), meter)


# -- a whole chordgrid chart ---------------------------------------------

def read_chart(text: str) -> tuple[str | None, dict[int, str]]:
    """The time signature and every bar's text in a chordgrid chart, by bar number.

    Reads what ``chart.chordgrid_lines`` writes: ``measure-num: N`` for where a
    block starts, and ``||: ... :||x3`` around a phrase played several times,
    which is expanded so every bar has its own number again.
    """
    signature = None
    bars: dict[int, str] = {}
    for block in _BLOCK.findall(text):
        number = 1
        pending: list[str] | None = None
        for line in (raw.strip() for raw in block.splitlines()):
            if not line:
                continue
            if _SIGNATURE.match(line):
                if signature and line != signature:
                    raise CorrectionError(f"the chart changes time signature to {line}")
                signature = line
                continue
            if not line.startswith("|"):
                start = _FIRST_BAR.search(line)
                if start and start.group(1):
                    number = int(start.group(1))
                continue
            opens = line.startswith("||:")
            close = _CLOSE.search(line)
            inner = line[3 if opens else 1: close.start() if close else len(line)]
            cells = [cell.strip() for cell in inner.split("|") if cell.strip()]
            if opens:
                if pending is not None:
                    raise CorrectionError("a repeat opens inside another")
                pending = []
            if pending is not None:
                pending += cells
            else:
                for cell in cells:
                    bars[number] = cell
                    number += 1
            if close:
                if pending is None:
                    raise CorrectionError("a repeat closes that never opened")
                for _ in range(int(close.group(1) or 2)):
                    for cell in pending:
                        bars[number] = cell
                        number += 1
                pending = None
        if pending is not None:
            raise CorrectionError("a repeat is never closed")
    return signature, bars


# -- the file beside the recording ---------------------------------------

def corrections_path(audio: Path) -> Path:
    return Path(audio).with_suffix(".chords.json")


def load(audio: Path) -> dict[str, dict]:
    path = corrections_path(audio)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return {str(k): v for k, v in data.get("bars", {}).items()
                if isinstance(v, dict) and isinstance(v.get("beats"), list)}
    except (OSError, ValueError, AttributeError) as exc:
        # Refusing beats guessing: an unreadable file may still hold someone's work.
        raise CorrectionError(f"{path.name} could not be read ({exc})") from exc


def save(audio: Path, corrections: dict[str, dict]) -> Path | None:
    path = corrections_path(audio)
    if not corrections:
        if path.exists():
            path.unlink()
        return None
    ordered = dict(sorted(corrections.items(), key=lambda kv: int(kv[0])))
    text = json.dumps({"omacap": "chord corrections", "bars": ordered},
                      ensure_ascii=False, indent=1)
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(text + "\n", encoding="utf-8")
    temp.replace(path)
    return path


def correction(bar, beats: list[str]) -> dict:
    return {"beats": beats, "start": round(bar.start, 3)}


# -- laying corrections over an analysis ---------------------------------

def _root(label: str) -> tuple[int, str]:
    if label == NO_CHORD:
        return -1, NO_CHORD
    name = label[:2] if len(label) > 1 and label[1] in "#b" else label[:1]
    names = PITCH_NAMES if name in PITCH_NAMES else FLAT_NAMES
    root = names.index(name) if name in names else -1
    return root, label[len(name):].split("/")[0]


def _find_bar(bars, number: int, start: float | None):
    """The bar a correction was made on, even if a newer analysis renumbered it."""
    by_number = next((b for b in bars if b.number == number), None)
    if start is None:
        return by_number
    near = lambda b: abs(b.start - start) <= b.duration / 2  # noqa: E731
    if by_number is not None and near(by_number):
        return by_number
    return min((b for b in bars if near(b)), key=lambda b: abs(b.start - start),
               default=None)


def apply(analysis, corrections: dict[str, dict]):
    """The analysis with the corrected bars written in, and the rest untouched."""
    if not corrections:
        return analysis
    meter = analysis.meter
    bars = [replace(bar) for bar in analysis.bars]
    spans = list(analysis.chords)
    for key, fix in corrections.items():
        bar = _find_bar(bars, int(key), fix.get("start"))
        beats = fix.get("beats") or []
        if bar is None or len(beats) != meter.beats_per_bar:
            continue
        kept = []
        for span in spans:
            if span.end <= bar.start or span.start >= bar.end:
                kept.append(span)
                continue
            if span.start < bar.start:
                kept.append(replace(span, end=bar.start))
            if span.end > bar.end:
                kept.append(replace(span, start=bar.end))
        step = bar.duration / len(beats)
        at = bar.start
        for label, count in runs(beats):
            root, quality = _root(label)
            kept.append(ChordSpan(label=label, start=at, end=at + count * step,
                                  root=root, quality=quality, strength=1.0))
            at += count * step
        spans = sorted(kept, key=lambda s: s.start)
        bar.chords = [label for label, _ in runs(beats)]
        bar.corrected = True
    return replace(analysis, bars=bars, chords=spans)


# -- rewriting a chart without losing a hand edit -------------------------

def _record_path() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or (Path.home() / ".cache")
    return Path(base) / "omacap" / "charts.json"


def _records() -> dict:
    try:
        return json.loads(_record_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _remember(chart: Path, text: str) -> None:
    records = _records()
    records[str(Path(chart).resolve())] = _digest(text)
    try:
        path = _record_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(records, indent=0), encoding="utf-8")
    except OSError:
        pass   # only costs re-reading the chart next time, which is safe


def edited_bars(analysis, text: str, render) -> dict[str, dict]:
    """Bars where a hand-edited chordgrid chart says something omacap would not write.

    Compared with the chart as it would be written now, cell by cell, rather than
    with the analysis bar by bar: a repeated phrase is written once, so a chart
    can rightly show a bar the way its first repetition sounded.
    """
    signature, cells = read_chart(text)
    meter = analysis.meter
    if signature and signature != meter.name:
        raise CorrectionError(f"the chart is in {signature}, the analysis in {meter.name}")
    _, expected = read_chart(render(analysis))
    if set(cells) != set(expected):
        raise CorrectionError(
            f"the chart has {len(cells)} bars where the recording has {len(expected)}")
    bars = {bar.number: bar for bar in analysis.bars}
    edits = {}
    had = was = None
    for number in sorted(cells):
        beats = parse_bar(cells[number], meter, had)
        wanted = parse_bar(expected[number], meter, was)
        had, was = beats, wanted
        if beats != wanted and number in bars:
            edits[str(number)] = correction(bars[number], beats)
    return edits


def prepare(analysis, audio: Path, target: Path, render, chordgrid: bool):
    """The analysis to chart, with every correction in it. Returns (analysis, write, notes).

    ``render`` turns an analysis into the chart's text, with the same options it
    will be written with. Reads hand edits in ``target`` back into corrections first. ``write`` is False
    when the chart was edited in a way that cannot be read back, and then it must
    be left alone. Call :func:`remember` after writing it.
    """
    notes: list[str] = []
    fixes = load(audio)
    fixed = apply(analysis, fixes)
    target = Path(target)
    if not target.is_file():
        return fixed, True, notes
    text = target.read_text(encoding="utf-8")
    known = _records().get(str(target.resolve()))
    if known == _digest(text):
        return fixed, True, notes
    if not chordgrid:
        if known is None:
            return fixed, True, notes   # nothing to tell a hand edit from an old chart by
        notes.append(f"{target.name} was edited by hand and only a chordgrid chart "
                     f"can be read back, so it was left as it is")
        return fixed, False, notes
    try:
        edits = edited_bars(fixed, text, render)
    except CorrectionError as exc:
        notes.append(f"{target.name} was edited and could not be read back ({exc}), "
                     f"so it was left as it is")
        return fixed, False, notes
    if edits:
        fixes.update(edits)
        where = save(audio, fixes)
        fixed = apply(analysis, fixes)
        notes.append(f"kept {len(edits)} bar{'s' if len(edits) != 1 else ''} changed "
                     f"in {target.name} (saved in {where.name})")
    return fixed, True, notes


def remember(target: Path) -> None:
    """Note what was just written, so a later edit to it can be recognised."""
    try:
        _remember(Path(target), Path(target).read_text(encoding="utf-8"))
    except OSError:
        pass
