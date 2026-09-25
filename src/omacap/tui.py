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

from . import capture, ui
from .analysis import analysis_available
from .chart import CHART_FORMATS, default_chart_path, write_chart
from .devices import AudioSystemError, Source, list_monitors, resolve_source
from .formats import FORMATS, AudioFormat, get_format, next_format
from .updater import notice_line, pending_update
from .recorder import (
    METER_FLOOR_DB,
    Recorder,
    format_is_available,
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
        split_options: capture.SplitOptions | None = None,
        stop_after_silence: float = 0.0,
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
        self.analyse_prompt: str | None = None
        self.split_prompt: str | None = None
        self.update_notice: str = ""
        self.split_options = split_options or capture.SplitOptions()
        self.stop_after_silence = stop_after_silence
        self.player: str | None = None
        self.session: capture.TrackSession | None = None
        self._stopper = capture.SilenceStopper()
        self._pending: tuple = ()          # (result, silences, changes)
        self._pieces: list[Path] = []
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
            clipping=bool(recorder is not None and active and recorder.clipping),
            next_name=self.next_name,
            analyse_prompt=self.analyse_prompt,
            split_prompt=self.split_prompt,
            track_count=self.session.track_count if self.session else 0,
            player_label=self._player_label(),
            update_notice=self.update_notice,
            chart_format=self.chart_format,
            can_analyse=self.last_recording is not None,
            recordings=self.recordings,
            message=self.message,
            message_kind=self.message_kind,
            show_help=self.show_help,
            name_prompt=self.name_prompt,
        )

    # -- actions -----------------------------------------------------------

    def _player_label(self) -> str:
        """What the followed player is playing, for the panel."""
        if not self.player:
            return ""
        from . import nowplaying

        if self.session is not None and self.session.changes:
            return self.session.changes[-1].track.label
        track = nowplaying.current_track(self.player)
        return track.label if track else ""

    def refresh_update_notice(self) -> None:
        """Look up whether a newer omacap is waiting. Never raises, never blocks."""
        try:
            status = pending_update()
        except Exception:
            self.update_notice = ""
            return
        self.update_notice = notice_line(status) if status else ""

    def notify(self, message: str, kind: str = "info") -> None:
        self.message = message
        self.message_kind = kind

    def toggle_recording(self) -> None:
        if self.recorder is not None and self.recorder.is_running:
            self.stop_recording()
        else:
            self.start_recording()

    def start_recording(self) -> None:
        self.analyse_prompt = None
        self.split_prompt = None
        self._pieces = []
        # Look again now rather than trusting what was running at startup: people
        # open omacap first and start the music afterwards.
        if self.player is None:
            self.player = capture.choose_player(self.split_options.player)
        path = build_output_path(self.output_dir, self.audio_format, self.next_name)
        config = RecorderConfig(
            source=self.source,
            audio_format=self.audio_format,
            output_path=path,
            bitrate=self.bitrate if self.audio_format.supports_bitrate else None,
            detect_silence=True,
        )
        recorder = Recorder(config)
        session = capture.TrackSession(recorder, self.player)
        try:
            session.start()
        except RecorderError as exc:
            self.notify(str(exc), "error")
            return
        self.session = session
        self._stopper = capture.SilenceStopper(self.stop_after_silence)
        self.recorder = recorder
        self.meter_db = METER_FLOOR_DB
        self.notify(f"Recording to {path.name}", "info")

    def stop_recording(self) -> None:
        recorder = self.recorder
        if recorder is None:
            return
        self.notify("Finishing file…")
        self.draw(force=True)
        # Read the gaps before stopping: the recorder closes an unfinished one at
        # whatever the duration is when asked.
        silences = recorder.silences
        changes = self.session.changes if self.session is not None else []
        result = self.session.stop() if self.session is not None else recorder.stop()
        self.session = None
        self.meter_db = METER_FLOOR_DB
        if recorder.error:
            self.notify(recorder.error, "error")
        else:
            self.last_recording = result.path
            self.recordings.append(
                f"{result.path.name}   {ui.format_duration(result.duration)}"
                f"   {ui.format_size(result.size_bytes)}"
            )
            if result.clipped:
                self.notify(
                    f"Saved {result.path.name}, but it clipped — "
                    f"peaked at {result.peak_db:.1f} dB. Lower the "
                    f"application's volume, not the speakers.",
                    "error",
                )
            else:
                self.notify(
                    f"Saved {result.path.name}"
                    f" ({ui.format_duration(result.duration)},"
                    f" {ui.format_size(result.size_bytes)})",
                    "success",
                )
            self._pending = (result, silences, changes)
            tracks = len({c.track.identity for c in changes})
            if tracks >= 2:
                self.split_prompt = (
                    f"{tracks} tracks in {ui.format_duration(result.duration)}"
                )
            elif analysis_available():
                self.analyse_prompt = self._analyse_offer(result)
        self.recorder = None
        self.next_name = None

    def _analyse_offer(self, result) -> str:
        return (
            f"{result.path.name}   "
            f"{ui.format_duration(result.duration)}   "
            f"{ui.format_size(result.size_bytes)}"
        )

    def answer_split_prompt(self, key: str) -> bool:
        """Handle the question asked when several tracks were recorded."""
        if key in ("y", "Y", "\r", "\n"):
            self.split_prompt = None
            self.split_last()
            return True
        self.split_prompt = None
        if key in ("n", "N", "\x1b"):
            self.notify("Kept as one file.")
            self._offer_analysis()
            return True
        return False

    def split_last(self) -> None:
        """Cut the recording just made into one file per track."""
        self.split_prompt = None
        if not self._pending:
            return
        result, silences, changes = self._pending
        self.notify("Splitting…")
        self.draw(force=True)
        options = capture.SplitOptions(
            enabled=True,
            min_gap=self.split_options.min_gap,
            min_track=self.split_options.min_track,
            pad=self.split_options.pad,
            directory=result.path.parent,
        )
        try:
            outcome = capture.split_recording(
                result, silences, changes, options, self.audio_format
            )
        except Exception as exc:
            self.notify(str(exc), "error")
            return
        if not outcome.happened:
            self.notify(f"Not split: {outcome.reason}")
        else:
            self._pieces = list(outcome.written)
            for path in outcome.written:
                self.recordings.append(f"  {path.name}")
            self.notify(
                f"Split into {len(outcome.written)} files, "
                f"named from {'the player' if outcome.named else 'numbering'}.",
                "success",
            )
        self._offer_analysis()

    def _offer_analysis(self) -> None:
        if not analysis_available() or not self._pending:
            return
        if self._pieces:
            self.analyse_prompt = f"{len(self._pieces)} files just split out"
        else:
            self.analyse_prompt = self._analyse_offer(self._pending[0])

    def answer_analyse_prompt(self, key: str) -> bool:
        """Handle the question asked after a take. True when the key was used."""
        if key in ("y", "Y", "\r", "\n"):
            self.analyse_prompt = None
            self.analyse_last()
            return True
        self.analyse_prompt = None
        if key in ("n", "N", "\x1b"):
            self.notify("Not analysed. Press a at any time to do it later.")
            return True
        return False        # any other key falls through to its normal action

    def analyse_last(self) -> None:
        """Write a chord chart for the most recent recording, or for its pieces."""
        self.analyse_prompt = None
        if self.busy("Stop recording before analysing."):
            return
        if self._pieces:
            self.analyse_pieces()
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

    def analyse_pieces(self) -> None:
        """Chart each file a split produced. One failure keeps the rest."""
        from .analysis.report import analyse_file

        charted = 0
        for path in self._pieces:
            self.notify(f"Analysing {path.name}…")
            self.draw(force=True)
            try:
                analysis = analyse_file(path)
                target = write_chart(
                    analysis,
                    default_chart_path(path, self.chart_format),
                    self.chart_format,
                )
            except Exception:
                continue
            charted += 1
            self.recordings.append(
                f"  {target.name}   {analysis.key.short_name}"
                f"   {analysis.tempo:.0f} BPM   {analysis.bar_count} bars"
            )
        if charted:
            self.notify(f"Charted {charted} of {len(self._pieces)} files.", "success")
        else:
            self.notify("None of the pieces could be charted.", "error")

    def cycle_chart_format(self) -> None:
        index = CHART_FORMATS.index(self.chart_format)
        self.chart_format = CHART_FORMATS[(index + 1) % len(CHART_FORMATS)]
        self.notify(f"Charts will be written as .{self.chart_format}.")

    def cycle_format(self, step: int = 1) -> None:
        if self.busy("Stop recording before changing the format."):
            return
        # Skip anything this ffmpeg build cannot encode, so the key never lands
        # on a format that would refuse to record.
        candidate = next_format(self.audio_format.name, step)
        for _ in range(len(FORMATS)):
            if format_is_available(candidate):
                break
            candidate = next_format(candidate.name, step)
        else:
            self.notify("No usable output formats in this ffmpeg build.", "error")
            return
        self.audio_format = candidate
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
        if self.split_prompt is not None and self.answer_split_prompt(key):
            return
        if self.analyse_prompt is not None and self.answer_analyse_prompt(key):
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
        if self.player is None:
            self.player = capture.choose_player(self.split_options.player)
        self.refresh_update_notice()
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
            if recorder is not None and recorder.is_running:
                if self._stopper.should_stop(recorder):
                    self.notify(
                        f"Silent for {self.stop_after_silence:g}s \u2014 stopping."
                    )
                    self.stop_recording()
                    continue
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
    split_options: capture.SplitOptions | None = None,
    stop_after_silence: float = 0.0,
) -> int:
    """Entry point used by the CLI."""
    audio_format = get_format(format_name)
    source = resolve_source(source_name)
    app = TuiApp(
        source=source,
        audio_format=audio_format,
        output_dir=output_dir or default_output_dir(),
        bitrate=bitrate,
        split_options=split_options,
        stop_after_silence=stop_after_silence,
    )
    signal.signal(signal.SIGINT, signal.default_int_handler)
    return app.run()
