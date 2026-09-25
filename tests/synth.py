"""Synthetic music with known tempo, metre, key and chords, for the analysis tests."""

from __future__ import annotations

import numpy as np

from omacap.analysis.features import midi_to_hz

SR = 22050

INTERVALS = {
    "": (0, 4, 7),
    "m": (0, 3, 7),
    "7": (0, 4, 7, 10),
    "m7": (0, 3, 7, 10),
    "maj7": (0, 4, 7, 11),
    "sus4": (0, 5, 7),
    "dim": (0, 3, 6),
}

PITCHES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")


def label(root: int, quality: str) -> str:
    return f"{PITCHES[root % 12]}{quality}"


def voicing(root: int, quality: str) -> list[int]:
    """A bass note plus a triad or seventh in the octave above middle C."""
    return [36 + root] + [48 + root + step for step in INTERVALS[quality]]


def chord_audio(root: int, quality: str, seconds: float) -> np.ndarray:
    length = int(SR * seconds)
    t = np.arange(length) / SR
    envelope = np.minimum(
        1.0, np.minimum(np.arange(length) / 200.0, (length - np.arange(length)) / 1200.0)
    )
    tones = sum(
        0.25 * np.sin(2 * np.pi * float(midi_to_hz(m)) * t)
        + 0.07 * np.sin(2 * np.pi * 2 * float(midi_to_hz(m)) * t)
        for m in voicing(root, quality)
    )
    return envelope * tones


def click(amplitude: float, length: int = 1200, accent: bool = False) -> np.ndarray:
    decay = np.exp(-np.arange(length) / 90.0)
    frequency = 1500 if accent else 950
    return amplitude * decay * np.sin(2 * np.pi * frequency * np.arange(length) / SR)


def song(
    progression,
    beats_per_bar: int = 4,
    bpm: float = 120.0,
    bars: int = 16,
    bars_per_chord: int = 1,
    accents=None,
) -> np.ndarray:
    """Render a chord progression with a click track marking the downbeats."""
    beat = 60.0 / bpm
    pieces = []
    for bar in range(bars):
        root, quality = progression[(bar // bars_per_chord) % len(progression)]
        for position in range(beats_per_bar):
            audio = chord_audio(root, quality, beat)
            amplitude = (
                accents[position] if accents else (1.0 if position == 0 else 0.45)
            )
            stroke = click(amplitude, accent=amplitude > 0.8)
            audio[: len(stroke)] += stroke
            pieces.append(audio)
    return np.concatenate(pieces).astype(np.float32)


def expected_labels(progression) -> list[str]:
    return [label(root, quality) for root, quality in progression]
