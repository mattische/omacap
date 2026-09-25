"""Recording and following the player at the same time, then cutting the result.

The recorder is left exactly as it was: one ffmpeg process writing one continuous
file. Alongside it, a watcher asks the media player what is playing, timing every
answer against the recorder's own elapsed time so the two land on one timeline.
The cutting happens afterwards, on a file that is already safely on disk.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from . import nowplaying, splitter, timeline
from .formats import AudioFormat
from .recorder import Recorder, RecordingResult

#: How long a recording must have been silent before it stops itself, when that
#: is switched on at all.
DEFAULT_STOP_AFTER_SILENCE = 30.0

#: A recording has to contain this much audio before the silence watchdog arms.
#: Starting omacap before pressing play would otherwise stop it immediately.
ARMING_SECONDS = 1.0
from .timeline import Segment


@dataclass
class SplitOptions:
    """How a recording should be cut up, if at all."""

    enabled: bool = False
    min_gap: float = timeline.DEFAULT_MIN_GAP
    min_track: float = timeline.DEFAULT_MIN_TRACK
    pad: float = timeline.DEFAULT_PAD
    directory: Path | None = None
    player: str | None = None


class SilenceStopper:
    """Stops a recording once it has been quiet long enough.

    This is the end of a playlist: the music stops and nothing follows. It only
    arms once some audio has actually been recorded, because otherwise starting
    omacap before pressing play would stop it straight away.
    """

    def __init__(self, after: float = 0.0) -> None:
        self.after = after
        self.armed = False

    @property
    def enabled(self) -> bool:
        return self.after > 0

    def should_stop(self, recorder) -> bool:
        if not self.enabled:
            return False
        if not self.armed:
            # Everything except the trailing silence counts as audio heard.
            if recorder.duration - recorder.silent_for >= ARMING_SECONDS:
                self.armed = True
            return False
        return recorder.silent_for >= self.after


@dataclass
class SplitResult:
    """What came of trying to cut a recording up."""

    segments: list[Segment] = field(default_factory=list)
    written: list[Path] = field(default_factory=list)
    named: bool = False
    reason: str = ""

    @property
    def happened(self) -> bool:
        return bool(self.written)


class TrackSession:
    """A recording, with the player followed for as long as it runs."""

    def __init__(self, recorder: Recorder, player: str | None = None) -> None:
        self.recorder = recorder
        self.player = player
        self.watcher: nowplaying.TrackWatcher | None = None

    @property
    def following(self) -> bool:
        return self.watcher is not None

    def start(self) -> None:
        self.recorder.start()
        if self.player:
            # Timed against the recorder, so a change lands on the file's own
            # timeline rather than on the wall clock.
            self.watcher = nowplaying.TrackWatcher(
                self.player, clock=lambda: self.recorder.duration
            )
            self.watcher.start()

    def stop(self) -> RecordingResult:
        if self.watcher is not None:
            self.watcher.stop()
        return self.recorder.stop()

    @property
    def changes(self) -> list[nowplaying.TrackChange]:
        return self.watcher.tracks if self.watcher is not None else []

    @property
    def track_count(self) -> int:
        return len({c.track.trackid for c in self.changes})


def choose_player(preferred: str | None = None) -> str | None:
    """The player to follow, or None when there is nothing to follow."""
    if not nowplaying.available():
        return None
    return nowplaying.find_player(preferred)


def plan_segments(
    duration: float,
    silences,
    changes,
    options: SplitOptions,
) -> tuple[list[Segment], bool]:
    """Work out the pieces, and say whether they carry real names.

    Track changes are used when there are at least two, because a single change
    describes the whole recording and says nothing about where to cut. Silence is
    the fallback, and gives numbered pieces.
    """
    if len({c.track.trackid for c in changes}) >= 2:
        segments = timeline.plan_from_changes(
            duration, changes, silences,
            min_gap=options.min_gap, min_track=options.min_track, pad=options.pad,
        )
        if segments:
            return segments, True
    segments = timeline.plan_from_silence(
        duration, silences,
        min_gap=options.min_gap, min_track=options.min_track, pad=options.pad,
    )
    return segments, False


def split_recording(
    result: RecordingResult,
    silences,
    changes,
    options: SplitOptions,
    audio_format: AudioFormat | None = None,
) -> SplitResult:
    """Cut a finished recording into its tracks. The recording is left alone."""
    if not result.exists:
        return SplitResult(reason="the recording is empty")

    segments, named = plan_segments(result.duration, silences, changes, options)
    if not segments:
        return SplitResult(
            reason=(
                f"nothing lasted the {options.min_track:g}s a track has to last"
            )
        )
    if len(segments) < 2:
        return SplitResult(
            segments=segments,
            named=named,
            reason="only one piece was found, so there was nothing to split",
        )

    written = splitter.split(
        result.path, segments, options.directory or result.path.parent, audio_format
    )
    return SplitResult(segments=segments, written=written, named=named)
