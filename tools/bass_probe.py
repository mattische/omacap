#!/usr/bin/env python3
"""Can the exact bass notes be recovered, and their rhythm? Measured.

The bass is a much easier problem than the chords above it: it is monophonic, so
it does not need the spectrum resolved into separate semitones, which is what
limits the chroma path down there (see RESOLUTION_KNEE in CLAUDE.md). A time
domain method works instead - autocorrelation on a low-passed, decimated signal -
and at 41 Hz that is comfortable rather than marginal.

WHAT WORKS: the pitch, and more precisely than expected.

  - On a bass line written here, 12 of 13 notes come back exactly, including E1
    at 41 Hz. The one miss is a short note next to a higher one.
  - Window length is the whole game, and shorter is better. Measured against
    notes of a known length:

        window     1000ms    500ms    250ms    125ms
         372ms       100%      12%       0%       0%
         186ms       100%     100%      38%      12%
          93ms       100%     100%     100%     100%
          46ms        62%      62%      50%      50%

    At 93 ms every note length survives, down to sixteenths at 120 BPM. Below
    that there are too few periods of a low E to correlate against. The 93 ms
    window also removes a bias the longer ones have: -0.03 semitones against
    +0.21 at 186 ms, and no octave errors at all.

WHAT IS NOT SETTLED: whether it is the bass being tracked, on any given mix.
Judged by how often the bar's tracked note is the chord root - a floor, not a
target, since a bass legitimately leaves the root:

    MEDS sessionmix          79%    misses spread thinly, no systematic error
    The Last Song            73%
    So Gung Ho               66%
    En psalm                 43%
    Cabrillos                34%    errors smeared across every interval

(An earlier version of this file reported 78/73/58/35/26%. Those were measured
against untrimmed audio while the bar times come from the trimmed analysis - 3.4
seconds out on MEDS. The numbers above are with that fixed; the picture is the
same but it was understated by five to eight points.)

On the three studio mixes this is a working tracker. On the other two it is not,
and the errors are not one fixable failure mode - not octaves, not fifths, but
spread. So something other than the bass is being followed part of the time.

RHYTHM: the pitch side is good down to sixteenths, but a bass *onset* sits in the
same band as the kick drum, and separating them is the same unsolved problem
`syncopation_probe.py` ran into. Note lengths would be reliable; note attacks
would not, yet.

WHAT IT WOULD BUY: slash chords - omacap never writes an inversion today - and a
written bass line. WHAT IT WOULD TAKE: harmonic-sum scoring rather than raw
autocorrelation, to stop the semitone and fifth confusions; a test for whether
there is a bass present at all; and bass onsets separated from the kick.

    python tools/bass_probe.py                 # the written line and the windows
    python tools/bass_probe.py FILE [FILE...]  # real recordings
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

SAMPLE_RATE = 22050
DECIMATE = 4                  # -> 5512 Hz; Nyquist well above the bass
FRAME = 512                   # 93 ms, the length that survived short notes
HOP = 128                     # 43.06 Hz, the frame rate omacap uses elsewhere
LOW_HZ, HIGH_HZ = 40.0, 400.0  # E1 up to about G4
CLARITY = 0.30                # the autocorrelation peak must reach this

NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
FLATS = {"Db": 1, "Eb": 3, "Gb": 6, "Ab": 8, "Bb": 10}


def note_name(midi: float) -> str:
    number = int(round(midi))
    return f"{NAMES[number % 12]}{number // 12 - 1}"


def _lowpass(samples, sample_rate: float, cutoff: float):
    """Taper everything above the bass away before decimating."""
    import numpy as np

    spectrum = np.fft.rfft(samples)
    freqs = np.fft.rfftfreq(len(samples), 1 / sample_rate)
    taper = np.clip((cutoff * 1.5 - freqs) / (cutoff * 0.5), 0.0, 1.0)
    return np.fft.irfft(spectrum * taper, n=len(samples))


def track(samples, sample_rate: float = SAMPLE_RATE, frame: int = FRAME):
    """Pitch per frame in MIDI numbers, NaN where nothing is clear enough."""
    import numpy as np

    signal = _lowpass(np.asarray(samples, dtype=float), sample_rate,
                      HIGH_HZ)[::DECIMATE]
    rate = sample_rate / DECIMATE
    low, high = int(rate / HIGH_HZ), int(rate / LOW_HZ) + 1
    size = 1 << (2 * frame - 1).bit_length()

    pitches = []
    for start in range(0, max(0, len(signal) - frame), HOP):
        window = signal[start:start + frame]
        window = window - window.mean()
        power = float(window @ window)
        if power < 1e-9:
            pitches.append(np.nan)
            continue
        spectrum = np.fft.rfft(window, size)
        acf = np.fft.irfft(spectrum * np.conj(spectrum), size)[:high + 2]
        search = acf[low:high]
        if len(search) < 3:
            pitches.append(np.nan)
            continue
        peak = int(np.argmax(search)) + low
        if peak <= 0 or acf[peak] / power < CLARITY:
            pitches.append(np.nan)
            continue
        # Parabolic interpolation, so the pitch is not quantised to whole lags.
        before, at, after = acf[peak - 1], acf[peak], acf[peak + 1]
        shift = 0.5 * (before - after) / max(before - 2 * at + after, 1e-12)
        lag = peak + min(1.0, max(-1.0, shift))
        pitches.append(69 + 12 * float(np.log2((rate / lag) / 440.0)))
    return np.array(pitches), rate / HOP


# -- a bass line written here, so the truth is known ---------------------

def bass_tone(midi: int, seconds: float, sample_rate: int = SAMPLE_RATE):
    """A plucked bass note: fundamental plus decaying harmonics, not a sine."""
    import numpy as np

    freq = 440.0 * 2 ** ((midi - 69) / 12)
    t = np.arange(int(seconds * sample_rate)) / sample_rate
    tone = sum(level * np.sin(2 * np.pi * freq * harmonic * t)
               for harmonic, level in [(1, 1.0), (2, 0.45), (3, 0.2), (4, 0.1)])
    envelope = np.minimum(1.0, np.exp(-t * 2.2) + 0.05)
    attack = np.minimum(1.0, t / 0.008)
    return tone * envelope * attack


#: Open strings and a walk down, in MIDI numbers with lengths in seconds.
LINE = [(28, .5), (28, .5), (33, .5), (33, .5), (38, .5), (40, .25), (41, .25),
        (43, .5), (36, .5), (35, .5), (33, 1.0), (31, .5), (28, 1.0)]


def written() -> int:
    import numpy as np

    print("=== a bass line written here ===")
    signal = np.concatenate([bass_tone(m, d) for m, d in LINE])
    pitch, rate = track(signal)
    truth = np.concatenate([[m] * int(round(d * rate)) for m, d in LINE])
    length = min(len(truth), len(pitch))
    truth, pitch = truth[:length], pitch[:length]
    voiced = ~np.isnan(pitch)
    error = pitch[voiced] - truth[voiced]
    print(f"  frames with a pitch:  {100 * voiced.mean():.0f}%")
    print(f"  median bias:          {np.median(error):+.3f} semitones")
    print(f"  exact note per frame: {100 * np.mean(np.round(pitch[voiced]) == truth[voiced]):.0f}%")
    print(f"  octave errors:        {100 * np.mean(np.abs(np.abs(error) - 12) < 0.5):.1f}%")

    print("\n  note by note:")
    at = 0
    for midi, duration in LINE:
        count = int(round(duration * rate))
        segment = pitch[at:at + count]
        at += count
        segment = segment[~np.isnan(segment)]
        heard = note_name(float(np.median(segment))) if len(segment) else "-"
        flag = "" if heard == note_name(midi) else "   MISS"
        print(f"    {note_name(midi):4} for {duration:4}s  ->  {heard:4}{flag}")

    print("\n=== how short a note survives, by window length ===")
    lengths = (1.0, 0.5, 0.25, 0.125)
    scale = [28, 35, 31, 38, 33, 40, 29, 36]
    print(f"{'window':>9}  " + "  ".join(f"{d * 1000:>5.0f}ms" for d in lengths))
    for frame in (2048, 1024, 512, 256):
        row = []
        for duration in lengths:
            signal = np.concatenate([bass_tone(m, duration) for m in scale])
            pitch, rate = track(signal, frame=frame)
            hits, at = 0, 0
            for midi in scale:
                count = int(round(duration * rate))
                segment = pitch[at:at + count]
                at += count
                segment = segment[~np.isnan(segment)]
                if len(segment) and round(float(np.median(segment))) == midi:
                    hits += 1
            row.append(f"{100 * hits / len(scale):5.0f}%")
        ms = frame / (SAMPLE_RATE / DECIMATE) * 1000
        print(f"{ms:7.0f}ms  " + "  ".join(row))
    return 0


# -- against real recordings ---------------------------------------------

def _root_of(label: str) -> int | None:
    if label == "N.C.":
        return None
    head = label[:2] if len(label) > 1 and label[1] in "#b" else label[:1]
    if head in FLATS:
        return FLATS[head]
    return NAMES.index(head) if head in NAMES else None


def recordings(paths: list[Path]) -> int:
    import numpy as np

    from omacap.analysis.audio import load_audio, trim_silence
    from omacap.analysis.report import analyse_file

    intervals = {0: "root", 1: "semitone", 2: "2nd", 3: "minor 3rd",
                 4: "major 3rd", 5: "fourth", 6: "tritone", 7: "fifth",
                 8: "minor 6th", 9: "6th", 10: "minor 7th", 11: "major 7th"}

    for path in paths:
        analysis = analyse_file(path, vocabulary="simple")
        # The analysis trims leading silence, and bar times are measured
        # from the trimmed audio, so the same trim has to happen here.
        buffer = trim_silence(load_audio(path))
        pitch, rate = track(np.asarray(buffer.samples), buffer.sample_rate)
        counts: dict[int, int] = {}
        freqs = []
        for bar in analysis.bars:
            segment = pitch[int(bar.start * rate):int(bar.end * rate)]
            segment = segment[~np.isnan(segment)]
            root = _root_of(bar.chords[0]) if bar.chords else None
            if len(segment) < 3 or root is None:
                continue
            middle = float(np.median(segment))
            freqs.append(440.0 * 2 ** ((middle - 69) / 12))
            note = int(round(middle)) % 12
            counts[(note - root) % 12] = counts.get((note - root) % 12, 0) + 1
        total = max(1, sum(counts.values()))
        print(f"\n{path.stem[:44]}  ({total} bars judged, "
              f"median {np.median(freqs):.0f} Hz)")
        print("  where the tracked note sits against the chord root:")
        for step, count in sorted(counts.items(), key=lambda kv: -kv[1])[:6]:
            print(f"    {intervals[step]:12} {100 * count / total:5.1f}%")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("files", nargs="*", type=Path,
                        help="recordings to probe; omit for the written line")
    args = parser.parse_args(argv)
    return recordings(args.files) if args.files else written()


if __name__ == "__main__":
    raise SystemExit(main())
