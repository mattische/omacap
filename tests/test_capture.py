"""Recording and following the player together, then cutting the result."""

from __future__ import annotations

import wave
from pathlib import Path

import numpy as np
import pytest

from omacap import capture, nowplaying
from omacap.capture import (
    SplitOptions,
    SplitResult,
    TrackSession,
    choose_player,
    plan_segments,
    split_recording,
)
from omacap.formats import get_format
from omacap.nowplaying import Track, TrackChange
from omacap.recorder import RecordingResult
from omacap.timeline import Silence

from synth import SR, song

FOUR = [(0, ""), (7, ""), (9, "m"), (5, "")]


def change(at: float, name: str) -> TrackChange:
    return TrackChange(at=at, track=Track(f"/t/{name}", title=name, artist="Band"))


def wait_for_tracks(session, count: int, timeout: float = 5.0) -> None:
    """The session runs its watcher in a thread, so wait rather than poll by hand."""
    import time

    deadline = time.monotonic() + timeout
    while len(session.changes) < count and time.monotonic() < deadline:
        time.sleep(0.02)


# -- choosing a player -----------------------------------------------------

def test_a_player_is_chosen_when_one_is_running(busctl):
    assert choose_player() == "org.mpris.MediaPlayer2.spotify"


def test_no_player_without_busctl(no_busctl):
    assert choose_player() is None


def test_no_player_when_none_is_running(busctl):
    busctl(players=[])
    assert choose_player() is None


# -- the session -----------------------------------------------------------

class FakeRecorder:
    def __init__(self):
        self.duration = 0.0
        self.started = False
        self.stopped = False

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True
        return RecordingResult(Path("/tmp/x.wav"), 10.0, 100, "wav")


def test_a_session_without_a_player_records_anyway():
    recorder = FakeRecorder()
    session = TrackSession(recorder, player=None)
    session.start()
    assert recorder.started
    assert not session.following
    assert session.changes == []
    assert session.track_count == 0
    session.stop()
    assert recorder.stopped


def test_a_session_with_a_player_follows_it(busctl):
    from conftest import mpris_track

    busctl(metadata=[mpris_track("/t/1", title="One")])
    recorder = FakeRecorder()
    session = TrackSession(recorder, player="org.mpris.MediaPlayer2.spotify")
    session.start()
    assert session.following
    wait_for_tracks(session, 1)
    session.stop()
    assert [c.track.title for c in session.changes] == ["One"]


def test_changes_are_timed_against_the_recording(busctl):
    from conftest import mpris_track

    busctl(metadata=[mpris_track("/t/1", title="One")])
    recorder = FakeRecorder()
    recorder.duration = 42.5
    session = TrackSession(recorder, player="org.mpris.MediaPlayer2.spotify")
    session.start()
    wait_for_tracks(session, 1)
    session.stop()
    assert session.changes[0].at == 42.5


def test_the_same_track_twice_counts_once(busctl):
    from conftest import mpris_track

    # The stub repeats its last entry, so polling twice sees the same track twice.
    busctl(metadata=[mpris_track("/t/1", title="One")])
    recorder = FakeRecorder()
    session = TrackSession(recorder, player="org.mpris.MediaPlayer2.spotify")
    session.start()
    wait_for_tracks(session, 1)
    import time

    time.sleep(0.3)          # long enough for several more polls
    session.stop()
    assert session.track_count == 1
    assert len(session.changes) == 1


# -- planning --------------------------------------------------------------

def test_track_changes_give_named_pieces():
    segments, named = plan_segments(
        180.0, [Silence(89.79, 92.24)],
        [change(0.0, "One"), change(91.57, "Two")],
        SplitOptions(min_track=5.0),
    )
    assert named is True
    assert [s.basename() for s in segments] == ["01 - Band - One", "02 - Band - Two"]


def test_a_single_track_change_falls_back_to_silence():
    """One change describes the whole recording; it says nothing about cutting."""
    segments, named = plan_segments(
        180.0, [Silence(89.79, 92.24)], [change(0.0, "One")],
        SplitOptions(min_track=5.0),
    )
    assert named is False
    assert [s.basename() for s in segments] == ["track 01", "track 02"]


def test_no_player_gives_numbered_pieces():
    segments, named = plan_segments(
        180.0, [Silence(89.79, 92.24)], [], SplitOptions(min_track=5.0)
    )
    assert named is False
    assert len(segments) == 2


def test_the_same_track_repeated_is_not_two_tracks():
    """Genuinely the same track: same id, same title, same artist."""
    same = Track("/t/One", title="One", artist="Band")
    repeated = [TrackChange(0.0, same), TrackChange(91.57, same)]
    _, named = plan_segments(
        180.0, [Silence(89.79, 92.24)], repeated, SplitOptions(min_track=5.0)
    )
    assert named is False


# -- splitting a finished recording ---------------------------------------

@pytest.fixture
def recording(tmp_path) -> RecordingResult:
    audio = np.concatenate([
        song(FOUR, bars=6),
        np.zeros(int(SR * 2.0), dtype=np.float32),
        song(FOUR, bars=6),
    ])
    path = tmp_path / "capture.wav"
    pcm = (audio / max(float(np.abs(audio).max()), 1e-9) * 30000).astype("<i2")
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(SR)
        handle.writeframes(pcm.tobytes())
    return RecordingResult(path, len(audio) / SR, path.stat().st_size, "wav")


def test_a_recording_is_cut_and_named(recording, tmp_path):
    outcome = split_recording(
        recording,
        [Silence(12.0, 14.0)],
        [change(0.0, "One"), change(13.0, "Two")],
        SplitOptions(min_track=5.0, directory=tmp_path / "out"),
        get_format("wav"),
    )
    assert outcome.happened and outcome.named
    assert [p.name for p in outcome.written] == [
        "01 - Band - One.wav", "02 - Band - Two.wav"
    ]
    for path in outcome.written:
        assert path.stat().st_size > 1000


def test_the_recording_survives_being_split(recording, tmp_path):
    before = recording.path.read_bytes()
    split_recording(recording, [Silence(12.0, 14.0)], [],
                    SplitOptions(min_track=5.0, directory=tmp_path / "out"),
                    get_format("wav"))
    assert recording.path.read_bytes() == before


def test_without_a_player_the_pieces_are_numbered(recording, tmp_path):
    outcome = split_recording(
        recording, [Silence(12.0, 14.0)], [],
        SplitOptions(min_track=5.0, directory=tmp_path / "out"), get_format("wav"),
    )
    assert outcome.happened and not outcome.named
    assert [p.name for p in outcome.written] == ["track 01.wav", "track 02.wav"]


def test_one_piece_is_not_a_split(recording, tmp_path):
    outcome = split_recording(
        recording, [], [], SplitOptions(min_track=5.0, directory=tmp_path / "out"),
        get_format("wav"),
    )
    assert not outcome.happened
    assert "only one piece" in outcome.reason
    assert not any((tmp_path / "out").glob("*")) if (tmp_path / "out").exists() else True


def test_nothing_long_enough_is_explained(recording, tmp_path):
    outcome = split_recording(
        recording, [Silence(12.0, 14.0)], [],
        SplitOptions(min_track=600.0, directory=tmp_path / "out"), get_format("wav"),
    )
    assert not outcome.happened
    assert "600s" in outcome.reason or "600" in outcome.reason


def test_an_empty_recording_is_not_split(tmp_path):
    missing = RecordingResult(tmp_path / "gone.wav", 0.0, 0, "wav")
    outcome = split_recording(missing, [], [], SplitOptions(), get_format("wav"))
    assert not outcome.happened
    assert "empty" in outcome.reason


def test_a_split_result_reports_nothing_written():
    assert SplitResult().happened is False


def test_a_browser_playlist_is_still_several_tracks():
    """Chromium keeps one trackid for the session, so the title has to count."""
    constant = "/org/chromium/MediaPlayer2/TrackList/Track880048"
    changes = [
        TrackChange(0.0, Track(constant, title="One", artist="Band")),
        TrackChange(91.57, Track(constant, title="Two", artist="Band")),
    ]
    segments, named = plan_segments(
        180.0, [Silence(89.79, 92.24)], changes, SplitOptions(min_track=5.0)
    )
    assert named is True
    assert [s.basename() for s in segments] == ["01 - Band - One", "02 - Band - Two"]


# -- naming a single recording -------------------------------------------

def test_one_track_for_the_whole_capture_names_the_file():
    from omacap import capture, nowplaying

    track = nowplaying.Track(trackid="/t/1", title="Reser bort", artist="Trampe")

    class Watcher:
        tracks = [nowplaying.TrackChange(at=0.0, track=track)]

    session = capture.TrackSession.__new__(capture.TrackSession)
    session.watcher = Watcher()
    assert session.only_track is track
    assert track.basename == "Trampe - Reser bort"


def test_several_tracks_name_nothing():
    """A single file called after one of two tracks would be wrong."""
    from omacap import capture, nowplaying

    first = nowplaying.Track(trackid="/t/1", title="One", artist="A")
    second = nowplaying.Track(trackid="/t/2", title="Two", artist="B")

    class Watcher:
        tracks = [nowplaying.TrackChange(at=0.0, track=first),
                  nowplaying.TrackChange(at=9.0, track=second)]

    session = capture.TrackSession.__new__(capture.TrackSession)
    session.watcher = Watcher()
    assert session.only_track is None


def test_no_player_names_nothing():
    from omacap import capture

    session = capture.TrackSession.__new__(capture.TrackSession)
    session.watcher = None
    assert session.only_track is None


def test_an_advert_is_not_a_track_to_name_a_file_after():
    from omacap import capture, nowplaying

    advert = nowplaying.Track(trackid="/com/spotify/ad/1", title="Advertisement")

    class Watcher:
        tracks = [nowplaying.TrackChange(at=0.0, track=advert)]

    session = capture.TrackSession.__new__(capture.TrackSession)
    session.watcher = Watcher()
    assert session.only_track is None


def test_renaming_keeps_the_extension_and_avoids_collisions(tmp_path):
    from omacap.recorder import rename_recording

    first = tmp_path / "omacap_2026-09-26_14-08-33.mp3"
    first.write_bytes(b"one")
    renamed = rename_recording(first, "Trampe - Reser bort")
    assert renamed.name == "Trampe - Reser bort.mp3"
    assert renamed.read_bytes() == b"one"

    second = tmp_path / "omacap_2026-09-26_14-09-00.mp3"
    second.write_bytes(b"two")
    again = rename_recording(second, "Trampe - Reser bort")
    assert again.name == "Trampe - Reser bort_2.mp3"
    assert renamed.read_bytes() == b"one"


def test_renaming_sanitises_a_title(tmp_path):
    from omacap.recorder import rename_recording

    path = tmp_path / "take.wav"
    path.write_bytes(b"x")
    renamed = rename_recording(path, "A/B: title?")
    assert "/" not in renamed.name and renamed.suffix == ".wav"


def test_renaming_to_the_same_name_is_a_no_op(tmp_path):
    from omacap.recorder import rename_recording

    path = tmp_path / "Trampe - Reser bort.mp3"
    path.write_bytes(b"x")
    assert rename_recording(path, "Trampe - Reser bort") == path
