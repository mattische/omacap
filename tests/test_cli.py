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
