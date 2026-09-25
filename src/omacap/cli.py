"""Command line entry point."""

from __future__ import annotations

import argparse
import shutil
import signal
import sys
import threading
import time
from pathlib import Path

from . import __version__
from . import capture, nowplaying, splitter, timeline
from .analysis import AnalysisUnavailable
from .analysis.audio import DecodeError
from .analysis.report import AnalysisError
from .splitter import SplitError
from .devices import AudioSystemError, list_monitors, list_sources, resolve_source
from .updater import (
    UpdateError,
    apply_update,
    check_now,
    find_installation,
    launchers_on_path,
    local_revision,
    notice_line,
    pending_update,
    running_launcher,
)
from .analysis.chords import DEFAULT_VOCABULARY, VOCABULARIES
from .formats import DEFAULT_FORMAT, FORMAT_NAMES, FORMATS, get_format
from .recorder import (
    METER_FLOOR_DB,
    Recorder,
    clipping_advice,
    sanitize_basename,
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
  omacap nowplaying             show what the media player is playing
  omacap split mix.wav          cut a recording into one file per track
  omacap record --split         record a playlist, one file per track
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
    _add_split_options(parser)
    parser.add_argument(
        "--stop-after-silence", type=float, default=0.0, metavar="SECONDS",
        help="stop recording once it has been silent this long; 0 turns it off",
    )
    parser.add_argument(
        "--player", metavar="NAME",
        help="MPRIS bus name to follow (default: Spotify if running)",
    )
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
    record.add_argument(
        "-S", "--split", action="store_true",
        help="cut the recording into one file per track when it stops, naming "
             "each one from the media player",
    )
    record.add_argument(
        "--player", default=argparse.SUPPRESS, metavar="NAME",
        help="MPRIS bus name to follow (default: Spotify if running)",
    )
    record.add_argument(
        "--stop-after-silence", type=float, default=None, metavar="SECONDS",
        help="stop once the recording has been silent this long; 0 turns it off "
             f"(default: {capture.DEFAULT_STOP_AFTER_SILENCE:g} with --split, "
             f"otherwise off)",
    )
    _add_split_options(record)
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
    analyze.add_argument(
        "files", nargs="+", metavar="FILE",
        help="audio files to analyse; a directory means every audio file in it",
    )
    analyze.add_argument(
        "-o", "--output", metavar="FILE",
        help="chart file to write (default: next to each recording); "
             "only valid for a single input",
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

    splitting = subparsers.add_parser(
        "split",
        help="cut a recording into one file per track, on the silences",
        description=(
            "Split a recording wherever the audio goes quiet for long enough. "
            "Without a track history there are no names to use, so the files are "
            "numbered; record with --split to get names from the media player."
        ),
    )
    splitting.add_argument("file", help="recording to split")
    splitting.add_argument(
        "-D", "--dir", metavar="PATH",
        help="where to write the pieces (default: beside the recording)",
    )
    _add_split_options(splitting)
    splitting.add_argument(
        "--threshold", type=float, default=splitter.DEFAULT_THRESHOLD_DB, metavar="DB",
        help=f"level below which audio counts as silence "
             f"(default: {splitter.DEFAULT_THRESHOLD_DB:g})",
    )
    splitting.add_argument(
        "-n", "--dry-run", action="store_true",
        help="show what would be written without writing it",
    )
    splitting.add_argument(
        "-A", "--analyze", "--analyse", dest="analyze", action="store_true",
        help="write a chord chart for each piece",
    )
    _add_analysis_options(splitting)

    watching = subparsers.add_parser(
        "nowplaying",
        help="show what the media player says is playing",
        description=(
            "Read the current track over MPRIS. This is the source omacap uses "
            "to name and split a playlist recording."
        ),
    )
    watching.add_argument(
        "-p", "--player", metavar="NAME",
        help="MPRIS bus name (default: Spotify if running, else the first player)",
    )
    watching.add_argument(
        "-w", "--watch", action="store_true",
        help="keep running and print each track change",
    )

    subparsers.add_parser("devices", help="list capture sources")
    subparsers.add_parser("formats", help="list output formats")
    subparsers.add_parser("doctor", help="check the installation")
    return parser


def _add_split_options(parser: argparse.ArgumentParser) -> None:
    """Options shared by ``split`` and by ``record --split``."""
    parser.add_argument(
        "--min-gap", type=float, default=timeline.DEFAULT_MIN_GAP, metavar="SECONDS",
        help=f"silence this long counts as a track boundary "
             f"(default: {timeline.DEFAULT_MIN_GAP})",
    )
    parser.add_argument(
        "--min-track", type=float, default=timeline.DEFAULT_MIN_TRACK,
        metavar="SECONDS",
        help=f"anything shorter is not kept (default: {timeline.DEFAULT_MIN_TRACK:g})",
    )
    parser.add_argument(
        "--pad", type=float, default=timeline.DEFAULT_PAD, metavar="SECONDS",
        help=f"keep this much either side of a cut (default: {timeline.DEFAULT_PAD})",
    )


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
        if args.command == "nowplaying":
            return cmd_nowplaying(args)
        if args.command == "split":
            return cmd_split(args)
        return cmd_tui(args)
    except (AudioSystemError, RecorderError, AnalysisError, AnalysisUnavailable,
            DecodeError, SplitError, ValueError) as exc:
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
        split_options=capture.SplitOptions(
            min_gap=args.min_gap,
            min_track=args.min_track,
            pad=args.pad,
            player=args.player,
        ),
        stop_after_silence=args.stop_after_silence,
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
    splitting = getattr(args, "split", False)
    stop_after = getattr(args, "stop_after_silence", None)
    if stop_after is None:
        stop_after = capture.DEFAULT_STOP_AFTER_SILENCE if splitting else 0.0
    recorder = Recorder(
        RecorderConfig(
            source=source,
            audio_format=audio_format,
            output_path=output_path,
            bitrate=args.bitrate if audio_format.supports_bitrate else None,
            duration=args.duration,
            # Needed for the peak reading and the clipping warning; the cost is
            # one filter and a line of log per frame.
            meter=True,
            # Gaps are only worth reporting when something will act on them.
            detect_silence=splitting or stop_after > 0,
            silence_min_gap=min(
                getattr(args, "min_gap", timeline.DEFAULT_MIN_GAP), 0.3
            ),
        )
    )
    if not args.quiet:
        print(f"source  {source.label}")
        print(f"format  {audio_format.name}")
        print(f"file    {output_path}")
        if args.duration:
            print(f"stops   after {args.duration:g}s")
        elif stop_after > 0:
            print(f"stops   on Ctrl-C, or after {stop_after:g}s of silence")
        else:
            print("stops   on Ctrl-C")
    split_options = capture.SplitOptions(
        enabled=getattr(args, "split", False),
        min_gap=getattr(args, "min_gap", timeline.DEFAULT_MIN_GAP),
        min_track=getattr(args, "min_track", timeline.DEFAULT_MIN_TRACK),
        pad=getattr(args, "pad", timeline.DEFAULT_PAD),
        directory=output_path.parent,
        player=getattr(args, "player", None),
    )
    player = capture.choose_player(split_options.player) if split_options.enabled else None
    if split_options.enabled and not args.quiet:
        if player:
            print(f"player  {player}")
        else:
            print("player  none found; pieces will be numbered, not named")

    session = capture.TrackSession(recorder, player)
    session.start()

    stop_requested = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: stop_requested.set())
    signal.signal(signal.SIGTERM, lambda *_: stop_requested.set())

    stopper = capture.SilenceStopper(stop_after)
    stopped_by_silence = False
    while recorder.is_running and not stop_requested.wait(0.2):
        if stopper.should_stop(recorder):
            stopped_by_silence = True
            break
    if stopped_by_silence and not args.quiet:
        print(f"silence for {stop_after:g}s \u2014 stopping.")
    silences = recorder.silences
    if recorder.is_running:
        result = session.stop()
    else:
        if session.watcher is not None:
            session.watcher.stop()
        result = recorder.wait()
    changes = session.changes

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
        if result.peak_db > METER_FLOOR_DB:
            print(f"peak    {result.peak_db:.1f} dBFS")
    if result.clipped:
        print(f"\nomacap: {clipping_advice()}", file=sys.stderr)

    pieces: list[Path] = []
    if split_options.enabled:
        outcome = capture.split_recording(
            result, silences, changes, split_options, audio_format
        )
        if not args.quiet:
            _report_split(outcome, result)
        pieces = outcome.written
        if args.quiet:
            for path in pieces:
                print(path)

    if getattr(args, "analyze", False):
        if pieces:
            return _analyse_many(pieces, args)
        return _analyse_recording(result.path, args, quiet=args.quiet)
    return 0


def _report_split(outcome, result) -> None:
    if not outcome.happened:
        print(f"split   not split: {outcome.reason}")
        return
    source = "the player" if outcome.named else "numbering"
    print(f"\nsplit into {len(outcome.written)} piece(s), named from {source}:")
    for path in outcome.written:
        print(f"  {path.name}")
    print(f"\n{result.path.name} is untouched.")


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


#: Extensions looked for when a directory is given. ffmpeg reads more than this,
#: but a folder should not be trawled for things that only might be audio.
AUDIO_SUFFIXES = frozenset(
    {".wav", ".flac", ".mp3", ".m4a", ".opus", ".ogg", ".aac", ".aif", ".aiff", ".wma"}
)


def resolve_inputs(names: list[str]) -> list[Path]:
    """Expand the arguments into a list of audio files, in a stable order.

    A directory contributes the audio files directly inside it. Charts omacap
    wrote earlier are not audio, so nothing it produced comes back as input.
    """
    found: list[Path] = []
    seen: set[Path] = set()
    for name in names:
        path = Path(name).expanduser()
        if path.is_dir():
            entries = sorted(
                child for child in path.iterdir()
                if child.is_file() and child.suffix.lower() in AUDIO_SUFFIXES
            )
            if not entries:
                raise ValueError(f"no audio files in {path}")
        else:
            entries = [path]
        for entry in entries:
            resolved = entry.expanduser()
            if resolved not in seen:
                seen.add(resolved)
                found.append(resolved)
    return found


def cmd_analyze(args: argparse.Namespace) -> int:
    sources = resolve_inputs(args.files)
    if args.output and len(sources) > 1:
        raise ValueError(
            f"--output names one file but {len(sources)} were given; "
            f"drop it and each chart is written beside its recording"
        )

    if args.chart_format:
        chart_format = get_chart_format(args.chart_format)
    elif args.output:
        chart_format = _chart_format_for(Path(args.output))
    else:
        chart_format = "md"

    failures = 0
    for index, source in enumerate(sources):
        if len(sources) > 1 and not args.to_stdout:
            print(f"[{index + 1}/{len(sources)}] {source.name}")
        try:
            analysis = _load_analysis(source, args.chords)
        except (AnalysisError, AnalysisUnavailable, DecodeError) as exc:
            print(f"omacap: {source.name}: {exc}", file=sys.stderr)
            failures += 1
            continue

        if args.to_stdout:
            if index:
                print()
            print(render(analysis, chart_format, args.bars_per_line))
            continue

        target = (
            Path(args.output).expanduser() if args.output
            else default_chart_path(source, chart_format)
        )
        write_chart(analysis, target, chart_format, args.bars_per_line)
        _print_analysis_summary(analysis, target)
        if len(sources) > 1:
            print()

    if len(sources) > 1 and not args.to_stdout:
        print(f"{len(sources) - failures} of {len(sources)} charted.")
    return 1 if failures and failures == len(sources) else 0


def _load_analysis(source: Path, vocabulary: str):
    from .analysis.report import analyse_file

    return analyse_file(source, vocabulary=vocabulary)


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


def cmd_split(args: argparse.Namespace) -> int:
    source = Path(args.file).expanduser()
    duration = splitter.probe_duration(source)
    silences = splitter.detect_silences(
        source, threshold_db=args.threshold, min_gap=min(args.min_gap, 0.3),
        duration=duration,
    )
    segments = timeline.plan_from_silence(
        duration, silences,
        min_gap=args.min_gap, min_track=args.min_track, pad=args.pad,
    )

    print(f"source   {source}")
    print(f"length   {format_duration(duration)}")
    print(f"silences {len(silences)} found at or above {args.min_gap:g}s")
    if not segments:
        print("\nNothing to split: no piece was long enough to be a track.")
        print(f"Try a smaller --min-track (currently {args.min_track:g}s) "
              f"or a smaller --min-gap (currently {args.min_gap:g}s).")
        return 1

    print(f"\n{len(segments)} piece(s):")
    for segment in segments:
        print(f"  {segment.index:02d}  {format_duration(segment.duration):>7}  "
              f"{sanitize_basename(segment.basename())}")
    if len(segments) == 1:
        print("\nOnly one piece — there was nothing that looked like a track break.")

    if args.dry_run:
        print("\nDry run; nothing written.")
        return 0

    directory = Path(args.dir).expanduser() if args.dir else source.parent
    written = splitter.split(source, segments, directory)
    print(f"\nwritten to {directory}")
    for path in written:
        print(f"  {path.name}")
    print(f"\n{source.name} is untouched.")

    if args.analyze:
        return _analyse_many(written, args)
    return 0


def _analyse_many(paths: list[Path], args: argparse.Namespace) -> int:
    """Chart each piece. One failure must not lose the rest."""
    from .analysis.report import analyse_file

    chart_format = get_chart_format(args.chart_format) if args.chart_format else "md"
    failures = 0
    print()
    for path in paths:
        try:
            analysis = analyse_file(path, vocabulary=args.chords)
        except (AnalysisError, AnalysisUnavailable, DecodeError) as exc:
            print(f"  {path.name}: not charted ({exc})", file=sys.stderr)
            failures += 1
            continue
        target = write_chart(
            analysis, default_chart_path(path, chart_format),
            chart_format, args.bars_per_line,
        )
        print(f"  {target.name}: {analysis.key.short_name}, "
              f"{analysis.tempo:.0f} BPM, {analysis.meter.name}, "
              f"{analysis.bar_count} bars")
    return 1 if failures == len(paths) else 0


def cmd_nowplaying(args: argparse.Namespace) -> int:
    if not nowplaying.available():
        print(
            "omacap: 'busctl' was not found, so omacap cannot read what is "
            "playing. It ships with systemd.",
            file=sys.stderr,
        )
        return 1

    players = nowplaying.list_players()
    if not players:
        print("No media player is publishing on the session bus.")
        print("Start one and press play, then try again.")
        return 1

    player = nowplaying.find_player(args.player)
    if player is None:
        print(f"omacap: no player called {args.player!r}. Running now:",
              file=sys.stderr)
        for name in players:
            print(f"  {name}", file=sys.stderr)
        return 1

    print(f"player   {player}")
    if len(players) > 1:
        print(f"         (also running: {', '.join(n for n in players if n != player)})")
    _print_track(player)

    if not args.watch:
        return 0

    print("\nwatching for track changes; press Ctrl-C to stop")
    watcher = nowplaying.TrackWatcher(player, clock=time.monotonic)
    seen = 0
    try:
        while True:
            change = watcher.poll_once()
            if change is not None:
                seen += 1
                marker = "  (advert)" if change.track.is_advert else ""
                print(f"  {seen:02d}  {change.track.label}{marker}")
            time.sleep(nowplaying.POLL_SECONDS)
    except KeyboardInterrupt:
        print(f"\n{seen} track change(s) seen.")
    return 0


def _print_track(player: str) -> None:
    track = nowplaying.current_track(player)
    status = nowplaying.playback_status(player)
    print(f"status   {status or 'unknown'}")
    if track is None:
        print("track    nothing reported")
        return
    print(f"artist   {track.artist or '-'}")
    print(f"title    {track.title or '-'}")
    print(f"album    {track.album or '-'}")
    if track.length:
        elapsed = nowplaying.position(player)
        print(f"position {elapsed:.0f} s of {track.length:.0f} s "
              f"({max(0.0, track.length - elapsed):.0f} s remaining)")
    print(f"trackid  {track.trackid}")
    print(f"filename {sanitize_basename(track.filename(1))}")
    if track.is_advert:
        print("         this looks like an advert, not a track")


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
    ok &= _report_launchers()

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


def _report_launchers() -> bool:
    """Say which omacap the shell would run, and flag a second installation."""
    on_path = launchers_on_path()
    running = running_launcher()

    if not on_path:
        hint = f" Add {running.parent} to your PATH." if running else ""
        print(f"[--] command   'omacap' is not on your PATH.{hint}")
        return True          # it clearly ran anyway, so not a failure

    first = on_path[0]
    print(f"[ok] command   {first}")

    if running is not None and first.resolve() != running.resolve():
        print(f"[--] conflict  this is {running}, but your shell would run "
              f"{first}")
    if len(on_path) > 1:
        print(f"[--] duplicate {len(on_path)} installations on PATH; "
              f"the first one wins:")
        for launcher in on_path:
            print(f"               {launcher} -> {launcher.resolve()}")
    return True


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
