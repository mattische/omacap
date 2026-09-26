"""Reading what a media player is currently playing, over MPRIS.

Every desktop media player - Spotify included - publishes what it is playing on
the session bus as ``org.mpris.MediaPlayer2``. That is a far better source of
track boundaries than listening for silence: it survives crossfade and gapless
playback, and it comes with the track's name, which is what the file should be
called.

Reading it needs no library. ``busctl --json=short`` returns ordinary JSON, so a
subprocess and :mod:`json` are enough, and ``busctl`` ships with systemd.

Nothing here raises for an absent player, an absent bus or an absent ``busctl``:
a recording must never fail because nobody could say what was playing.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass

MPRIS_PREFIX = "org.mpris.MediaPlayer2."
OBJECT_PATH = "/org/mpris/MediaPlayer2"
PLAYER_INTERFACE = "org.mpris.MediaPlayer2.Player"

BUSCTL_TIMEOUT = 5.0

#: How often :class:`TrackWatcher` asks. A call costs about 2 ms, so this is
#: nothing, and a quarter second is far finer than the boundary needs to be.
POLL_SECONDS = 0.25

#: Players worth preferring when several are running.
PREFERRED = ("spotify",)


@dataclass(frozen=True)
class Track:
    """What the player says is playing."""

    trackid: str
    title: str = ""
    artist: str = ""
    album: str = ""
    track_number: int | None = None
    length: float = 0.0            # seconds; 0 when the player does not say

    @property
    def is_advert(self) -> bool:
        """Adverts on a free account arrive as tracks; they are not music."""
        return "/ad/" in self.trackid or self.title.strip().lower() in (
            "advertisement", "spotify"
        )

    @property
    def identity(self) -> tuple[str, str, str]:
        """What makes this a different track from the last one.

        ``mpris:trackid`` alone is not enough. Chromium publishes one id for the
        whole session and only changes the title, so keying on the id would see a
        browser playing a playlist as a single endless track. Spotify's desktop
        client does move the id, and including it keeps two different tracks that
        happen to share a title apart.
        """
        return (self.trackid, self.title, self.artist)

    @property
    def label(self) -> str:
        """A one-line description, for the interface."""
        if self.artist and self.title:
            return f"{self.artist} – {self.title}"
        return self.title or self.trackid or "unknown"

    @property
    def basename(self) -> str:
        """What to call a file holding just this track, with no number."""
        parts = [self.artist] if self.artist else []
        parts.append(self.title or "untitled")
        return " - ".join(parts)

    def filename(self, index: int) -> str:
        """The basename for this track's file, numbered by its place in the capture."""
        return f"{index:02d} - {self.basename}"


def available() -> bool:
    """Whether MPRIS can be read at all on this machine."""
    return shutil.which("busctl") is not None


def _busctl(*args: str) -> str | None:
    """Run busctl, returning stdout, or None for any failure whatsoever."""
    if not available():
        return None
    env = dict(os.environ)
    env.setdefault("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    try:
        proc = subprocess.run(
            ["busctl", "--user", *args],
            capture_output=True,
            text=True,
            timeout=BUSCTL_TIMEOUT,
            env=env,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return proc.stdout if proc.returncode == 0 else None


def list_players() -> list[str]:
    """Every MPRIS player currently on the session bus."""
    output = _busctl("list", "--no-legend")
    if not output:
        return []
    names = {
        line.split()[0]
        for line in output.splitlines()
        if line.startswith(MPRIS_PREFIX)
    }
    return sorted(names)


def find_player(preferred: str | None = None) -> str | None:
    """Pick a player to follow.

    An explicit name wins. Otherwise Spotify is preferred over whatever else
    happens to be running, since it is the case this was built for.
    """
    players = list_players()
    if preferred:
        return preferred if preferred in players else None
    for name in players:
        if any(hint in name.lower() for hint in PREFERRED):
            return name
    return players[0] if players else None


def _get_property(player: str, name: str):
    """One property of the Player interface, already unwrapped from its variant."""
    output = _busctl(
        "--json=short", "get-property", player, OBJECT_PATH, PLAYER_INTERFACE, name
    )
    if not output:
        return None
    try:
        return json.loads(output).get("data")
    except (ValueError, AttributeError):
        return None


def _unwrap(value):
    """busctl wraps every variant as {"type": ..., "data": ...}."""
    if isinstance(value, dict) and "data" in value:
        return value["data"]
    return value


def parse_metadata(data) -> Track | None:
    """Turn a decoded MPRIS metadata dictionary into a :class:`Track`."""
    if not isinstance(data, dict):
        return None
    field = {key: _unwrap(value) for key, value in data.items()}

    trackid = field.get("mpris:trackid") or ""
    if not isinstance(trackid, str):
        trackid = str(trackid)

    artist = field.get("xesam:artist") or []
    if isinstance(artist, str):
        artist = [artist]
    artist_text = ", ".join(str(a) for a in artist if a)

    length = field.get("mpris:length") or 0
    try:
        length_seconds = max(0.0, float(length) / 1_000_000)
    except (TypeError, ValueError):
        length_seconds = 0.0

    number = field.get("xesam:trackNumber")
    try:
        track_number = int(number) if number is not None else None
    except (TypeError, ValueError):
        track_number = None

    title = str(field.get("xesam:title") or "")
    if not trackid and not title:
        return None
    return Track(
        trackid=trackid or title,
        title=title,
        artist=artist_text,
        album=str(field.get("xesam:album") or ""),
        track_number=track_number,
        length=length_seconds,
    )


def current_track(player: str) -> Track | None:
    """What ``player`` is playing right now, or None if it will not say."""
    return parse_metadata(_get_property(player, "Metadata"))


def playback_status(player: str) -> str:
    """``Playing``, ``Paused``, ``Stopped``, or an empty string when unknown."""
    status = _get_property(player, "PlaybackStatus")
    return status if isinstance(status, str) else ""


def position(player: str) -> float:
    """How far into the track the player is, in seconds. 0 when unknown."""
    value = _get_property(player, "Position")
    try:
        return max(0.0, float(value) / 1_000_000)
    except (TypeError, ValueError):
        return 0.0


@dataclass
class TrackChange:
    """A track starting, timed against whatever clock the caller supplied."""

    at: float
    track: Track


class TrackWatcher:
    """Follows a player in the background and records when the track changes.

    ``clock`` is whatever the caller wants events timed against - during a
    recording that is the recorder's own elapsed time, which puts every change
    directly on the file's timeline with no wall-clock conversion.
    """

    def __init__(
        self,
        player: str,
        clock,
        poll_seconds: float = POLL_SECONDS,
    ) -> None:
        self.player = player
        self.clock = clock
        self.poll_seconds = poll_seconds
        self.changes: list[TrackChange] = []
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None:
            raise RuntimeError("watcher already started")
        self._thread = threading.Thread(
            target=self._run, daemon=True, name="omacap-nowplaying"
        )
        self._thread.start()

    def stop(self, timeout: float = 2.0) -> list[TrackChange]:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
        return self.tracks

    @property
    def tracks(self) -> list[TrackChange]:
        with self._lock:
            return list(self.changes)

    def poll_once(self) -> TrackChange | None:
        """Read the player once, recording a change if the track is new."""
        track = current_track(self.player)
        if track is None:
            return None
        with self._lock:
            if self.changes and self.changes[-1].track.identity == track.identity:
                return None
            change = TrackChange(at=self.clock(), track=track)
            self.changes.append(change)
        return change

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.poll_once()
            except Exception:
                pass          # never let a watcher fault reach the recording
            self._stop.wait(self.poll_seconds)
