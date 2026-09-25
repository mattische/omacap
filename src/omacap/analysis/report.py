"""Ties the analysis stages together into one result."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from . import require_numpy
from .audio import ANALYSIS_RATE, AudioBuffer, load_audio, trim_silence
from .chords import DEFAULT_VOCABULARY, ChordSpan, decode, get_vocabulary, merge_adjacent
from .features import HOP_LENGTH, analyse_spectral, chromagram
from .key import Key, detect_key_with_chords, diatonic_bonus
from .meter import Meter, bar_boundaries, detect_meter
from .tempo import BeatGrid, analyse_tempo

#: Analysis below this length is not worth reporting: there are too few beats
#: to establish a tempo, let alone a metre.
MIN_DURATION = 5.0


class AnalysisError(RuntimeError):
    """Raised when a recording cannot be analysed."""


@dataclass
class Bar:
    """One bar of music and the chords in it."""

    number: int
    start: float
    end: float
    chords: list[str] = field(default_factory=list)

    @property
    def duration(self) -> float:
        return self.end - self.start

    @property
    def label(self) -> str:
        return " ".join(self.chords) if self.chords else "%"


@dataclass
class Analysis:
    """Everything detected about a recording."""

    source: Path
    duration: float
    tempo: float
    meter: Meter
    key: Key
    bars: list[Bar]
    chords: list[ChordSpan]
    beat_count: int
    tempo_confidence: float

    @property
    def bar_count(self) -> int:
        return len(self.bars)

    @property
    def chord_vocabulary(self) -> list[str]:
        """Distinct chords in the chart, most used first.

        Counted from the bars rather than the raw spans, so it lists what the
        chart actually shows.
        """
        counts: dict[str, int] = {}
        for bar in self.bars:
            for label in bar.chords:
                counts[label] = counts.get(label, 0) + 1
        return [label for label, _ in sorted(counts.items(), key=lambda kv: -kv[1])]


def analyse_file(
    path: Path,
    sample_rate: int = ANALYSIS_RATE,
    vocabulary: str = DEFAULT_VOCABULARY,
) -> Analysis:
    """Run the whole analysis over an audio file."""
    buffer = trim_silence(load_audio(path, sample_rate))
    return analyse_buffer(buffer, source=Path(path), vocabulary=vocabulary)


def analyse_buffer(
    buffer: AudioBuffer, source: Path, vocabulary: str = DEFAULT_VOCABULARY
) -> Analysis:
    """Run the whole analysis over already-decoded audio."""
    np = require_numpy()
    if buffer.duration < MIN_DURATION:
        raise AnalysisError(
            f"{source.name} is only {buffer.duration:.1f}s long; "
            f"at least {MIN_DURATION:.0f}s is needed to find a tempo."
        )

    spectral = analyse_spectral(buffer.samples, buffer.sample_rate)
    grid = analyse_tempo(spectral.onset, spectral.frame_rate)
    if len(grid) < 4:
        raise AnalysisError(
            f"no steady beat found in {source.name}; it may not be music."
        )

    # Chords are recognised per beat, so a change part-way through a bar is not
    # averaged away with the chord before it.
    beat_edges = _beat_edges(grid)
    spans = decode(
        spectral.chroma, spectral.frame_rate, beat_edges, get_vocabulary(vocabulary)
    )
    beat_chroma = _beat_chroma(spectral, beat_edges)

    meter = detect_meter(
        grid.beats, spectral.onset, spectral.frame_rate, beat_chroma,
        low_onset=spectral.low_onset,
    )
    key = detect_key_with_chords(spectral.chroma, merge_adjacent(spans))

    # Now that the key is known, recognise the chords again with it in mind. Most
    # of what a song plays is diatonic, and this settles the close calls.
    spans = decode(
        spectral.chroma, spectral.frame_rate, beat_edges,
        get_vocabulary(vocabulary), diatonic_bonus(key),
    )
    key = detect_key_with_chords(spectral.chroma, merge_adjacent(spans))
    bars = build_bars(grid, meter, spans, duration=buffer.duration)

    return Analysis(
        source=Path(source),
        duration=buffer.duration,
        tempo=grid.bpm,
        meter=meter,
        key=key,
        bars=bars,
        chords=merge_adjacent(spans),
        beat_count=len(grid),
        tempo_confidence=grid.confidence,
    )


def build_bars(
    grid: BeatGrid,
    meter: Meter,
    spans: list[ChordSpan],
    duration: float | None = None,
) -> list[Bar]:
    """Group beat-level chords into bars."""
    np = require_numpy()
    boundaries = bar_boundaries(grid.beats, meter, duration)
    if boundaries.size < 2:
        return []
    bars = []
    for index, (start, end) in enumerate(zip(boundaries[:-1], boundaries[1:]), start=1):
        labels = bar_chords(spans, float(start), float(end))
        bars.append(Bar(number=index, start=float(start), end=float(end), chords=labels))
    return bars


#: A chord has to hold this much of a bar to be listed on its own.
DOMINANT_SHARE = 0.62
#: Below this share a chord is passing detail, not part of the chart.
MINOR_SHARE = 0.22
#: Most bars carry one or two chords; more than that is detector noise.
MAX_CHORDS_PER_BAR = 2


#: Qualities that share a root and a third belong to one family.
_MINOR_QUALITIES = frozenset({"m", "m7"})


def chord_family(span: ChordSpan) -> tuple[int, str]:
    """The chord a listener would name, ignoring which extension was detected."""
    if span.root < 0:
        return (-1, "none")
    if span.quality in _MINOR_QUALITIES:
        return (span.root, "minor")
    if span.quality == "dim":
        return (span.root, "dim")
    return (span.root, "major")


def bar_chords(spans: list[ChordSpan], start: float, end: float) -> list[str]:
    """The chords worth writing in one bar.

    Beat-level labels flicker on real music, so a bar is summarised by how long
    each chord actually holds it rather than by every label in it.
    """
    span_length = end - start
    if span_length <= 0:
        return []

    # Group by chord family first. D and Dmaj7 are the same chord as far as a
    # chart is concerned, and letting them compete makes a steady bar look as if
    # it changed half way through.
    families: dict[tuple[int, str], float] = {}
    variants: dict[tuple[int, str], dict[str, float]] = {}
    order: list[tuple[int, str]] = []
    for span in spans:
        overlap = min(span.end, end) - max(span.start, start)
        if overlap <= 1e-6:
            continue
        family = chord_family(span)
        if family not in families:
            order.append(family)
            variants[family] = {}
        families[family] = families.get(family, 0.0) + overlap
        variants[family][span.label] = variants[family].get(span.label, 0.0) + overlap
    if not families:
        return []

    held = {
        max(variants[family].items(), key=lambda item: item[1])[0]: duration
        for family, duration in families.items()
    }
    order = [max(variants[family].items(), key=lambda item: item[1])[0] for family in order]
    ranked = sorted(held.items(), key=lambda item: -item[1])
    if ranked[0][1] / span_length >= DOMINANT_SHARE:
        return [ranked[0][0]]

    keep = {
        label for label, duration in ranked[:MAX_CHORDS_PER_BAR]
        if duration / span_length >= MINOR_SHARE
    } or {ranked[0][0]}
    return [label for label in order if label in keep]


def _beat_edges(grid: BeatGrid):
    """Beat times with a closing edge one beat period past the last beat."""
    np = require_numpy()
    beats = np.asarray(grid.beats, dtype=np.float64)
    period = float(np.median(np.diff(beats))) if beats.size > 1 else 0.5
    return np.append(beats, beats[-1] + period)


def _beat_chroma(spectral, beat_edges):
    from .chords import synchronise

    return synchronise(spectral.chroma, spectral.frame_rate, beat_edges)
