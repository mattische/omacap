"""Turning a recording's silences and track changes into a list of segments.

This module is deliberately free of subprocesses, files and clocks. Every awkward
decision about where one track ends and the next begins lives here, so all of it
can be tested by handing in numbers.

Two sources feed it, and they are not equal partners:

* **Track changes**, read from the player over MPRIS, decide *whether* to split.
* **Silences**, found in the audio, decide *where* the cut falls.

Silence never splits on its own when track changes are available. Pausing or
seeking inside a track both produce silence while the track stays the same, and
splitting there would be wrong. With no track changes to go on - splitting a file
after the fact, say - silence is all there is, and every gap becomes a boundary.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .nowplaying import Track

#: ffmpeg's silencedetect filter announces itself on the log, in stream time.
SILENCE_RE = re.compile(r"silence_(start|end):\s*(-?[\d.]+)")

#: A gap must last at least this long to be treated as a track boundary. Measured
#: against Spotify, the gap between tracks was 2.45 s; gaps inside a piece of
#: music are rarely this long.
DEFAULT_MIN_GAP = 0.8

#: Anything shorter than this is not a track. It is a fragment left by skipping,
#: or an advert, or the tail of the recording.
DEFAULT_MIN_TRACK = 20.0

#: Kept either side of a segment so a fade-in or fade-out is not clipped.
DEFAULT_PAD = 0.25

#: How far a track change may sit from a silence and still be paired with it.
SNAP_WINDOW = 3.0


def parse_silence_line(line: str) -> tuple[str, float] | None:
    """Read one ``silence_start`` or ``silence_end`` out of an ffmpeg log line."""
    match = SILENCE_RE.search(line)
    if match is None:
        return None
    try:
        return match.group(1), float(match.group(2))
    except ValueError:
        return None


def silences_from_events(events, duration: float) -> list[Silence]:
    """Pair up start and end events into intervals.

    A start with no end is the recording trailing off into silence - exactly the
    case that should stop a capture - so it is closed at the end of the recording
    rather than discarded.
    """
    silences: list[Silence] = []
    open_at: float | None = None
    for kind, moment in events:
        moment = max(0.0, min(moment, duration))
        if kind == "start":
            if open_at is None:
                open_at = moment
        elif open_at is not None:
            if moment > open_at:
                silences.append(Silence(open_at, moment))
            open_at = None
    if open_at is not None and duration > open_at:
        silences.append(Silence(open_at, duration))
    return silences


@dataclass(frozen=True)
class Silence:
    """A stretch of the recording with no audio in it."""

    start: float
    end: float

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)

    @property
    def middle(self) -> float:
        return (self.start + self.end) / 2.0

    def contains(self, moment: float) -> bool:
        return self.start <= moment <= self.end


@dataclass(frozen=True)
class Segment:
    """One track's worth of the recording."""

    index: int
    start: float
    end: float
    track: Track | None = None

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)

    @property
    def title(self) -> str:
        return self.track.label if self.track else f"track {self.index:02d}"

    def basename(self) -> str:
        """The filename stem for this segment, before sanitising."""
        if self.track is not None:
            return self.track.filename(self.index)
        return f"track {self.index:02d}"


def usable_silences(silences, min_gap: float = DEFAULT_MIN_GAP) -> list[Silence]:
    """Silences long enough to be a boundary, in order."""
    return sorted(
        (s for s in silences if s.duration >= min_gap), key=lambda s: s.start
    )


def snap(moment: float, silences, window: float = SNAP_WINDOW) -> Silence | None:
    """The silence a track change belongs to, if any.

    A signal that falls *inside* a gap is the normal case - measured against
    Spotify, the track change arrived 1.78 s into a 2.45 s gap - so containment is
    tried first. Only if the signal falls outside every gap is the nearest one
    within ``window`` considered, which covers a player whose announcement runs
    ahead of or behind its audio.
    """
    ordered = sorted(silences, key=lambda s: s.start)
    for silence in ordered:
        if silence.contains(moment):
            return silence
    nearest, best = None, window
    for silence in ordered:
        distance = min(abs(moment - silence.start), abs(moment - silence.end))
        if distance <= best:
            nearest, best = silence, distance
    return nearest


def boundaries_from_changes(changes, silences, window: float = SNAP_WINDOW) -> list[float]:
    """Where to cut, given what the player said and where the audio went quiet."""
    cuts = []
    for change in changes:
        gap = snap(change.at, silences, window)
        cuts.append(gap.middle if gap is not None else change.at)
    return cuts


def plan_from_changes(
    duration: float,
    changes,
    silences,
    min_gap: float = DEFAULT_MIN_GAP,
    min_track: float = DEFAULT_MIN_TRACK,
    pad: float = DEFAULT_PAD,
) -> list[Segment]:
    """Segments driven by track changes, with silences placing the cuts."""
    gaps = usable_silences(silences, min_gap)
    ordered = sorted(changes, key=lambda c: c.at)
    if not ordered:
        return []

    cuts = boundaries_from_changes(ordered, gaps)
    # The first track was already playing when recording started, so it begins at
    # the start of the recording rather than at its own announcement.
    edges = [0.0] + cuts[1:] + [duration]
    tracks = [c.track for c in ordered]

    planned = []
    for start, end, track in zip(edges[:-1], edges[1:], tracks):
        planned.append((start, end, track))
    return _finish(planned, duration, gaps, min_track, pad)


def plan_from_silence(
    duration: float,
    silences,
    min_gap: float = DEFAULT_MIN_GAP,
    min_track: float = DEFAULT_MIN_TRACK,
    pad: float = DEFAULT_PAD,
) -> list[Segment]:
    """Segments driven by silence alone, for a file with no track history."""
    gaps = usable_silences(silences, min_gap)
    planned = []
    cursor = 0.0
    for gap in gaps:
        if gap.start > cursor:
            planned.append((cursor, gap.start, None))
        cursor = gap.end
    if cursor < duration:
        planned.append((cursor, duration, None))
    return _finish(planned, duration, gaps, min_track, pad)


def _finish(planned, duration, gaps, min_track, pad) -> list[Segment]:
    """Trim each span to the audio inside it, drop what is not a track, number them."""
    segments = []
    for start, end, track in planned:
        start, end = _trim(start, end, gaps, pad, duration)
        if end - start < min_track:
            continue
        if track is not None and track.is_advert:
            continue
        segments.append((start, end, track))
    return [
        Segment(index=number, start=start, end=end, track=track)
        for number, (start, end, track) in enumerate(segments, start=1)
    ]


def _trim(start: float, end: float, gaps, pad: float, duration: float):
    """Pull a span past any silence overlapping its edges, then pad it back.

    A cut is placed in the middle of a gap, which leaves silence hanging off both
    neighbours. Trimming to the audio and padding a little keeps a fade from being
    clipped without carrying the whole gap into the file.
    """
    for gap in gaps:
        if gap.start <= start < gap.end:        # the span begins inside a gap
            start = gap.end
        if gap.start < end <= gap.end:          # the span ends inside a gap
            end = gap.start
    start = max(0.0, start - pad)
    end = min(duration, end + pad)
    return start, max(start, end)
