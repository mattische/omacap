"""Silence detection and cutting, against real ffmpeg and synthesised audio."""

from __future__ import annotations

import wave
from pathlib import Path

import numpy as np
import pytest

from omacap.formats import get_format
from omacap.splitter import (
    DEFAULT_THRESHOLD_DB,
    SplitError,
    cut,
    detect_silences,
    probe_duration,
    split,
    target_path,
)
from omacap.timeline import Segment, Silence, plan_from_silence

from synth import SR, song

FOUR = [(0, ""), (7, ""), (9, "m"), (5, "")]


def write_wav(path: Path, samples: np.ndarray, rate: int = SR) -> Path:
    peak = float(np.abs(samples).max()) or 1.0
    pcm = (samples / peak * 30000).astype("<i2")
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(pcm.tobytes())
    return path


def silent(seconds: float) -> np.ndarray:
    return np.zeros(int(SR * seconds), dtype=np.float32)


@pytest.fixture(scope="module")
def playlist(tmp_path_factory) -> Path:
    """Three 'tracks' with a 2 s gap, a 1 s gap, and 6 s of trailing silence."""
    directory = tmp_path_factory.mktemp("playlist")
    audio = np.concatenate([
        song(FOUR, bars=6),          # ~12 s
        silent(2.0),
        song(FOUR, bars=6),
        silent(1.0),
        song(FOUR, bars=6),
        silent(6.0),
    ])
    return write_wav(directory / "playlist.wav", audio)


# -- probing ---------------------------------------------------------------

def test_the_duration_is_read(playlist):
    assert probe_duration(playlist) == pytest.approx(12 * 3 + 2 + 1 + 6, abs=0.5)


def test_a_missing_file_is_reported(tmp_path):
    with pytest.raises(SplitError, match="could not read|no such file"):
        probe_duration(tmp_path / "gone.wav")


# -- detection -------------------------------------------------------------

def test_every_gap_is_found(playlist):
    silences = detect_silences(playlist)
    assert len(silences) == 3
    assert [round(s.duration) for s in silences] == [2, 1, 6]


def test_gaps_are_where_they_should_be(playlist):
    first = detect_silences(playlist)[0]
    assert first.start == pytest.approx(12.0, abs=0.5)
    assert first.end == pytest.approx(14.0, abs=0.5)


def test_the_trailing_silence_is_closed_at_the_end(playlist):
    """A silence with no end is the recording trailing off, not a mistake."""
    last = detect_silences(playlist)[-1]
    assert last.end == pytest.approx(probe_duration(playlist), abs=0.2)


def test_continuous_music_has_no_gaps(tmp_path):
    path = write_wav(tmp_path / "solid.wav", song(FOUR, bars=8))
    assert detect_silences(path) == []


def test_a_stricter_threshold_still_finds_true_silence(playlist):
    assert len(detect_silences(playlist, threshold_db=-80.0)) == 3


def test_detecting_in_a_missing_file(tmp_path):
    with pytest.raises(SplitError, match="no such file"):
        detect_silences(tmp_path / "gone.wav")


# -- naming ----------------------------------------------------------------

def test_the_target_keeps_a_readable_title(tmp_path):
    from omacap.nowplaying import Track

    segment = Segment(1, 0.0, 10.0, Track("/t/1", title="What's Going On?", artist="Marvin"))
    target = target_path(segment, tmp_path, get_format("flac"))
    assert target.name == "01 - Marvin - What's Going On.flac"


def test_an_existing_file_is_not_overwritten(tmp_path):
    segment = Segment(1, 0.0, 10.0)
    first = target_path(segment, tmp_path, get_format("wav"))
    first.touch()
    assert target_path(segment, tmp_path, get_format("wav")).name == "track 01_2.wav"


# -- cutting ---------------------------------------------------------------

def decoded_seconds(path: Path) -> float:
    import subprocess

    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path), "-f", "s16le", "-ac", "1",
         "-ar", str(SR), "-"],
        capture_output=True).stdout
    return len(raw) / 2 / SR


def test_a_segment_is_cut_to_length(playlist, tmp_path):
    target = cut(playlist, Segment(1, 2.0, 10.0), tmp_path / "piece.wav", get_format("wav"))
    assert target.is_file()
    assert decoded_seconds(target) == pytest.approx(8.0, abs=0.2)


def test_a_cut_flac_reports_its_real_length(playlist, tmp_path):
    """FLAC stream copy writes the source's duration into the header, so the
    splitter re-encodes instead. The header and the audio must agree."""
    source = tmp_path / "whole.flac"
    import subprocess
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(playlist),
                    "-c:a", "flac", "-y", str(source)], check=True)
    target = cut(source, Segment(1, 2.0, 10.0), tmp_path / "piece.flac", get_format("flac"))
    assert probe_duration(target) == pytest.approx(8.0, abs=0.2)
    assert decoded_seconds(target) == pytest.approx(8.0, abs=0.2)


@pytest.mark.parametrize("name", ["wav", "mp3", "m4a", "opus"])
def test_every_format_cuts(playlist, tmp_path, name):
    audio_format = get_format(name)
    import subprocess
    source = tmp_path / f"whole{audio_format.extension}"
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(playlist),
                    *audio_format.encoder_args(), "-y", str(source)], check=True)
    target = cut(source, Segment(1, 2.0, 10.0),
                 tmp_path / f"piece{audio_format.extension}", audio_format)
    assert decoded_seconds(target) == pytest.approx(8.0, abs=0.3)


def test_cutting_past_the_end_is_reported(playlist, tmp_path):
    """ffmpeg exits 0 and writes a bare header, so the size alone proves nothing."""
    target = tmp_path / "nope.wav"
    with pytest.raises(SplitError, match="came out empty"):
        cut(playlist, Segment(1, 500.0, 600.0), target, get_format("wav"))
    assert not target.exists()      # no empty file left behind


# -- splitting -------------------------------------------------------------

def test_a_playlist_is_split_into_its_tracks(playlist, tmp_path):
    duration = probe_duration(playlist)
    segments = plan_from_silence(duration, detect_silences(playlist), min_track=5.0)
    written = split(playlist, segments, tmp_path)

    assert len(written) == 3
    assert [p.name for p in written] == ["track 01.wav", "track 02.wav", "track 03.wav"]
    for path in written:
        assert decoded_seconds(path) == pytest.approx(12.0, abs=1.0)


def test_the_original_is_left_alone(playlist, tmp_path):
    before = playlist.stat().st_size
    segments = plan_from_silence(probe_duration(playlist), detect_silences(playlist),
                                 min_track=5.0)
    split(playlist, segments, tmp_path)
    assert playlist.is_file() and playlist.stat().st_size == before


def test_the_trailing_silence_becomes_no_file(playlist, tmp_path):
    segments = plan_from_silence(probe_duration(playlist), detect_silences(playlist),
                                 min_track=5.0)
    assert len(split(playlist, segments, tmp_path)) == 3      # not 4


def test_pieces_land_beside_the_source_by_default(playlist, tmp_path):
    copy = tmp_path / "copy.wav"
    copy.write_bytes(playlist.read_bytes())
    segments = plan_from_silence(probe_duration(copy), detect_silences(copy),
                                 min_track=5.0)
    for path in split(copy, segments):
        assert path.parent == tmp_path


def test_splitting_a_missing_file(tmp_path):
    with pytest.raises(SplitError, match="no such file"):
        split(tmp_path / "gone.wav", [Segment(1, 0.0, 1.0)], tmp_path)


def test_an_unknown_format_cannot_be_split(tmp_path):
    odd = tmp_path / "recording.xyz"
    odd.write_bytes(b"not audio")
    with pytest.raises(SplitError, match="cannot tell the format"):
        split(odd, [Segment(1, 0.0, 1.0)], tmp_path)


def test_nothing_to_split_writes_nothing(playlist, tmp_path):
    assert split(playlist, [], tmp_path) == []


def test_a_split_piece_carries_the_track_s_tags(tmp_path, monkeypatch):
    """The player already said what the piece is; the file should say it too."""
    import subprocess

    from omacap import splitter
    from omacap.nowplaying import Track
    from omacap.timeline import Segment

    seen = {}

    def fake_run(command, **kwargs):
        seen["command"] = command
        Path(command[-1]).write_bytes(b"x" * 4096)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(splitter.subprocess, "run", fake_run)
    monkeypatch.setattr(splitter, "_written_duration", lambda path: 30.0)
    monkeypatch.setattr(splitter, "ensure_ffmpeg", lambda: None)

    track = Track(trackid="/t/1", title="Reser bort", artist="Trampe")
    segment = Segment(index=3, start=0.0, end=30.0, track=track)
    source = tmp_path / "take.mp3"
    source.write_bytes(b"x")
    splitter.cut(source, segment, tmp_path / "out.mp3", get_format("mp3"))

    command = seen["command"]
    assert "title=Reser bort" in command
    assert "artist=Trampe" in command
    # Its place in the capture, when the player did not say which track it is.
    assert "track=3" in command


def test_an_unnamed_piece_is_still_numbered(tmp_path, monkeypatch):
    import subprocess

    from omacap import splitter
    from omacap.timeline import Segment

    seen = {}

    def fake_run(command, **kwargs):
        seen["command"] = command
        Path(command[-1]).write_bytes(b"x" * 4096)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(splitter.subprocess, "run", fake_run)
    monkeypatch.setattr(splitter, "_written_duration", lambda path: 30.0)
    monkeypatch.setattr(splitter, "ensure_ffmpeg", lambda: None)

    source = tmp_path / "take.mp3"
    source.write_bytes(b"x")
    splitter.cut(source, Segment(index=2, start=0.0, end=30.0),
                 tmp_path / "out.mp3", get_format("mp3"))
    assert "track=2" in seen["command"]
    assert not any(a.startswith("title=") for a in seen["command"])
