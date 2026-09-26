"""The recording engine: an ffmpeg process plus live progress parsing."""

from __future__ import annotations

import math
import os
import re
import shutil
import signal
import subprocess
import threading
import time
from functools import lru_cache
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path

from .devices import Source, _pactl_env
from .formats import AudioFormat
from .timeline import parse_silence_line, silences_from_events

#: ffmpeg returns 255 when it is interrupted, which is exactly how we stop it.
_CLEAN_EXIT_CODES = frozenset({0, 255})

_PROGRESS_KEYS = {"out_time_us", "out_time_ms", "total_size"}
_PEAK_RE = re.compile(r"lavfi\.astats\.Overall\.Peak_level=(-?[\d.]+|-?inf|nan)")

#: Informational ffmpeg chatter that must not be mistaken for an error.
_NOISE_RE = re.compile(
    r"^\[Parsed_ametadata|^\s*frame:\d|^Input #|^Output #|^Stream mapping|"
    r"^\s*Stream #|^\s*Metadata:|^\s*Duration:|^\s*encoder\s*:|^\s*ISFT|"
    r"Guessed Channel Layout|^\s*major_brand|^\s*minor_version|"
    r"^\s*compatible_brands|^size=|^\s*handler_name"
)

_ERROR_RE = re.compile(
    r"error|invalid|failed|unable|cannot|no such|not found|denied|refused|"
    r"connection",
    re.IGNORECASE,
)

#: Quietest level the meter will show; anything below reads as silence.
METER_FLOOR_DB = -60.0

#: Writing tags is a stream copy, so it is quick; this is only a backstop.
TAG_TIMEOUT = 60.0

#: A peak at or above this has run out of headroom. Samples here are sitting at
#: the top of the scale, which is where a digital recording starts to distort.
CLIP_THRESHOLD_DB = -0.1

#: One reading at the ceiling can be a single loud transient. Several means the
#: recording is genuinely being squared off.
CLIP_READINGS = 3

#: Filter chain behind the live level meter. astats publishes the peak level as
#: frame metadata and ametadata prints it to ffmpeg's log.
METER_FILTER = (
    "astats=metadata=1:reset=8:measure_perchannel=none:measure_overall=Peak_level,"
    "ametadata=mode=print:key=lavfi.astats.Overall.Peak_level"
)

#: Level below which audio counts as a gap between tracks. See splitter.py for
#: how this was measured.
SILENCE_THRESHOLD_DB = -60.0
#: The shortest gap reported. Deliberately finer than the gap that counts as a
#: track boundary, so the decision stays with the caller rather than with ffmpeg.
SILENCE_MIN_GAP = 0.3


def silence_filter(
    threshold_db: float = SILENCE_THRESHOLD_DB, min_gap: float = SILENCE_MIN_GAP
) -> str:
    """The filter that reports gaps, on the same log channel as the meter."""
    return f"silencedetect=noise={threshold_db}dB:d={min_gap}"


class RecorderError(RuntimeError):
    """Raised when recording cannot start, or ffmpeg dies unexpectedly."""


class State(str, Enum):
    IDLE = "idle"
    RECORDING = "recording"
    STOPPING = "stopping"
    FINISHED = "finished"
    FAILED = "failed"


@dataclass
class RecordingResult:
    """What came out of a finished recording."""

    path: Path
    duration: float
    size_bytes: int
    format_name: str
    peak_db: float = METER_FLOOR_DB
    clipped: bool = False

    @property
    def exists(self) -> bool:
        return self.path.is_file() and self.size_bytes > 0


@dataclass
class RecorderConfig:
    """Everything needed to build the ffmpeg command line."""

    source: Source
    audio_format: AudioFormat
    output_path: Path
    bitrate: str | None = None
    sample_rate: int = 48000
    channels: int = 2
    meter: bool = True
    detect_silence: bool = False
    silence_threshold_db: float = SILENCE_THRESHOLD_DB
    silence_min_gap: float = SILENCE_MIN_GAP
    duration: float | None = None
    extra_ffmpeg_args: list[str] = field(default_factory=list)

    def command(self) -> list[str]:
        """The full ffmpeg argument vector."""
        cmd = [
            "ffmpeg",
            "-hide_banner",
            "-nostdin",
            "-nostats",
            # Filter metadata is logged at info level, so the meter needs it.
            "-loglevel", "info" if (self.meter or self.detect_silence) else "error",
            "-f", "pulse",
            "-i", self.source.name,
        ]
        chain = []
        if self.detect_silence:
            chain.append(
                silence_filter(self.silence_threshold_db, self.silence_min_gap)
            )
        if self.meter:
            # The log is flushed line by line, whereas ametadata's own file=-
            # output is block-buffered and would only arrive once recording
            # ended - no good for a live meter.
            chain.append(METER_FILTER)
        if chain:
            cmd += ["-af", ",".join(chain)]
        cmd += ["-ac", str(self.channels), "-ar", str(self.sample_rate)]
        cmd += self.audio_format.encoder_args(self.bitrate)
        if self.duration is not None:
            cmd += ["-t", f"{self.duration:.3f}"]
        cmd += self.extra_ffmpeg_args
        cmd += ["-progress", "pipe:1", "-y", str(self.output_path)]
        return cmd


def clipping_advice() -> str:
    """Why a recording clipped, and what to change.

    The trap is that on a hardware output the volume is applied in the device,
    after the monitor is tapped. Turning the speakers down makes it quieter to
    listen to and changes the recording not at all. The application's own stream
    volume is the one that reaches the recording.
    """
    return (
        "The recording reached full scale and is being clipped. On a hardware "
        "output, turning the speakers down will not help: the volume is applied "
        "after omacap taps the monitor. Lower the application's own stream "
        "instead:\n"
        "  pactl list short sink-inputs\n"
        "  pactl set-sink-input-volume <id> 80%"
    )


def default_output_dir() -> Path:
    """Where recordings land unless the user says otherwise.

    Uses the XDG music directory when one is configured, so files show up where
    the desktop already looks for audio.
    """
    env = os.environ.get("OMACAP_OUTPUT_DIR")
    if env:
        return Path(env).expanduser()
    music = os.environ.get("XDG_MUSIC_DIR")
    if music and Path(music).expanduser().is_dir():
        return Path(music).expanduser() / "omacap"
    return Path.home() / "Recordings" / "omacap"


def build_output_path(
    directory: Path,
    audio_format: AudioFormat,
    basename: str | None = None,
    now: datetime | None = None,
) -> Path:
    """A collision-free path inside ``directory`` for this format."""
    stamp = (now or datetime.now()).strftime("%Y-%m-%d_%H-%M-%S")
    stem = sanitize_basename(basename) if basename else f"omacap_{stamp}"
    candidate = directory / f"{stem}{audio_format.extension}"
    counter = 2
    while candidate.exists():
        candidate = directory / f"{stem}_{counter}{audio_format.extension}"
        counter += 1
    return candidate


#: The tags omacap writes, in the order it writes them.
TAG_NAMES = ("title", "artist", "album", "track")


def metadata_args(tags: dict | None) -> list[str]:
    """ffmpeg arguments writing these tags, in a stable order.

    Written at both container and stream level, because the two families differ:
    MP3, MP4, FLAC and WAV take container metadata, while Ogg and Opus keep Vorbis
    comments on the stream and silently ignore the container form. Measured on all
    six formats omacap writes - the Ogg pair came back with nothing at all until
    the stream-level arguments were added.
    """
    if not tags:
        return []
    args: list[str] = []
    for name in TAG_NAMES:
        value = tags.get(name)
        if value:
            args += ["-metadata", f"{name}={value}",
                     "-metadata:s:a:0", f"{name}={value}"]
    return args


def read_tags(path: Path) -> dict:
    """The tags a file actually carries, container or stream, or {} if unreadable."""
    fields = ",".join(TAG_NAMES)
    command = [
        "ffprobe", "-v", "error", "-show_entries",
        f"format_tags={fields}:stream_tags={fields}",
        "-of", "default=nw=1", str(path),
    ]
    try:
        proc = subprocess.run(command, capture_output=True, text=True,
                              timeout=TAG_TIMEOUT)
    except (OSError, subprocess.TimeoutExpired):
        return {}
    found = {}
    for line in (proc.stdout or "").splitlines():
        if line.startswith("TAG:") and "=" in line:
            name, _, value = line[4:].partition("=")
            if value and name.lower() in TAG_NAMES:
                found.setdefault(name.lower(), value)
    return found


def write_tags(path: Path, tags: dict | None) -> bool:
    """Put these tags into a finished recording, without re-encoding it.

    A second pass rather than tags on the original command, because what the
    player was playing is not reliably known when recording starts - omacap is
    often opened first and play pressed after.

    Returns whether anything was written. A failure is not raised: an untagged
    recording is worth more than no recording.
    """
    args = metadata_args(tags)
    if not args or not path.is_file():
        return False
    ensure_ffmpeg()
    temporary = path.with_name(f".{path.stem}.tagging{path.suffix}")
    command = [
        "ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error",
        "-i", str(path), "-map", "0", "-c", "copy", *args, "-y", str(temporary),
    ]
    try:
        proc = subprocess.run(command, capture_output=True, text=True,
                              timeout=TAG_TIMEOUT)
        if proc.returncode != 0 or not temporary.is_file() \
                or temporary.stat().st_size == 0:
            temporary.unlink(missing_ok=True)
            return False
        temporary.replace(path)
        # Report what landed, not what was asked for. ffmpeg exits 0 while writing
        # no tags at all if the arguments are the wrong shape for the container,
        # which is how the Ogg formats were failing without saying so.
        return read_tags(path).get("title") == tags.get("title")
    except (OSError, subprocess.TimeoutExpired):
        temporary.unlink(missing_ok=True)
        return False


def rename_recording(path: Path, basename: str) -> Path:
    """Rename a finished recording to ``basename``, keeping its extension.

    Returns the new path, or the old one if the rename could not be done - a
    recording that exists under the wrong name is worth more than an error.
    """
    stem = sanitize_basename(basename)
    if not stem or stem == path.stem:
        return path
    target = path.with_name(f"{stem}{path.suffix}")
    counter = 2
    while target.exists() and target != path:
        target = path.with_name(f"{stem}_{counter}{path.suffix}")
        counter += 1
    try:
        return path.rename(target)
    except OSError:
        return path


#: Punctuation kept in a filename. Track titles are full of brackets, ampersands
#: and apostrophes - "Kärlek & Kaos", "Sång nr. 3 [Live]", "What's Going On" - and
#: replacing those with underscores makes a chart folder unreadable. Everything
#: else, path separators and shell globs included, is replaced.
_ALLOWED_PUNCTUATION = r"\-.,&'()\[\]!+ "
_DISALLOWED = re.compile(rf"[^\w{_ALLOWED_PUNCTUATION}]+")


def sanitize_basename(name: str) -> str:
    r"""Strip path separators and awkward characters out of a name.

    ``\w`` is Unicode-aware, so accented and non-Latin letters survive; only
    characters that would confuse a filesystem are replaced. A replacement left
    at the end is dropped - a title ending in "?" should not leave a trailing
    underscore - but one at the start is kept, so "../x" cannot come back as a
    name beginning with a dot.
    """
    cleaned = _DISALLOWED.sub("_", name.strip()).strip(" .").rstrip("_ .")
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned or "omacap"


def ensure_ffmpeg() -> str:
    """Return the ffmpeg path, or explain how to get it."""
    path = shutil.which("ffmpeg")
    if path is None:
        raise RecorderError(
            "ffmpeg was not found on PATH. Install it first "
            "(Arch: sudo pacman -S ffmpeg, Debian/Ubuntu: sudo apt install ffmpeg)."
        )
    return path


@lru_cache(maxsize=1)
def available_encoders() -> frozenset[str]:
    """Audio encoder names this ffmpeg build can use.

    An empty set means the question could not be answered, in which case callers
    must not treat a format as unavailable.
    """
    try:
        proc = subprocess.run(
            [ensure_ffmpeg(), "-hide_banner", "-encoders"],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired, RecorderError):
        return frozenset()
    if proc.returncode != 0:
        return frozenset()

    names = set()
    listing_started = False
    for line in proc.stdout.splitlines():
        if not listing_started:
            # A row of dashes separates the flag legend from the encoder list.
            listing_started = line.strip().startswith("---")
            continue
        fields = line.split()
        if len(fields) >= 2 and len(fields[0]) == 6 and fields[0][0] == "A":
            names.add(fields[1])
    return frozenset(names)


def format_is_available(audio_format: AudioFormat) -> bool:
    """Whether this ffmpeg can encode to ``audio_format``."""
    encoders = available_encoders()
    return not encoders or audio_format.codec in encoders


def ensure_format(audio_format: AudioFormat) -> None:
    """Check the encoder exists, before a recording silently fails to start."""
    if not format_is_available(audio_format):
        raise RecorderError(
            f"this ffmpeg build cannot encode {audio_format.name} "
            f"(encoder {audio_format.codec!r} is missing). "
            f"Run 'omacap formats' to see what is available, or install a "
            f"fuller ffmpeg build."
        )


@lru_cache(maxsize=8)
def filter_supported(chain: str) -> bool:
    """Whether this ffmpeg understands a filter chain.

    An unusable filter makes ffmpeg refuse to start at all, so a build that does
    not know one of these would lose the whole recording rather than just the
    feature. Probing once per chain is cheap insurance.
    """
    try:
        proc = subprocess.run(
            [
                ensure_ffmpeg(), "-hide_banner", "-loglevel", "error", "-nostdin",
                "-f", "lavfi", "-i", "anullsrc=r=8000:cl=mono",
                "-af", chain,
                "-t", "0.05", "-f", "null", os.devnull,
            ],
            capture_output=True,
            timeout=15,
            stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.TimeoutExpired, RecorderError):
        return False
    return proc.returncode == 0


def metering_supported() -> bool:
    """Whether the live level meter can run."""
    return filter_supported(METER_FILTER)


def silence_detection_supported() -> bool:
    """Whether gaps can be reported while recording."""
    return filter_supported(silence_filter())


def parse_peak_db(line: str) -> float | None:
    """Extract a peak level in dBFS from an ametadata line, if present."""
    match = _PEAK_RE.search(line)
    if match is None:
        return None
    raw = match.group(1)
    try:
        value = float(raw)
    except ValueError:
        return METER_FLOOR_DB
    if math.isnan(value) or math.isinf(value):
        return METER_FLOOR_DB
    return value


class Recorder:
    """Runs one recording and exposes live progress while it does.

    The object is single-use: call :meth:`start`, poll the properties, then
    :meth:`stop`. Build a new :class:`Recorder` for the next take.
    """

    def __init__(self, config: RecorderConfig) -> None:
        self.config = config
        self.state = State.IDLE
        self.error: str | None = None
        self._process: subprocess.Popen[str] | None = None
        self._reader: threading.Thread | None = None
        self._lock = threading.Lock()
        self._started_at: float | None = None
        self._stopped_at: float | None = None
        self._out_time = 0.0
        self._total_size = 0
        self._peak_db = METER_FLOOR_DB
        self._peak_hold = METER_FLOOR_DB
        self._clip_readings = 0
        self._silence_events: list[tuple[str, float]] = []
        self._stderr_tail: list[str] = []

    # -- lifecycle ---------------------------------------------------------

    def start(self) -> None:
        if self.state is not State.IDLE:
            raise RecorderError(f"recorder already used (state: {self.state.value})")
        ensure_ffmpeg()
        ensure_format(self.config.audio_format)
        if self.config.meter and not metering_supported():
            # Lose the meter rather than the recording.
            self.config.meter = False
        self.config.output_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._process = subprocess.Popen(
                self.config.command(),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                stdin=subprocess.DEVNULL,
                text=True,
                bufsize=1,
                env=_pactl_env(),
            )
        except OSError as exc:
            self.state = State.FAILED
            self.error = f"could not launch ffmpeg: {exc}"
            raise RecorderError(self.error) from exc
        self._started_at = time.monotonic()
        self.state = State.RECORDING
        self._reader = threading.Thread(target=self._read_progress, daemon=True)
        self._reader.start()
        threading.Thread(target=self._read_stderr, daemon=True).start()

    def stop(self, timeout: float = 8.0) -> RecordingResult:
        """Ask ffmpeg to finalise the file, then report what was written."""
        if self.state is State.IDLE or self._process is None:
            raise RecorderError("recorder was never started")
        if self.state is State.RECORDING:
            self.state = State.STOPPING
            self._stopped_at = time.monotonic()
            self._terminate(timeout)
        return self._finish()

    def wait(self, timeout: float | None = None) -> RecordingResult:
        """Block until ffmpeg exits on its own (used with ``--duration``)."""
        if self._process is None:
            raise RecorderError("recorder was never started")
        try:
            self._process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            return self.stop()
        self._stopped_at = self._stopped_at or time.monotonic()
        return self._finish()

    def _terminate(self, timeout: float) -> None:
        assert self._process is not None
        # SIGINT is how ffmpeg is told to wrap up: it flushes the encoder and
        # writes trailers such as the MP4 moov atom. SIGKILL would truncate.
        for sig, wait in ((signal.SIGINT, timeout), (signal.SIGTERM, 3.0)):
            try:
                self._process.send_signal(sig)
            except ProcessLookupError:
                return
            try:
                self._process.wait(timeout=wait)
                return
            except subprocess.TimeoutExpired:
                continue
        self._process.kill()
        self._process.wait(timeout=3.0)

    def _finish(self) -> RecordingResult:
        assert self._process is not None
        returncode = self._process.poll()
        if self._reader is not None:
            self._reader.join(timeout=2.0)
        path = self.config.output_path
        size = path.stat().st_size if path.is_file() else 0
        duration = self.duration
        clean = returncode in _CLEAN_EXIT_CODES or (
            returncode is not None and returncode < 0
        )
        if not clean or size == 0:
            self.state = State.FAILED
            detail = self.failure_hint
            self.error = (
                f"ffmpeg exited with code {returncode}"
                + (f": {detail}" if detail else "")
                + ("" if size else " (no data was written)")
            )
        else:
            self.state = State.FINISHED
        return RecordingResult(
            path=path,
            duration=duration,
            size_bytes=size,
            format_name=self.config.audio_format.name,
            peak_db=self.peak_hold,
            clipped=self.clipping,
        )

    # -- live progress -----------------------------------------------------

    def _read_progress(self) -> None:
        assert self._process is not None and self._process.stdout is not None
        for line in self._process.stdout:
            line = line.strip()
            if not line:
                continue
            key, _, value = line.partition("=")
            if key not in _PROGRESS_KEYS:
                continue
            try:
                number = int(value)
            except ValueError:
                continue
            with self._lock:
                if key == "out_time_us":
                    self._out_time = number / 1_000_000
                elif key == "out_time_ms":
                    # ffmpeg reports out_time_ms in microseconds; only trust it
                    # when out_time_us has not been seen.
                    self._out_time = self._out_time or number / 1_000_000
                elif key == "total_size":
                    self._total_size = number

    def _read_stderr(self) -> None:
        assert self._process is not None and self._process.stderr is not None
        for raw in self._process.stderr:
            line = raw.strip()
            if not line:
                continue
            peak = parse_peak_db(line)
            if peak is not None:
                with self._lock:
                    self._peak_db = peak
                    self._peak_hold = max(self._peak_hold, peak)
                    if peak >= CLIP_THRESHOLD_DB:
                        self._clip_readings += 1
                continue
            event = parse_silence_line(line)
            if event is not None:
                with self._lock:
                    self._silence_events.append(event)
                continue
            if _NOISE_RE.search(line):
                continue
            self._stderr_tail.append(line)
            del self._stderr_tail[:-10]

    @property
    def failure_hint(self) -> str:
        """The most useful line from ffmpeg's output for an error message."""
        for line in reversed(self._stderr_tail):
            if _ERROR_RE.search(line):
                return line
        return self._stderr_tail[-1] if self._stderr_tail else ""

    # -- observable state --------------------------------------------------

    @property
    def is_running(self) -> bool:
        return self._process is not None and self._process.poll() is None

    @property
    def duration(self) -> float:
        """Seconds of audio written, as reported by ffmpeg.

        Falls back to wall-clock time before the first progress line arrives.
        """
        with self._lock:
            if self._out_time > 0:
                return self._out_time
        if self._started_at is None:
            return 0.0
        end = self._stopped_at if self._stopped_at is not None else time.monotonic()
        return max(0.0, end - self._started_at)

    @property
    def size_bytes(self) -> int:
        with self._lock:
            if self._total_size:
                return self._total_size
        path = self.config.output_path
        return path.stat().st_size if path.is_file() else 0

    @property
    def peak_db(self) -> float:
        with self._lock:
            return self._peak_db

    @property
    def peak_hold(self) -> float:
        """The loudest the recording has been, in dBFS."""
        with self._lock:
            return self._peak_hold

    @property
    def clipping(self) -> bool:
        """Whether the level has run out of headroom often enough to matter."""
        with self._lock:
            return self._clip_readings >= CLIP_READINGS

    @property
    def silences(self):
        """Gaps seen so far, with an unfinished one closed at the current time.

        A gap still open is the recording trailing off into silence, which is
        exactly what a caller watching for the end of a playlist wants to see.
        """
        with self._lock:
            events = list(self._silence_events)
        return silences_from_events(events, self.duration)

    @property
    def silent_for(self) -> float:
        """How long the recording has been silent, right now. 0.0 if it is not."""
        gaps = self.silences
        if not gaps:
            return 0.0
        last = gaps[-1]
        return last.duration if last.end >= self.duration - 0.05 else 0.0

    @property
    def stderr_tail(self) -> list[str]:
        return list(self._stderr_tail)
