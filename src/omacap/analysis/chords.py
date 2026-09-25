"""Chord recognition: template matching on beat-synchronous chroma, then Viterbi."""

from __future__ import annotations

from dataclasses import dataclass

from . import require_numpy
from .features import PITCH_NAMES

#: Chord qualities, as (suffix, intervals in semitones from the root).
#: Weighting the root and third above the extensions keeps a seventh chord from
#: outscoring the plain triad whenever a passing note happens to fit.
QUALITIES: tuple[tuple[str, tuple[int, ...]], ...] = (
    ("", (0, 4, 7)),           # major
    ("m", (0, 3, 7)),          # minor
    ("7", (0, 4, 7, 10)),      # dominant seventh
    ("m7", (0, 3, 7, 10)),     # minor seventh
    ("maj7", (0, 4, 7, 11)),   # major seventh
    ("sus4", (0, 5, 7)),       # suspended fourth
    ("dim", (0, 3, 6)),        # diminished
)

#: Relative weight of each chord tone, by position in the interval tuple.
TONE_WEIGHTS = (1.0, 0.85, 0.8, 0.55, 0.5)

#: A small handicap, in correlation units, for the less common qualities. Real
#: music is full of passing notes and melody, and without this a plain triad
#: with a passing sixth in the tune is read as a suspension or a seventh.
QUALITY_PENALTY = {
    "": 0.0, "m": 0.0, "7": 0.020, "m7": 0.020,
    "maj7": 0.035, "sus4": 0.045, "dim": 0.050,
}

#: Label used where no chord is playing.
NO_CHORD = "N.C."

#: Named vocabularies. "simple" keeps a chart readable by writing only triads;
#: "full" allows every quality the recogniser knows.
VOCABULARIES: dict[str, tuple[str, ...]] = {
    "simple": ("", "m"),
    "standard": ("", "m", "7", "m7"),
    "full": tuple(suffix for suffix, _ in QUALITIES),
}
DEFAULT_VOCABULARY = "standard"


def get_vocabulary(name: str) -> tuple[str, ...]:
    """Look up a vocabulary by name."""
    key = name.strip().lower()
    if key not in VOCABULARIES:
        raise ValueError(
            f"unknown chord vocabulary {name!r}; "
            f"choose one of: {', '.join(VOCABULARIES)}"
        )
    return VOCABULARIES[key]

#: How much better a different chord must correlate before the decoder is willing
#: to switch to it. Expressed in correlation units, so it is directly comparable
#: with the template scores.
CHANGE_PENALTY = 0.06

#: Correlation a chord must beat to be preferred over "no chord".
NO_CHORD_SCORE = 0.38


@dataclass
class ChordSpan:
    """One chord, and where it sits in the recording."""

    label: str
    start: float
    end: float
    root: int          # pitch class, -1 for no chord
    quality: str
    strength: float    # 0-1 match quality

    @property
    def duration(self) -> float:
        return self.end - self.start


def chord_labels() -> list[str]:
    """Every chord label the recogniser can produce, ending with no-chord."""
    return [
        f"{PITCH_NAMES[root]}{suffix}"
        for suffix, _ in QUALITIES
        for root in range(12)
    ] + [NO_CHORD]


def chord_templates():
    """A ``(n_chords, 12)`` matrix of normalised chord templates."""
    np = require_numpy()
    rows = []
    for _, intervals in QUALITIES:
        for root in range(12):
            template = np.zeros(12)
            for position, interval in enumerate(intervals):
                weight = TONE_WEIGHTS[min(position, len(TONE_WEIGHTS) - 1)]
                template[(root + interval) % 12] = weight
            rows.append(template / np.linalg.norm(template))
    return np.array(rows)


#: Fraction of each segment ignored at both ends. The chroma window is 371 ms
#: long, so frames near a chord change contain both chords; skipping the edges
#: keeps the neighbouring chord's notes out of this one.
EDGE_MARGIN = 0.2


def synchronise(chroma, frame_rate: float, boundaries, margin: float = EDGE_MARGIN):
    """Average the chromagram over each interval between ``boundaries``."""
    np = require_numpy()
    chroma = np.asarray(chroma, dtype=np.float64)
    boundaries = np.asarray(boundaries, dtype=np.float64)
    if boundaries.size < 2:
        return np.zeros((0, 12))
    n_frames = chroma.shape[1]
    segments = []
    for start, end in zip(boundaries[:-1], boundaries[1:]):
        inset = (end - start) * margin
        first = int(round((start + inset) * frame_rate))
        last = int(round((end - inset) * frame_rate))
        if last <= first:
            first = int(round(start * frame_rate))
            last = first + 1
        first = min(max(first, 0), max(0, n_frames - 1))
        last = min(max(last, first + 1), n_frames)
        window = chroma[:, first:last]
        segments.append(window.mean(axis=1) if window.size else np.zeros(12))
    return np.array(segments)


def match_templates(
    segments,
    no_chord_score: float = NO_CHORD_SCORE,
    qualities: tuple[str, ...] | None = None,
):
    """Correlate every segment against every chord template.

    Correlation rather than plain cosine similarity: subtracting the mean makes
    the *absence* of a pitch count as evidence too, which is what separates a
    triad from the seventh chord that contains it.

    Returns ``(n_segments, n_chords + 1)``; the last column is no-chord, a fixed
    score that wins when nothing correlates convincingly.
    """
    np = require_numpy()
    segments = np.asarray(segments, dtype=np.float64)
    if segments.size == 0:
        return np.zeros((0, len(chord_labels())))
    templates = chord_templates()
    centred_segments = segments - segments.mean(axis=1, keepdims=True)
    centred_templates = templates - templates.mean(axis=1, keepdims=True)
    denominator = (
        np.linalg.norm(centred_segments, axis=1, keepdims=True)
        * np.linalg.norm(centred_templates, axis=1)[None, :]
    )
    scores = np.maximum(centred_segments @ centred_templates.T / np.maximum(denominator, 1e-9), 0.0)
    scores = scores - quality_penalties()[None, :]
    if qualities is not None:
        allowed = np.array(
            [suffix in qualities for suffix, _ in QUALITIES for _ in range(12)]
        )
        scores = np.where(allowed[None, :], scores, -np.inf)
    return np.column_stack([scores, np.full(len(segments), no_chord_score)])


def quality_penalties():
    """Per-chord handicap, aligned with :func:`chord_labels`."""
    np = require_numpy()
    return np.array(
        [QUALITY_PENALTY.get(suffix, 0.0) for suffix, _ in QUALITIES for _ in range(12)]
    )


def viterbi(scores, change_penalty: float = CHANGE_PENALTY):
    """The chord path maximising total correlation minus a cost per change.

    Chords are held unless a different one correlates at least ``change_penalty``
    better, which stops the labels flickering between a chord and its relatives
    from beat to beat. Because every change costs the same, the best predecessor
    is either the same state or the best state overall, so each step is linear in
    the number of chords rather than quadratic.
    """
    np = require_numpy()
    scores = np.asarray(scores, dtype=np.float64)
    if scores.size == 0:
        return np.array([], dtype=int)
    n_segments, n_states = scores.shape

    best = scores[0].copy()
    backlink = np.zeros((n_segments, n_states), dtype=np.int64)
    states = np.arange(n_states)
    for index in range(1, n_segments):
        previous_best = int(np.argmax(best))
        from_switch = best[previous_best] - change_penalty
        keep = best >= from_switch
        backlink[index] = np.where(keep, states, previous_best)
        best = np.where(keep, best, from_switch) + scores[index]

    path = np.zeros(n_segments, dtype=np.int64)
    path[-1] = int(np.argmax(best))
    for index in range(n_segments - 1, 0, -1):
        path[index - 1] = backlink[index, path[index]]
    return path


def decode(
    chroma, frame_rate: float, boundaries, qualities: tuple[str, ...] | None = None
) -> list[ChordSpan]:
    """Recognise one chord per interval between ``boundaries``."""
    np = require_numpy()
    segments = synchronise(chroma, frame_rate, boundaries)
    if segments.shape[0] == 0:
        return []
    scores = match_templates(segments, qualities=qualities)
    path = viterbi(scores)
    labels = chord_labels()
    boundaries = np.asarray(boundaries, dtype=np.float64)

    spans = []
    for index, state in enumerate(path):
        label = labels[state]
        if label == NO_CHORD:
            root, quality = -1, NO_CHORD
        else:
            root = state % 12
            quality = QUALITIES[state // 12][0]
        spans.append(
            ChordSpan(
                label=label,
                start=float(boundaries[index]),
                end=float(boundaries[index + 1]),
                root=root,
                quality=quality,
                strength=float(max(scores[index, state], 0.0)),
            )
        )
    return spans


def merge_adjacent(spans: list[ChordSpan]) -> list[ChordSpan]:
    """Join consecutive spans that carry the same chord."""
    merged: list[ChordSpan] = []
    for span in spans:
        if merged and merged[-1].label == span.label:
            previous = merged[-1]
            merged[-1] = ChordSpan(
                label=previous.label,
                start=previous.start,
                end=span.end,
                root=previous.root,
                quality=previous.quality,
                strength=max(previous.strength, span.strength),
            )
        else:
            merged.append(span)
    return merged
