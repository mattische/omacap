"""Tempo estimation and beat tracking from an onset-strength envelope."""

from __future__ import annotations

from dataclasses import dataclass

from . import require_numpy

MIN_BPM = 50.0
MAX_BPM = 210.0
#: Tempo octave errors (60 vs 120 BPM) are the classic failure here, so
#: candidates are weighted by a log-normal prior centred on a typical tempo.
PRIOR_CENTRE_BPM = 120.0
PRIOR_WIDTH_OCTAVES = 1.0
#: How strongly the beat tracker resists straying from the estimated period.
TIGHTNESS = 100.0

#: How much better an octave away has to look before it is taken. Judged on the
#: kick band, a correct tempo ties with its own double (1.00-1.02) while a tempo
#: that is genuinely half of the truth loses badly (1.17 and worse), so the
#: threshold sits between those two measured groups.
OCTAVE_GAIN = 1.10


@dataclass
class BeatGrid:
    """A tempo estimate and the beat times it produced."""

    bpm: float
    beats: object          # numpy array of beat times in seconds
    confidence: float      # 0-1, how regular the beat spacing is

    @property
    def beat_period(self) -> float:
        return 60.0 / self.bpm if self.bpm > 0 else 0.0

    def __len__(self) -> int:
        return len(self.beats)


def tempo_autocorrelation(onset, frame_rate: float):
    """Prior-weighted autocorrelation of the onset envelope, per candidate BPM.

    Returns ``(bpms, strengths)`` sorted from slow to fast.
    """
    np = require_numpy()
    onset = np.asarray(onset, dtype=np.float64)
    if onset.size < 4:
        return np.array([]), np.array([])
    centred = onset - onset.mean()
    correlation = np.correlate(centred, centred, mode="full")[len(centred) - 1:]
    if correlation[0] > 0:
        correlation = correlation / correlation[0]

    min_lag = max(1, int(round(frame_rate * 60.0 / MAX_BPM)))
    max_lag = min(len(correlation) - 1, int(round(frame_rate * 60.0 / MIN_BPM)))
    if max_lag <= min_lag:
        return np.array([]), np.array([])

    lags = np.arange(min_lag, max_lag + 1)
    bpms = 60.0 * frame_rate / lags
    prior = np.exp(
        -0.5 * (np.log2(bpms / PRIOR_CENTRE_BPM) / PRIOR_WIDTH_OCTAVES) ** 2
    )
    strengths = np.maximum(correlation[lags], 0.0) * prior
    order = np.argsort(bpms)
    return bpms[order], strengths[order]


def estimate_tempo(onset, frame_rate: float) -> float:
    """Best single tempo in BPM, or 0 when there is nothing periodic."""
    np = require_numpy()
    bpms, strengths = tempo_autocorrelation(onset, frame_rate)
    if bpms.size == 0 or strengths.max() <= 0:
        return 0.0
    return float(bpms[int(np.argmax(strengths))])


def track_beats(onset, frame_rate: float, bpm: float) -> object:
    """Beat times in seconds, using Ellis's dynamic-programming beat tracker.

    Every frame gets the best score achievable by a beat sequence ending there:
    the onset strength at that frame plus the best predecessor, penalised for
    straying from the expected beat period. The best path is then traced back.
    """
    np = require_numpy()
    onset = np.asarray(onset, dtype=np.float64)
    if bpm <= 0 or onset.size < 4:
        return np.array([])

    period = frame_rate * 60.0 / bpm
    if period < 2 or period >= onset.size:
        return np.array([])

    # Localise the envelope so loud passages do not attract every beat.
    local = onset - _smooth(onset, int(round(period)))
    local = np.maximum(local, 0.0)
    peak = local.max()
    if peak <= 0:
        return np.array([])
    local = local / peak

    first = max(1, int(round(period / 2)))
    last = int(round(2 * period))
    offsets = np.arange(first, last + 1)
    # Penalty is zero at exactly one period and falls off quadratically in log-time.
    penalties = -TIGHTNESS * (np.log(offsets / period) ** 2)

    scores = local.copy()
    backlink = np.full(onset.size, -1, dtype=np.int64)
    for frame in range(first, onset.size):
        valid = offsets[offsets <= frame]
        candidates = scores[frame - valid] + penalties[: valid.size]
        best = int(np.argmax(candidates))
        if candidates[best] > 0:
            scores[frame] += candidates[best]
            backlink[frame] = frame - valid[best]

    # Start from a strong beat near the end rather than the very last frame.
    tail = max(0, onset.size - int(round(period)))
    end = int(tail + np.argmax(scores[tail:])) if tail < onset.size else int(np.argmax(scores))

    path = []
    while end >= 0:
        path.append(end)
        end = int(backlink[end])
    path.reverse()
    return np.array(path, dtype=np.float64) / frame_rate


def beat_confidence(beats, bpm: float) -> float:
    """How evenly spaced the beats are, from 0 (erratic) to 1 (metronomic)."""
    np = require_numpy()
    beats = np.asarray(beats, dtype=np.float64)
    if beats.size < 3 or bpm <= 0:
        return 0.0
    intervals = np.diff(beats)
    expected = 60.0 / bpm
    deviation = np.abs(intervals - expected) / expected
    return float(np.clip(1.0 - 2.0 * np.median(deviation), 0.0, 1.0))


def onset_coverage(beats, onset, frame_rate: float, tolerance: float = 0.07) -> float:
    """Fraction of prominent onsets that land on a beat.

    This is what separates a tempo from its own half: at half speed only every
    other onset is accounted for.
    """
    np = require_numpy()
    beats = np.asarray(beats, dtype=np.float64)
    if beats.size < 2:
        return 0.0
    peaks = _onset_peaks(onset, frame_rate)
    if peaks.size == 0:
        return 0.0
    distances = np.abs(peaks[:, None] - beats[None, :]).min(axis=1)
    return float(np.mean(distances <= tolerance))


def _onset_peaks(onset, frame_rate: float, threshold: float = 0.25):
    """Times of local maxima in the onset envelope."""
    np = require_numpy()
    onset = np.asarray(onset, dtype=np.float64)
    if onset.size < 3:
        return np.array([])
    interior = onset[1:-1]
    is_peak = (interior >= onset[:-2]) & (interior > onset[2:]) & (interior > threshold)
    return (np.flatnonzero(is_peak) + 1) / frame_rate


def refine_tempo(beats, fallback_bpm: float) -> float:
    """A precise tempo from the beat times, immune to frame quantisation.

    Each beat time is only accurate to one analysis frame (23 ms), so the median
    spacing is coarse. Fitting a straight line through beat index against beat
    time averages that error away over the whole recording.
    """
    np = require_numpy()
    beats = np.asarray(beats, dtype=np.float64)
    if beats.size < 4:
        return fallback_bpm
    intervals = np.diff(beats)
    median_interval = float(np.median(intervals))
    if median_interval <= 0:
        return fallback_bpm
    # Allow for a beat the tracker skipped: count how many periods each gap spans.
    steps = np.maximum(1, np.round(intervals / median_interval))
    indices = np.concatenate([[0.0], np.cumsum(steps)])
    slope, _ = np.polyfit(indices, beats, 1)
    if slope <= 0:
        return fallback_bpm
    refined = 60.0 / float(slope)
    return refined if MIN_BPM <= refined <= MAX_BPM else fallback_bpm


def analyse_tempo(onset, frame_rate: float, low_onset=None) -> BeatGrid:
    """Estimate the tempo and lay a beat grid over the recording.

    `low_onset` is the 40-120 Hz onset envelope - the kick drum. It is what the
    octave decision is judged on; see below for why the full band cannot do it.
    """
    np = require_numpy()
    base = estimate_tempo(onset, frame_rate)
    if base <= 0:
        return BeatGrid(bpm=0.0, beats=np.array([]), confidence=0.0)

    # Autocorrelation cannot tell a tempo from its own half or double, so try
    # both. The judgement is made on the kick band rather than the whole
    # spectrum, because a doubled beat grid is a *superset* of the true one -
    # every real onset still lands on a beat - so any measure of how well the
    # beats explain the full band must prefer the double or tie. Hi-hats on
    # every eighth are exactly the doubled grid. The kick plays on beats, so it
    # collapses at half speed (measured: 0.20 against 1.00) while staying level
    # between a tempo and its double, which is what makes a tie readable as "the
    # base was already right".
    judge = onset if low_onset is None else low_onset
    best_bpm, best_beats = base, track_beats(onset, frame_rate, base)
    best_score = onset_coverage(best_beats, judge, frame_rate)
    for factor in (2.0, 0.5):
        candidate = base * factor
        if not (MIN_BPM <= candidate <= MAX_BPM):
            continue
        beats = track_beats(onset, frame_rate, candidate)
        score = onset_coverage(beats, judge, frame_rate)
        if score > best_score * OCTAVE_GAIN:
            best_bpm, best_beats, best_score = candidate, beats, score

    bpm = refine_tempo(best_beats, best_bpm)
    return BeatGrid(
        bpm=bpm, beats=best_beats, confidence=beat_confidence(best_beats, bpm)
    )


def _smooth(values, window: int):
    np = require_numpy()
    window = max(3, int(window) | 1)
    kernel = np.ones(window) / window
    return np.convolve(values, kernel, mode="same")
