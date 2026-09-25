"""Pure rendering for the TUI.

Everything here turns a :class:`ViewModel` into a list of terminal lines. Keeping
it free of I/O means the whole screen can be rendered - and asserted on - in tests.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

from . import __version__
from .recorder import METER_FLOOR_DB

MIN_WIDTH = 48
MAX_WIDTH = 76
LABEL_WIDTH = 8

RESET = "\x1b[0m"
BOLD = "\x1b[1m"
DIM = "\x1b[2m"
RED = "\x1b[31m"
GREEN = "\x1b[32m"
YELLOW = "\x1b[33m"
CYAN = "\x1b[36m"
GREY = "\x1b[90m"

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")

KEY_HELP: tuple[tuple[str, str], ...] = (
    ("space / r", "start or stop recording"),
    ("f / F", "next / previous output format"),
    ("b", "cycle bitrate (lossy formats only)"),
    ("d", "cycle capture source"),
    ("n", "name the next recording"),
    ("a", "analyse the last take into a chord chart"),
    ("y / n", "answer the question asked after a recording"),
    ("y / n", "answer the question after a recording"),
    ("t", "chart format: markdown or plain text"),
    ("?", "toggle this help"),
    ("q", "quit"),
)


def colors_enabled(stream_is_tty: bool = True) -> bool:
    """Honour NO_COLOR, TERM=dumb and non-tty output."""
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("TERM") in ("dumb", ""):
        return False
    return stream_is_tty


@dataclass
class ViewModel:
    """A snapshot of everything the screen shows."""

    source_label: str
    format_name: str
    format_description: str
    output_dir: str
    state: str = "idle"
    bitrate: str | None = None
    elapsed: float = 0.0
    size_bytes: int = 0
    meter_db: float = METER_FLOOR_DB
    next_name: str | None = None
    chart_format: str = "md"
    can_analyse: bool = False
    recordings: list[str] = field(default_factory=list)
    message: str = ""
    message_kind: str = "info"
    show_help: bool = False
    name_prompt: str | None = None
    analyse_prompt: str | None = None
    split_prompt: str | None = None
    track_count: int = 0
    clipping: bool = False
    player_label: str = ""
    update_notice: str = ""


def visible_len(text: str) -> int:
    """Length of ``text`` ignoring ANSI colour codes."""
    return len(_ANSI_RE.sub("", text))


def format_duration(seconds: float) -> str:
    """``h:mm:ss`` once an hour is reached, ``mm:ss`` before that."""
    total = int(max(0.0, seconds))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def format_size(num_bytes: int) -> str:
    """Human-readable byte count."""
    value = float(max(0, num_bytes))
    for unit in ("B", "KB", "MB"):
        if value < 1024:
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.2f} GB"


def meter_bar(db: float, width: int) -> tuple[str, str]:
    """A level bar plus the colour it should be drawn in.

    The scale runs from :data:`METER_FLOOR_DB` up to 0 dBFS.
    """
    width = max(4, width)
    fraction = 0.0 if db <= METER_FLOOR_DB else min(
        1.0, (db - METER_FLOOR_DB) / -METER_FLOOR_DB
    )
    filled = int(round(fraction * width))
    if db >= -3.0:
        colour = RED
    elif db >= -12.0:
        colour = YELLOW
    else:
        colour = GREEN
    return "█" * filled + "┄" * (width - filled), colour


def level_text(db: float) -> str:
    return "  silent" if db <= METER_FLOOR_DB else f"{db:6.1f} dB"


def truncate(text: str, limit: int) -> str:
    """Shorten plain text to ``limit`` columns with a trailing ellipsis."""
    if limit <= 0:
        return ""
    if len(text) <= limit:
        return text
    if limit == 1:
        return "…"
    return text[: limit - 1] + "…"


class Screen:
    """Builds the framed lines of the interface."""

    def __init__(self, width: int, use_color: bool = True) -> None:
        self.inner = max(MIN_WIDTH, min(MAX_WIDTH, width)) - 2
        self.use_color = use_color
        self.lines: list[str] = []

    def paint(self, text: str, *codes: str) -> str:
        if not self.use_color or not codes:
            return text
        return "".join(codes) + text + RESET

    def top(self, title: str, right: str = "") -> None:
        title = f" {title} "
        right = f" {right} " if right else ""
        fill = self.inner - len(title) - len(right)
        self.lines.append(
            "┌"
            + self.paint(title, BOLD, CYAN)
            + "─" * max(0, fill)
            + self.paint(right, GREY)
            + "┐"
        )

    def divider(self, title: str = "") -> None:
        if title:
            label = f" {title} "
            fill = self.inner - len(label)
            self.lines.append(
                "├" + self.paint(label, GREY) + "─" * max(0, fill) + "┤"
            )
        else:
            self.lines.append("├" + "─" * self.inner + "┤")

    def bottom(self) -> None:
        self.lines.append("└" + "─" * self.inner + "┘")

    def row(self, content: str = "") -> None:
        pad = self.inner - visible_len(content)
        self.lines.append("│" + content + " " * max(0, pad) + "│")

    def field(self, label: str, value: str, *codes: str) -> None:
        room = self.inner - LABEL_WIDTH - 3
        self.row(
            "  "
            + self.paint(label.ljust(LABEL_WIDTH), GREY)
            + self.paint(truncate(value, room), *codes)
        )


def _status_badge(screen: Screen, state: str) -> str:
    if state == "recording":
        return screen.paint("● REC ", BOLD, RED)
    if state == "stopping":
        return screen.paint("■ SAVING", BOLD, YELLOW)
    return screen.paint("○ READY", BOLD, GREEN)


def render(vm: ViewModel, width: int = 72, use_color: bool = True) -> list[str]:
    """Render the whole interface as terminal lines."""
    screen = Screen(width, use_color)
    screen.top("omacap", f"v{__version__}")
    screen.row()

    badge = _status_badge(screen, vm.state)
    stats = f"{format_duration(vm.elapsed)}   {format_size(vm.size_bytes)}"
    gap = screen.inner - 4 - visible_len(badge) - 3 - len(stats)
    screen.row("  " + badge + "   " + " " * max(0, gap) + stats + "  ")

    meter_width = screen.inner - 13
    bar, colour = meter_bar(vm.meter_db, meter_width)
    screen.row("  " + screen.paint(bar, colour) + " " + screen.paint(level_text(vm.meter_db), GREY))
    screen.row()

    screen.field("Source", vm.source_label)
    fmt_value = vm.format_name + (f"  ·  {vm.bitrate}" if vm.bitrate else "")
    screen.field("Format", fmt_value, BOLD)
    screen.field("", vm.format_description, DIM)
    screen.field("Folder", vm.output_dir)
    screen.field("Chart", f".{vm.chart_format}")
    if vm.player_label:
        playing = vm.player_label
        if vm.state == "recording" and vm.track_count:
            playing += f"   ({vm.track_count} track"
            playing += ")" if vm.track_count == 1 else "s)"
        screen.field("Playing", playing, CYAN)
    if vm.next_name:
        screen.field("Name", vm.next_name, CYAN)
    screen.row()

    if vm.name_prompt is not None:
        screen.divider("name for next recording")
        screen.row("  " + screen.paint("> ", CYAN) + vm.name_prompt + screen.paint("█", DIM))
        screen.row("  " + screen.paint("enter to accept · esc to cancel", GREY))
    elif vm.split_prompt is not None:
        screen.divider("split into separate files?")
        screen.row("  " + screen.paint(truncate(vm.split_prompt, screen.inner - 3), BOLD))
        screen.row(
            "  "
            + screen.paint(
                "One file per track, named after each one. The recording is kept.",
                GREY,
            )
        )
        screen.row(
            "  " + screen.paint("y", BOLD, GREEN) + screen.paint(" split now", GREY)
            + screen.paint("    n", BOLD) + screen.paint(" keep as one file", GREY)
        )
    elif vm.analyse_prompt is not None:
        screen.divider("analyse this recording?")
        screen.row(
            "  " + screen.paint(truncate(vm.analyse_prompt, screen.inner - 3), BOLD)
        )
        screen.row(
            "  "
            + screen.paint(
                "Find the key, tempo, bars and chords, and write a chord chart.",
                GREY,
            )
        )
        screen.row(
            "  " + screen.paint("y", BOLD, GREEN) + screen.paint(" analyse now", GREY)
            + screen.paint("    n", BOLD) + screen.paint(" skip", GREY)
        )
    elif vm.show_help:
        screen.divider("keys")
        for key, description in KEY_HELP:
            screen.row("  " + screen.paint(key.ljust(11), BOLD) + screen.paint(description, GREY))
    elif vm.recordings:
        screen.divider("saved this session")
        for entry in vm.recordings[-5:]:
            screen.row("  " + truncate(entry, screen.inner - 3))

    if vm.message:
        screen.divider()
        codes = {"error": (RED,), "success": (GREEN,)}.get(vm.message_kind, (GREY,))
        screen.row("  " + screen.paint(truncate(vm.message, screen.inner - 3), *codes))

    if vm.clipping:
        screen.divider()
        screen.row(
            "  " + screen.paint("⚠ clipping", BOLD, RED)
            + screen.paint("  turn the application's own volume down, not the "
                           "speakers", GREY)
        )

    if vm.update_notice:
        screen.divider()
        screen.row("  " + screen.paint(truncate(vm.update_notice, screen.inner - 3), CYAN))

    screen.divider()
    for line in _footer_lines(vm):
        screen.row("  " + screen.paint(truncate(line, screen.inner - 3), GREY))
    screen.bottom()
    return screen.lines


def _footer_lines(vm: ViewModel) -> list[str]:
    if vm.name_prompt is not None:
        return ["typing a name · enter to accept"]
    if vm.analyse_prompt is not None:
        return ["y analyse · n skip · any other key dismisses it"]
    if vm.split_prompt is not None:
        return ["y split · n keep as one · any other key dismisses it"]
    action = "stop" if vm.state == "recording" else "record"
    analyse = "a analyse · " if vm.can_analyse else ""
    return [
        f"space {action} · f format · b bitrate · d source",
        f"{analyse}n name · t chart · ? help · q quit",
    ]
