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
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path

from .devices import Source, _pactl_env
from .formats import AudioFormat

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
            "-loglevel", "info" if self.meter else "error",
            "-f", "pulse",
            "-i", self.source.name,
        ]
        if self.meter:
            # astats publishes the peak level as frame metadata and ametadata
            # prints it to ffmpeg's log. The log is flushed line by line, whereas
            # ametadata's own file=- output is block-buffered and would only
            # arrive once recording ended - no good for a live meter.
            cmd += [
                "-af",
                "astats=metadata=1:reset=8:measure_perchannel=none"
                ":measure_overall=Peak_level,"
                "ametadata=mode=print:key=lavfi.astats.Overall.Peak_level",
            ]
        cmd += ["-ac", str(self.channels), "-ar", str(self.sample_rate)]
        cmd += self.audio_format.encoder_args(self.bitrate)
        if self.duration is not None:
            cmd += ["-t", f"{self.duration:.3f}"]
        cmd += self.extra_ffmpeg_args
        cmd += ["-progress", "pipe:1", "-y", str(self.output_path)]
        return cmd


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


def sanitize_basename(name: str) -> str:
    """Strip path separators and awkward characters out of a user-typed name."""
    cleaned = re.sub(r"[^\w\-. ]+", "_", name.strip()).strip(" .")
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
        self._stderr_tail: list[str] = []

    # -- lifecycle ---------------------------------------------------------

    def start(self) -> None:
        if self.state is not State.IDLE:
            raise RecorderError(f"recorder already used (state: {self.state.value})")
        ensure_ffmpeg()
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
    def stderr_tail(self) -> list[str]:
        return list(self._stderr_tail)
