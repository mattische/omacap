#!/usr/bin/env python3
"""Is syncopation readable off a recording? Measured, not assumed.

Syncopation is a note on a weak metrical position whose next *stronger* position
is empty. Longuet-Higgins & Lee (1984) score that by the difference in metrical
weight, and this reads the same score off a continuous onset profile instead of
off notated notes.

WHAT WORKS: the score itself. On synthesised patterns it says what music theory
says, with no tuning:

    four on the floor      0.00   #...#...#...#...
    straight eighths       0.00   #.#.#.#.#.#.#.#.
    offbeat eighths only   1.37   ..#...#...#...#.
    classic syncopation    1.08   #..#..#...#..#..
    anticipated downbeat   1.57   ...##..#...#...#

Note that straight eighths score zero, correctly: every off-beat there is
followed by a stronger position that *is* played, so nothing is displaced.

A CORRECTION. An earlier version of this file reported that the bar profile was
unusable - that on MEDS it peaked 1.75 beats into the bar. That was a bug here,
not a property of the audio: the analysis trims leading silence and measures bar
times from the trimmed audio, while this computed onsets on the untrimmed file.
On MEDS that is 3.4 seconds, nearly two bars. With the trim applied the profiles
are what the music actually does:

    MEDS                4/4   0.00   #.#.#.#.#.#.#.#.   straight eighths
    The Last Song       4/4   0.00   #...#...#...#...   four quarter notes
    So Gung Ho          4/4   0.00   #.#.#.#.#.#.#...

The downbeat lands on position 0 on every one, and the scores are 0.00 because
these songs are straight - which is the right answer, not a failure.

WHAT STILL DOES NOT WORK, for two reasons that survive the fix:

  - Averaged over a whole song, syncopation washes out. A syncopated chorus and
    a straight verse average to straight. Anything useful would have to be
    measured per section, which omacap now knows how to divide.
  - On dense material every one of the sixteen positions clears the floor, so
    nothing can be called syncopated whatever the music does. Separating the
    onset into kick, snare and hat bands was tried against exactly this and does
    not help - all three bands stay full. The floor is the problem, not the
    mixing of bands.

Band separation does earn one thing: the kick band alone (40-120 Hz) puts the
peak on the downbeat on every recording tried, where the full band peaks on the
backbeat instead. That is the band omacap's metre detection already uses, so the
measurement confirms that choice rather than improving on it.

    python tools/syncopation_probe.py                 # the synthesised patterns
    python tools/syncopation_probe.py FILE [FILE...]  # real recordings
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

#: Sixteenths in a 4/4 bar.
POSITIONS = 16

#: Metrical weight of each sixteenth: the downbeat, then beat 3, then beats 2
#: and 4, then the eighths, then the rest. Standard metrical hierarchy.
WEIGHTS = [4, 1, 2, 1, 3, 1, 2, 1, 3.5, 1, 2, 1, 3, 1, 2, 1]

#: A position has to reach this share of the strongest one to count as played.
FLOOR = 0.2


def bar_profile(onset, frame_rate: float, downbeats, bar_length: float):
    """Average onset strength at each sixteenth of the bar."""
    import numpy as np

    total = np.zeros(POSITIONS)
    counts = np.zeros(POSITIONS)
    for downbeat in downbeats:
        for position in range(POSITIONS):
            when = downbeat + position * bar_length / POSITIONS
            index = int(round(when * frame_rate))
            if 0 <= index < len(onset):
                window = onset[max(0, index - 1): min(len(onset), index + 2)]
                total[position] += float(window.max())
                counts[position] += 1
    profile = total / np.maximum(counts, 1)
    return profile / max(float(profile.max()), 1e-9)


def syncopation(profile, floor: float = FLOOR) -> float:
    """Longuet-Higgins & Lee, read off the profile rather than off notes."""
    played = [value >= floor for value in profile]
    score = 0.0
    for position in range(POSITIONS):
        if not played[position]:
            continue
        # Find the next position that is metrically stronger than this one. If
        # nothing is played there, this note has displaced it.
        for step in range(position + 1, position + POSITIONS + 1):
            other = step % POSITIONS
            if WEIGHTS[other] > WEIGHTS[position]:
                if not played[other]:
                    score += (WEIGHTS[other] - WEIGHTS[position]) * profile[position]
                break
    return score / max(sum(played), 1)


def shape(profile, floor: float = FLOOR) -> str:
    return "".join("#" if value >= floor else "." for value in profile)


# -- the synthesised patterns --------------------------------------------

PATTERNS = {
    "four on the floor": [0, 4, 8, 12],
    "straight eighths": [0, 2, 4, 6, 8, 10, 12, 14],
    "offbeat eighths only": [2, 6, 10, 14],
    "classic syncopation": [0, 3, 6, 10, 13],
    "anticipated downbeat": [3, 4, 7, 11, 15],
}

BPM = 120.0
BEATS = 4
BARS = 16


def synthesised() -> int:
    import numpy as np

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))
    from synth import SR, click

    from omacap.analysis.features import HOP_LENGTH, onset_strengths

    beat = 60.0 / BPM
    bar_length = BEATS * beat
    step = bar_length / POSITIONS
    downbeats = [index * bar_length for index in range(BARS)]

    print(f"{'pattern':24}{'score':>7}   profile (sixteenths)")
    for name, hits in PATTERNS.items():
        samples = np.zeros(int(BARS * bar_length * SR) + 4000)
        for bar in range(BARS):
            for hit in hits:
                start = int((bar * bar_length + hit * step) * SR)
                sound = click(0.5, accent=(hit == 0))
                samples[start:start + len(sound)] += sound
        onset, _ = onset_strengths(samples, SR)
        profile = bar_profile(onset, SR / HOP_LENGTH, downbeats, bar_length)
        print(f"{name:24}{syncopation(profile):7.2f}   {shape(profile)}")
    return 0


def recordings(paths: list[Path]) -> int:
    import numpy as np

    from omacap.analysis.audio import load_audio, trim_silence
    from omacap.analysis.features import HOP_LENGTH, onset_strengths
    from omacap.analysis.report import analyse_file

    print(f"{'song':33}{'metre':6}{'score':>6}   profile (sixteenths)")
    for path in paths:
        analysis = analyse_file(path, vocabulary="simple")
        # The analysis trims leading silence, and bar times are measured
        # from the trimmed audio, so the same trim has to happen here.
        buffer = trim_silence(load_audio(path))
        onset, _ = onset_strengths(np.asarray(buffer.samples), buffer.sample_rate)
        bar_length = float(np.median([bar.duration for bar in analysis.bars]))
        profile = bar_profile(onset, buffer.sample_rate / HOP_LENGTH,
                              [bar.start for bar in analysis.bars], bar_length)
        note = "" if analysis.meter.beats_per_bar == 4 else "  <- weights assume 4/4"
        print(f"{path.stem[:32]:33}{analysis.meter.name:6}"
              f"{syncopation(profile):6.2f}   {shape(profile)}{note}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("files", nargs="*", type=Path,
                        help="recordings to probe; omit for the synthesised patterns")
    args = parser.parse_args(argv)
    if args.files:
        return recordings(args.files)
    return synthesised()


if __name__ == "__main__":
    raise SystemExit(main())
