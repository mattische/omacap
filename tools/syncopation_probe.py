#!/usr/bin/env python3
"""The harness syncopation detection was worked out on. It now ships.

The measure lives in omacap/analysis/rhythm.py - metrical_weights, bar_profile,
syncopation - and is reported per song and per section. This file is kept because
it is where the anchors were established, and where two wrong conclusions were
found and corrected.

THE MEASURE. Syncopation is a note on a weak metrical position where the stronger
position after it is weaker, or empty: the note has displaced it. Longuet-Higgins
& Lee (1984) score that on notated music by the difference in metrical weight.
Read off a continuous onset profile the same idea needs NO THRESHOLD: whatever a
weak position has above the stronger one after it is the displacement, and a
quieter one contributes nothing without being judged. That matters, because the
first version used a fixed floor and dense material cleared it at every position,
which made the score meaningless there.

THE ANCHORS, from patterns built to test it:

    four on the floor      0.00
    straight eighths       0.00     correct: every off-beat is followed by a
                                    strong position that IS played
    classic syncopation    0.37
    offbeat eighths only   0.44
    anticipated downbeat   0.62

Every real recording tried sits between 0.00 and 0.10, so the threshold for
calling a section syncopated is 0.30 - where the written-out patterns start, not
where the recordings happen to fall.

WHY PER SECTION. Averaged over a whole song a syncopated chorus and a straight
verse cancel out. One recording here shows it plainly: 0.001 for the whole song
and 0.283 for its one repeated section.

TWO CORRECTIONS this file earned. An earlier version reported the bar profile as
unusable, peaking 1.75 beats into the bar on MEDS. That was a bug here: the
analysis trims leading silence and measures bar times from the trimmed audio,
while this loaded the untrimmed file - 3.4 seconds out, nearly two bars. The same
bug understated bass_probe.py by five to eight points. Multi-band onset was then
tried against the dense-material problem, which is what it was proposed for, and
does not help: kick, snare and hat bands all stay full. It does confirm that the
kick band alone puts the peak on the downbeat where the full band peaks on the
backbeat, which is the band meter.py already uses.

    python tools/syncopation_probe.py                 # the patterns and anchors
    python tools/syncopation_probe.py FILE [FILE...]  # real recordings
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from omacap.analysis.rhythm import (  # noqa: E402
    BAR_SUBDIVISIONS,
    SYNCOPATED,
    bar_profile,
    metrical_weights,
    syncopation,
)


class Bar:
    """The little a bar profile needs: where the bar starts and how long it is."""

    def __init__(self, start: float, duration: float):
        self.start = start
        self.duration = duration


def shape(profile, floor: float = 0.2) -> str:
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
    positions = BEATS * BAR_SUBDIVISIONS
    step = bar_length / positions
    weights = metrical_weights(BEATS, BAR_SUBDIVISIONS)
    bars = [Bar(index * bar_length, bar_length) for index in range(BARS)]

    print(f"{'pattern':24}{'score':>7}   profile (sixteenths)"
          f"     threshold {SYNCOPATED}")
    for name, hits in PATTERNS.items():
        samples = np.zeros(int(BARS * bar_length * SR) + 4000)
        for bar in range(BARS):
            for hit in hits:
                start = int((bar * bar_length + hit * step) * SR)
                sound = click(0.5, accent=(hit == 0))
                samples[start:start + len(sound)] += sound
        onset, _ = onset_strengths(samples, SR)
        profile = bar_profile(onset, SR / HOP_LENGTH, bars, positions)
        print(f"{name:24}{syncopation(profile, weights):7.2f}   {shape(profile)}")
    return 0


def recordings(paths: list[Path]) -> int:
    import numpy as np

    from omacap.analysis.report import analyse_file

    print(f"{'song':33}{'metre':6}{'song':>6}{'section range':>16}   flagged")
    for path in paths:
        analysis = analyse_file(path)
        sections = analysis.section_syncopation
        span = (f"{min(sections.values()):.3f}-{max(sections.values()):.3f}"
                if sections else "none")
        flagged = sorted(analysis.syncopated_sections) or "-"
        print(f"{path.stem[:32]:33}{analysis.meter.name:6}"
              f"{analysis.syncopation:6.3f}{span:>16}   {flagged}")
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
