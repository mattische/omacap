"""Recorder tests.

The ones marked as integration run a stub ``ffmpeg`` for real, so the subprocess
plumbing, progress parsing and SIGINT shutdown are all genuinely exercised.
"""

from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path

import pytest

from omacap.formats import get_format
from omacap.recorder import (
    METER_FLOOR_DB,
    Recorder,
    RecorderConfig,
    RecorderError,
    State,
    build_output_path,
    default_output_dir,
    ensure_ffmpeg,
    parse_peak_db,
    sanitize_basename,
)


# -- command construction -------------------------------------------------

def make_config(monitor_source, tmp_path, fmt="mp3", **kwargs):
    return RecorderConfig(
        source=monitor_source,
        audio_format=get_format(fmt),
        output_path=tmp_path / f"take.{fmt}",
        **kwargs,
    )


def test_command_reads_the_monitor_source(monitor_source, tmp_path):
    cmd = make_config(monitor_source, tmp_path).command()
    assert cmd[0] == "ffmpeg"
    assert "-f" in cmd and cmd[cmd.index("-f") + 1] == "pulse"
    assert cmd[cmd.index("-i") + 1] == monitor_source.name


def test_command_writes_to_the_requested_path(monitor_source, tmp_path):
    cmd = make_config(monitor_source, tmp_path).command()
    assert cmd[-1] == str(tmp_path / "take.mp3")
    assert cmd[-2] == "-y"


def test_command_asks_for_machine_readable_progress(monitor_source, tmp_path):
    cmd = make_config(monitor_source, tmp_path).command()
    assert cmd[cmd.index("-progress") + 1] == "pipe:1"


def test_metering_uses_the_log_not_a_buffered_file(monitor_source, tmp_path):
    """ametadata's file output only flushes at exit, so the meter must use the log."""
    cmd = make_config(monitor_source, tmp_path).command()
    filters = cmd[cmd.index("-af") + 1]
    assert "ametadata" in filters and "file=" not in filters
    assert cmd[cmd.index("-loglevel") + 1] == "info"


def test_metering_can_be_turned_off(monitor_source, tmp_path):
    cmd = make_config(monitor_source, tmp_path, meter=False).command()
    assert "-af" not in cmd
    assert cmd[cmd.index("-loglevel") + 1] == "error"


def test_command_carries_the_chosen_encoder(monitor_source, tmp_path):
    cmd = make_config(monitor_source, tmp_path, "flac").command()
    assert cmd[cmd.index("-c:a") + 1] == "flac"


def test_duration_becomes_a_hard_limit(monitor_source, tmp_path):
    cmd = make_config(monitor_source, tmp_path, duration=12.5).command()
    assert cmd[cmd.index("-t") + 1] == "12.500"


def test_sample_rate_and_channels_are_explicit(monitor_source, tmp_path):
    cmd = make_config(monitor_source, tmp_path, sample_rate=44100, channels=1).command()
    assert cmd[cmd.index("-ar") + 1] == "44100"
    assert cmd[cmd.index("-ac") + 1] == "1"


# -- helpers --------------------------------------------------------------

@pytest.mark.parametrize(
    "line,expected",
    [
        ("lavfi.astats.Overall.Peak_level=-12.5", -12.5),
        ("[Parsed_ametadata_1 @ 0x0] lavfi.astats.Overall.Peak_level=-0.004", -0.004),
        ("lavfi.astats.Overall.Peak_level=-inf", METER_FLOOR_DB),
        ("lavfi.astats.Overall.Peak_level=nan", METER_FLOOR_DB),
        ("out_time_us=123", None),
        ("", None),
    ],
)
def test_peak_parsing(line, expected):
    assert parse_peak_db(line) == expected


@pytest.mark.parametrize(
    "given,expected",
    [
        ("my song", "my song"),
        ("  spaced  out  ", "spaced out"),
        ("../../etc/passwd", "_.._etc_passwd"),
        ("with/slash", "with_slash"),
        ("weird:*?chars", "weird_chars"),
        ("", "omacap"),
        ("...", "omacap"),
    ],
)
def test_basenames_are_sanitised(given, expected):
    assert sanitize_basename(given) == expected


@pytest.mark.parametrize(
    "hostile",
    ["../../etc/passwd", "/absolute/path", "a/b/c", "..", "~/other", "a\\b"],
)
def test_sanitised_names_stay_inside_the_output_folder(hostile, tmp_path):
    path = build_output_path(tmp_path, get_format("wav"), hostile)
    assert path.parent == tmp_path
    assert not path.name.startswith(".")


def test_generated_names_are_timestamped(tmp_path):
    when = datetime(2026, 9, 25, 14, 30, 5)
    path = build_output_path(tmp_path, get_format("mp3"), None, now=when)
    assert path.name == "omacap_2026-09-25_14-30-05.mp3"


def test_custom_names_keep_the_format_extension(tmp_path):
    path = build_output_path(tmp_path, get_format("flac"), "Live Take")
    assert path.name == "Live Take.flac"


def test_existing_files_are_never_overwritten(tmp_path):
    (tmp_path / "take.wav").touch()
    (tmp_path / "take_2.wav").touch()
    assert build_output_path(tmp_path, get_format("wav"), "take").name == "take_3.wav"


def test_output_dir_honours_the_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv("OMACAP_OUTPUT_DIR", str(tmp_path / "elsewhere"))
    assert default_output_dir() == tmp_path / "elsewhere"


def test_output_dir_prefers_the_xdg_music_folder(monkeypatch, tmp_path):
    monkeypatch.delenv("OMACAP_OUTPUT_DIR", raising=False)
    music = tmp_path / "Music"
    music.mkdir()
    monkeypatch.setenv("XDG_MUSIC_DIR", str(music))
    assert default_output_dir() == music / "omacap"


def test_missing_ffmpeg_explains_how_to_install_it(no_ffmpeg):
    with pytest.raises(RecorderError, match="Install it"):
        ensure_ffmpeg()


# -- integration against the stub ffmpeg ----------------------------------

def wait_until(predicate, timeout=5.0, interval=0.05):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


def test_stop_finalises_the_file(fake_ffmpeg, monitor_source, tmp_path):
    recorder = Recorder(make_config(monitor_source, tmp_path))
    recorder.start()
    assert recorder.state is State.RECORDING
    assert wait_until(lambda: recorder.duration > 0.2)
    result = recorder.stop()

    assert recorder.state is State.FINISHED
    assert recorder.error is None
    assert result.exists
    assert result.duration > 0.2
    assert result.size_bytes == result.path.stat().st_size
    # The stub writes a trailer only on a clean shutdown, mirroring a real
    # container trailer such as the MP4 moov atom.
    assert result.path.read_bytes().endswith(b"TRAILER!")


def test_progress_lines_drive_the_reported_duration(fake_ffmpeg, monitor_source, tmp_path):
    recorder = Recorder(make_config(monitor_source, tmp_path))
    recorder.start()
    assert wait_until(lambda: recorder.size_bytes > 1000)
    first = recorder.duration
    assert wait_until(lambda: recorder.duration > first + 0.2)
    recorder.stop()


def test_peak_level_reaches_the_meter(fake_ffmpeg, monitor_source, tmp_path):
    recorder = Recorder(make_config(monitor_source, tmp_path))
    recorder.start()
    assert wait_until(lambda: recorder.peak_db > METER_FLOOR_DB, timeout=6.0)
    recorder.stop()


def test_duration_limit_stops_on_its_own(fake_ffmpeg, monitor_source, tmp_path):
    recorder = Recorder(make_config(monitor_source, tmp_path, duration=0.5))
    recorder.start()
    result = recorder.wait(timeout=8.0)
    assert recorder.state is State.FINISHED
    assert 0.4 <= result.duration <= 2.0
    assert result.exists


def test_a_recorder_cannot_be_reused(fake_ffmpeg, monitor_source, tmp_path):
    recorder = Recorder(make_config(monitor_source, tmp_path))
    recorder.start()
    with pytest.raises(RecorderError, match="already used"):
        recorder.start()
    recorder.stop()


def test_stopping_before_starting_is_an_error(monitor_source, tmp_path):
    with pytest.raises(RecorderError, match="never started"):
        Recorder(make_config(monitor_source, tmp_path)).stop()


def test_a_failing_ffmpeg_is_reported(failing_ffmpeg, monitor_source, tmp_path):
    recorder = Recorder(make_config(monitor_source, tmp_path))
    recorder.start()
    result = recorder.wait(timeout=5.0)
    assert recorder.state is State.FAILED
    assert "Connection refused" in (recorder.error or "")
    assert not result.exists


def test_parent_directories_are_created(fake_ffmpeg, monitor_source, tmp_path):
    nested = tmp_path / "a" / "b" / "c"
    config = RecorderConfig(
        source=monitor_source,
        audio_format=get_format("wav"),
        output_path=nested / "take.wav",
    )
    recorder = Recorder(config)
    recorder.start()
    assert wait_until(lambda: recorder.duration > 0.1)
    assert recorder.stop().exists
