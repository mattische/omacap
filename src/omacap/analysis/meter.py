"""Time signature and downbeat detection.

Beats are already known; what is left is how they group into bars. Two cues do
the work: downbeats are usually accented, and chords usually change on them.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import require_numpy

#: Groupings worth testing, with a mild prior: four-four is far more common than
#: anything else, so it wins ties.
#:
#: 2/4 is deliberately absent. Every other beat being accented is exactly what
#: 4/4 looks like too, so the two cannot be told apart from audio - and popular
#: music writes 4/4 either way.
CANDIDATES: tuple[tuple[int, str, float], ...] = (
    (4, "4/4", 1.00),
    (3, "3/4", 0.93),
    (6, "6/8", 0.88),
    (5, "5/4", 0.80),
    (7, "7/8", 0.78),
)

#: A bar of six only counts as 6/8 if its middle is audibly weaker than its
#: start. When the two are equally accented the music is really in three, played
#: with a two-bar harmonic rhythm. Measured on the accents alone: a chord that
#: lasts two bars makes the *harmony* look like six either way, so only the
#: metrical accent can tell them apart.
COMPOUND_MIDBAR_RATIO = 0.90


@dataclass
class Meter:
    """How the beats group into bars."""

    beats_per_bar: int
    phase: int            # index of the first downbeat within the beat list
    name: str             # e.g. "4/4"
    confidence: float     # 0-1

    @property
    def is_compound(self) -> bool:
        return self.name.endswith("/8")


def harmonic_novelty(beat_chroma):
    """How much the chroma changed at each beat, from 0 (same) to 1 (unrelated)."""
    np = require_numpy()
    beat_chroma = np.asarray(beat_chroma, dtype=np.float64)
    if beat_chroma.shape[0] == 0:
        return np.array([])
    norms = np.linalg.norm(beat_chroma, axis=1, keepdims=True)
    normalised = beat_chroma / np.maximum(norms, 1e-9)
    similarity = np.sum(normalised[1:] * normalised[:-1], axis=1)
    # The first beat has no predecessor; treat it as a full change.
    return np.concatenate([[1.0], np.clip(1.0 - similarity, 0.0, 1.0)])


def beat_accents(beats, onset, frame_rate: float):
    """Onset strength at each beat, scaled to a maximum of 1."""
    np = require_numpy()
    beats = np.asarray(beats, dtype=np.float64)
    onset = np.asarray(onset, dtype=np.float64)
    if beats.size == 0 or onset.size == 0:
        return np.zeros(beats.size)
    indices = np.clip(np.round(beats * frame_rate).astype(int), 0, onset.size - 1)
    # Take the local maximum: a beat may sit a frame either side of its onset.
    accents = np.array(
        [onset[max(0, i - 1): i + 2].max() for i in indices], dtype=np.float64
    )
    peak = accents.max()
    return accents / peak if peak > 0 else accents


def score_grouping(strength, beats_per_bar: int, phase: int) -> float:
    """How much stronger the candidate downbeats are than the other beats."""
    np = require_numpy()
    strength = np.asarray(strength, dtype=np.float64)
    if strength.size < beats_per_bar * 2:
        return 0.0
    positions = np.arange(strength.size)
    is_downbeat = (positions - phase) % beats_per_bar == 0
    if not is_downbeat.any() or is_downbeat.all():
        return 0.0
    downbeat_mean = float(strength[is_downbeat].mean())
    other_mean = float(strength[~is_downbeat].mean())
    return downbeat_mean - other_mean


def detect_meter(beats, onset, frame_rate: float, beat_chroma) -> Meter:
    """Choose a time signature and the phase of the first downbeat."""
    np = require_numpy()
    beats = np.asarray(beats, dtype=np.float64)
    if beats.size < 6:
        return Meter(beats_per_bar=4, phase=0, name="4/4", confidence=0.0)

    accents = beat_accents(beats, onset, frame_rate)
    novelty = harmonic_novelty(beat_chroma)
    if novelty.size != accents.size:
        size = min(novelty.size, accents.size)
        accents, novelty = accents[:size], novelty[:size]
    # Chord changes are the more reliable cue on anything but drums, so they
    # carry the larger share.
    strength = 0.4 * accents + 0.6 * _normalise(novelty)

    results = []
    for beats_per_bar, name, prior in CANDIDATES:
        best_phase, best_score = 0, -1e9
        for phase in range(beats_per_bar):
            score = score_grouping(strength, beats_per_bar, phase)
            if score > best_score:
                best_phase, best_score = phase, score
        results.append((best_score * prior, beats_per_bar, name, best_phase, best_score))

    results.sort(key=lambda item: item[0], reverse=True)
    weighted, beats_per_bar, name, phase, raw = results[0]
    runner_up = results[1][0] if len(results) > 1 else 0.0
    margin = max(0.0, weighted - runner_up)
    confidence = float(np.clip(margin / 0.08, 0.0, 1.0)) if raw > 0 else 0.0

    if beats_per_bar == 6 and _midbar_is_as_strong(accents, phase):
        beats_per_bar, name, phase = 3, "3/4", phase % 3
        confidence = max(confidence, 0.4)

    return Meter(
        beats_per_bar=beats_per_bar, phase=phase, name=name, confidence=confidence
    )


def _midbar_is_as_strong(accents, phase: int) -> bool:
    """True when beat 4 of a six is as accented as beat 1, i.e. it is really 3/4."""
    np = require_numpy()
    accents = np.asarray(accents, dtype=np.float64)
    positions = np.arange(accents.size)
    downbeats = accents[(positions - phase) % 6 == 0]
    midbars = accents[(positions - phase - 3) % 6 == 0]
    if downbeats.size == 0 or midbars.size == 0:
        return False
    return float(np.median(midbars)) >= COMPOUND_MIDBAR_RATIO * float(np.median(downbeats))


def bar_boundaries(beats, meter: Meter, duration: float | None = None):
    """Times where each bar starts, including the end of the final bar.

    The beat tracker often misses the very first beat of a recording, which would
    leave bar 1 outside the grid and shift the whole chart by a bar. Whenever a
    full bar fits before the first detected downbeat, it is extrapolated back in.
    A trailing bar that is more than half empty is dropped instead: it is the
    tail of the recording, not a bar anybody played.
    """
    np = require_numpy()
    beats = np.asarray(beats, dtype=np.float64)
    if beats.size < 2:
        return np.array([])
    downbeats = list(range(meter.phase, beats.size, meter.beats_per_bar))
    if not downbeats:
        return np.array([])

    period = float(np.median(np.diff(beats)))
    bar_length = period * meter.beats_per_bar
    starts = [float(beats[i]) for i in downbeats]

    earlier = starts[0] - bar_length
    while earlier > -0.5 * period:
        starts.insert(0, max(0.0, earlier))
        earlier -= bar_length

    last_beat = float(beats[-1])
    # A final bar needs at least half its beats to have actually been tracked.
    while len(starts) > 1 and starts[-1] > last_beat - 0.5 * bar_length + period:
        starts.pop()

    end = starts[-1] + bar_length
    if duration is not None:
        end = min(end, max(duration, starts[-1] + period))
    if len(starts) < 1:
        return np.array([])
    return np.array(starts + [end])


def _normalise(values):
    np = require_numpy()
    values = np.asarray(values, dtype=np.float64)
    peak = values.max() if values.size else 0.0
    return values / peak if peak > 0 else values
