"""Writing a bar the way the Obsidian chordgrid plugin reads it.

The plugin decides per bar whether it is looking at chords or at rhythm: the
bar's text is chords only if the *whole* of it matches ``chord( / chord)*``, and
anything else is parsed as note values. That makes a stray character worse than
useless - a bar written ``N.C.?`` is not a chord with a question mark after it,
it is a bar of rhythm that means nothing, and the plugin draws it as such.

So this module knows the plugin's grammar rather than guessing at it. The
patterns below are transcribed from ``src/parser/ChordGridParser.ts`` in
MathieuCGit/ChordGrid_for_Obsidian at plugin version 2.2.0, and
``tests/test_chordgrid.py`` checks omacap's output against them, so the two
cannot drift apart silently.

The other thing the plugin is good at is rhythm, and that solves a real problem:
two chords in a bar written ``C / G`` are drawn evenly, half the bar each. If
the chord actually changed on the last beat that is a lie. Where a bar's chords
do not divide it evenly, their true lengths are written as note values instead.
"""

from __future__ import annotations

import re

# -- the plugin's chord grammar -------------------------------------------
# Transcribed from ChordGridParser.ts. Kept as the same pieces with the same
# names so the two can be compared line by line.

_ROOT = "[A-G][#b♯♭]?"
_QUALITY = "(?:mMaj|mmaj|mM|Mmaj|major|minor|maj|min|dim|aug|M|m|ø|o|\\+|\\-)?"
_EXTENSION = "[0-9]+"
_ALTERATION = "(?:\\([#b♯♭]?[0-9]+\\)|[#b♯♭][0-9]+)"
_SUS = "(?:\\((?:sus[24]?|add[#b♯♭]?[0-9]+)\\)|sus[24]?|add[#b♯♭]?[0-9]+)"

CHORD = (f"{_ROOT}{_QUALITY}(?:{_EXTENSION}|{_ALTERATION}|{_SUS})*"
         f"(?:/{_ROOT})?")

#: A bar the plugin will read as chords rather than as rhythm.
CHORDS_ONLY = re.compile(f"^{CHORD}(?:\\s+/\\s+{CHORD})*$")

#: Chords in a bar are separated by a slash *with spaces*. Without them a slash
#: means a bass note, so "C/E" is one chord and "C / E" is two.
SEPARATOR = " / "


def is_chords(text: str) -> bool:
    """Would the plugin read this bar as chords?"""
    return bool(CHORDS_ONLY.match(text.strip()))


# -- note values ----------------------------------------------------------
# Keyed by length in quarter notes, which is the unit the plugin's note values
# are defined against: 4 = quarter, 8 = eighth, and a trailing dot is half again.

NOTE_VALUES = {
    6.0: "1.", 4.0: "1", 3.0: "2.", 2.0: "2", 1.5: "4.", 1.0: "4",
    0.75: "8.", 0.5: "8", 0.375: "16.", 0.25: "16",
}


def quarters_per_beat(meter) -> float:
    """How many quarter notes one beat of this metre lasts.

    A compound metre counts in eighths, so its beat is half a quarter note.
    """
    return 0.5 if meter.is_compound else 1.0


def note_value(quarters: float) -> str | None:
    """The plugin's note value for a length in quarter notes, or None.

    None means the length is not a single note value - a bar of 7/8 split three
    against four, say. The caller writes such a bar another way rather than
    inventing notation the plugin cannot read.
    """
    for length, token in NOTE_VALUES.items():
        if abs(quarters - length) < 1e-6:
            return token
    return None


def rest(quarters: float) -> str | None:
    """A rest of the given length, which is a note value with a ``-`` in front."""
    value = note_value(quarters)
    return None if value is None else f"-{value}"


# -- writing a bar --------------------------------------------------------

def beat_labels(bar, spans, meter) -> list[str]:
    """Which chord holds each beat of the bar.

    A beat is credited to whichever chord covers its middle, which is steadier
    than looking at the boundaries: a chord change detected a few milliseconds
    early should not move a whole beat.
    """
    beats = max(1, meter.beats_per_bar)
    step = bar.duration / beats
    labels = []
    for index in range(beats):
        middle = bar.start + (index + 0.5) * step
        holder = next((s.label for s in spans if s.start <= middle < s.end), None)
        labels.append(holder)
    return labels


def _runs(labels: list[str], keep: list[str]) -> list[tuple[str, int]]:
    """Run-length encode the beats, keeping only the chords the bar is written with.

    ``keep`` is what the analysis decided the bar says, which is a summary: a
    one-beat flicker was already dropped from it. A beat holding a dropped chord
    joins the run before it rather than becoming a chord of its own.
    """
    runs: list[list] = []
    for label in labels:
        if label not in keep:
            label = runs[-1][0] if runs else (keep[0] if keep else label)
        if runs and runs[-1][0] == label:
            runs[-1][1] += 1
        else:
            runs.append([label, 1])
    return [(label, count) for label, count in runs]


def no_chord_bar(meter) -> str:
    """A bar with no chord in it, written as rests.

    The plugin has no notation for "no chord" - ``N.C.`` is not a chord name and
    would be read as rhythm - but a bar of rests says the same thing and is what
    a chart would show anyway.
    """
    per_beat = quarters_per_beat(meter)
    whole = rest(meter.beats_per_bar * per_beat)
    if whole is not None:
        return whole
    beat = rest(per_beat) or "-4"
    return " ".join([beat] * meter.beats_per_bar)


def bar_source(bar, spans, meter, no_chord: str = "N.C.") -> str:
    """The bar, written so the plugin reads what the analysis actually found."""
    chords = [c for c in bar.chords if c != no_chord]
    if not chords:
        return no_chord_bar(meter)
    if len(chords) == 1:
        return chords[0]

    runs = [(label, count) for label, count in
            _runs(beat_labels(bar, spans, meter), bar.chords)
            if label != no_chord]
    if not runs:
        return chords[0]
    if len(runs) == 1:
        return runs[0][0]

    # Evenly divided is the common case and the readable one: no note values,
    # just the chords, which the plugin spaces across the bar itself.
    counts = {count for _, count in runs}
    if len(counts) == 1 and meter.beats_per_bar % (len(runs)) == 0:
        return SEPARATOR.join(label for label, _ in runs)

    # Otherwise the lengths differ, and writing them as chords alone would draw
    # a change in the middle of a bar where the chord changed on the last beat.
    per_beat = quarters_per_beat(meter)
    values = [note_value(count * per_beat) for _, count in runs]
    if all(values):
        return " ".join(f"{label}[{value}]"
                        for (label, _), value in zip(runs, values))

    # No notation fits, so say less rather than something untrue: the chord that
    # holds most of the bar, alone.
    return max(runs, key=lambda run: run[1])[0]
