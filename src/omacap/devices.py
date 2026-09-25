"""Discovery of PulseAudio / PipeWire capture sources.

To record what the computer is *playing* we do not read a microphone; we read the
"monitor" source that PulseAudio and PipeWire expose for every output device.
Recording ``<sink>.monitor`` gives us exactly the mix that reaches the speakers.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass

PACTL_TIMEOUT = 5.0


class AudioSystemError(RuntimeError):
    """Raised when the PulseAudio/PipeWire server cannot be reached."""


@dataclass(frozen=True)
class Source:
    """A capture source we can hand to ffmpeg's ``-f pulse -i``."""

    name: str
    description: str
    is_monitor: bool

    @property
    def label(self) -> str:
        return self.description or self.name


def _pactl_env() -> dict[str, str]:
    """Environment for pactl, with XDG_RUNTIME_DIR filled in if it is missing.

    Non-login shells and some service managers drop XDG_RUNTIME_DIR, and without
    it pactl cannot find the server socket.
    """
    env = dict(os.environ)
    if not env.get("XDG_RUNTIME_DIR"):
        candidate = f"/run/user/{os.getuid()}"
        if os.path.isdir(candidate):
            env["XDG_RUNTIME_DIR"] = candidate
    return env


def _run_pactl(args: list[str]) -> str:
    if shutil.which("pactl") is None:
        raise AudioSystemError(
            "'pactl' was not found. Install it with your package manager "
            "(Arch: libpulse, Debian/Ubuntu: pulseaudio-utils)."
        )
    try:
        proc = subprocess.run(
            ["pactl", *args],
            capture_output=True,
            text=True,
            timeout=PACTL_TIMEOUT,
            env=_pactl_env(),
        )
    except subprocess.TimeoutExpired as exc:
        raise AudioSystemError("pactl timed out talking to the audio server.") from exc
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip().splitlines()
        hint = detail[0] if detail else f"exit code {proc.returncode}"
        raise AudioSystemError(f"pactl failed: {hint}")
    return proc.stdout


def parse_sources_json(payload: str) -> list[Source]:
    """Parse ``pactl --format=json list sources`` output."""
    data = json.loads(payload)
    sources: list[Source] = []
    for entry in data:
        name = entry.get("name")
        if not name:
            continue
        monitor_of = entry.get("monitor_source") or entry.get("monitor_of_sink")
        is_monitor = name.endswith(".monitor") or (
            isinstance(monitor_of, str) and monitor_of not in ("", "n/a")
        )
        sources.append(
            Source(
                name=name,
                description=entry.get("description") or "",
                is_monitor=is_monitor,
            )
        )
    return sources


def parse_sources_short(payload: str) -> list[Source]:
    """Parse ``pactl list short sources`` output (fallback for old pactl)."""
    sources: list[Source] = []
    for line in payload.splitlines():
        fields = line.split("\t")
        if len(fields) < 2 or not fields[1]:
            continue
        name = fields[1]
        sources.append(Source(name=name, description="", is_monitor=name.endswith(".monitor")))
    return sources


def list_sources() -> list[Source]:
    """Every capture source the audio server knows about."""
    try:
        return parse_sources_json(_run_pactl(["--format=json", "list", "sources"]))
    except (json.JSONDecodeError, KeyError, TypeError):
        return parse_sources_short(_run_pactl(["list", "short", "sources"]))


def list_monitors() -> list[Source]:
    """Only the monitor sources, i.e. the ones that carry playback audio."""
    return [s for s in list_sources() if s.is_monitor]


def default_sink() -> str:
    """Name of the sink the system is currently playing through."""
    return _run_pactl(["get-default-sink"]).strip()


def default_monitor() -> Source:
    """The monitor source for the current default sink.

    Falls back to the first available monitor if the default sink has no
    matching monitor (which can happen with virtual or filter sinks).
    """
    monitors = list_monitors()
    if not monitors:
        raise AudioSystemError(
            "No monitor sources found. Your audio server exposes no playback "
            "monitor, so system audio cannot be captured."
        )
    try:
        wanted = f"{default_sink()}.monitor"
    except AudioSystemError:
        return monitors[0]
    for source in monitors:
        if source.name == wanted:
            return source
    return monitors[0]


def resolve_source(name: str | None) -> Source:
    """Turn a user-supplied source name into a :class:`Source`.

    ``None`` means "the default sink's monitor". A sink name is accepted and
    silently upgraded to its ``.monitor`` counterpart.
    """
    if name is None:
        return default_monitor()
    candidates = list_sources()
    by_name = {s.name: s for s in candidates}
    if name in by_name:
        return by_name[name]
    if f"{name}.monitor" in by_name:
        return by_name[f"{name}.monitor"]
    known = ", ".join(s.name for s in candidates) or "none"
    raise AudioSystemError(f"Unknown audio source {name!r}. Available: {known}")
