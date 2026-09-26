"""What the beat is divided into, and whether it is played straight or swung.

Tempo says how fast the beats come and metre says how they group. Neither says
what happens *inside* a beat, which is most of what a groove is. Averaging the
onset strength over every beat answers it: where the off-beat sits tells straight
from swung, and how many distinct peaks there are says how busy the beat is.

Measured against synthesised patterns, the off-beat position is recovered to
within 0.01 of the truth - straight eighths read 0.508 where 0.500 was played, and
triplet-swung eighths read 0.669 where 0.667 was played. The peak count is
reliable up to eighths and over-reads on busier material, so density is reported
in words rather than as a claim about the exact subdivision.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from . import require_numpy

#: Positions sampled inside each beat. Divisible by 2, 3 and 4, so straight,
#: triplet and sixteenth placements all land on a sample.
SUBDIVISIONS = 24

#: An off-beat has to reach this share of the downbeat to count as played at all.
OFFBEAT_FLOOR = 0.15

#: A peak has to reach this share of the strongest position to be a separate hit.
PEAK_FLOOR = 0.22

#: Where the off-beat sits, and what that is called. Straight is half way; triplet
#: swing is two thirds. The bands come from the measured values, which sat a
#: hundredth above the truth.
STRAIGHT_BELOW = 0.56
SWUNG_BELOW = 0.72


@dataclass
class Rhythm:
    """How the beat is divided."""

    offbeat: float | None = None      # where the off-beat falls, 0-1 of a beat
    offbeat_strength: float = 0.0     # relative to the downbeat
    peaks: list[float] = field(default_factory=list)
    profile: object = None            # onset strength at each position in a beat

    @property
    def busy(self) -> bool:
        """More going on than a downbeat and one off-beat."""
        return len(self.peaks) > 2

    @property
    def feel(self) -> str:
        """A phrase a musician would recognise."""
        if self.offbeat is None:
            return "on the beat, little between" if self.peaks else "unclear"
        if self.busy:
            # With several hits inside a beat there is no single off-beat, so
            # calling it straight or swung would be claiming more than is known.
            return "busier than eighths"
        if self.offbeat < STRAIGHT_BELOW:
            return "straight eighths"
        if self.offbeat < SWUNG_BELOW:
            return "swung eighths, close to triplets"
        return "heavily swung"

    @property
    def description(self) -> str:
        """The feel, with the number behind it when one is meaningful."""
        if self.offbeat is None or self.busy:
            return self.feel
        return f"{self.feel} (off-beat at {self.offbeat:.2f} of the beat)"


def beat_profile(onset, frame_rate: float, beats, subdivisions: int = SUBDIVISIONS):
    """Average onset strength at each position inside a beat."""
    np = require_numpy()
    onset = np.asarray(onset, dtype=np.float64)
    beats = np.asarray(beats, dtype=np.float64)
    total = np.zeros(subdivisions)
    counts = np.zeros(subdivisions)
    for start, end in zip(beats[:-1], beats[1:]):
        span = end - start
        if span <= 0:
            continue
        for step in range(subdivisions):
            index = int(round((start + span * step / subdivisions) * frame_rate))
            if 0 <= index < onset.size:
                total[step] += onset[index]
                counts[step] += 1
    return total / np.maximum(counts, 1.0)


def profile_peaks(profile, floor: float = PEAK_FLOOR) -> list[float]:
    """Positions inside the beat that carry a distinct onset."""
    np = require_numpy()
    profile = np.asarray(profile, dtype=np.float64)
    peak = profile.max() if profile.size else 0.0
    if peak <= 0:
        return []
    scaled = profile / peak
    size = scaled.size
    found = []
    for index in range(size):
        # The beat wraps: the position before the first is the last one.
        here = scaled[index]
        previous = scaled[index - 1]
        following = scaled[(index + 1) % size]
        if here >= floor and here >= previous and here > following:
            found.append(index / size)
    return found


def analyse_rhythm(onset, frame_rate: float, beats) -> Rhythm:
    """Work out how the beat is divided, and how it is played."""
    np = require_numpy()
    beats = np.asarray(beats, dtype=np.float64)
    if beats.size < 3:
        return Rhythm()

    profile = beat_profile(onset, frame_rate, beats)
    peak = profile.max()
    if peak <= 0:
        return Rhythm(profile=profile)
    scaled = profile / peak
    peaks = profile_peaks(profile)

    # Look strictly between the beats, away from either edge, so the downbeat's
    # own attack and the next beat's are not mistaken for an off-beat.
    size = scaled.size
    low, high = int(round(0.30 * size)), int(round(0.85 * size))
    window = scaled[low:high]
    strength = float(window.max()) if window.size else 0.0
    if strength < OFFBEAT_FLOOR * max(scaled[0], 1e-9):
        return Rhythm(offbeat=None, offbeat_strength=strength, peaks=peaks,
                      profile=profile)

    # Centre of mass around the peak, so the answer is not quantised to a sample.
    centre = low + int(np.argmax(window))
    span = slice(max(0, centre - 2), min(size, centre + 3))
    weights = scaled[span]
    positions = np.arange(span.start, span.stop)
    offbeat = float((positions * weights).sum() / max(weights.sum(), 1e-12) / size)
    return Rhythm(offbeat=offbeat, offbeat_strength=strength, peaks=peaks,
                  profile=profile)


# -- syncopation ----------------------------------------------------------
#
# Syncopation is a note on a weak metrical position where the stronger position
# that follows it is weaker, or empty: the note has displaced it. Longuet-Higgins
# & Lee (1984) score that on notated music by the difference in metrical weight.
# Read off a continuous onset profile the same idea needs no threshold at all,
# which matters - a fixed floor made dense material unreadable, because every
# position cleared it.
#
# Anchored on patterns built to test it: four on the floor and straight eighths
# both score 0.00, which is right (every off-beat there is followed by a strong
# position that IS played), and genuinely displaced patterns score 0.37 to 0.62.
# Every real recording tried so far sits at 0.00 to 0.10.

#: Sixteenths per beat, the finest placement worth asking about.
BAR_SUBDIVISIONS = 4

#: Where the synthesised syncopated patterns start. Below it, straight.
SYNCOPATED = 0.30
#: Below this, nothing worth mentioning - the range real straight songs sit in.
STRAIGHT_SYNCOPATION = 0.10


def metrical_weights(beats_per_bar: int, subdivisions: int = BAR_SUBDIVISIONS):
    """How strong each position in the bar is, by the metrical hierarchy.

    A position's weight is the number of levels it lands on: the bar, the
    half-bar where there is one, the beat, the half-beat, the rest. The downbeat
    lands on all of them. A metre with an odd number of beats has no half-bar, so
    4/4 gets a strong third beat and 3/4 does not.
    """
    np = require_numpy()
    total = max(1, beats_per_bar) * subdivisions
    levels = {total, subdivisions, max(1, subdivisions // 2), 1}
    if beats_per_bar % 2 == 0:
        levels.add(total // 2)
    ordered = sorted(levels, reverse=True)
    return np.array([sum(1 for level in ordered if position % level == 0)
                     for position in range(total)], dtype=np.float64)


def bar_profile(onset, frame_rate: float, bars, positions: int):
    """Average onset strength at each position of the bar, over these bars.

    Each bar is sampled from its own start, so a tempo that drifts does not smear
    the average. The window of one frame either side allows for an attack landing
    between frames.
    """
    np = require_numpy()
    onset = np.asarray(onset, dtype=np.float64)
    total = np.zeros(positions)
    counts = np.zeros(positions)
    for bar in bars:
        step = bar.duration / positions
        for position in range(positions):
            index = int(round((bar.start + position * step) * frame_rate))
            if 0 <= index < onset.size:
                window = onset[max(0, index - 1):min(onset.size, index + 2)]
                total[position] += float(window.max())
                counts[position] += 1
    profile = total / np.maximum(counts, 1)
    peak = float(profile.max())
    return profile / peak if peak > 0 else profile


def syncopation(profile, weights) -> float:
    """How much of this profile sits on weak positions at a strong one's expense.

    For each position, find the next one that is metrically stronger. Whatever
    this position has *above* that stronger one is displacement. Whatever it has
    below contributes nothing, so a quiet off-beat is not judged and no threshold
    decides what counts as played.
    """
    np = require_numpy()
    profile = np.asarray(profile, dtype=np.float64)
    weights = np.asarray(weights, dtype=np.float64)
    size = profile.size
    if size == 0 or size != weights.size:
        return 0.0
    score = 0.0
    for position in range(size):
        for step in range(1, size + 1):
            other = (position + step) % size
            if weights[other] > weights[position]:
                score += (weights[other] - weights[position]) * max(
                    0.0, profile[position] - profile[other])
                break
    return score / size


def syncopation_word(score: float) -> str:
    """What to call a score, in words a musician would use."""
    if score < STRAIGHT_SYNCOPATION:
        return "played straight"
    if score < SYNCOPATED:
        return "slightly syncopated"
    return "syncopated"
