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

args = sys.argv[1:]
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
print("[pulse @ 0x0] Cannot connect to server: Connection refused", file=sys.stderr)
sys.exit(1)
'''


def _install(directory: Path, name: str, body: str) -> Path:
    path = directory / name
    path.write_text(body.replace("#!/usr/bin/env python3", f"#!{sys.executable}"))
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


@pytest.fixture
def fake_ffmpeg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    bindir = tmp_path / "bin"
    bindir.mkdir()
    path = _install(bindir, "ffmpeg", FAKE_FFMPEG)
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")
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
