"""Output formats and the ffmpeg encoder settings behind each one."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class AudioFormat:
    """A user-selectable output format."""

    name: str
    extension: str
    codec: str
    description: str
    lossless: bool
    default_bitrate: str | None = None
    extra_args: tuple[str, ...] = field(default_factory=tuple)

    @property
    def supports_bitrate(self) -> bool:
        return self.default_bitrate is not None

    def encoder_args(self, bitrate: str | None = None) -> list[str]:
        """ffmpeg arguments that select this format's encoder."""
        args = ["-c:a", self.codec]
        if self.supports_bitrate:
            args += ["-b:a", bitrate or self.default_bitrate]
        args += list(self.extra_args)
        return args


#: Ordered so the most common picks come first in the TUI.
FORMATS: tuple[AudioFormat, ...] = (
    AudioFormat(
        name="wav",
        extension=".wav",
        codec="pcm_s16le",
        description="Uncompressed PCM. Perfect quality, large files. Opens everywhere.",
        lossless=True,
    ),
    AudioFormat(
        name="flac",
        extension=".flac",
        codec="flac",
        description="Lossless compression, roughly half the size of WAV. Best archive format.",
        lossless=True,
    ),
    AudioFormat(
        name="mp3",
        extension=".mp3",
        codec="libmp3lame",
        description="Lossy, universally playable. Good default for sharing.",
        lossless=False,
        default_bitrate="192k",
    ),
    AudioFormat(
        name="m4a",
        extension=".m4a",
        codec="aac",
        description="Lossy AAC in an MP4 container. This is what 'mp4 audio' means.",
        lossless=False,
        default_bitrate="192k",
        extra_args=("-movflags", "+faststart"),
    ),
    AudioFormat(
        name="opus",
        extension=".opus",
        codec="libopus",
        description="Lossy, best quality per byte. Modern players only.",
        lossless=False,
        default_bitrate="128k",
    ),
    AudioFormat(
        name="ogg",
        extension=".ogg",
        codec="libvorbis",
        description="Lossy Vorbis in an Ogg container. Fully open, widely supported.",
        lossless=False,
        default_bitrate="192k",
    ),
)

FORMAT_NAMES: tuple[str, ...] = tuple(f.name for f in FORMATS)

DEFAULT_FORMAT = "mp3"


def get_format(name: str) -> AudioFormat:
    """Look up a format by name, case-insensitively.

    Accepts a leading dot and the 'mp4' alias for 'm4a'.
    """
    key = name.strip().lower().lstrip(".")
    if key == "mp4":
        key = "m4a"
    for fmt in FORMATS:
        if fmt.name == key:
            return fmt
    raise ValueError(
        f"unknown format {name!r}; choose one of: {', '.join(FORMAT_NAMES)}"
    )


def next_format(name: str, step: int = 1) -> AudioFormat:
    """The format after ``name``, wrapping around. Used by the TUI's format key."""
    current = get_format(name)
    index = FORMATS.index(current)
    return FORMATS[(index + step) % len(FORMATS)]
