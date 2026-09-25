"""The interactive terminal interface."""

from __future__ import annotations

import os
import select
import shutil
import signal
import sys
import termios
import time
import tty
from pathlib import Path

from . import ui
from .chart import CHART_FORMATS, default_chart_path, write_chart
from .devices import AudioSystemError, Source, list_monitors, resolve_source
from .formats import AudioFormat, get_format, next_format
from .recorder import (
    METER_FLOOR_DB,
    Recorder,
    RecorderConfig,
    RecorderError,
    build_output_path,
    default_output_dir,
    ensure_ffmpeg,
    sanitize_basename,
)

FRAME_SECONDS = 1 / 15
#: How fast the meter falls back towards silence, in dB per second.
METER_DECAY_DB_PER_SEC = 90.0

BITRATE_CHOICES: tuple[str, ...] = ("96k", "128k", "160k", "192k", "256k", "320k")

ENTER_ALT_SCREEN = "\x1b[?1049h"
LEAVE_ALT_SCREEN = "\x1b[?1049l"
HIDE_CURSOR = "\x1b[?25l"
SHOW_CURSOR = "\x1b[?25h"
CLEAR_SCREEN = "\x1b[2J"
HOME = "\x1b[H"
CLEAR_TO_EOL = "\x1b[K"


class TuiApp:
    """Owns the terminal, the key loop and the current recorder."""

    def __init__(
        self,
        source: Source,
        audio_format: AudioFormat,
        output_dir: Path,
        bitrate: str | None = None,
    ) -> None:
        self.sources: list[Source] = []
        self.source = source
        self.audio_format = audio_format
        self.output_dir = output_dir
        self.bitrate = bitrate or audio_format.default_bitrate
        self.recorder: Recorder | None = None
        self.last_recording: Path | None = None
        self.chart_format: str = CHART_FORMATS[0]
        self.next_name: str | None = None
        self.name_prompt: str | None = None
        self.recordings: list[str] = []
        self.message = "Press space to start recording."
        self.message_kind = "info"
        self.show_help = False
        self.meter_db = METER_FLOOR_DB
        self.running = True
        self._last_frame = 0.0
        self._rendered: list[str] = []

    # -- view --------------------------------------------------------------

    @property
    def state(self) -> str:
        if self.recorder is None:
            return "idle"
        return self.recorder.state.value if self.recorder.is_running else "idle"

    def view_model(self) -> ui.ViewModel:
        recorder = self.recorder
        active = recorder is not None and recorder.is_running
        return ui.ViewModel(
            source_label=self.source.label,
            format_name=self.audio_format.name,
            format_description=self.audio_format.description,
            output_dir=shorten_home(self.output_dir),
            state="recording" if active else "idle",
            bitrate=self.bitrate if self.audio_format.supports_bitrate else None,
            elapsed=recorder.duration if active else 0.0,
            size_bytes=recorder.size_bytes if active else 0,
            meter_db=self.meter_db,
            next_name=self.next_name,
            chart_format=self.chart_format,
            can_analyse=self.last_recording is not None,
            recordings=self.recordings,
            message=self.message,
            message_kind=self.message_kind,
            show_help=self.show_help,
            name_prompt=self.name_prompt,
        )

    # -- actions -----------------------------------------------------------

    def notify(self, message: str, kind: str = "info") -> None:
        self.message = message
        self.message_kind = kind

    def toggle_recording(self) -> None:
        if self.recorder is not None and self.recorder.is_running:
            self.stop_recording()
        else:
            self.start_recording()

    def start_recording(self) -> None:
        path = build_output_path(self.output_dir, self.audio_format, self.next_name)
        config = RecorderConfig(
            source=self.source,
            audio_format=self.audio_format,
            output_path=path,
            bitrate=self.bitrate if self.audio_format.supports_bitrate else None,
        )
        recorder = Recorder(config)
        try:
            recorder.start()
        except RecorderError as exc:
            self.notify(str(exc), "error")
            return
        self.recorder = recorder
        self.meter_db = METER_FLOOR_DB
        self.notify(f"Recording to {path.name}", "info")

    def stop_recording(self) -> None:
        recorder = self.recorder
        if recorder is None:
            return
        self.notify("Finishing file…")
        self.draw(force=True)
        result = recorder.stop()
        self.meter_db = METER_FLOOR_DB
        if recorder.error:
            self.notify(recorder.error, "error")
        else:
            self.last_recording = result.path
            self.recordings.append(
                f"{result.path.name}   {ui.format_duration(result.duration)}"
                f"   {ui.format_size(result.size_bytes)}"
            )
            self.notify(
                f"Saved {result.path.name}"
                f" ({ui.format_duration(result.duration)},"
                f" {ui.format_size(result.size_bytes)})",
                "success",
            )
        self.recorder = None
        self.next_name = None

    def analyse_last(self) -> None:
        """Write a chord chart for the most recent recording."""
        if self.busy("Stop recording before analysing."):
            return
        if self.last_recording is None or not self.last_recording.is_file():
            self.notify("Record something first, then press a to analyse it.", "error")
            return

        self.notify(f"Analysing {self.last_recording.name}\u2026")
        self.draw(force=True)
        try:
            from .analysis.report import analyse_file

            analysis = analyse_file(self.last_recording)
            target = write_chart(
                analysis,
                default_chart_path(self.last_recording, self.chart_format),
                self.chart_format,
            )
        except Exception as exc:  # surfaced in the interface, never a traceback
            self.notify(str(exc), "error")
            return
        self.recordings.append(
            f"{target.name}   {analysis.key.short_name}"
            f"   {analysis.tempo:.0f} BPM   {analysis.meter.name}"
            f"   {analysis.bar_count} bars"
        )
        self.notify(
            f"Chart written to {target.name}: {analysis.key.name},"
            f" {analysis.tempo:.0f} BPM, {analysis.meter.name},"
            f" {analysis.bar_count} bars",
            "success",
        )

    def cycle_chart_format(self) -> None:
        index = CHART_FORMATS.index(self.chart_format)
        self.chart_format = CHART_FORMATS[(index + 1) % len(CHART_FORMATS)]
        self.notify(f"Charts will be written as .{self.chart_format}.")

    def cycle_format(self, step: int = 1) -> None:
        if self.busy("Stop recording before changing the format."):
            return
        self.audio_format = next_format(self.audio_format.name, step)
        self.bitrate = self.audio_format.default_bitrate or self.bitrate
        self.notify(f"Format set to {self.audio_format.name}.")

    def cycle_bitrate(self) -> None:
        if self.busy("Stop recording before changing the bitrate."):
            return
        if not self.audio_format.supports_bitrate:
            self.notify(f"{self.audio_format.name} is lossless — no bitrate to set.")
            return
        current = self.bitrate or self.audio_format.default_bitrate
        try:
            index = BITRATE_CHOICES.index(current or "")
        except ValueError:
            index = -1
        self.bitrate = BITRATE_CHOICES[(index + 1) % len(BITRATE_CHOICES)]
        self.notify(f"Bitrate set to {self.bitrate}.")

    def cycle_source(self) -> None:
        if self.busy("Stop recording before changing the source."):
            return
        try:
            self.sources = list_monitors() or self.sources
        except AudioSystemError as exc:
            self.notify(str(exc), "error")
            return
        if not self.sources:
            self.notify("No monitor sources available.", "error")
            return
        names = [s.name for s in self.sources]
        index = names.index(self.source.name) if self.source.name in names else -1
        self.source = self.sources[(index + 1) % len(self.sources)]
        self.notify(f"Source set to {self.source.label}.")

    def busy(self, message: str) -> bool:
        if self.recorder is not None and self.recorder.is_running:
            self.notify(message, "error")
            return True
        return False

    # -- key handling ------------------------------------------------------

    def handle_key(self, key: str) -> None:
        if self.name_prompt is not None:
            self.handle_name_key(key)
            return
        if key in ("q", "\x03", "\x04"):
            self.running = False
        elif key in (" ", "r", "\r", "\n"):
            self.toggle_recording()
        elif key == "s":
            self.stop_recording()
        elif key == "f":
            self.cycle_format(1)
        elif key == "F":
            self.cycle_format(-1)
        elif key == "b":
            self.cycle_bitrate()
        elif key == "d":
            self.cycle_source()
        elif key == "a":
            self.analyse_last()
        elif key == "t":
            self.cycle_chart_format()
        elif key == "n":
            if not self.busy("Stop recording before naming the next file."):
                self.name_prompt = self.next_name or ""
        elif key in ("?", "h"):
            self.show_help = not self.show_help

    def handle_name_key(self, key: str) -> None:
        assert self.name_prompt is not None
        if key in ("\r", "\n"):
            name = self.name_prompt.strip()
            self.next_name = sanitize_basename(name) if name else None
            self.name_prompt = None
            self.notify(
                f"Next recording will be called {self.next_name}."
                if self.next_name
                else "Using automatic timestamped names."
            )
        elif key == "\x1b":
            self.name_prompt = None
            self.notify("Name unchanged.")
        elif key in ("\x7f", "\b"):
            self.name_prompt = self.name_prompt[:-1]
        elif key == "\x03":
            self.name_prompt = None
            self.running = False
        elif key.isprintable():
            self.name_prompt = (self.name_prompt + key)[:60]

    # -- meter -------------------------------------------------------------

    def update_meter(self, dt: float) -> None:
        recorder = self.recorder
        if recorder is None or not recorder.is_running:
            self.meter_db = METER_FLOOR_DB
            return
        target = recorder.peak_db
        if target >= self.meter_db:
            self.meter_db = target
        else:
            self.meter_db = max(target, self.meter_db - METER_DECAY_DB_PER_SEC * dt)

    # -- drawing -----------------------------------------------------------

    def draw(self, force: bool = False) -> None:
        width = shutil.get_terminal_size(fallback=(72, 24)).columns
        lines = ui.render(self.view_model(), width, ui.colors_enabled(sys.stdout.isatty()))
        if not force and lines == self._rendered:
            return
        out = [HOME]
        for line in lines:
            out.append(line + CLEAR_TO_EOL + "\r\n")
        out.append("\x1b[J")
        sys.stdout.write("".join(out))
        sys.stdout.flush()
        self._rendered = lines

    # -- main loop ---------------------------------------------------------

    def run(self) -> int:
        ensure_ffmpeg()
        try:
            self.sources = list_monitors()
        except AudioSystemError as exc:
            self.notify(str(exc), "error")
        with raw_terminal():
            sys.stdout.write(ENTER_ALT_SCREEN + HIDE_CURSOR + CLEAR_SCREEN)
            sys.stdout.flush()
            try:
                self._loop()
            finally:
                if self.recorder is not None and self.recorder.is_running:
                    self.recorder.stop()
                sys.stdout.write(SHOW_CURSOR + LEAVE_ALT_SCREEN)
                sys.stdout.flush()
        for entry in self.recordings:
            print(f"saved  {entry}")
        return 0

    def _loop(self) -> None:
        previous = time.monotonic()
        while self.running:
            now = time.monotonic()
            self.update_meter(now - previous)
            previous = now
            self.draw()
            for key in read_keys(FRAME_SECONDS):
                self.handle_key(key)
                if not self.running:
                    break
            recorder = self.recorder
            if recorder is not None and not recorder.is_running:
                # ffmpeg exited by itself, which usually means the source vanished.
                self.stop_recording()


class raw_terminal:
    """Context manager putting stdin in cbreak mode and restoring it after."""

    def __init__(self, stream: int | None = None) -> None:
        self.fd = stream if stream is not None else sys.stdin.fileno()
        self.saved: list | None = None

    def __enter__(self) -> "raw_terminal":
        try:
            self.saved = termios.tcgetattr(self.fd)
            tty.setcbreak(self.fd)
        except (termios.error, ValueError):
            self.saved = None
        return self

    def __exit__(self, *exc_info: object) -> None:
        if self.saved is not None:
            termios.tcsetattr(self.fd, termios.TCSADRAIN, self.saved)


def read_keys(timeout: float) -> list[str]:
    """Every keystroke waiting on stdin, blocking at most ``timeout`` seconds."""
    try:
        ready, _, _ = select.select([sys.stdin], [], [], timeout)
    except (OSError, ValueError):
        time.sleep(timeout)
        return []
    if not ready:
        return []
    try:
        data = os.read(sys.stdin.fileno(), 64).decode("utf-8", errors="ignore")
    except OSError:
        return []
    return list(data)


def shorten_home(path: Path) -> str:
    """``/home/me/x`` rendered as ``~/x``."""
    text = str(path)
    home = str(Path.home())
    return "~" + text[len(home):] if text.startswith(home) else text


def run_tui(
    source_name: str | None,
    format_name: str,
    output_dir: Path | None,
    bitrate: str | None,
) -> int:
    """Entry point used by the CLI."""
    audio_format = get_format(format_name)
    source = resolve_source(source_name)
    app = TuiApp(
        source=source,
        audio_format=audio_format,
        output_dir=output_dir or default_output_dir(),
        bitrate=bitrate,
    )
    signal.signal(signal.SIGINT, signal.default_int_handler)
    return app.run()
