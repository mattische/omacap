"""Command line entry point."""

from __future__ import annotations

import argparse
import shutil
import signal
import sys
import threading
from pathlib import Path

from . import __version__
from .devices import AudioSystemError, list_monitors, list_sources, resolve_source
from .formats import DEFAULT_FORMAT, FORMAT_NAMES, FORMATS, get_format
from .recorder import (
    Recorder,
    RecorderConfig,
    RecorderError,
    build_output_path,
    default_output_dir,
)
from .ui import format_duration, format_size

EPILOG = """\
examples:
  omacap                        launch the interactive recorder
  omacap --format wav           launch it with WAV pre-selected
  omacap record -d 30           record 30 seconds, then exit
  omacap record -o mix.flac     record to a specific file until Ctrl-C
  omacap devices                list the sources that carry playback audio
  omacap doctor                 check that everything needed is installed
"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="omacap",
        description="Record whatever your computer is playing.",
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"omacap {__version__}")
    _add_common(parser)
    subparsers = parser.add_subparsers(dest="command")

    record = subparsers.add_parser(
        "record",
        help="record without the interactive interface",
        description="Record straight away and stop on Ctrl-C or after --duration.",
    )
    _add_common(record, suppress=True)
    record.add_argument(
        "-o", "--output", metavar="FILE",
        help="exact output file; its extension picks the format",
    )
    record.add_argument(
        "-d", "--duration", type=float, metavar="SECONDS",
        help="stop automatically after this many seconds",
    )
    record.add_argument("-n", "--name", help="basename for the generated filename")
    record.add_argument("-q", "--quiet", action="store_true", help="only print the saved path")

    subparsers.add_parser("devices", help="list capture sources")
    subparsers.add_parser("formats", help="list output formats")
    subparsers.add_parser("doctor", help="check the installation")
    return parser


def _add_common(parser: argparse.ArgumentParser, suppress: bool = False) -> None:
    """Shared options.

    Subparsers use ``SUPPRESS`` as the default so that an option given before the
    subcommand (``omacap -f flac record``) is not clobbered by the subparser's
    own ``None`` default.
    """
    default = argparse.SUPPRESS if suppress else None
    parser.add_argument(
        "-f", "--format", default=default, metavar="FMT",
        help=f"output format: {', '.join(FORMAT_NAMES)} (default: {DEFAULT_FORMAT})",
    )
    parser.add_argument(
        "-s", "--source", default=default, metavar="NAME",
        help="capture source or sink name (default: the current output's monitor)",
    )
    parser.add_argument(
        "-D", "--dir", default=default, metavar="PATH",
        help="folder for recordings (default: ~/Recordings/omacap)",
    )
    parser.add_argument(
        "-b", "--bitrate", default=default, metavar="RATE",
        help="bitrate for lossy formats, e.g. 192k",
    )


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "devices":
            return cmd_devices()
        if args.command == "formats":
            return cmd_formats()
        if args.command == "doctor":
            return cmd_doctor()
        if args.command == "record":
            return cmd_record(args)
        return cmd_tui(args)
    except (AudioSystemError, RecorderError, ValueError) as exc:
        print(f"omacap: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


def cmd_tui(args: argparse.Namespace) -> int:
    from .tui import run_tui

    if not sys.stdin.isatty() or not sys.stdout.isatty():
        print(
            "omacap: the interactive interface needs a terminal. "
            "Use 'omacap record' instead.",
            file=sys.stderr,
        )
        return 1
    return run_tui(
        source_name=args.source,
        format_name=args.format or DEFAULT_FORMAT,
        output_dir=Path(args.dir).expanduser() if args.dir else None,
        bitrate=args.bitrate,
    )


def cmd_record(args: argparse.Namespace) -> int:
    source = resolve_source(args.source)
    if args.output:
        output_path = Path(args.output).expanduser()
        audio_format = get_format(args.format) if args.format else _format_for(output_path)
    else:
        audio_format = get_format(args.format or DEFAULT_FORMAT)
        directory = Path(args.dir).expanduser() if args.dir else default_output_dir()
        output_path = build_output_path(directory, audio_format, args.name)

    recorder = Recorder(
        RecorderConfig(
            source=source,
            audio_format=audio_format,
            output_path=output_path,
            bitrate=args.bitrate if audio_format.supports_bitrate else None,
            duration=args.duration,
            meter=False,
        )
    )
    if not args.quiet:
        print(f"source  {source.label}")
        print(f"format  {audio_format.name}")
        print(f"file    {output_path}")
        if args.duration:
            print(f"stops   after {args.duration:g}s")
        else:
            print("stops   on Ctrl-C")
    recorder.start()

    stop_requested = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: stop_requested.set())
    signal.signal(signal.SIGTERM, lambda *_: stop_requested.set())

    while recorder.is_running and not stop_requested.wait(0.2):
        pass
    result = recorder.stop() if recorder.is_running else recorder.wait()

    if recorder.error:
        print(f"omacap: {recorder.error}", file=sys.stderr)
        return 1
    if args.quiet:
        print(result.path)
    else:
        print(
            f"saved   {result.path} "
            f"({format_duration(result.duration)}, {format_size(result.size_bytes)})"
        )
    return 0


def _format_for(path: Path) -> object:
    try:
        return get_format(path.suffix)
    except ValueError as exc:
        raise ValueError(
            f"cannot tell the format from {path.name!r}; pass --format explicitly"
        ) from exc


def cmd_devices() -> int:
    monitors = list_monitors()
    default = None
    try:
        from .devices import default_monitor

        default = default_monitor().name
    except AudioSystemError:
        pass
    print("Playback monitors (these carry what the computer is playing):")
    if not monitors:
        print("  none found")
    for source in monitors:
        marker = "*" if source.name == default else " "
        print(f" {marker} {source.name}")
        if source.description:
            print(f"     {source.description}")
    others = [s for s in list_sources() if not s.is_monitor]
    if others:
        print("\nOther inputs (microphones and line inputs):")
        for source in others:
            print(f"   {source.name}")
    print("\n* = default. Pass any name with --source.")
    return 0


def cmd_formats() -> int:
    print("Output formats:")
    for fmt in FORMATS:
        kind = "lossless" if fmt.lossless else f"lossy, default {fmt.default_bitrate}"
        print(f"  {fmt.name:<5} {fmt.extension:<6} {kind}")
        print(f"        {fmt.description}")
    print("\n'mp4' is accepted as an alias for 'm4a'.")
    return 0


def cmd_doctor() -> int:
    ok = True
    print(f"omacap {__version__}\n")

    ffmpeg = shutil.which("ffmpeg")
    print(f"[{'ok' if ffmpeg else 'XX'}] ffmpeg    {ffmpeg or 'not found - install ffmpeg'}")
    ok &= bool(ffmpeg)

    pactl = shutil.which("pactl")
    print(f"[{'ok' if pactl else 'XX'}] pactl     {pactl or 'not found - install pulseaudio-utils or libpulse'}")
    ok &= bool(pactl)

    try:
        monitors = list_monitors()
        print(f"[{'ok' if monitors else 'XX'}] monitors  {len(monitors)} playback monitor(s) found")
        ok &= bool(monitors)
        from .devices import default_monitor

        print(f"[ok] default   {default_monitor().name}")
    except AudioSystemError as exc:
        print(f"[XX] audio     {exc}")
        ok = False

    directory = default_output_dir()
    writable = _can_write(directory)
    print(f"[{'ok' if writable else 'XX'}] folder    {directory} {'is writable' if writable else 'is not writable'}")
    ok &= writable

    print("\n" + ("Everything looks good." if ok else "Some checks failed - see above."))
    return 0 if ok else 1


def _can_write(directory: Path) -> bool:
    try:
        directory.mkdir(parents=True, exist_ok=True)
        probe = directory / ".omacap-write-test"
        probe.write_bytes(b"")
        probe.unlink()
        return True
    except OSError:
        return False


if __name__ == "__main__":
    sys.exit(main())
