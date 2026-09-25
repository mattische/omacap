"""Finding the silences in a recording, and cutting it into per-track files."""

from __future__ import annotations

import subprocess
from pathlib import Path

from .formats import AudioFormat, get_format
from .recorder import RecorderError, ensure_ffmpeg, sanitize_basename
from .timeline import Segment, Silence, parse_silence_line, silences_from_events

#: Level below which audio counts as a gap. Measured against Spotify through a
#: filter-chain sink the gap sat at -70 dB while the music either side was -29 dB
#: and -15 dB, so this separates them with room to spare. Raising it starts
#: splitting inside quiet music.
DEFAULT_THRESHOLD_DB = -60.0

DETECT_TIMEOUT = 900.0
CUT_TIMEOUT = 600.0

#: Formats whose stream copy cannot be trusted to write a correct duration.
#: FLAC copies the *source's* length into the header - a 13.5 s cut reported 58.8 s
#: while decoding correctly - so it is re-encoded instead. That is lossless.
_REENCODE = frozenset({"flac"})


class SplitError(RuntimeError):
    """Raised when a recording cannot be examined or cut."""


def probe_duration(path: Path) -> float:
    """How long a file really is, according to ffprobe."""
    try:
        proc = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "csv=p=0", str(path)],
            capture_output=True, text=True, timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SplitError(f"could not read {path.name}: {exc}") from exc
    try:
        return float(proc.stdout.strip())
    except ValueError:
        raise SplitError(f"could not read the duration of {path.name}") from None


def detect_silences(
    path: Path,
    threshold_db: float = DEFAULT_THRESHOLD_DB,
    min_gap: float = 0.3,
    duration: float | None = None,
) -> list[Silence]:
    """Scan a file for silences.

    A full decode, but a cheap one: measured at roughly 880x realtime, so an hour
    of audio is scanned in about four seconds.
    """
    ensure_ffmpeg()
    path = Path(path)
    if not path.is_file():
        raise SplitError(f"no such file: {path}")
    if duration is None:
        duration = probe_duration(path)

    command = [
        "ffmpeg", "-hide_banner", "-nostdin", "-nostats", "-loglevel", "info",
        "-i", str(path),
        "-af", f"silencedetect=noise={threshold_db}dB:d={min_gap}",
        "-f", "null", "-",
    ]
    try:
        proc = subprocess.run(command, capture_output=True, text=True,
                              timeout=DETECT_TIMEOUT)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SplitError(f"could not scan {path.name}: {exc}") from exc

    events = []
    for line in proc.stderr.splitlines():
        event = parse_silence_line(line)
        if event is not None:
            events.append(event)
    return silences_from_events(events, duration)


def _written_duration(path: Path) -> float:
    """The duration of a file just written, or 0.0 when it has none."""
    try:
        return probe_duration(path)
    except SplitError:
        return 0.0


def target_path(segment: Segment, directory: Path, audio_format: AudioFormat) -> Path:
    """Where a segment's file goes, without overwriting anything."""
    stem = sanitize_basename(segment.basename())
    candidate = directory / f"{stem}{audio_format.extension}"
    counter = 2
    while candidate.exists():
        candidate = directory / f"{stem}_{counter}{audio_format.extension}"
        counter += 1
    return candidate


def cut(source: Path, segment: Segment, target: Path, audio_format: AudioFormat) -> Path:
    """Write one segment out.

    Stream copy wherever it can be trusted: the cut falls inside silence, so the
    few tens of milliseconds a frame boundary costs are inaudible, and copying
    avoids re-encoding a lossy file a second time.
    """
    ensure_ffmpeg()
    target.parent.mkdir(parents=True, exist_ok=True)
    codec = (
        ["-c:a", audio_format.codec]
        if audio_format.name in _REENCODE
        else ["-c", "copy"]
    )
    command = [
        "ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error",
        "-ss", f"{segment.start:.3f}", "-to", f"{segment.end:.3f}",
        "-i", str(source), *codec, "-y", str(target),
    ]
    try:
        proc = subprocess.run(command, capture_output=True, text=True,
                              timeout=CUT_TIMEOUT)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SplitError(f"could not write {target.name}: {exc}") from exc
    if proc.returncode != 0 or not target.is_file() or target.stat().st_size == 0:
        detail = (proc.stderr or "").strip().splitlines()
        raise SplitError(
            f"could not write {target.name}"
            + (f": {detail[-1]}" if detail else "")
        )

    # ffmpeg is happy to write a container with no audio in it - asking for a
    # span past the end of the file exits 0 and leaves a bare header - so the
    # segment is only accepted once it is known to hold something.
    written = _written_duration(target)
    if written < 0.1 or written < segment.duration * 0.5:
        target.unlink(missing_ok=True)
        raise SplitError(
            f"{target.name} came out empty: asked for "
            f"{segment.duration:.1f}s from {segment.start:.1f}s, got {written:.1f}s. "
            f"Is the segment inside the recording?"
        )
    return target


def split(
    source: Path,
    segments,
    directory: Path | None = None,
    audio_format: AudioFormat | None = None,
) -> list[Path]:
    """Cut a recording into one file per segment. The source is left alone."""
    source = Path(source)
    if not source.is_file():
        raise SplitError(f"no such file: {source}")
    if audio_format is None:
        try:
            audio_format = get_format(source.suffix)
        except ValueError as exc:
            raise SplitError(
                f"cannot tell the format of {source.name}; "
                f"splitting needs a format omacap knows"
            ) from exc
    directory = Path(directory) if directory else source.parent

    written: list[Path] = []
    for segment in segments:
        written.append(
            cut(source, segment, target_path(segment, directory, audio_format), audio_format)
        )
    return written
