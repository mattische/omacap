#!/usr/bin/env python3
"""Live end-to-end check against the real sound card.

pytest covers everything with a stub ffmpeg and synthesised audio, which is fast
and reproducible but never touches the audio server. This script does the part
that cannot be faked: it plays audio out of the default sink, records it back
through the monitor source, drives the real TUI over a pty, and analyses the
result.

Run it by hand after changing anything about recording, the TUI or the audio
plumbing, and on any new machine::

    python tools/live_check.py                    # uses a generated test tone
    python tools/live_check.py --music song.mp3   # uses real music
    python tools/live_check.py --keep             # keep the recordings

It makes sound. Playback is attenuated, but it is audible.
"""

from __future__ import annotations

import argparse
import fcntl
import os
import re
import select
import shutil
import struct
import subprocess
import sys
import tempfile
import termios
import time
from pathlib import Path

ANSI = re.compile(r"\x1b\[[0-9;?]*[a-zA-Z]")
REPO_ROOT = Path(__file__).resolve().parent.parent

class Checks:
    def __init__(self) -> None:
        self.results: list[tuple[str, bool]] = []

    def add(self, label: str, ok: bool, detail: str = "") -> bool:
        self.results.append((label, ok))
        mark = "PASS" if ok else "FAIL"
        print(f"  [{mark}] {label}" + (f"  {detail}" if detail else ""), flush=True)
        return ok

    @property
    def failed(self) -> list[str]:
        return [label for label, ok in self.results if not ok]

    def report(self) -> int:
        passed = len(self.results) - len(self.failed)
        print(f"\n{passed}/{len(self.results)} checks passed")
        for label in self.failed:
            print(f"  failed: {label}")
        return 1 if self.failed else 0


def audio_env() -> dict[str, str]:
    env = dict(os.environ)
    env.setdefault("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    env["TERM"] = "xterm-256color"
    return env


def default_sink(env) -> str:
    return subprocess.run(
        ["pactl", "get-default-sink"], capture_output=True, text=True, env=env
    ).stdout.strip()


def play(source: Path | None, seconds: float, env, volume: float = 0.3):
    """Start playback into the default sink and return the process."""
    sink = default_sink(env)
    if source is not None:
        command = ["ffmpeg", "-hide_banner", "-loglevel", "error",
                   "-ss", "30", "-t", f"{seconds:g}", "-i", str(source),
                   "-af", f"volume={volume}"]
    else:
        command = ["ffmpeg", "-hide_banner", "-loglevel", "error",
                   "-filter_complex", tone_filter(seconds, volume), "-map", "[out]"]
    command += ["-f", "pulse", "-device", sink, "default"]
    return subprocess.Popen(command, env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def tone_filter(seconds: float, volume: float = 0.3) -> str:
    """A filter graph playing C - G - Am - F at 120 BPM, four beats to the bar.

    Each beat is struck rather than sustained, with a click on top that is louder
    on the downbeat. Sustained chords would have no onsets at all, and the
    analysis needs onsets to find a tempo, let alone a time signature.
    """
    beat = 0.5                                 # 120 BPM
    beats_per_bar = 4
    bars = [
        (261.63, 329.63, 392.00),              # C major
        (392.00, 493.88, 587.33),              # G major
        (440.00, 523.25, 659.26),              # A minor
        (349.23, 440.00, 523.25),              # F major
    ]
    cycle = beat * beats_per_bar * len(bars)
    cycles = max(1, round(seconds / cycle))

    parts: list[str] = []
    beat_labels: list[str] = []
    index = 0
    for _ in range(cycles):
        for chord in bars:
            for position in range(beats_per_bar):
                downbeat = position == 0
                voices = []
                for note, frequency in enumerate(chord):
                    tag = f"v{index}_{note}"
                    parts.append(
                        f"sine=frequency={frequency}:duration={beat}"
                        f",afade=t=out:st=0.08:d={beat - 0.08:g}:curve=exp[{tag}]"
                    )
                    voices.append(f"[{tag}]")
                click_tag = f"c{index}"
                click_hz = 1500 if downbeat else 950
                click_gain = 0.9 if downbeat else 0.4
                parts.append(
                    f"sine=frequency={click_hz}:duration=0.06"
                    f",afade=t=out:st=0:d=0.06,volume={click_gain}"
                    f",apad=whole_dur={beat}[{click_tag}]"
                )
                voices.append(f"[{click_tag}]")
                label = f"b{index}"
                parts.append(
                    f"{''.join(voices)}amix=inputs={len(voices)}:normalize=0[{label}]"
                )
                beat_labels.append(f"[{label}]")
                index += 1

    parts.append(f"{''.join(beat_labels)}concat=n={len(beat_labels)}:v=0:a=1[joined]")
    parts.append(f"[joined]volume={volume},atrim=duration={seconds:g}[out]")
    return ";".join(parts)


def peak_dbfs(path: Path) -> float:
    """Peak level of a file, for telling real audio from silence."""
    result = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", "-i", str(path),
         "-af", "volumedetect", "-f", "null", os.devnull],
        capture_output=True, text=True,
    )
    match = re.search(r"max_volume: (-?[\d.]+) dB", result.stderr)
    return float(match.group(1)) if match else -999.0


def omacap_command() -> list[str]:
    """How to invoke omacap: the installed entry point, or the local source."""
    if shutil.which("omacap"):
        return ["omacap"]
    return [sys.executable, "-m", "omacap"]


# -- phase 1: the CLI ------------------------------------------------------

def check_cli(checks: Checks, out_dir: Path, music: Path | None, env) -> None:
    print("\n== CLI ==")
    base = omacap_command()

    doctor = subprocess.run([*base, "doctor"], capture_output=True, text=True, env=env)
    checks.add("doctor reports a usable installation", doctor.returncode == 0,
               doctor.stdout.strip().splitlines()[-1] if doctor.stdout else "")
    for line in doctor.stdout.splitlines():
        if line.startswith("[--]"):
            print(f"         note: {line}")

    player = play(music, 6.0, env)
    record = subprocess.run(
        [*base, "record", "-d", "7", "-f", "flac", "-D", str(out_dir), "-n", "live cli"],
        capture_output=True, text=True, env=env,
    )
    player.wait()
    written = out_dir / "live cli.flac"
    checks.add("record wrote a file", record.returncode == 0 and written.is_file(),
               record.stderr.strip()[:120] if record.returncode else written.name)
    if written.is_file():
        peak = peak_dbfs(written)
        checks.add("the recording contains real audio, not silence", peak > -40.0,
                   f"peak {peak:.1f} dBFS")


# -- phase 2: the TUI over a pty ------------------------------------------

class Tui:
    """Drives the real interface through a pseudo-terminal."""

    def __init__(self, out_dir: Path, env, columns: int = 78, rows: int = 32) -> None:
        import pty

        self.master, slave = pty.openpty()
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", rows, columns, 0, 0))
        self.process = subprocess.Popen(
            [*omacap_command(), "-D", str(out_dir), "-f", "wav"],
            stdin=slave, stdout=slave, stderr=slave, env=env, close_fds=True,
        )
        os.close(slave)
        self.buffer = b""

    def pump(self, seconds: float) -> None:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            ready, _, _ = select.select([self.master], [], [], 0.1)
            if ready:
                try:
                    self.buffer += os.read(self.master, 65536)
                except OSError:
                    return

    def send(self, keys: str) -> None:
        os.write(self.master, keys.encode())

    def screen(self) -> str:
        text = ANSI.sub("", self.buffer.decode("utf-8", "ignore"))
        chunks = text.split("┌ omacap")
        return ("┌ omacap" + chunks[-1]) if len(chunks) > 1 else text

    def quit(self, timeout: float = 8.0) -> int:
        self.send("q")
        self.pump(2.0)
        try:
            return self.process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            self.process.kill()
            return self.process.wait()


def check_tui(checks: Checks, out_dir: Path, music: Path | None, env,
              analyse: bool) -> None:
    print("\n== TUI ==")
    tui = Tui(out_dir, env)
    tui.pump(1.5)
    checks.add("interface renders and is ready", "READY" in tui.screen())

    tui.send("?")
    tui.pump(0.6)
    checks.add("help opens", "start or stop recording" in tui.screen())
    tui.send("?")
    tui.pump(0.6)

    tui.send("f")
    tui.pump(0.5)
    cycled = tui.screen()
    tui.send("F")
    tui.pump(0.5)
    checks.add("format cycles forward and back",
               "Format set to" in cycled and "wav" in tui.screen())

    tui.send("n")
    tui.pump(0.5)
    tui.send("live tui")
    tui.pump(0.3)
    tui.send("\r")
    tui.pump(0.5)
    checks.add("the next take can be named", "live tui" in tui.screen())

    print("  -- recording; audio will play")
    tui.send(" ")
    tui.pump(1.0)
    checks.add("recording started", "REC" in tui.screen())
    player = play(music, 16.0, env)
    tui.pump(4.0)
    metered = re.search(r"-\s?\d+\.\d dB", tui.screen())
    checks.add("the level meter shows live audio", metered is not None,
               metered.group(0) if metered else "meter stayed silent")
    player.wait()
    tui.pump(1.0)
    tui.send(" ")
    tui.pump(3.0)
    checks.add("the take was saved", "Saved" in tui.screen())

    if analyse:
        checks.add("the interface offers to analyse the take",
                   "analyse this recording?" in tui.screen())
        print("  -- answering yes; analysing takes a moment")
        tui.send("y")
        tui.pump(30.0)
        screen = tui.screen()
        detail = (screen.split("Chart written to")[-1].split("│")[0].strip()
                  if "Chart written to" in screen else "")
        checks.add("a chord chart was written from the TUI",
                   "Chart written to" in screen, detail)

    code = tui.quit()
    checks.add("quits cleanly", code == 0, f"exit {code}")

    takes = sorted(out_dir.glob("live tui*.wav"))
    checks.add("the recording is on disk", bool(takes) and takes[0].stat().st_size > 50_000,
               f"{takes[0].stat().st_size // 1024} KB" if takes else "missing")
    if takes:
        peak = peak_dbfs(takes[0])
        checks.add("the TUI recording contains real audio", peak > -40.0,
                   f"peak {peak:.1f} dBFS")
    if analyse:
        charts = sorted(out_dir.glob("live tui*.md"))
        checks.add("the chart file exists", bool(charts),
                   charts[0].name if charts else "missing")
        if charts:
            text = charts[0].read_text(encoding="utf-8")
            missing = [f for f in ("Key", "Tempo", "Time signature", "Bars")
                       if f not in text]
            checks.add("the chart reports every detected fact", not missing,
                       f"missing {missing}" if missing else "")
            print("\n--- chart ---")
            print("\n".join(text.splitlines()[:22]))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--music", type=Path,
                        help="audio file to play; a generated chord progression by default")
    parser.add_argument("--skip-analysis", action="store_true",
                        help="skip the chord-chart checks (they need numpy)")
    parser.add_argument("--keep", action="store_true", help="keep the recordings")
    args = parser.parse_args()

    if args.music is not None and not args.music.is_file():
        print(f"live_check: no such file: {args.music}", file=sys.stderr)
        return 2
    for tool in ("ffmpeg", "pactl"):
        if shutil.which(tool) is None:
            print(f"live_check: {tool} is required", file=sys.stderr)
            return 2

    env = audio_env()
    env["PYTHONPATH"] = str(REPO_ROOT / "src") + os.pathsep + env.get("PYTHONPATH", "")
    out_dir = Path(tempfile.mkdtemp(prefix="omacap-live-"))
    print(f"omacap live check\noutput: {out_dir}")
    print(f"source: {args.music or 'generated chord progression'}")

    checks = Checks()
    try:
        check_cli(checks, out_dir, args.music, env)
        check_tui(checks, out_dir, args.music, env, analyse=not args.skip_analysis)
    finally:
        if args.keep:
            print(f"\nrecordings kept in {out_dir}")
        else:
            shutil.rmtree(out_dir, ignore_errors=True)
    return checks.report()


if __name__ == "__main__":
    sys.exit(main())
