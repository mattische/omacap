"""Command line entry point."""

from __future__ import annotations

import argparse
import shutil
import signal
import sys
import threading
from pathlib import Path

from . import __version__
from .analysis import AnalysisUnavailable
from .analysis.audio import DecodeError
from .analysis.report import AnalysisError
from .devices import AudioSystemError, list_monitors, list_sources, resolve_source
from .updater import (
    UpdateError,
    apply_update,
    check_now,
    find_installation,
    local_revision,
    notice_line,
    pending_update,
)
from .analysis.chords import DEFAULT_VOCABULARY, VOCABULARIES
from .formats import DEFAULT_FORMAT, FORMAT_NAMES, FORMATS, get_format
from .recorder import (
    Recorder,
    RecorderConfig,
    RecorderError,
    available_encoders,
    build_output_path,
    ensure_format,
    default_output_dir,
    format_is_available,
    metering_supported,
)
from .chart import (
    BARS_PER_LINE,
    default_chart_path,
    get_chart_format,
    render,
    write_chart,
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
  omacap analyze song.mp3       write a chord chart next to the recording
  omacap record -d 60 -A        record a minute, then chart it straight away
  omacap update                 install the latest version
"""


class _VersionAction(argparse.Action):
    """Prints the version with its git revision.

    A custom action rather than argparse's built-in one so the revision is only
    looked up when asked for, instead of on every single command.
    """

    def __call__(self, parser, namespace, values, option_string=None):
        print(version_string())
        parser.exit()


def version_string() -> str:
    installation = find_installation()
    revision = (
        local_revision(installation.checkout) if installation.checkout else ""
    )
    return f"omacap {__version__}" + (f" ({revision})" if revision else "")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="omacap",
        description="Record whatever your computer is playing.",
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--version", action=_VersionAction, nargs=0,
        help="show the version and the installed revision, then exit",
    )
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
    record.add_argument(
        "-A", "--analyze", "--analyse", dest="analyze", action="store_true",
        help="analyse the recording as soon as it stops and write a chord chart",
    )
    _add_analysis_options(record)

    analyze = subparsers.add_parser(
        "analyze",
        aliases=["analyse"],
        help="detect key, tempo, metre and chords, and write a chord chart",
        description=(
            "Analyse a recording and write a chord chart: time signature, key, "
            "tempo, bar count and the chords in each bar."
        ),
    )
    analyze.add_argument("file", help="audio file to analyse")
    analyze.add_argument(
        "-o", "--output", metavar="FILE",
        help="chart file to write (default: next to the recording)",
    )
    _add_analysis_options(analyze)
    analyze.add_argument(
        "-p", "--print", dest="to_stdout", action="store_true",
        help="print the chart instead of writing a file",
    )

    update = subparsers.add_parser(
        "update",
        help="update omacap to the latest version",
        description="Fetch and install the latest omacap from its git remote.",
    )
    update.add_argument(
        "--check", action="store_true",
        help="only report whether an update is available",
    )

    subparsers.add_parser("devices", help="list capture sources")
    subparsers.add_parser("formats", help="list output formats")
    subparsers.add_parser("doctor", help="check the installation")
    return parser


def _add_analysis_options(parser: argparse.ArgumentParser) -> None:
    """Options shared by ``analyze`` and by ``record --analyze``."""
    parser.add_argument(
        "-t", "--chart-format", default=None, metavar="FMT",
        choices=("md", "txt", "markdown", "text"),
        help="chart format: md or txt (default: md, or taken from --output)",
    )
    parser.add_argument(
        "-c", "--chords", default=DEFAULT_VOCABULARY, metavar="SET",
        choices=tuple(VOCABULARIES),
        help=(
            "chord vocabulary: simple (triads only), standard (adds sevenths) "
            f"or full (adds suspensions and diminished). Default: {DEFAULT_VOCABULARY}"
        ),
    )
    parser.add_argument(
        "--bars-per-line", type=int, default=BARS_PER_LINE, metavar="N",
        help=f"bars per line in the chart (default: {BARS_PER_LINE})",
    )


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
    if args.command != "update":
        print_update_notice()
    try:
        if args.command == "devices":
            return cmd_devices()
        if args.command == "formats":
            return cmd_formats()
        if args.command == "doctor":
            return cmd_doctor()
        if args.command == "record":
            return cmd_record(args)
        if args.command in ("analyze", "analyse"):
            return cmd_analyze(args)
        if args.command == "update":
            return cmd_update(args)
        return cmd_tui(args)
    except (AudioSystemError, RecorderError, AnalysisError, AnalysisUnavailable,
            DecodeError, ValueError) as exc:
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

    ensure_format(audio_format)
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

    if getattr(args, "analyze", False):
        return _analyse_recording(result.path, args, quiet=args.quiet)
    return 0


def _analyse_recording(path: Path, args: argparse.Namespace, quiet: bool = False) -> int:
    """Chart a recording that was just made. Shared by `record -A`."""
    from .analysis.report import analyse_file

    chart_format = (
        get_chart_format(args.chart_format) if args.chart_format else "md"
    )
    if not quiet:
        print("analysing…", flush=True)
    try:
        analysis = analyse_file(path, vocabulary=args.chords)
    except (AnalysisError, AnalysisUnavailable, DecodeError) as exc:
        # The recording itself is safe on disk; a failed chart must not lose it.
        print(f"omacap: the recording was saved, but analysis failed: {exc}",
              file=sys.stderr)
        return 1
    target = write_chart(
        analysis,
        default_chart_path(path, chart_format),
        chart_format,
        args.bars_per_line,
    )
    if quiet:
        print(target)
    else:
        _print_analysis_summary(analysis, target)
    return 0


def _print_analysis_summary(analysis, target: Path) -> None:
    print(f"key     {analysis.key.name} ({analysis.key.signature})")
    print(f"tempo   {analysis.tempo:.0f} BPM")
    print(f"metre   {analysis.meter.name}")
    print(f"bars    {analysis.bar_count}")
    print(f"chart   {target}")


def _format_for(path: Path) -> object:
    try:
        return get_format(path.suffix)
    except ValueError as exc:
        raise ValueError(
            f"cannot tell the format from {path.name!r}; pass --format explicitly"
        ) from exc


def cmd_analyze(args: argparse.Namespace) -> int:
    from .analysis.report import analyse_file

    source = Path(args.file).expanduser()
    if args.chart_format:
        chart_format = get_chart_format(args.chart_format)
    elif args.output:
        chart_format = _chart_format_for(Path(args.output))
    else:
        chart_format = "md"

    analysis = analyse_file(source, vocabulary=args.chords)
    text = render(analysis, chart_format, args.bars_per_line)

    if args.to_stdout:
        print(text)
        return 0

    target = Path(args.output).expanduser() if args.output else default_chart_path(
        source, chart_format
    )
    write_chart(analysis, target, chart_format, args.bars_per_line)
    _print_analysis_summary(analysis, target)
    return 0


def _chart_format_for(path: Path) -> str:
    try:
        return get_chart_format(path.suffix)
    except ValueError:
        return "md"


def cmd_update(args: argparse.Namespace) -> int:
    installation = find_installation()
    print(f"installed as  {installation.description}")
    if not installation.updatable:
        print(
            "omacap cannot update itself unless it was installed from a git "
            "checkout. Reinstall with the install script to enable updates:\n"
            "  curl -fsSL https://raw.githubusercontent.com/mattische/omacap"
            "/main/install.sh | bash",
            file=sys.stderr,
        )
        return 1

    status = check_now(installation)
    print(f"installed     {status.local or 'unknown'}")
    if status.error:
        print(f"omacap: {status.error}", file=sys.stderr)
        return 1
    print(f"latest        {status.remote}")

    if not status.available:
        print("\nAlready up to date.")
        return 0
    if args.check:
        print("\nAn update is available. Run 'omacap update' to install it.")
        return 0

    try:
        result = apply_update(installation)
    except UpdateError as exc:
        print(f"omacap: {exc}", file=sys.stderr)
        return 1
    print()
    for line in result.changes:
        print(f"  {line}")
    print(f"\n{result.message}")
    return 0


def print_update_notice() -> None:
    """One line on stderr when a newer version is waiting.

    stderr so that piping `omacap record -q` somewhere still yields only a path.
    """
    try:
        status = pending_update()
    except Exception:
        return
    if status is not None:
        print(f"omacap: {notice_line(status)}", file=sys.stderr)


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
    missing = []
    for fmt in FORMATS:
        kind = "lossless" if fmt.lossless else f"lossy, default {fmt.default_bitrate}"
        available = format_is_available(fmt)
        if not available:
            missing.append(fmt.name)
        mark = "" if available else "   [unavailable in this ffmpeg build]"
        print(f"  {fmt.name:<5} {fmt.extension:<6} {kind}{mark}")
        print(f"        {fmt.description}")
    print("\n'mp4' is accepted as an alias for 'm4a'.")
    if missing:
        print(
            f"Unavailable here: {', '.join(missing)}. "
            "Install a fuller ffmpeg build to use them."
        )
    return 0


def cmd_doctor() -> int:
    ok = True
    print(f"omacap {__version__}\n")

    ffmpeg = shutil.which("ffmpeg")
    print(f"[{'ok' if ffmpeg else 'XX'}] ffmpeg    {ffmpeg or 'not found - install ffmpeg'}")
    ok &= bool(ffmpeg)

    if ffmpeg:
        encoders = available_encoders()
        usable = [f.name for f in FORMATS if format_is_available(f)]
        unusable = [f.name for f in FORMATS if not format_is_available(f)]
        if not encoders:
            print("[--] formats   could not query ffmpeg encoders; assuming all work")
        elif unusable:
            print(f"[--] formats   {', '.join(usable)} (missing: {', '.join(unusable)})")
        else:
            print(f"[ok] formats   all {len(usable)} formats available")
        if metering_supported():
            print("[ok] meter     live level meter available")
        else:
            print("[--] meter     this ffmpeg cannot run the meter filter; "
                  "recording still works")

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

    installation = find_installation()
    if installation.updatable:
        revision = local_revision(installation.checkout) or "unknown"
        print(f"[ok] install   {installation.description} ({revision})")
    else:
        print(f"[--] install   {installation.description}; "
              f"'omacap update' is unavailable")

    # Optional: only needed for 'omacap analyze', so it never fails the check.
    try:
        from .analysis import require_numpy

        version = require_numpy().__version__
        print(f"[ok] analysis  numpy {version} - chord charts available")
    except AnalysisUnavailable:
        print("[--] analysis  numpy not installed - "
              "run \"pip install 'omacap[analyze]'\" for chord charts")

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
