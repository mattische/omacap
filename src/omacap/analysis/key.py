"""Key detection with the Krumhansl-Schmuckler profiles."""

from __future__ import annotations

from dataclasses import dataclass

from . import require_numpy
from .features import PITCH_NAMES

#: Krumhansl-Kessler tonal hierarchies: how strongly each scale degree belongs
#: to a major and a minor key. Correlating a rotation of these against the
#: chromagram gives the key.
MAJOR_PROFILE = (6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88)
MINOR_PROFILE = (6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17)

#: How each tonic is spelled, following the usual key signatures rather than
#: always writing sharps: E flat major, not D sharp major.
MAJOR_SPELLING = ("C", "Db", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B")
MINOR_SPELLING = ("C", "C#", "D", "Eb", "E", "F", "F#", "G", "G#", "A", "Bb", "B")

#: Number of sharps (positive) or flats (negative) in each major key signature.
MAJOR_ACCIDENTALS = (0, -5, 2, -3, 4, -1, 6, 1, -4, 3, -2, 5)


@dataclass
class Key:
    """A detected key."""

    tonic: int             # pitch class, 0 = C
    mode: str              # "major" or "minor"
    correlation: float     # how well the profile matched, -1 to 1
    confidence: float      # 0-1 margin over the runner-up

    @property
    def name(self) -> str:
        spelling = MAJOR_SPELLING if self.mode == "major" else MINOR_SPELLING
        return f"{spelling[self.tonic]} {self.mode}"

    @property
    def short_name(self) -> str:
        spelling = MAJOR_SPELLING if self.mode == "major" else MINOR_SPELLING
        return spelling[self.tonic] + ("" if self.mode == "major" else "m")

    @property
    def relative(self) -> "Key":
        """The relative major or minor, which shares every note with this key.

        The two cannot be told apart by pitch content at all - only by which
        chord behaves as home - so when the choice was a close one, naming the
        other is more use than insisting on one.
        """
        if self.mode == "major":
            return Key((self.tonic + 9) % 12, "minor", self.correlation, self.confidence)
        return Key((self.tonic + 3) % 12, "major", self.correlation, self.confidence)

    @property
    def signature(self) -> str:
        """The key signature, e.g. ``2 sharps`` or ``3 flats``."""
        relative_major = self.tonic if self.mode == "major" else (self.tonic + 3) % 12
        count = MAJOR_ACCIDENTALS[relative_major]
        if count == 0:
            return "no sharps or flats"
        kind = "sharp" if count > 0 else "flat"
        number = abs(count)
        return f"{number} {kind}{'s' if number > 1 else ''}"


def _correlate(vector, profile):
    """Pearson correlation between two 12-element vectors."""
    np = require_numpy()
    a = np.asarray(vector, dtype=np.float64) - np.mean(vector)
    b = np.asarray(profile, dtype=np.float64) - np.mean(profile)
    denominator = np.linalg.norm(a) * np.linalg.norm(b)
    return float(a @ b / denominator) if denominator > 0 else 0.0


def key_scores(chroma_mean):
    """Correlation of every one of the 24 keys against a chroma vector."""
    np = require_numpy()
    vector = np.asarray(chroma_mean, dtype=np.float64)
    scores = []
    for mode, profile in (("major", MAJOR_PROFILE), ("minor", MINOR_PROFILE)):
        for tonic in range(12):
            rotated = np.roll(profile, tonic)
            scores.append((tonic, mode, _correlate(vector, rotated)))
    return sorted(scores, key=lambda item: item[2], reverse=True)


def detect_key(chroma) -> Key:
    """Detect the key from a chromagram or a single averaged chroma vector."""
    np = require_numpy()
    chroma = np.asarray(chroma, dtype=np.float64)
    vector = chroma.mean(axis=1) if chroma.ndim == 2 else chroma
    total = vector.sum()
    if total <= 0:
        return Key(tonic=0, mode="major", correlation=0.0, confidence=0.0)

    ranked = key_scores(vector / total)
    tonic, mode, correlation = ranked[0]
    runner_up = ranked[1][2]
    margin = max(0.0, correlation - runner_up)
    confidence = float(np.clip(margin / 0.15, 0.0, 1.0)) if correlation > 0 else 0.0
    return Key(tonic=tonic, mode=mode, correlation=correlation, confidence=confidence)


#: How much a chord that belongs to the key is favoured on a second pass.
#: Measured against a real song with a known chart: 0.04-0.08 all gave the same
#: improvement, so the middle is taken rather than the edge of what was tried.
KEY_BONUS = 0.05


def diatonic_bonus(key: Key, strength: float = KEY_BONUS):
    """A per-chord nudge towards the chords that belong to ``key``.

    Chord recognition happens before the key is known, but once it is, most of
    what a song plays is diatonic. Recognising the chords again with that in mind
    corrects the ones that were a close call the first time.
    """
    np = require_numpy()
    from .chords import QUALITIES, chord_labels

    diatonic = MAJOR_DIATONIC if key.mode == "major" else MINOR_DIATONIC
    bonus = np.zeros(len(chord_labels()))
    for index in range(len(chord_labels()) - 1):          # the last is no-chord
        degree = (index % 12 - key.tonic) % 12
        quality = QUALITIES[index // 12][0]
        if quality in diatonic.get(degree, ()):
            bonus[index] = strength
    return bonus


def pitch_name(pitch_class: int, key: Key | None = None) -> str:
    """Spell a pitch class, preferring flats in flat keys."""
    from .features import FLAT_NAMES

    pitch_class %= 12
    if key is not None:
        relative_major = key.tonic if key.mode == "major" else (key.tonic + 3) % 12
        if MAJOR_ACCIDENTALS[relative_major] < 0:
            return FLAT_NAMES[pitch_class]
    return PITCH_NAMES[pitch_class]


#: Scale degrees of the major and natural minor scales, in semitones.
MAJOR_SCALE = (0, 2, 4, 5, 7, 9, 11)
MINOR_SCALE = (0, 2, 3, 5, 7, 8, 10)

#: The chord built on each scale degree. A chord that fits here is strong
#: evidence for the key; one that does not is evidence against it.
MAJOR_DIATONIC = {
    0: ("", "maj7", "sus4"), 2: ("m", "m7"), 4: ("m", "m7"),
    5: ("", "maj7", "sus4"), 7: ("", "7", "sus4"), 9: ("m", "m7"), 11: ("dim",),
}
MINOR_DIATONIC = {
    0: ("m", "m7"), 2: ("dim",), 3: ("", "maj7"), 5: ("m", "m7"),
    # Both the natural v and the harmonic-minor V are idiomatic.
    7: ("m", "m7", "", "7"), 8: ("", "maj7"), 10: ("", "7"),
}

#: How much the chord evidence counts against the raw chroma correlation.
CHORD_EVIDENCE_WEIGHT = 0.45
#: Songs tend to start and, especially, end on the tonic.
FIRST_CHORD_BONUS = 0.15
LAST_CHORD_BONUS = 0.30


def chord_evidence(spans, tonic: int, mode: str) -> float:
    """How well a chord sequence fits one key, from roughly -1 to 1.

    This is what separates a key from its relative major or minor: the two share
    every note, so only which chord behaves as home can tell them apart.
    """
    scale = MAJOR_SCALE if mode == "major" else MINOR_SCALE
    diatonic = MAJOR_DIATONIC if mode == "major" else MINOR_DIATONIC
    tonic_qualities = diatonic[0]

    playing = [span for span in spans if span.root >= 0 and span.duration > 0]
    if not playing:
        return 0.0
    total_time = sum(span.duration for span in playing)
    if total_time <= 0:
        return 0.0

    score = 0.0
    for span in playing:
        degree = (span.root - tonic) % 12
        weight = span.duration / total_time
        if span.quality in diatonic.get(degree, ()):
            score += weight
        elif degree in scale:
            score += 0.35 * weight          # right note, unexpected quality
        else:
            score -= 0.45 * weight          # outside the key altogether
        if degree == 0 and span.quality in tonic_qualities:
            score += 0.45 * weight          # time spent on home

    if playing[0].root == tonic and playing[0].quality in tonic_qualities:
        score += FIRST_CHORD_BONUS
    if playing[-1].root == tonic and playing[-1].quality in tonic_qualities:
        score += LAST_CHORD_BONUS
    return score


def detect_key_with_chords(chroma, spans) -> Key:
    """Detect the key using both the chromagram and the recognised chords.

    The Krumhansl-Schmuckler profiles alone cannot separate a key from its
    relative - A minor and C major contain identical notes - so the chord
    sequence casts the deciding vote.
    """
    np = require_numpy()
    chroma = np.asarray(chroma, dtype=np.float64)
    vector = chroma.mean(axis=1) if chroma.ndim == 2 else chroma
    total = vector.sum()
    if total <= 0:
        return Key(tonic=0, mode="major", correlation=0.0, confidence=0.0)
    if not spans:
        return detect_key(chroma)

    normalised = vector / total
    combined = []
    for tonic, mode, correlation in key_scores(normalised):
        evidence = chord_evidence(spans, tonic, mode)
        combined.append(
            (
                (1.0 - CHORD_EVIDENCE_WEIGHT) * correlation
                + CHORD_EVIDENCE_WEIGHT * evidence,
                tonic,
                mode,
                correlation,
            )
        )
    combined.sort(key=lambda item: item[0], reverse=True)
    best_score, tonic, mode, correlation = combined[0]
    margin = max(0.0, best_score - combined[1][0])
    return Key(
        tonic=tonic,
        mode=mode,
        correlation=correlation,
        confidence=float(np.clip(margin / 0.12, 0.0, 1.0)),
    )
