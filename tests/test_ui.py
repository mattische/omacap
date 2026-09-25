import re

import pytest

from omacap.recorder import METER_FLOOR_DB
from omacap.ui import (
    MAX_WIDTH,
    MIN_WIDTH,
    ViewModel,
    colors_enabled,
    format_duration,
    format_size,
    level_text,
    meter_bar,
    render,
    truncate,
    visible_len,
)

ANSI = re.compile(r"\x1b\[[0-9;]*m")


def sample(**overrides) -> ViewModel:
    base = dict(
        source_label="Monitor of USB Audio Analog Stereo",
        format_name="mp3",
        format_description="Lossy, universally playable.",
        output_dir="~/Recordings/omacap",
    )
    base.update(overrides)
    return ViewModel(**base)


@pytest.mark.parametrize(
    "seconds,expected",
    [(0, "00:00"), (5.9, "00:05"), (59, "00:59"), (60, "01:00"),
     (599, "09:59"), (3600, "1:00:00"), (3725, "1:02:05"), (-4, "00:00")],
)
def test_duration_formatting(seconds, expected):
    assert format_duration(seconds) == expected


@pytest.mark.parametrize(
    "size,expected",
    [(0, "0 B"), (512, "512 B"), (1024, "1.0 KB"), (1536, "1.5 KB"),
     (1048576, "1.0 MB"), (1073741824, "1.00 GB")],
)
def test_size_formatting(size, expected):
    assert format_size(size) == expected


def test_meter_is_empty_at_the_floor():
    bar, _ = meter_bar(METER_FLOOR_DB, 20)
    assert "█" not in bar
    assert len(bar) == 20


def test_meter_is_full_at_zero_dbfs():
    bar, _ = meter_bar(0.0, 20)
    assert bar == "█" * 20


def test_meter_grows_with_level():
    widths = [meter_bar(db, 40)[0].count("█") for db in (-50, -30, -10, -1)]
    assert widths == sorted(widths)
    assert len(set(widths)) == 4


def test_meter_keeps_a_constant_width():
    for db in (-100, METER_FLOOR_DB, -25, 0, 6):
        assert len(meter_bar(db, 17)[0]) == 17


def test_meter_colour_warns_as_it_approaches_clipping():
    green = meter_bar(-40, 10)[1]
    amber = meter_bar(-8, 10)[1]
    red = meter_bar(-1, 10)[1]
    assert len({green, amber, red}) == 3


def test_silence_is_spelled_out():
    assert level_text(METER_FLOOR_DB).strip() == "silent"
    assert "dB" in level_text(-12.3)


@pytest.mark.parametrize("width", [MIN_WIDTH, 60, 72, MAX_WIDTH, 200, 10])
def test_every_line_is_the_same_width(width):
    lines = render(sample(state="recording", elapsed=75, size_bytes=2_000_000,
                          meter_db=-9.0, bitrate="192k"), width, use_color=False)
    widths = {len(line) for line in lines}
    assert len(widths) == 1
    assert MIN_WIDTH <= widths.pop() <= MAX_WIDTH


def test_frame_is_closed_on_all_sides():
    lines = render(sample(), 72, use_color=False)
    assert lines[0].startswith("┌") and lines[0].endswith("┐")
    assert lines[-1].startswith("└") and lines[-1].endswith("┘")
    for line in lines[1:-1]:
        assert line[0] in "│├" and line[-1] in "│┤"


def test_colour_codes_do_not_change_the_layout():
    plain = render(sample(state="recording"), 72, use_color=False)
    coloured = render(sample(state="recording"), 72, use_color=True)
    assert [ANSI.sub("", line) for line in coloured] == plain
    assert all(visible_len(line) == 72 for line in coloured)


def test_idle_screen_says_ready():
    text = "\n".join(render(sample(), 72, use_color=False))
    assert "READY" in text and "REC" not in text
    assert "space record" in text


def test_recording_screen_says_rec_and_offers_stop():
    text = "\n".join(render(sample(state="recording"), 72, use_color=False))
    assert "REC" in text
    assert "space stop" in text


def test_settings_are_all_visible():
    text = "\n".join(render(sample(bitrate="256k"), 72, use_color=False))
    assert "Monitor of USB Audio Analog Stereo" in text
    assert "mp3" in text and "256k" in text
    assert "~/Recordings/omacap" in text


def test_lossless_formats_show_no_bitrate():
    text = "\n".join(render(sample(format_name="wav", bitrate=None), 72, use_color=False))
    assert "192k" not in text


def test_help_lists_every_key():
    text = "\n".join(render(sample(show_help=True), 72, use_color=False))
    for key in ("space / r", "f / F", "b", "d", "n", "?", "q"):
        assert key in text


def test_name_prompt_replaces_the_help_and_session_list():
    vm = sample(name_prompt="my take", show_help=True, recordings=["old.mp3"])
    text = "\n".join(render(vm, 72, use_color=False))
    assert "my take" in text and "esc to cancel" in text
    assert "old.mp3" not in text


def test_session_list_shows_the_most_recent_takes():
    vm = sample(recordings=[f"take{i}.mp3" for i in range(9)])
    text = "\n".join(render(vm, 72, use_color=False))
    assert "take8.mp3" in text and "take4.mp3" in text
    assert "take0.mp3" not in text


def test_long_values_are_truncated_not_wrapped():
    vm = sample(source_label="x" * 500, output_dir="/very/long/" + "y" * 500)
    lines = render(vm, 60, use_color=False)
    assert len({len(line) for line in lines}) == 1


@pytest.mark.parametrize(
    "text,limit,expected",
    [("hello", 10, "hello"), ("hello", 5, "hello"), ("hello", 4, "hel…"),
     ("hello", 1, "…"), ("hello", 0, "")],
)
def test_truncation(text, limit, expected):
    assert truncate(text, limit) == expected


def test_messages_are_shown():
    text = "\n".join(render(sample(message="Saved take.mp3", message_kind="success"),
                            72, use_color=False))
    assert "Saved take.mp3" in text


def test_no_color_env_is_respected(monkeypatch):
    monkeypatch.setenv("NO_COLOR", "1")
    assert colors_enabled(True) is False


def test_dumb_terminals_get_no_colour(monkeypatch):
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("TERM", "dumb")
    assert colors_enabled(True) is False


def test_colour_is_on_for_a_normal_tty(monkeypatch):
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("TERM", "xterm-256color")
    assert colors_enabled(True) is True
    assert colors_enabled(False) is False
