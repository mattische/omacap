"""Spectral features: a log-frequency spectrogram, chroma, and onset strength.

Everything here is plain numpy. The frequency axis is mapped onto semitones so
that folding to twelve pitch classes is a simple sum over octaves.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import require_numpy

#: Chroma needs fine frequency resolution: a 8192-point window at 22050 Hz
#: resolves 2.7 Hz, enough to separate semitones down to C2.
N_FFT_CHROMA = 8192
#: Onsets need fine *time* resolution instead, so they use a short window.
N_FFT_ONSET = 2048
#: Both share one hop, which keeps their time axes aligned.
HOP_LENGTH = 512

N_FFT = N_FFT_CHROMA

#: Analysed pitch range, as MIDI note numbers: C2 (65 Hz) up to B6 (1976 Hz).
MIN_MIDI = 36
MAX_MIDI = 96

PITCH_NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")
FLAT_NAMES = ("C", "Db", "D", "Eb", "E", "F", "Gb", "G", "Ab", "A", "Bb", "B")


@dataclass
class Spectral:
    """Frame-wise features sharing one time axis."""

    chroma: object          # (12, n_frames) normalised pitch-class energy
    onset: object           # (n_frames,) onset strength
    frame_rate: float       # frames per second
    n_frames: int

    def frame_times(self):
        np = require_numpy()
        return np.arange(self.n_frames) / self.frame_rate


def midi_to_hz(midi):
    np = require_numpy()
    return 440.0 * 2.0 ** ((np.asarray(midi, dtype=np.float64) - 69.0) / 12.0)


def stft_magnitude(samples, n_fft: int = N_FFT, hop_length: int = HOP_LENGTH):
    """Magnitude STFT with a Hann window, centred frames and reflect padding."""
    np = require_numpy()
    samples = np.asarray(samples, dtype=np.float64)
    if samples.size < n_fft:
        samples = np.pad(samples, (0, n_fft - samples.size))
    pad = n_fft // 2
    padded = np.pad(samples, pad, mode="reflect")
    n_frames = 1 + (len(padded) - n_fft) // hop_length
    window = np.hanning(n_fft + 1)[:-1]
    # A strided view avoids materialising one copy per frame.
    frames = np.lib.stride_tricks.as_strided(
        padded,
        shape=(n_frames, n_fft),
        strides=(padded.strides[0] * hop_length, padded.strides[0]),
        writeable=False,
    )
    return np.abs(np.fft.rfft(frames * window, axis=1)).T


def semitone_filterbank(
    sample_rate: int,
    n_fft: int = N_FFT,
    min_midi: int = MIN_MIDI,
    max_midi: int = MAX_MIDI,
    sigma_semitones: float = 0.3,
):
    """A ``(n_semitones, n_bins)`` matrix mapping FFT bins onto semitones.

    Each semitone gets a Gaussian window in log-frequency. Low notes are narrower
    than the FFT resolution, so their windows are widened to span at least two
    bins - without that they would collect no energy at all.
    """
    np = require_numpy()
    freqs = np.fft.rfftfreq(n_fft, 1.0 / sample_rate)
    centres = midi_to_hz(np.arange(min_midi, max_midi))
    bin_width = sample_rate / n_fft

    safe_freqs = np.maximum(freqs, 1e-6)
    # Distance in semitones from each centre to each bin.
    distance = 12.0 * np.log2(safe_freqs[None, :] / centres[:, None])
    # Widen the window where a semitone is narrower than the FFT can resolve.
    semitone_hz = centres * (2.0 ** (1.0 / 12.0) - 1.0)
    widening = np.maximum(1.0, (2.0 * bin_width) / np.maximum(semitone_hz, 1e-9))
    sigma = (sigma_semitones * widening)[:, None]

    weights = np.exp(-0.5 * (distance / sigma) ** 2)
    weights[:, 0] = 0.0                      # never weight DC
    weights[weights < 1e-3] = 0.0
    totals = weights.sum(axis=1, keepdims=True)
    return weights / np.maximum(totals, 1e-12)


def chromagram(
    samples, sample_rate: int, n_fft: int = N_FFT_CHROMA, hop_length: int = HOP_LENGTH
):
    """A ``(12, n_frames)`` chromagram, each frame scaled to a maximum of 1.

    Magnitudes are used as they are: log compression was tried and flattened the
    contrast between the notes of a chord and everything else so far that triads
    stopped being identifiable.
    """
    np = require_numpy()
    magnitude = stft_magnitude(samples, n_fft, hop_length)
    bank = semitone_filterbank(sample_rate, n_fft)
    semitones = bank @ magnitude

    chroma = np.zeros((12, semitones.shape[1]), dtype=np.float64)
    for index in range(semitones.shape[0]):
        chroma[(MIN_MIDI + index) % 12] += semitones[index]
    peaks = chroma.max(axis=0, keepdims=True)
    return chroma / np.maximum(peaks, 1e-9)


def onset_strength(
    samples, sample_rate: int, n_fft: int = N_FFT_ONSET, hop_length: int = HOP_LENGTH
):
    """Spectral flux: how much energy appeared since the previous frame."""
    np = require_numpy()
    magnitude = stft_magnitude(samples, n_fft, hop_length)
    compressed = np.log1p(500.0 * magnitude)
    flux = np.diff(compressed, axis=1, prepend=compressed[:, :1])
    envelope = np.maximum(flux, 0.0).sum(axis=0)
    # Subtract a local median so a loud section does not dominate a quiet one.
    envelope = envelope - _moving_median(envelope, window=int(round(sample_rate / hop_length)))
    envelope = np.maximum(envelope, 0.0)
    peak = envelope.max()
    return envelope / peak if peak > 0 else envelope


def _moving_median(values, window: int):
    np = require_numpy()
    window = max(3, window | 1)
    half = window // 2
    padded = np.pad(values, half, mode="edge")
    strided = np.lib.stride_tricks.sliding_window_view(padded, window)
    return np.median(strided, axis=-1)


def analyse_spectral(samples, sample_rate: int) -> Spectral:
    """Compute every frame-level feature in one pass."""
    chroma = chromagram(samples, sample_rate)
    onset = onset_strength(samples, sample_rate)
    n_frames = min(chroma.shape[1], onset.shape[0])
    return Spectral(
        chroma=chroma[:, :n_frames],
        onset=onset[:n_frames],
        frame_rate=sample_rate / HOP_LENGTH,
        n_frames=n_frames,
    )
