"""Shared fixtures.

The suite never touches real audio hardware: a stub ``ffmpeg`` on PATH stands in
for the real one, so the process handling and parsing are exercised for real
while staying reproducible.
"""

from __future__ import annotations

import os
import stat
import sys
from pathlib import Path

import pytest

#: A stand-in for ffmpeg. It writes a growing file, prints progress lines the way
#: `-progress pipe:1` does, logs peak levels the way ametadata does, and finalises
#: the file on SIGINT exactly as ffmpeg does.
FAKE_FFMPEG = r'''#!/usr/bin/env python3
import signal, sys, time
from pathlib import Path

ENCODER_LISTING = """Encoders:
 V..... = Video
 A..... = Audio
 ------
 A....D pcm_s16le            PCM signed 16-bit little-endian
 A....D flac                 FLAC (Free Lossless Audio Codec)
 A....D libmp3lame           libmp3lame MP3 (codec mp3)
 A....D aac                  AAC (Advanced Audio Coding)
 A....D libopus              libopus Opus (codec opus)
 A....D libvorbis            libvorbis (codec vorbis)"""

args = sys.argv[1:]

# Capability probes come first: omacap asks what this build can do before it
# records anything. Without these the stub would treat "-encoders" as an output
# path and record into it until the probe timed out.
if "-encoders" in args:
    print(ENCODER_LISTING)
    sys.exit(0)
if "null" in args:
    sys.exit(0)          # the level-meter probe

out = Path(args[-1])
duration = None
if "-t" in args:
    duration = float(args[args.index("-t") + 1])
metering = any("ametadata" in a for a in args)

stop = False
def on_signal(signum, frame):
    global stop
    stop = True
signal.signal(signal.SIGINT, on_signal)
signal.signal(signal.SIGTERM, on_signal)

out.parent.mkdir(parents=True, exist_ok=True)
handle = out.open("wb")
handle.write(b"HEADER--")
handle.flush()

elapsed = 0.0
step = 0.05
written = 8
while not stop and (duration is None or elapsed < duration):
    time.sleep(step)
    elapsed += step
    handle.write(b"\0" * 480)
    handle.flush()
    written += 480
    print(f"out_time_us={int(elapsed * 1_000_000)}", flush=True)
    print(f"total_size={written}", flush=True)
    if metering:
        level = "-inf" if int(elapsed * 10) % 20 < 5 else f"{-30 + elapsed:.6f}"
        print(
            f"[Parsed_ametadata_1 @ 0x0] lavfi.astats.Overall.Peak_level={level}",
            file=sys.stderr, flush=True,
        )

handle.write(b"TRAILER!")
handle.close()
print("progress=end", flush=True)
sys.exit(255 if stop else 0)
'''

#: Refuses to start, the way ffmpeg does for a bad source.
FAILING_FFMPEG = r'''#!/usr/bin/env python3
import sys
ENCODER_LISTING = """Encoders:
 ------
 A....D pcm_s16le            PCM signed 16-bit little-endian
 A....D flac                 FLAC (Free Lossless Audio Codec)
 A....D libmp3lame           libmp3lame MP3 (codec mp3)
 A....D aac                  AAC (Advanced Audio Coding)
 A....D libopus              libopus Opus (codec opus)
 A....D libvorbis            libvorbis (codec vorbis)"""
args = sys.argv[1:]
# Answer the capability probes, then fail at the actual recording.
if "-encoders" in args:
    print(ENCODER_LISTING)
    sys.exit(0)
if "null" in args:
    sys.exit(0)
print("[pulse @ 0x0] Cannot connect to server: Connection refused", file=sys.stderr)
sys.exit(1)
'''


def _install(directory: Path, name: str, body: str) -> Path:
    path = directory / name
    path.write_text(body.replace("#!/usr/bin/env python3", f"#!{sys.executable}"))
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


def _reset_capability_caches() -> None:
    """Capability probes are cached per process; each test needs a clean slate."""
    from omacap import recorder

    recorder.available_encoders.cache_clear()
    recorder.metering_supported.cache_clear()


@pytest.fixture(autouse=True)
def fresh_capability_probes():
    _reset_capability_caches()
    yield
    _reset_capability_caches()


@pytest.fixture
def fake_ffmpeg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    bindir = tmp_path / "bin"
    bindir.mkdir()
    path = _install(bindir, "ffmpeg", FAKE_FFMPEG)
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")
    _reset_capability_caches()
    return path


#: Mimics an ffmpeg that lists encoders but rejects the meter's filter chain,
#: the way an older build does.
NO_METER_FFMPEG = r'''#!/usr/bin/env python3
import subprocess, sys
args = sys.argv[1:]
if any("astats" in a for a in args):
    print("Error applying option 'measure_overall' to filter 'astats': "
          "Option not found", file=sys.stderr)
    sys.exit(8)
sys.exit(subprocess.call([REAL_FAKE] + args))
'''

#: Mimics a minimal build with no MP3 encoder.
NO_MP3_ENCODERS = """Encoders:
 V..... = Video
 ------
 A....D pcm_s16le            PCM signed 16-bit little-endian
 A....D flac                 FLAC (Free Lossless Audio Codec)
 A....D aac                  AAC (Advanced Audio Coding)
"""


@pytest.fixture
def ffmpeg_without_meter(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An ffmpeg that records fine but cannot run the level meter."""
    bindir = tmp_path / "nometer"
    bindir.mkdir()
    inner = _install(bindir, "ffmpeg-real", FAKE_FFMPEG)
    body = NO_METER_FFMPEG.replace("REAL_FAKE", repr(str(inner)))
    path = _install(bindir, "ffmpeg", body)
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")
    _reset_capability_caches()
    return path


@pytest.fixture
def failing_ffmpeg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    bindir = tmp_path / "badbin"
    bindir.mkdir()
    path = _install(bindir, "ffmpeg", FAILING_FFMPEG)
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")
    return path


@pytest.fixture
def no_ffmpeg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    empty = tmp_path / "emptybin"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))


@pytest.fixture
def monitor_source():
    from omacap.devices import Source

    return Source(
        name="alsa_output.test.monitor",
        description="Monitor of Test Output",
        is_monitor=True,
    )
