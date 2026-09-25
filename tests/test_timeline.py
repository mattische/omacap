"""Segment planning. Pure logic, so every case is just numbers."""

from __future__ import annotations

import pytest

from omacap.nowplaying import Track, TrackChange
from omacap.timeline import (
    DEFAULT_MIN_GAP,
    DEFAULT_MIN_TRACK,
    DEFAULT_PAD,
    Segment,
    Silence,
    boundaries_from_changes,
    plan_from_changes,
    plan_from_silence,
    snap,
    usable_silences,
)


def track(name: str, trackid: str | None = None) -> Track:
    return Track(trackid=trackid or f"/t/{name}", title=name, artist="Band")


def change(at: float, name: str, trackid: str | None = None) -> TrackChange:
    return TrackChange(at=at, track=track(name, trackid))


# -- silence -------------------------------------------------------------

def test_a_silence_knows_its_shape():
    gap = Silence(10.0, 12.5)
    assert gap.duration == 2.5
    assert gap.middle == 11.25
    assert gap.contains(11.0) and not gap.contains(9.0) and not gap.contains(13.0)


def test_short_gaps_are_not_boundaries():
    gaps = [Silence(1.0, 1.2), Silence(5.0, 8.0)]
    assert usable_silences(gaps, min_gap=0.8) == [Silence(5.0, 8.0)]


def test_usable_silences_come_back_in_order():
    gaps = [Silence(30.0, 32.0), Silence(5.0, 8.0)]
    assert [g.start for g in usable_silences(gaps)] == [5.0, 30.0]


# -- snapping ------------------------------------------------------------

def test_a_signal_inside_a_gap_belongs_to_it():
    """The measured case: Spotify announced 1.78 s into a 2.45 s gap."""
    gaps = [Silence(89.79, 92.24)]
    assert snap(91.57, gaps) == gaps[0]


def test_containment_beats_a_closer_neighbour():
    inside = Silence(10.0, 20.0)
    closer = Silence(20.4, 21.0)
    assert snap(19.9, [inside, closer]) == inside


def test_a_signal_just_outside_snaps_to_the_nearest():
    gaps = [Silence(10.0, 12.0)]
    assert snap(13.0, gaps) == gaps[0]
    assert snap(9.0, gaps) == gaps[0]


def test_a_signal_far_from_everything_snaps_to_nothing():
    assert snap(60.0, [Silence(10.0, 12.0)]) is None


def test_snapping_without_any_silence():
    assert snap(10.0, []) is None


# -- boundaries ----------------------------------------------------------

def test_boundaries_land_in_the_middle_of_the_gap():
    gaps = [Silence(10.0, 14.0)]
    assert boundaries_from_changes([change(12.0, "Two")], gaps) == [12.0]


def test_a_boundary_with_no_gap_uses_the_signal_itself():
    """Crossfade and gapless leave nothing to snap to."""
    assert boundaries_from_changes([change(30.0, "Two")], []) == [30.0]


# -- planning from track changes -----------------------------------------

def test_the_measured_spotify_shape():
    segments = plan_from_changes(
        180.0,
        [change(0.01, "One"), change(91.57, "Two")],
        [Silence(89.79, 92.24)],
    )
    assert len(segments) == 2
    assert segments[0].start == 0.0
    assert segments[0].end == pytest.approx(89.79 + DEFAULT_PAD)
    assert segments[1].start == pytest.approx(92.24 - DEFAULT_PAD)
    assert segments[1].end == 180.0


def test_the_first_track_starts_at_the_recording_not_its_announcement():
    """Recording usually begins part way through the first track."""
    segments = plan_from_changes(
        120.0, [change(3.0, "One"), change(61.0, "Two")], [Silence(59.0, 62.0)]
    )
    assert segments[0].start == 0.0


def test_segments_are_numbered_and_named():
    segments = plan_from_changes(
        200.0,
        [change(0.0, "One"), change(70.0, "Two"), change(140.0, "Three")],
        [Silence(68.0, 71.0), Silence(138.0, 141.0)],
    )
    assert [s.index for s in segments] == [1, 2, 3]
    assert segments[1].basename() == "02 - Band - Two"
    assert segments[1].title == "Band – Two"


def test_segments_do_not_overlap_and_stay_in_the_recording():
    segments = plan_from_changes(
        300.0,
        [change(0.0, "a"), change(100.0, "b"), change(200.0, "c")],
        [Silence(98.0, 101.0), Silence(198.0, 201.0)],
    )
    for earlier, later in zip(segments, segments[1:]):
        assert earlier.end <= later.start
    assert segments[0].start >= 0.0
    assert segments[-1].end <= 300.0


def test_a_fragment_from_skipping_is_dropped():
    segments = plan_from_changes(
        200.0,
        [change(0.0, "One"), change(60.0, "Skipped"), change(64.0, "Three")],
        [Silence(58.0, 61.0), Silence(62.0, 65.0)],
        min_track=20.0,
    )
    assert [s.track.title for s in segments] == ["One", "Three"]
    assert [s.index for s in segments] == [1, 2]      # renumbered after the drop


def test_adverts_are_dropped():
    advert = TrackChange(at=60.0, track=Track("/com/spotify/ad/9", title="Ad"))
    segments = plan_from_changes(
        200.0,
        [change(0.0, "One"), advert, change(120.0, "Two")],
        [Silence(58.0, 61.0), Silence(118.0, 121.0)],
    )
    assert [s.track.title for s in segments] == ["One", "Two"]


def test_no_track_changes_means_no_plan():
    assert plan_from_changes(200.0, [], [Silence(10.0, 12.0)]) == []


def test_crossfade_still_splits_without_any_silence():
    segments = plan_from_changes(200.0, [change(0.0, "One"), change(100.0, "Two")], [])
    assert len(segments) == 2
    assert segments[0].end == pytest.approx(100.0 + DEFAULT_PAD)
    assert segments[1].start == pytest.approx(100.0 - DEFAULT_PAD)


# -- planning from silence alone -----------------------------------------

def test_silence_alone_splits_a_file():
    segments = plan_from_silence(
        200.0, [Silence(60.0, 63.0), Silence(130.0, 133.0)]
    )
    assert len(segments) == 3
    assert segments[0].start == 0.0
    assert segments[0].end == pytest.approx(60.0 + DEFAULT_PAD)
    assert segments[1].start == pytest.approx(63.0 - DEFAULT_PAD)


def test_silence_alone_names_segments_by_number():
    segments = plan_from_silence(200.0, [Silence(60.0, 63.0)])
    assert segments[0].basename() == "track 01"
    assert segments[0].title == "track 01"
    assert segments[0].track is None


def test_a_file_with_no_gaps_is_one_segment():
    segments = plan_from_silence(200.0, [])
    assert len(segments) == 1
    assert (segments[0].start, segments[0].end) == (0.0, 200.0)


def test_trailing_silence_does_not_become_a_track():
    segments = plan_from_silence(200.0, [Silence(120.0, 200.0)])
    assert len(segments) == 1
    assert segments[0].end == pytest.approx(120.0 + DEFAULT_PAD)


def test_leading_silence_is_skipped():
    segments = plan_from_silence(200.0, [Silence(0.0, 30.0)])
    assert len(segments) == 1
    assert segments[0].start == pytest.approx(30.0 - DEFAULT_PAD)


def test_short_gaps_do_not_split_a_track():
    segments = plan_from_silence(200.0, [Silence(100.0, 100.3)], min_gap=0.8)
    assert len(segments) == 1


def test_a_recording_too_short_to_hold_a_track():
    assert plan_from_silence(10.0, [], min_track=20.0) == []


@pytest.mark.parametrize("pad", [0.0, 0.25, 1.0])
def test_padding_widens_the_segment_without_leaving_the_recording(pad):
    segments = plan_from_silence(200.0, [Silence(60.0, 63.0)], pad=pad)
    assert segments[0].end == pytest.approx(60.0 + pad)
    assert segments[0].start == 0.0
    assert segments[-1].end == 200.0


def test_a_segment_is_never_backwards():
    for segment in plan_from_silence(200.0, [Silence(0.0, 199.0)], min_track=0.0):
        assert segment.end >= segment.start


# -- reading ffmpeg's silence events ---------------------------------------

from omacap.timeline import parse_silence_line, silences_from_events


@pytest.mark.parametrize(
    "line,expected",
    [
        ("[silencedetect @ 0x0] silence_start: 89.79", ("start", 89.79)),
        ("[silencedetect @ 0x0] silence_end: 92.24 | silence_duration: 2.45",
         ("end", 92.24)),
        ("silence_start: -0.043", ("start", -0.043)),
        ("lavfi.astats.Overall.Peak_level=-12.5", None),
        ("", None),
    ],
)
def test_silence_lines_are_read(line, expected):
    assert parse_silence_line(line) == expected


def test_events_pair_into_intervals():
    events = [("start", 12.0), ("end", 14.0), ("start", 26.0), ("end", 26.8)]
    assert silences_from_events(events, 60.0) == [Silence(12.0, 14.0), Silence(26.0, 26.8)]


def test_a_silence_that_never_ends_is_closed_at_the_recording_end():
    """Exactly the case that should stop a capture."""
    assert silences_from_events([("start", 38.8)], 58.8) == [Silence(38.8, 58.8)]


def test_a_stray_end_is_ignored():
    assert silences_from_events([("end", 5.0), ("start", 10.0), ("end", 12.0)], 60.0) == [
        Silence(10.0, 12.0)
    ]


def test_a_repeated_start_does_not_reopen():
    assert silences_from_events(
        [("start", 10.0), ("start", 11.0), ("end", 12.0)], 60.0
    ) == [Silence(10.0, 12.0)]


def test_events_are_clamped_to_the_recording():
    """ffmpeg reports a slightly negative time for silence at the very start."""
    assert silences_from_events([("start", -0.04), ("end", 2.0)], 60.0) == [
        Silence(0.0, 2.0)
    ]


def test_no_events_means_no_silence():
    assert silences_from_events([], 60.0) == []
