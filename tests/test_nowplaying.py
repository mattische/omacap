"""Reading the current track over MPRIS, against a scripted busctl."""

from __future__ import annotations

import threading
import time

import pytest

from omacap import nowplaying
from omacap.nowplaying import (
    Track,
    TrackWatcher,
    available,
    current_track,
    find_player,
    list_players,
    parse_metadata,
    playback_status,
    position,
)

from conftest import mpris_track

SPOTIFY = "org.mpris.MediaPlayer2.spotify"

#: The real reply from a Spotify client, as recorded during development.
REAL_TRACK = mpris_track(
    "/com/spotify/track/5vSEGQVLbUtOUaQCNQ9WcX",
    title="Jag vill vara (en del av din morgondag)",
    artist=["trampe|strandberg"],
    album="det är din stund på jorden",
    number=8,
    length=226818000,
)


# -- discovery -------------------------------------------------------------

def test_mpris_is_available_when_busctl_is(busctl):
    assert available() is True


def test_unavailable_without_busctl(no_busctl):
    assert available() is False
    assert list_players() == []
    assert current_track(SPOTIFY) is None
    assert playback_status(SPOTIFY) == ""
    assert position(SPOTIFY) == 0.0


def test_players_are_listed(busctl):
    busctl(players=[SPOTIFY, "org.mpris.MediaPlayer2.mpv"])
    assert list_players() == ["org.mpris.MediaPlayer2.mpv", SPOTIFY]


def test_spotify_is_preferred_over_other_players(busctl):
    busctl(players=["org.mpris.MediaPlayer2.mpv", SPOTIFY])
    assert find_player() == SPOTIFY


def test_the_only_player_is_used(busctl):
    busctl(players=["org.mpris.MediaPlayer2.mpv"])
    assert find_player() == "org.mpris.MediaPlayer2.mpv"


def test_an_explicit_player_is_honoured(busctl):
    busctl(players=[SPOTIFY, "org.mpris.MediaPlayer2.mpv"])
    assert find_player("org.mpris.MediaPlayer2.mpv") == "org.mpris.MediaPlayer2.mpv"


def test_an_unknown_explicit_player_is_refused(busctl):
    busctl(players=[SPOTIFY])
    assert find_player("org.mpris.MediaPlayer2.vlc") is None


def test_no_players_at_all(busctl):
    busctl(players=[])
    assert list_players() == []
    assert find_player() is None


# -- reading a track -------------------------------------------------------

def test_a_real_spotify_reply_is_read(busctl):
    busctl(metadata=[REAL_TRACK])
    track = current_track(SPOTIFY)
    assert track.trackid == "/com/spotify/track/5vSEGQVLbUtOUaQCNQ9WcX"
    assert track.title == "Jag vill vara (en del av din morgondag)"
    assert track.artist == "trampe|strandberg"
    assert track.album == "det är din stund på jorden"
    assert track.track_number == 8
    assert track.length == pytest.approx(226.818)


def test_length_is_converted_from_microseconds():
    track = parse_metadata(mpris_track("x", title="t", length=226818000))
    assert track.length == pytest.approx(226.818)


def test_several_artists_are_joined():
    track = parse_metadata(mpris_track("x", title="t", artist=["A", "B"]))
    assert track.artist == "A, B"


def test_a_single_artist_string_is_accepted():
    """Not every player wraps the artist in a list."""
    assert parse_metadata(mpris_track("x", title="t", artist="Solo")).artist == "Solo"


def test_missing_fields_are_tolerated():
    track = parse_metadata(mpris_track("/x/1"))
    assert track.trackid == "/x/1"
    assert track.title == "" and track.artist == "" and track.album == ""
    assert track.track_number is None and track.length == 0.0


def test_a_nonsense_length_does_not_raise():
    assert parse_metadata(mpris_track("x", title="t", length="soon")).length == 0.0


def test_a_title_without_a_trackid_still_identifies_the_track():
    assert parse_metadata({"xesam:title": {"type": "s", "data": "Song"}}).trackid == "Song"


def test_nothing_identifiable_gives_nothing():
    assert parse_metadata({}) is None
    assert parse_metadata(None) is None
    assert parse_metadata("not a mapping") is None


def test_unparseable_output_is_not_an_error(busctl):
    busctl(garbage=True)
    assert current_track(SPOTIFY) is None


def test_a_failing_busctl_is_not_an_error(busctl):
    busctl(fail=True)
    assert current_track(SPOTIFY) is None
    assert position(SPOTIFY) == 0.0


def test_status_and_position_are_read(busctl):
    busctl(metadata=[REAL_TRACK], status="Paused", position=118000000)
    assert playback_status(SPOTIFY) == "Paused"
    assert position(SPOTIFY) == pytest.approx(118.0)


# -- track presentation ----------------------------------------------------

def test_the_label_reads_naturally():
    track = parse_metadata(REAL_TRACK)
    assert track.label == "trampe|strandberg – Jag vill vara (en del av din morgondag)"


def test_the_label_falls_back_to_the_title():
    assert parse_metadata(mpris_track("x", title="Only Title")).label == "Only Title"


def test_the_filename_is_numbered_by_position_in_the_capture():
    track = parse_metadata(REAL_TRACK)
    assert track.filename(3) == (
        "03 - trampe|strandberg - Jag vill vara (en del av din morgondag)"
    )


def test_an_untitled_track_still_gets_a_filename():
    assert parse_metadata(mpris_track("/x/1")).filename(1) == "01 - untitled"


@pytest.mark.parametrize(
    "trackid,title,expected",
    [
        ("/com/spotify/ad/1234", "Some Ad", True),
        ("/com/spotify/track/abc", "Advertisement", True),
        ("/com/spotify/track/abc", "Jag vill vara", False),
    ],
)
def test_adverts_are_recognised(trackid, title, expected):
    assert Track(trackid=trackid, title=title).is_advert is expected


# -- the watcher -----------------------------------------------------------

def make_watcher(clock=None):
    times = iter(range(1000))
    return TrackWatcher(SPOTIFY, clock or (lambda: float(next(times))))


def test_the_first_track_is_recorded(busctl):
    busctl(metadata=[REAL_TRACK])
    watcher = make_watcher()
    change = watcher.poll_once()
    assert change is not None
    assert change.track.title == "Jag vill vara (en del av din morgondag)"
    assert change.at == 0.0


def test_the_same_track_is_not_recorded_twice(busctl):
    busctl(metadata=[REAL_TRACK])
    watcher = make_watcher()
    assert watcher.poll_once() is not None
    assert watcher.poll_once() is None
    assert len(watcher.tracks) == 1


def test_each_new_track_is_recorded_with_its_time(busctl):
    busctl(metadata=[
        mpris_track("/t/1", title="One"),
        mpris_track("/t/2", title="Two"),
        mpris_track("/t/3", title="Three"),
    ])
    watcher = make_watcher()
    for _ in range(3):
        watcher.poll_once()
    assert [c.track.title for c in watcher.tracks] == ["One", "Two", "Three"]
    assert [c.at for c in watcher.tracks] == [0.0, 1.0, 2.0]


def test_nothing_is_recorded_when_no_player_answers(busctl):
    busctl(players=[])
    watcher = make_watcher()
    assert watcher.poll_once() is None
    assert watcher.tracks == []


def test_the_watcher_runs_in_the_background(busctl):
    busctl(metadata=[
        mpris_track("/t/1", title="One"),
        mpris_track("/t/2", title="Two"),
    ])
    watcher = TrackWatcher(SPOTIFY, clock=time.monotonic, poll_seconds=0.02)
    watcher.start()
    deadline = time.monotonic() + 5
    while len(watcher.tracks) < 2 and time.monotonic() < deadline:
        time.sleep(0.02)
    changes = watcher.stop()
    assert [c.track.title for c in changes] == ["One", "Two"]


def test_a_watcher_cannot_be_started_twice(busctl):
    watcher = TrackWatcher(SPOTIFY, clock=time.monotonic, poll_seconds=0.05)
    watcher.start()
    with pytest.raises(RuntimeError, match="already started"):
        watcher.start()
    watcher.stop()


def test_a_fault_in_the_watcher_never_escapes(busctl, monkeypatch):
    """A recording must not fail because the player misbehaved."""
    def boom(_player):
        raise RuntimeError("bus exploded")

    monkeypatch.setattr(nowplaying, "current_track", boom)
    watcher = TrackWatcher(SPOTIFY, clock=time.monotonic, poll_seconds=0.02)
    watcher.start()
    time.sleep(0.1)
    assert watcher.stop() == []


def test_stopping_a_watcher_that_never_started(busctl):
    assert TrackWatcher(SPOTIFY, clock=time.monotonic).stop() == []


def test_the_watcher_is_thread_safe(busctl):
    busctl(metadata=[mpris_track("/t/1", title="One")])
    watcher = make_watcher(clock=time.monotonic)
    errors = []

    def reader():
        try:
            for _ in range(200):
                watcher.tracks
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=reader) for _ in range(4)]
    for t in threads:
        t.start()
    watcher.poll_once()
    for t in threads:
        t.join()
    assert not errors


# -- what counts as a different track --------------------------------------

def test_a_player_that_never_moves_its_trackid_still_works(busctl):
    """Chromium publishes one trackid for the whole session and only changes the
    title, so keying on the id alone sees a browser playlist as one endless track."""
    constant = "/org/chromium/MediaPlayer2/TrackList/Track880048AED15C7770"
    busctl(metadata=[
        mpris_track(constant, title="Danceteria Afterhours", artist=["Madonna"]),
        mpris_track(constant, title="Kejsar", artist=["A36"]),
    ])
    watcher = make_watcher()
    watcher.poll_once()
    watcher.poll_once()
    assert [c.track.title for c in watcher.tracks] == [
        "Danceteria Afterhours", "Kejsar"
    ]


def test_the_same_track_is_still_only_one_track(busctl):
    busctl(metadata=[mpris_track("/t/1", title="One", artist=["Band"])])
    watcher = make_watcher()
    watcher.poll_once()
    watcher.poll_once()
    assert len(watcher.tracks) == 1


def test_identity_uses_more_than_the_trackid():
    same_id = Track("/same", title="One", artist="Band")
    other_title = Track("/same", title="Two", artist="Band")
    other_artist = Track("/same", title="One", artist="Other")
    assert same_id.identity != other_title.identity
    assert same_id.identity != other_artist.identity


def test_identity_still_separates_two_tracks_sharing_a_title():
    """A live and a studio take can have the same name; the id keeps them apart."""
    assert Track("/a", title="Song").identity != Track("/b", title="Song").identity
