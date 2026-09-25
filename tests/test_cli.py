"""CLI tests. Audio discovery is stubbed; ffmpeg is the stub from conftest."""

from __future__ import annotations

from pathlib import Path

import pytest

from omacap import cli
from omacap.devices import AudioSystemError, Source

MONITOR = Source("alsa_output.test.monitor", "Monitor of Test Output", True)
OTHER = Source("alsa_output.second.monitor", "Monitor of Second Output", True)
MIC = Source("alsa_input.mic", "Built-in Mic", False)


@pytest.fixture
def stub_audio(monkeypatch):
    monkeypatch.setattr(cli, "resolve_source", lambda name: MONITOR)
    monkeypatch.setattr(cli, "list_monitors", lambda: [MONITOR, OTHER])
    monkeypatch.setattr(cli, "list_sources", lambda: [MONITOR, OTHER, MIC])
    monkeypatch.setattr("omacap.devices.default_monitor", lambda: MONITOR)


# -- argument parsing -----------------------------------------------------

def test_no_subcommand_means_the_interactive_app():
    assert cli.build_parser().parse_args([]).command is None


def test_record_accepts_its_options():
    args = cli.build_parser().parse_args(
        ["record", "-f", "wav", "-d", "30", "-o", "x.wav", "-n", "take", "-q"]
    )
    assert (args.command, args.format, args.duration, args.output, args.name, args.quiet) == (
        "record", "wav", 30.0, "x.wav", "take", True
    )


def test_common_options_work_before_a_subcommand():
    args = cli.build_parser().parse_args(["-f", "flac", "record"])
    assert args.format == "flac"


def test_version_flag_exits_zero(capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(["--version"])
    assert exc.value.code == 0
    assert "omacap" in capsys.readouterr().out


def test_help_mentions_the_subcommands(capsys):
    with pytest.raises(SystemExit):
        cli.main(["--help"])
    out = capsys.readouterr().out
    for word in ("record", "devices", "formats", "doctor"):
        assert word in out


# -- informational subcommands -------------------------------------------

def test_formats_lists_everything_with_its_extension(capsys, stub_audio):
    assert cli.main(["formats"]) == 0
    out = capsys.readouterr().out
    for name in ("wav", "flac", "mp3", "m4a", "opus", "ogg"):
        assert name in out
    assert "mp4" in out          # the alias is documented


def test_devices_separates_monitors_from_microphones(capsys, stub_audio):
    assert cli.main(["devices"]) == 0
    out = capsys.readouterr().out
    monitors, _, inputs = out.partition("Other inputs")
    assert MONITOR.name in monitors and OTHER.name in monitors
    assert MIC.name in inputs and MIC.name not in monitors


def test_devices_marks_the_default(capsys, stub_audio):
    cli.main(["devices"])
    line = next(l for l in capsys.readouterr().out.splitlines() if MONITOR.name in l)
    assert line.strip().startswith("*")


def test_doctor_passes_when_everything_is_present(capsys, stub_audio, fake_ffmpeg, tmp_path, monkeypatch):
    monkeypatch.setenv("OMACAP_OUTPUT_DIR", str(tmp_path / "recordings"))
    assert cli.main(["doctor"]) == 0
    out = capsys.readouterr().out
    assert "XX" not in out
    assert "Everything looks good" in out


def test_doctor_fails_without_ffmpeg(capsys, stub_audio, no_ffmpeg, tmp_path, monkeypatch):
    monkeypatch.setenv("OMACAP_OUTPUT_DIR", str(tmp_path / "recordings"))
    assert cli.main(["doctor"]) == 1
    assert "ffmpeg" in capsys.readouterr().out


def test_doctor_reports_an_unreachable_audio_server(capsys, fake_ffmpeg, tmp_path, monkeypatch):
    def boom():
        raise AudioSystemError("Connection refused")

    monkeypatch.setattr(cli, "list_monitors", boom)
    monkeypatch.setenv("OMACAP_OUTPUT_DIR", str(tmp_path / "recordings"))
    assert cli.main(["doctor"]) == 1
    assert "Connection refused" in capsys.readouterr().out


# -- record ---------------------------------------------------------------

def test_record_with_a_duration_writes_the_file(capsys, stub_audio, fake_ffmpeg, tmp_path):
    code = cli.main(["record", "-d", "0.4", "-f", "wav", "-D", str(tmp_path), "-n", "take"])
    assert code == 0
    written = tmp_path / "take.wav"
    assert written.is_file() and written.stat().st_size > 0
    out = capsys.readouterr().out
    assert "saved" in out and str(written) in out


def test_record_quiet_prints_only_the_path(capsys, stub_audio, fake_ffmpeg, tmp_path):
    assert cli.main(["record", "-d", "0.3", "-D", str(tmp_path), "-q"]) == 0
    lines = capsys.readouterr().out.strip().splitlines()
    assert len(lines) == 1
    assert Path(lines[0]).is_file()


def test_output_extension_selects_the_format(capsys, stub_audio, fake_ffmpeg, tmp_path):
    target = tmp_path / "mix.flac"
    assert cli.main(["record", "-d", "0.3", "-o", str(target)]) == 0
    assert target.is_file()
    assert "flac" in capsys.readouterr().out


def test_an_unknown_output_extension_is_rejected(capsys, stub_audio, fake_ffmpeg, tmp_path):
    assert cli.main(["record", "-d", "0.3", "-o", str(tmp_path / "mix.xyz")]) == 1
    assert "pass --format" in capsys.readouterr().err


def test_an_explicit_format_overrides_the_extension(stub_audio, fake_ffmpeg, tmp_path):
    target = tmp_path / "mix.bin"
    assert cli.main(["record", "-d", "0.3", "-f", "wav", "-o", str(target)]) == 0
    assert target.is_file()


def test_an_unknown_format_name_is_rejected(capsys, stub_audio, fake_ffmpeg, tmp_path):
    assert cli.main(["record", "-f", "aiff", "-D", str(tmp_path)]) == 1
    assert "unknown format" in capsys.readouterr().err


def test_an_unknown_source_is_rejected(capsys, fake_ffmpeg, tmp_path, monkeypatch):
    def boom(name):
        raise AudioSystemError(f"Unknown audio source {name!r}")

    monkeypatch.setattr(cli, "resolve_source", boom)
    assert cli.main(["record", "-s", "nope", "-D", str(tmp_path)]) == 1
    assert "Unknown audio source" in capsys.readouterr().err


def test_a_failing_ffmpeg_gives_a_non_zero_exit(capsys, stub_audio, failing_ffmpeg, tmp_path):
    assert cli.main(["record", "-d", "0.3", "-D", str(tmp_path)]) == 1
    assert "omacap:" in capsys.readouterr().err


def test_record_reports_its_settings_first(capsys, stub_audio, fake_ffmpeg, tmp_path):
    cli.main(["record", "-d", "0.3", "-f", "mp3", "-b", "320k", "-D", str(tmp_path)])
    out = capsys.readouterr().out
    assert "source" in out and "Monitor of Test Output" in out
    assert "format" in out and "mp3" in out
    assert "stops   after 0.3s" in out


# -- interactive guard ----------------------------------------------------

def test_the_interactive_app_refuses_a_non_tty(capsys, stub_audio, monkeypatch):
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    assert cli.main([]) == 1
    assert "needs a terminal" in capsys.readouterr().err


# -- analyze --------------------------------------------------------------

def _synthetic_wav(path: Path) -> Path:
    import wave

    import numpy as np

    from synth import SR, song

    audio = song([(0, ""), (7, ""), (9, "m"), (5, "")], bars=12)
    pcm = (audio / max(float(np.abs(audio).max()), 1e-9) * 32000).astype("<i2")
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(SR)
        handle.writeframes(pcm.tobytes())
    return path


@pytest.fixture
def recording(tmp_path):
    return _synthetic_wav(tmp_path / "Loop Take.wav")


def test_analyze_writes_a_chart_beside_the_recording(capsys, recording):
    assert cli.main(["analyze", str(recording)]) == 0
    chart = recording.with_suffix(".md")
    assert chart.is_file()
    text = chart.read_text(encoding="utf-8")
    assert text.startswith("# Loop Take")
    assert "4/4" in text and "120 BPM" in text
    out = capsys.readouterr().out
    assert "key" in out and "tempo" in out and "bars" in out


def test_analyse_is_accepted_as_a_spelling(recording):
    assert cli.main(["analyse", str(recording)]) == 0
    assert recording.with_suffix(".md").is_file()


def test_analyze_honours_an_explicit_output(recording, tmp_path):
    target = tmp_path / "charts" / "chart.txt"
    assert cli.main(["analyze", str(recording), "-o", str(target)]) == 0
    assert "```" not in target.read_text(encoding="utf-8")


def test_the_output_extension_selects_the_chart_format(recording, tmp_path):
    target = tmp_path / "chart.txt"
    cli.main(["analyze", str(recording), "-o", str(target)])
    assert "**" not in target.read_text(encoding="utf-8")


def test_the_chart_format_flag_wins_over_the_extension(recording, tmp_path):
    target = tmp_path / "chart.txt"
    cli.main(["analyze", str(recording), "-o", str(target), "-t", "md"])
    assert target.read_text(encoding="utf-8").startswith("# ")


def test_analyze_can_print_instead_of_writing(capsys, recording):
    assert cli.main(["analyze", str(recording), "--print"]) == 0
    assert "# Loop Take" in capsys.readouterr().out
    assert not recording.with_suffix(".md").exists()


def test_the_simple_vocabulary_writes_only_triads(capsys, recording):
    cli.main(["analyze", str(recording), "-c", "simple", "--print"])
    grid = capsys.readouterr().out.split("```")[1]
    assert "maj7" not in grid and "sus4" not in grid


def test_bars_per_line_is_configurable(capsys, recording):
    cli.main(["analyze", str(recording), "--bars-per-line", "2", "--print"])
    grid = capsys.readouterr().out.split("```")[1].strip().splitlines()
    assert len(grid) == 6          # 12 bars, two per line


def test_a_missing_file_is_reported(capsys, tmp_path):
    assert cli.main(["analyze", str(tmp_path / "gone.wav")]) == 1
    assert "no such file" in capsys.readouterr().err


def test_a_clip_too_short_to_analyse_is_reported(capsys, tmp_path):
    import wave

    path = tmp_path / "blip.wav"
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(22050)
        handle.writeframes(b"\0\0" * 22050)
    assert cli.main(["analyze", str(path)]) == 1
    assert "omacap:" in capsys.readouterr().err


def test_an_unknown_vocabulary_is_rejected(capsys, recording):
    with pytest.raises(SystemExit):
        cli.main(["analyze", str(recording), "-c", "bebop"])
    assert "invalid choice" in capsys.readouterr().err


# -- ffmpeg capability reporting -----------------------------------------

def test_doctor_reports_the_available_formats(capsys, stub_audio, fake_ffmpeg, tmp_path, monkeypatch):
    monkeypatch.setenv("OMACAP_OUTPUT_DIR", str(tmp_path / "recordings"))
    cli.main(["doctor"])
    out = capsys.readouterr().out
    assert "formats" in out and "6 formats available" in out
    assert "meter" in out


def test_doctor_names_the_formats_this_ffmpeg_cannot_write(
    capsys, stub_audio, fake_ffmpeg, tmp_path, monkeypatch
):
    monkeypatch.setattr(
        "omacap.recorder.available_encoders", lambda: frozenset({"flac", "pcm_s16le"})
    )
    monkeypatch.setenv("OMACAP_OUTPUT_DIR", str(tmp_path / "recordings"))
    cli.main(["doctor"])
    out = capsys.readouterr().out
    assert "missing: mp3" in out
    assert "wav, flac" in out


def test_doctor_warns_when_the_meter_is_unavailable(
    capsys, stub_audio, ffmpeg_without_meter, tmp_path, monkeypatch
):
    monkeypatch.setenv("OMACAP_OUTPUT_DIR", str(tmp_path / "recordings"))
    cli.main(["doctor"])
    out = capsys.readouterr().out
    assert "cannot run the meter filter" in out
    assert "recording still works" in out
    # A missing meter is not a failed installation.
    assert "Everything looks good" in out


def test_formats_marks_what_this_ffmpeg_cannot_write(capsys, fake_ffmpeg, monkeypatch):
    monkeypatch.setattr(
        "omacap.recorder.available_encoders", lambda: frozenset({"flac", "pcm_s16le"})
    )
    cli.main(["formats"])
    out = capsys.readouterr().out
    assert "[unavailable in this ffmpeg build]" in out
    assert "Unavailable here: mp3, m4a, opus, ogg" in out


def test_recording_an_unavailable_format_fails_before_the_banner(
    capsys, stub_audio, fake_ffmpeg, tmp_path, monkeypatch
):
    monkeypatch.setattr(
        "omacap.recorder.available_encoders", lambda: frozenset({"flac", "pcm_s16le"})
    )
    assert cli.main(["record", "-d", "0.3", "-f", "mp3", "-D", str(tmp_path)]) == 1
    captured = capsys.readouterr()
    assert "cannot encode mp3" in captured.err
    assert captured.out == ""


# -- record --analyze ------------------------------------------------------

def test_record_can_chart_the_take_it_just_made(capsys, stub_audio, fake_ffmpeg, tmp_path, monkeypatch):
    charted = {}

    class FakeAnalysis:
        key = type("K", (), {"name": "C major", "signature": "no sharps or flats"})()
        meter = type("M", (), {"name": "4/4"})()
        tempo = 120.0
        bar_count = 16

    def fake_analyse(path, **kwargs):
        charted["path"] = path
        charted["vocabulary"] = kwargs.get("vocabulary")
        return FakeAnalysis()

    monkeypatch.setattr("omacap.analysis.report.analyse_file", fake_analyse)
    monkeypatch.setattr("omacap.cli.write_chart", lambda a, target, *rest: target)

    code = cli.main(
        ["record", "-d", "0.3", "-f", "wav", "-D", str(tmp_path), "-n", "take", "-A"]
    )
    assert code == 0
    assert charted["path"] == tmp_path / "take.wav"
    out = capsys.readouterr().out
    assert "saved" in out and "analysing" in out
    assert "C major" in out and "120 BPM" in out and "4/4" in out


def test_the_long_spelling_also_works(stub_audio, fake_ffmpeg, tmp_path, monkeypatch):
    monkeypatch.setattr(
        "omacap.analysis.report.analyse_file", lambda path, **kw: _cli_fake_analysis()
    )
    monkeypatch.setattr("omacap.cli.write_chart", lambda a, target, *rest: target)
    assert cli.main(["record", "-d", "0.3", "-D", str(tmp_path), "--analyse"]) == 0


def test_the_chord_vocabulary_reaches_the_analysis(stub_audio, fake_ffmpeg, tmp_path, monkeypatch):
    seen = {}

    def fake_analyse(path, **kwargs):
        seen.update(kwargs)
        return _cli_fake_analysis()

    monkeypatch.setattr("omacap.analysis.report.analyse_file", fake_analyse)
    monkeypatch.setattr("omacap.cli.write_chart", lambda a, target, *rest: target)
    cli.main(["record", "-d", "0.3", "-D", str(tmp_path), "-A", "-c", "simple"])
    assert seen["vocabulary"] == "simple"


def test_the_chart_format_is_honoured(stub_audio, fake_ffmpeg, tmp_path, monkeypatch):
    written = {}
    monkeypatch.setattr(
        "omacap.analysis.report.analyse_file", lambda path, **kw: _cli_fake_analysis()
    )

    def fake_write(analysis, target, chart_format, *rest):
        written["target"] = target
        written["format"] = chart_format
        return target

    monkeypatch.setattr("omacap.cli.write_chart", fake_write)
    cli.main(["record", "-d", "0.3", "-D", str(tmp_path), "-n", "x", "-A", "-t", "txt"])
    assert written["format"] == "txt"
    assert written["target"].suffix == ".txt"


def test_a_failed_analysis_never_loses_the_recording(capsys, stub_audio, fake_ffmpeg, tmp_path, monkeypatch):
    from omacap.analysis.report import AnalysisError

    def boom(path, **kwargs):
        raise AnalysisError("no steady beat found")

    monkeypatch.setattr("omacap.analysis.report.analyse_file", boom)
    code = cli.main(
        ["record", "-d", "0.3", "-f", "wav", "-D", str(tmp_path), "-n", "take", "-A"]
    )
    assert code == 1
    assert (tmp_path / "take.wav").is_file()
    captured = capsys.readouterr()
    assert "the recording was saved" in captured.err
    assert "saved" in captured.out


def test_recording_without_the_flag_does_not_analyse(stub_audio, fake_ffmpeg, tmp_path, monkeypatch):
    monkeypatch.setattr(
        "omacap.analysis.report.analyse_file",
        lambda path, **kw: pytest.fail("analysis should not have run"),
    )
    assert cli.main(["record", "-d", "0.3", "-D", str(tmp_path)]) == 0


def test_quiet_with_analysis_prints_only_the_chart_path(capsys, stub_audio, fake_ffmpeg, tmp_path, monkeypatch):
    monkeypatch.setattr(
        "omacap.analysis.report.analyse_file", lambda path, **kw: _cli_fake_analysis()
    )
    monkeypatch.setattr("omacap.cli.write_chart", lambda a, target, *rest: target)
    cli.main(["record", "-d", "0.3", "-D", str(tmp_path), "-q", "-A"])
    lines = capsys.readouterr().out.strip().splitlines()
    assert len(lines) == 2                   # the recording, then the chart
    assert lines[1].endswith(".md")


def _cli_fake_analysis():
    return type(
        "A", (),
        {
            "key": type("K", (), {"name": "C major", "signature": "1 sharp"})(),
            "meter": type("M", (), {"name": "4/4"})(),
            "tempo": 120.0,
            "bar_count": 8,
        },
    )()


# -- update ----------------------------------------------------------------

def test_update_check_reports_when_current(capsys, monkeypatch):
    from omacap.updater import Installation, UpdateStatus

    monkeypatch.setattr(
        cli, "find_installation", lambda: Installation(Path("/tmp/x"), managed=True)
    )
    monkeypatch.setattr(
        cli, "check_now",
        lambda inst: UpdateStatus(available=False, local="aaa", remote="aaa"),
    )
    assert cli.main(["update", "--check"]) == 0
    assert "Already up to date" in capsys.readouterr().out


def test_update_check_reports_an_available_version(capsys, monkeypatch):
    from omacap.updater import Installation, UpdateStatus

    monkeypatch.setattr(
        cli, "find_installation", lambda: Installation(Path("/tmp/x"), managed=True)
    )
    monkeypatch.setattr(
        cli, "check_now",
        lambda inst: UpdateStatus(available=True, local="aaa", remote="bbb"),
    )
    monkeypatch.setattr(
        cli, "apply_update", lambda inst: pytest.fail("--check must not install")
    )
    assert cli.main(["update", "--check"]) == 0
    out = capsys.readouterr().out
    assert "aaa" in out and "bbb" in out
    assert "An update is available" in out


def test_update_installs_and_lists_what_changed(capsys, monkeypatch):
    from omacap.updater import Installation, UpdateResult, UpdateStatus

    monkeypatch.setattr(
        cli, "find_installation", lambda: Installation(Path("/tmp/x"), managed=True)
    )
    monkeypatch.setattr(
        cli, "check_now",
        lambda inst: UpdateStatus(available=True, local="aaa", remote="bbb"),
    )
    monkeypatch.setattr(
        cli, "apply_update",
        lambda inst: UpdateResult(True, "aaa", "bbb", ["bbb fix a thing"], "Updated aaa → bbb."),
    )
    assert cli.main(["update"]) == 0
    out = capsys.readouterr().out
    assert "fix a thing" in out and "Updated" in out


def test_update_explains_a_non_git_installation(capsys, monkeypatch):
    from omacap.updater import Installation

    monkeypatch.setattr(
        cli, "find_installation", lambda: Installation(None, managed=False)
    )
    assert cli.main(["update"]) == 1
    assert "install script" in capsys.readouterr().err


def test_update_reports_an_unreachable_remote(capsys, monkeypatch):
    from omacap.updater import Installation, UpdateStatus

    monkeypatch.setattr(
        cli, "find_installation", lambda: Installation(Path("/tmp/x"), managed=True)
    )
    monkeypatch.setattr(
        cli, "check_now",
        lambda inst: UpdateStatus(available=False, error="could not reach the remote"),
    )
    assert cli.main(["update"]) == 1
    assert "could not reach" in capsys.readouterr().err


def test_a_failed_update_is_reported(capsys, monkeypatch):
    from omacap.updater import Installation, UpdateError, UpdateStatus

    monkeypatch.setattr(
        cli, "find_installation", lambda: Installation(Path("/tmp/x"), managed=True)
    )
    monkeypatch.setattr(
        cli, "check_now",
        lambda inst: UpdateStatus(available=True, local="aaa", remote="bbb"),
    )

    def boom(inst):
        raise UpdateError("uncommitted changes")

    monkeypatch.setattr(cli, "apply_update", boom)
    assert cli.main(["update"]) == 1
    assert "uncommitted changes" in capsys.readouterr().err


# -- the startup notice ----------------------------------------------------

def test_the_notice_goes_to_stderr_so_stdout_stays_pipeable(capsys, stub_audio, fake_ffmpeg, tmp_path, monkeypatch):
    from omacap.updater import UpdateStatus

    monkeypatch.setattr(
        cli, "pending_update",
        lambda: UpdateStatus(available=True, local="aaa", remote="bbb"),
    )
    cli.main(["record", "-d", "0.3", "-D", str(tmp_path), "-q"])
    captured = capsys.readouterr()
    assert "update is available" in captured.err
    assert Path(captured.out.strip()).is_file()      # stdout is still just a path


def test_no_notice_when_nothing_is_waiting(capsys, stub_audio, fake_ffmpeg, tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "pending_update", lambda: None)
    cli.main(["record", "-d", "0.3", "-D", str(tmp_path), "-q"])
    assert "update" not in capsys.readouterr().err


def test_a_broken_check_never_breaks_a_command(capsys, stub_audio, fake_ffmpeg, tmp_path, monkeypatch):
    def boom():
        raise RuntimeError("network on fire")

    monkeypatch.setattr(cli, "pending_update", boom)
    assert cli.main(["record", "-d", "0.3", "-D", str(tmp_path), "-q"]) == 0


def test_update_itself_does_not_print_the_notice(capsys, monkeypatch):
    from omacap.updater import Installation, UpdateStatus

    monkeypatch.setattr(
        cli, "pending_update", lambda: pytest.fail("update reports in full itself")
    )
    monkeypatch.setattr(
        cli, "find_installation", lambda: Installation(Path("/tmp/x"), managed=True)
    )
    monkeypatch.setattr(
        cli, "check_now",
        lambda inst: UpdateStatus(available=False, local="aaa", remote="aaa"),
    )
    assert cli.main(["update", "--check"]) == 0


def test_version_includes_the_revision(capsys, monkeypatch):
    from omacap.updater import Installation

    monkeypatch.setattr(
        cli, "find_installation", lambda: Installation(Path("/tmp/x"), managed=True)
    )
    monkeypatch.setattr(cli, "local_revision", lambda root: "abc1234")
    with pytest.raises(SystemExit):
        cli.main(["--version"])
    assert "abc1234" in capsys.readouterr().out


def test_version_without_a_revision(capsys, monkeypatch):
    from omacap.updater import Installation

    monkeypatch.setattr(
        cli, "find_installation", lambda: Installation(None, managed=False)
    )
    with pytest.raises(SystemExit):
        cli.main(["--version"])
    out = capsys.readouterr().out
    assert "omacap" in out and "(" not in out


# -- doctor: which omacap would actually run -------------------------------

def _launcher(directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "omacap"
    path.write_text("#!/bin/sh\n")
    path.chmod(0o755)
    return path


def test_doctor_names_the_command_the_shell_would_run(capsys, stub_audio, fake_ffmpeg, tmp_path, monkeypatch):
    launcher = _launcher(tmp_path / "bin")
    monkeypatch.setattr(cli, "launchers_on_path", lambda: [launcher])
    monkeypatch.setattr(cli, "running_launcher", lambda: launcher)
    monkeypatch.setenv("OMACAP_OUTPUT_DIR", str(tmp_path / "recordings"))
    cli.main(["doctor"])
    assert f"command   {launcher}" in capsys.readouterr().out


def test_doctor_warns_when_omacap_is_not_on_path(capsys, stub_audio, fake_ffmpeg, tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "launchers_on_path", lambda: [])
    monkeypatch.setattr(cli, "running_launcher", lambda: _launcher(tmp_path / "venv"))
    monkeypatch.setenv("OMACAP_OUTPUT_DIR", str(tmp_path / "recordings"))
    cli.main(["doctor"])
    out = capsys.readouterr().out
    assert "not on your PATH" in out
    assert str(tmp_path / "venv") in out
    assert "Everything looks good" in out      # not being on PATH is not a failure


def test_doctor_flags_a_second_installation(capsys, stub_audio, fake_ffmpeg, tmp_path, monkeypatch):
    """Two installs on PATH is exactly the confusion worth naming."""
    first = _launcher(tmp_path / "managed")
    second = _launcher(tmp_path / "old-clone")
    monkeypatch.setattr(cli, "launchers_on_path", lambda: [first, second])
    monkeypatch.setattr(cli, "running_launcher", lambda: first)
    monkeypatch.setenv("OMACAP_OUTPUT_DIR", str(tmp_path / "recordings"))
    cli.main(["doctor"])
    out = capsys.readouterr().out
    assert "2 installations on PATH" in out
    assert str(first) in out and str(second) in out


def test_doctor_flags_running_a_different_copy_than_path_would(capsys, stub_audio, fake_ffmpeg, tmp_path, monkeypatch):
    on_path = _launcher(tmp_path / "managed")
    running = _launcher(tmp_path / "dev-clone")
    monkeypatch.setattr(cli, "launchers_on_path", lambda: [on_path])
    monkeypatch.setattr(cli, "running_launcher", lambda: running)
    monkeypatch.setenv("OMACAP_OUTPUT_DIR", str(tmp_path / "recordings"))
    cli.main(["doctor"])
    out = capsys.readouterr().out
    assert "conflict" in out
    assert str(running) in out and str(on_path) in out


# -- nowplaying ------------------------------------------------------------

def test_nowplaying_reports_the_current_track(capsys, busctl):
    from conftest import mpris_track

    busctl(metadata=[mpris_track(
        "/com/spotify/track/abc",
        title="Jag vill vara (en del av din morgondag)",
        artist=["trampe|strandberg"],
        album="det är din stund på jorden",
        number=8, length=226818000,
    )], status="Playing", position=118000000)

    assert cli.main(["nowplaying"]) == 0
    out = capsys.readouterr().out
    assert "org.mpris.MediaPlayer2.spotify" in out
    assert "Jag vill vara (en del av din morgondag)" in out
    assert "trampe|strandberg" in out
    assert "Playing" in out
    assert "118 s of 227 s" in out and "109 s remaining" in out


def test_nowplaying_shows_the_filename_it_would_use(capsys, busctl):
    from conftest import mpris_track

    busctl(metadata=[mpris_track("/t/1", title="What's Going On?", artist=["Marvin"])])
    cli.main(["nowplaying"])
    out = capsys.readouterr().out
    # Readable, and free of the characters that would confuse a filesystem.
    assert "01 - Marvin - What's Going On" in out


def test_nowplaying_flags_an_advert(capsys, busctl):
    from conftest import mpris_track

    busctl(metadata=[mpris_track("/com/spotify/ad/1", title="Some Ad")])
    cli.main(["nowplaying"])
    assert "advert" in capsys.readouterr().out


def test_nowplaying_when_no_player_is_running(capsys, busctl):
    busctl(players=[])
    assert cli.main(["nowplaying"]) == 1
    assert "No media player" in capsys.readouterr().out


def test_nowplaying_lists_the_players_when_the_named_one_is_absent(capsys, busctl):
    busctl(players=["org.mpris.MediaPlayer2.mpv"])
    assert cli.main(["nowplaying", "-p", "org.mpris.MediaPlayer2.spotify"]) == 1
    err = capsys.readouterr().err
    assert "no player called" in err
    assert "org.mpris.MediaPlayer2.mpv" in err


def test_nowplaying_mentions_other_players(capsys, busctl):
    from conftest import mpris_track

    busctl(players=["org.mpris.MediaPlayer2.spotify", "org.mpris.MediaPlayer2.mpv"],
           metadata=[mpris_track("/t/1", title="One")])
    cli.main(["nowplaying"])
    assert "also running" in capsys.readouterr().out


def test_nowplaying_without_busctl(capsys, no_busctl):
    assert cli.main(["nowplaying"]) == 1
    assert "busctl" in capsys.readouterr().err


# -- split -----------------------------------------------------------------

@pytest.fixture
def split_recording(tmp_path):
    """Two 'tracks' with a gap, then trailing silence."""
    import wave

    import numpy as np

    from synth import SR, song

    audio = np.concatenate([
        song([(0, ""), (7, ""), (9, "m"), (5, "")], bars=6),
        np.zeros(int(SR * 2.0), dtype=np.float32),
        song([(0, ""), (7, ""), (9, "m"), (5, "")], bars=6),
        np.zeros(int(SR * 5.0), dtype=np.float32),
    ])
    path = tmp_path / "playlist.wav"
    pcm = (audio / max(float(np.abs(audio).max()), 1e-9) * 30000).astype("<i2")
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(SR)
        handle.writeframes(pcm.tobytes())
    return path


def test_split_writes_one_file_per_track(capsys, split_recording, tmp_path):
    target = tmp_path / "pieces"
    assert cli.main(["split", str(split_recording), "-D", str(target),
                     "--min-track", "5"]) == 0
    written = sorted(target.glob("*.wav"))
    assert [p.name for p in written] == ["track 01.wav", "track 02.wav"]
    out = capsys.readouterr().out
    assert "2 piece(s)" in out
    assert "is untouched" in out


def test_split_leaves_the_original_alone(split_recording, tmp_path):
    before = split_recording.read_bytes()
    cli.main(["split", str(split_recording), "-D", str(tmp_path / "out"),
              "--min-track", "5"])
    assert split_recording.read_bytes() == before


def test_a_dry_run_writes_nothing(capsys, split_recording, tmp_path):
    target = tmp_path / "pieces"
    assert cli.main(["split", str(split_recording), "-D", str(target),
                     "--min-track", "5", "--dry-run"]) == 0
    assert not target.exists()
    assert "Dry run" in capsys.readouterr().out


def test_split_writes_beside_the_recording_by_default(split_recording):
    cli.main(["split", str(split_recording), "--min-track", "5"])
    assert (split_recording.parent / "track 01.wav").is_file()


def test_a_long_min_track_leaves_nothing_and_says_why(capsys, split_recording, tmp_path):
    assert cli.main(["split", str(split_recording), "-D", str(tmp_path / "out"),
                     "--min-track", "600"]) == 1
    out = capsys.readouterr().out
    assert "Nothing to split" in out
    assert "--min-track" in out


def test_a_long_min_gap_keeps_it_in_one_piece(capsys, split_recording, tmp_path):
    assert cli.main(["split", str(split_recording), "-D", str(tmp_path / "out"),
                     "--min-track", "5", "--min-gap", "30"]) == 0
    assert "Only one piece" in capsys.readouterr().out


def test_splitting_a_missing_file(capsys, tmp_path):
    assert cli.main(["split", str(tmp_path / "gone.wav")]) == 1
    assert "omacap:" in capsys.readouterr().err


def test_split_can_chart_each_piece(capsys, split_recording, tmp_path, monkeypatch):
    charted = []

    class FakeAnalysis:
        key = type("K", (), {"short_name": "C"})()
        meter = type("M", (), {"name": "4/4"})()
        tempo = 120.0
        bar_count = 8

    def fake_analyse(path, **kwargs):
        charted.append(path)
        return FakeAnalysis()

    monkeypatch.setattr("omacap.analysis.report.analyse_file", fake_analyse)
    monkeypatch.setattr("omacap.cli.write_chart", lambda a, target, *rest: target)

    assert cli.main(["split", str(split_recording), "-D", str(tmp_path / "out"),
                     "--min-track", "5", "--analyze"]) == 0
    assert len(charted) == 2
    out = capsys.readouterr().out
    assert out.count("120 BPM") == 2


def test_one_failed_chart_does_not_stop_the_others(capsys, split_recording, tmp_path, monkeypatch):
    from omacap.analysis.report import AnalysisError

    calls = []

    def flaky(path, **kwargs):
        calls.append(path)
        if len(calls) == 1:
            raise AnalysisError("no steady beat found")
        return type("A", (), {
            "key": type("K", (), {"short_name": "C"})(),
            "meter": type("M", (), {"name": "4/4"})(),
            "tempo": 120.0, "bar_count": 8,
        })()

    monkeypatch.setattr("omacap.analysis.report.analyse_file", flaky)
    monkeypatch.setattr("omacap.cli.write_chart", lambda a, target, *rest: target)

    assert cli.main(["split", str(split_recording), "-D", str(tmp_path / "out"),
                     "--min-track", "5", "--analyze"]) == 0
    captured = capsys.readouterr()
    assert "not charted" in captured.err
    assert "120 BPM" in captured.out


# -- record --split --------------------------------------------------------

def test_record_split_reports_the_player_it_follows(capsys, stub_audio, fake_ffmpeg, tmp_path, monkeypatch):
    from omacap.capture import SplitResult

    monkeypatch.setattr(cli.capture, "choose_player", lambda name: "org.mpris.MediaPlayer2.spotify")
    monkeypatch.setattr(cli.capture, "split_recording",
                        lambda *a, **k: SplitResult(reason="nothing to do"))
    cli.main(["record", "-d", "0.3", "-f", "wav", "-D", str(tmp_path), "-S"])
    assert "org.mpris.MediaPlayer2.spotify" in capsys.readouterr().out


def test_record_split_says_when_there_is_no_player(capsys, stub_audio, fake_ffmpeg, tmp_path, monkeypatch):
    from omacap.capture import SplitResult

    monkeypatch.setattr(cli.capture, "choose_player", lambda name: None)
    monkeypatch.setattr(cli.capture, "split_recording",
                        lambda *a, **k: SplitResult(reason="nothing to do"))
    cli.main(["record", "-d", "0.3", "-f", "wav", "-D", str(tmp_path), "-S"])
    out = capsys.readouterr().out
    assert "numbered, not named" in out


def test_record_split_lists_the_pieces(capsys, stub_audio, fake_ffmpeg, tmp_path, monkeypatch):
    from omacap.capture import SplitResult

    pieces = [tmp_path / "01 - Band - One.wav", tmp_path / "02 - Band - Two.wav"]
    for piece in pieces:
        piece.write_bytes(b"\0" * 100)
    monkeypatch.setattr(cli.capture, "choose_player", lambda name: "player")
    monkeypatch.setattr(cli.capture, "split_recording",
                        lambda *a, **k: SplitResult(written=pieces, named=True))
    assert cli.main(["record", "-d", "0.3", "-f", "wav", "-D", str(tmp_path), "-S"]) == 0
    out = capsys.readouterr().out
    assert "split into 2 piece(s), named from the player" in out
    assert "01 - Band - One.wav" in out
    assert "is untouched" in out


def test_quiet_split_prints_only_the_paths(capsys, stub_audio, fake_ffmpeg, tmp_path, monkeypatch):
    from omacap.capture import SplitResult

    pieces = [tmp_path / "a.wav", tmp_path / "b.wav"]
    for piece in pieces:
        piece.write_bytes(b"\0" * 100)
    monkeypatch.setattr(cli.capture, "choose_player", lambda name: None)
    monkeypatch.setattr(cli.capture, "split_recording",
                        lambda *a, **k: SplitResult(written=pieces))
    cli.main(["record", "-d", "0.3", "-f", "wav", "-D", str(tmp_path), "-q", "-S"])
    lines = capsys.readouterr().out.strip().splitlines()
    assert lines[1:] == [str(p) for p in pieces]


def test_a_recording_that_would_not_split_says_why(capsys, stub_audio, fake_ffmpeg, tmp_path, monkeypatch):
    from omacap.capture import SplitResult

    monkeypatch.setattr(cli.capture, "choose_player", lambda name: None)
    monkeypatch.setattr(
        cli.capture, "split_recording",
        lambda *a, **k: SplitResult(reason="only one piece was found"),
    )
    assert cli.main(["record", "-d", "0.3", "-f", "wav", "-D", str(tmp_path), "-S"]) == 0
    assert "not split: only one piece was found" in capsys.readouterr().out


def test_splitting_turns_on_gap_detection(stub_audio, fake_ffmpeg, tmp_path, monkeypatch):
    """Without --split there is nothing to act on a gap, so it is not asked for."""
    built = []
    real = cli.Recorder

    class Spy(real):
        def __init__(self, config):
            built.append(config)
            super().__init__(config)

    monkeypatch.setattr(cli, "Recorder", Spy)
    monkeypatch.setattr(cli.capture, "choose_player", lambda name: None)
    monkeypatch.setattr(cli.capture, "split_recording",
                        lambda *a, **k: __import__("omacap.capture", fromlist=["x"]).SplitResult())

    cli.main(["record", "-d", "0.3", "-f", "wav", "-D", str(tmp_path)])
    assert built[-1].detect_silence is False

    cli.main(["record", "-d", "0.3", "-f", "wav", "-D", str(tmp_path), "-S"])
    assert built[-1].detect_silence is True


def test_split_and_analyze_charts_each_piece(capsys, stub_audio, fake_ffmpeg, tmp_path, monkeypatch):
    from omacap.capture import SplitResult

    pieces = [tmp_path / "one.wav", tmp_path / "two.wav"]
    for piece in pieces:
        piece.write_bytes(b"\0" * 100)
    charted = []

    def fake_analyse(path, **kwargs):
        charted.append(path)
        return type("A", (), {
            "key": type("K", (), {"short_name": "C"})(),
            "meter": type("M", (), {"name": "4/4"})(),
            "tempo": 120.0, "bar_count": 8,
        })()

    monkeypatch.setattr(cli.capture, "choose_player", lambda name: None)
    monkeypatch.setattr(cli.capture, "split_recording",
                        lambda *a, **k: SplitResult(written=pieces))
    monkeypatch.setattr("omacap.analysis.report.analyse_file", fake_analyse)
    monkeypatch.setattr("omacap.cli.write_chart", lambda a, target, *rest: target)

    assert cli.main(["record", "-d", "0.3", "-f", "wav", "-D", str(tmp_path),
                     "-S", "-A"]) == 0
    assert charted == pieces          # the pieces, not the whole recording
