import pytest

from omacap.formats import (
    DEFAULT_FORMAT,
    FORMAT_NAMES,
    FORMATS,
    get_format,
    next_format,
)


def test_all_formats_are_distinct():
    assert len(set(FORMAT_NAMES)) == len(FORMATS)
    assert len({f.extension for f in FORMATS}) == len(FORMATS)


def test_default_format_exists():
    assert get_format(DEFAULT_FORMAT).name == DEFAULT_FORMAT


@pytest.mark.parametrize(
    "given,expected",
    [("mp3", "mp3"), ("MP3", "mp3"), (".wav", "wav"), (" Flac ", "flac"),
     ("mp4", "m4a"), (".mp4", "m4a")],
)
def test_lookup_is_forgiving(given, expected):
    assert get_format(given).name == expected


def test_unknown_format_lists_the_valid_ones():
    with pytest.raises(ValueError, match="unknown format"):
        get_format("aiff")


def test_lossless_formats_take_no_bitrate():
    for fmt in FORMATS:
        if fmt.lossless:
            assert not fmt.supports_bitrate
            assert "-b:a" not in fmt.encoder_args()


def test_lossy_formats_apply_the_requested_bitrate():
    args = get_format("mp3").encoder_args("320k")
    assert args[:2] == ["-c:a", "libmp3lame"]
    assert "-b:a" in args and args[args.index("-b:a") + 1] == "320k"


def test_bitrate_falls_back_to_the_default():
    fmt = get_format("opus")
    assert fmt.encoder_args(None)[-1] == fmt.default_bitrate


def test_m4a_writes_a_streamable_container():
    assert "+faststart" in get_format("m4a").encoder_args()


def test_cycling_wraps_in_both_directions():
    first, last = FORMATS[0], FORMATS[-1]
    assert next_format(last.name, 1).name == first.name
    assert next_format(first.name, -1).name == last.name


def test_cycling_visits_every_format_once():
    name = FORMATS[0].name
    seen = [name]
    for _ in range(len(FORMATS) - 1):
        name = next_format(name).name
        seen.append(name)
    assert sorted(seen) == sorted(FORMAT_NAMES)
