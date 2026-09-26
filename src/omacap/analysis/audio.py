"""Decoding audio files into numpy arrays, using the ffmpeg we already depend on."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from ..recorder import ensure_ffmpeg
from . import require_numpy

#: Analysis sample rate. 22050 Hz keeps every musical partial we care about
#: (up to 11 kHz) while halving the work compared with 44100.
ANALYSIS_RATE = 22050


class DecodeError(RuntimeError):
    """Raised when ffmpeg cannot read the file."""


@dataclass
class AudioBuffer:
    """Mono audio at a known sample rate."""

    samples: object  # numpy.ndarray of float32
    sample_rate: int
    #: Where this buffer starts in the file it came from. Trimming silence moves
    #: it, and anything that has to line up with the original audio - a player,
    #: a metronome - needs to know by how much.
    start: float = 0.0

    @property
    def duration(self) -> float:
        return len(self.samples) / self.sample_rate

    def __len__(self) -> int:
        return len(self.samples)


def load_audio(path: Path, sample_rate: int = ANALYSIS_RATE) -> AudioBuffer:
    """Decode ``path`` to mono float32 at ``sample_rate``.

    Anything ffmpeg can read works, so recordings in every format omacap writes
    can be analysed without extra decoders.
    """
    np = require_numpy()
    ensure_ffmpeg()
    path = Path(path)
    if not path.is_file():
        raise DecodeError(f"no such file: {path}")

    command = [
        "ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error",
        "-i", str(path),
        "-map", "0:a:0",
        "-f", "f32le", "-acodec", "pcm_f32le",
        "-ac", "1", "-ar", str(sample_rate),
        "pipe:1",
    ]
    proc = subprocess.run(command, capture_output=True)
    if proc.returncode != 0 or not proc.stdout:
        detail = proc.stderr.decode("utf-8", "ignore").strip().splitlines()
        hint = detail[-1] if detail else f"exit code {proc.returncode}"
        raise DecodeError(f"could not decode {path.name}: {hint}")

    samples = np.frombuffer(proc.stdout, dtype="<f4").astype(np.float32)
    samples = np.nan_to_num(samples, nan=0.0, posinf=0.0, neginf=0.0)
    return AudioBuffer(samples=samples, sample_rate=sample_rate)


def trim_silence(buffer: AudioBuffer, threshold_db: float = -50.0) -> AudioBuffer:
    """Drop leading and trailing silence.

    Recordings usually start before playback does; leaving that silence in would
    shift every bar boundary.
    """
    np = require_numpy()
    samples = buffer.samples
    if samples.size == 0:
        return buffer
    window = max(1, buffer.sample_rate // 100)
    trimmed_len = (samples.size // window) * window
    if trimmed_len == 0:
        return buffer
    frames = samples[:trimmed_len].reshape(-1, window)
    rms = np.sqrt(np.mean(frames.astype(np.float64) ** 2, axis=1))
    peak = rms.max()
    if peak <= 0:
        return buffer
    loud = np.flatnonzero(20 * np.log10(np.maximum(rms, 1e-12) / peak) > threshold_db)
    if loud.size == 0:
        return buffer
    start = int(loud[0]) * window
    end = min(samples.size, (int(loud[-1]) + 1) * window)
    return AudioBuffer(
        samples=samples[start:end],
        sample_rate=buffer.sample_rate,
        start=buffer.start + start / buffer.sample_rate,
    )
