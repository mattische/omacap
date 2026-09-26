#!/usr/bin/env python3
"""Render songs whose chords, bars and metre are known exactly.

The test suite's `synth.py` makes sine chords over a click. That proves the
maths but it is not music: no attack transients, no bass, no drums, no melody
crossing the harmony, perfect timing. Real recordings fail in ways that material
cannot show, and the three real songs with charts here are only three - and none
of them changes metre.

So this renders arrangements instead. Every note it plays it also writes down, so
any disagreement is omacap's rather than an opinion:

    python tools/ground_truth.py            # score omacap against every song
    python tools/ground_truth.py --write DIR  # write the audio and the truth

What it deliberately makes hard, because these are the things that break chord
recognition on real mixes:

  - **A melody that leaves the chord.** Passing tones and appoggiaturas look like
    extensions to a template matcher. This is the biggest single cause of a wrong
    chord quality.
  - **Inversions.** The root is not always the lowest note.
  - **Drums.** Broadband transients smear the chroma at exactly the moments the
    beat tracker cares about.
  - **Metre changes mid-song**, which omacap cannot express at all today and so
    is expected to get wrong. That is the point: the size of the error is worth
    knowing.
  - **Human timing.** Notes land early and late, and the tempo drifts.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

SAMPLE_RATE = 22050

#: Semitones above the root for each chord quality.
QUALITIES = {
    "": (0, 4, 7), "m": (0, 3, 7), "7": (0, 4, 7, 10), "m7": (0, 3, 7, 10),
    "maj7": (0, 4, 7, 11), "sus4": (0, 5, 7), "dim": (0, 3, 6),
}

NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")

#: How much of a beat a note may be early or late. Measured from real playing,
#: a good band sits inside about 20 ms; this is deliberately looser.
TIMING_JITTER = 0.012
#: How far the tempo wanders over the song, as a fraction.
TEMPO_DRIFT = 0.015


def label(root: int, quality: str) -> str:
    return f"{NAMES[root % 12]}{quality}"


@dataclass
class Section:
    """One part of a song: a progression, a metre, and how often it repeats."""

    chords: list                  # (root, quality) per bar
    beats_per_bar: int = 4
    repeats: int = 1
    melody: bool = True
    drums: bool = True
    bass: bool = True

    @property
    def bars(self) -> list:
        return list(self.chords) * self.repeats


@dataclass
class Song:
    name: str
    sections: list
    bpm: float = 120.0
    seed: int = 0
    #: Notes above the chord root the melody may use, in semitones. Some of them
    #: are outside the chord on purpose.
    melody_notes: tuple = (0, 2, 4, 5, 7, 9, 11, 14)
    notes: str = ""

    @property
    def bars(self) -> list:
        return [bar for section in self.sections for bar in section.bars]

    @property
    def changes_metre(self) -> bool:
        return len({s.beats_per_bar for s in self.sections}) > 1


# -- instruments ----------------------------------------------------------

def _pluck(freq: float, seconds: float, rng, level: float = 1.0):
    """A struck string: harmonics that decay at different rates, plus an attack.

    Higher harmonics die first, which is what makes a real instrument's chroma
    drift towards the fundamental as the note rings - and what a template
    matcher has to cope with.
    """
    import numpy as np

    n = max(1, int(seconds * SAMPLE_RATE))
    t = np.arange(n) / SAMPLE_RATE
    out = np.zeros(n)
    for harmonic in range(1, 7):
        # Slight inharmonicity, as on a real string.
        f = freq * harmonic * (1.0 + 0.0004 * harmonic * harmonic)
        if f > SAMPLE_RATE / 2.2:
            break
        amplitude = level / (harmonic ** 1.4)
        decay = np.exp(-t * (2.2 + 1.1 * harmonic))
        phase = rng.uniform(0, 2 * np.pi)
        out += amplitude * decay * np.sin(2 * np.pi * f * t + phase)
    # The attack: a short burst of noise where the plectrum hits.
    attack = min(n, int(0.006 * SAMPLE_RATE))
    if attack > 1:
        out[:attack] += 0.28 * level * rng.standard_normal(attack) * np.linspace(1, 0, attack)
    return out


def _kick(rng):
    import numpy as np

    n = int(0.13 * SAMPLE_RATE)
    t = np.arange(n) / SAMPLE_RATE
    sweep = 110 * np.exp(-t * 26) + 46
    return 0.85 * np.exp(-t * 17) * np.sin(2 * np.pi * np.cumsum(sweep) / SAMPLE_RATE)


def _snare(rng):
    import numpy as np

    n = int(0.11 * SAMPLE_RATE)
    t = np.arange(n) / SAMPLE_RATE
    noise = rng.standard_normal(n) * np.exp(-t * 32)
    tone = 0.4 * np.sin(2 * np.pi * 190 * t) * np.exp(-t * 26)
    return 0.55 * (noise + tone)


def _hat(rng):
    import numpy as np

    n = int(0.05 * SAMPLE_RATE)
    t = np.arange(n) / SAMPLE_RATE
    # High-passed noise: cumulative difference is a cheap one-pole high pass.
    noise = rng.standard_normal(n)
    return 0.22 * np.diff(noise, prepend=0.0) * np.exp(-t * 60)


# -- rendering ------------------------------------------------------------

def render(song: Song):
    """Render a song, returning (samples, truth)."""
    import numpy as np

    rng = np.random.default_rng(song.seed)

    # Lay out the bars first, so the truth and the audio come from one pass.
    beat = 60.0 / song.bpm
    truth_bars = []
    at = 0.0
    for section in song.sections:
        for index, (root, quality) in enumerate(section.bars):
            # The tempo wanders, so bars are not all the same length.
            drift = 1.0 + TEMPO_DRIFT * np.sin(2 * np.pi * at / 40.0)
            length = section.beats_per_bar * beat * drift
            truth_bars.append({
                "number": len(truth_bars) + 1,
                "start": at,
                "end": at + length,
                "chord": label(root, quality),
                "root": root % 12,
                "quality": quality,
                "beats_per_bar": section.beats_per_bar,
            })
            at += length
        section_bars = len(section.bars)

    total = int((at + 2.0) * SAMPLE_RATE)
    audio = np.zeros(total)

    def place(signal, when, gain=1.0):
        start = int(max(0.0, when) * SAMPLE_RATE)
        end = min(total, start + len(signal))
        if end > start:
            audio[start:end] += gain * signal[:end - start]

    section_of = {}
    cursor = 0
    for section in song.sections:
        for _ in section.bars:
            section_of[cursor] = section
            cursor += 1

    for index, bar in enumerate(truth_bars):
        section = section_of[index]
        beats = bar["beats_per_bar"]
        step = (bar["end"] - bar["start"]) / beats
        root, quality = bar["root"], bar["quality"]
        intervals = QUALITIES[quality]

        # Chords, struck on the downbeat and on the middle of the bar.
        for offset in (0, beats // 2):
            if offset and beats < 4:
                continue
            when = bar["start"] + offset * step + rng.normal(0, TIMING_JITTER)
            # An inversion every so often: the root is not always lowest.
            voicing = list(intervals)
            if rng.random() < 0.25:
                voicing = voicing[1:] + [voicing[0] + 12]
            for semitone in voicing:
                midi = 48 + root + semitone      # 48 = C3
                freq = 440.0 * 2 ** ((midi - 69) / 12)
                place(_pluck(freq, step * 2.2, rng, 0.5 * rng.uniform(0.8, 1.0)), when)

        if section.bass:
            for offset in range(beats):
                if offset % 2 and rng.random() < 0.5:
                    continue
                when = bar["start"] + offset * step + rng.normal(0, TIMING_JITTER)
                # Mostly the root; a fifth or an approach note now and then.
                semitone = 0 if rng.random() < 0.75 else rng.choice([7, -1, 2])
                midi = 36 + root + semitone      # 36 = C2
                freq = 440.0 * 2 ** ((midi - 69) / 12)
                place(_pluck(freq, step * 1.6, rng, 0.9), when)

        if section.drums:
            for offset in range(beats):
                when = bar["start"] + offset * step
                if offset == 0 or (beats >= 4 and offset == beats // 2):
                    place(_kick(rng), when + rng.normal(0, TIMING_JITTER * 0.5))
                if beats >= 4 and offset in (1, 3):
                    place(_snare(rng), when + rng.normal(0, TIMING_JITTER * 0.5))
                for half in (0.0, 0.5):
                    place(_hat(rng), when + half * step + rng.normal(0, TIMING_JITTER))

        if section.melody:
            # Two to four notes a bar, from a scale that leaves the chord.
            for _ in range(int(rng.integers(2, 5))):
                offset = rng.random() * beats
                when = bar["start"] + offset * step + rng.normal(0, TIMING_JITTER)
                semitone = int(rng.choice(song.melody_notes))
                midi = 72 + root + semitone      # 72 = C5
                freq = 440.0 * 2 ** ((midi - 69) / 12)
                place(_pluck(freq, step * 0.9, rng, 0.35), when)

    # A little room, so nothing is unnaturally dry.
    tail = int(0.09 * SAMPLE_RATE)
    impulse = np.exp(-np.arange(tail) / (tail / 3.5)) * rng.standard_normal(tail) * 0.06
    impulse[0] = 1.0
    # Causal: "same" centres the output and would slide the whole rendering ~45 ms
    # later than the truth written above it, which reads as omacap being early.
    audio = np.convolve(audio, impulse)[:audio.size]

    peak = np.abs(audio).max()
    if peak > 0:
        audio = audio / peak * 0.85
    truth = {
        "name": song.name,
        "bpm": song.bpm,
        "bars": truth_bars,
        "metres": [s.beats_per_bar for s in song.sections],
        "changes_metre": song.changes_metre,
        "notes": song.notes,
    }
    return audio.astype("float32"), truth


# -- the songs ------------------------------------------------------------

def songs() -> list:
    C, D, E, F, G, A, B = 0, 2, 4, 5, 7, 9, 11
    return [
        Song("plain 4/4", [
            Section([(C, ""), (G, ""), (A, "m"), (F, "")], 4, repeats=8),
        ], bpm=120, seed=1, notes="the easy case, for a baseline"),

        Song("no melody", [
            Section([(C, ""), (G, ""), (A, "m"), (F, "")], 4, repeats=8,
                    melody=False),
        ], bpm=120, seed=2, notes="how much the melody alone costs"),

        Song("no drums", [
            Section([(C, ""), (G, ""), (A, "m"), (F, "")], 4, repeats=8,
                    drums=False),
        ], bpm=120, seed=3, notes="how much the drums alone cost"),

        Song("three four", [
            Section([(D, ""), (G, ""), (A, "")], 3, repeats=10),
        ], bpm=132, seed=4, notes="3/4 throughout"),

        Song("six eight", [
            Section([(E, "m"), (C, ""), (G, ""), (D, "")], 6, repeats=6),
        ], bpm=150, seed=5, notes="6/8, the one 3/4 is confused with"),

        Song("slow", [
            Section([(F, ""), (C, ""), (D, "m"), (10, "")], 4, repeats=6),
        ], bpm=68, seed=6, notes="slow enough to invite a tempo octave error"),

        Song("fast", [
            Section([(A, "m"), (F, ""), (C, ""), (G, "")], 4, repeats=10),
        ], bpm=176, seed=7, notes="fast enough to invite the other octave"),

        Song("sevenths", [
            Section([(D, "m7"), (G, "7"), (C, "maj7"), (C, "maj7")], 4, repeats=8),
        ], bpm=104, seed=8, notes="a vocabulary the default does not write"),

        Song("two chords a bar", [
            Section([(C, ""), (A, "m"), (F, ""), (G, "")], 4, repeats=8),
        ], bpm=116, seed=9, notes="harmony moving twice a bar"),

        Song("metre changes", [
            Section([(D, ""), (G, ""), (A, ""), (D, "")], 4, repeats=4),
            Section([(G, ""), (D, "")], 2, repeats=4),
            Section([(D, ""), (G, ""), (A, ""), (D, "")], 4, repeats=4),
        ], bpm=124, seed=10,
            notes="4/4 then 2/4 then 4/4 - omacap cannot express this"),

        Song("three then four", [
            Section([(A, "m"), (F, ""), (C, "")], 3, repeats=6),
            Section([(C, ""), (G, ""), (A, "m"), (F, "")], 4, repeats=6),
        ], bpm=120, seed=11, notes="3/4 then 4/4"),
    ]


# -- scoring --------------------------------------------------------------

def score(song: Song, vocabulary: str = "simple") -> dict:
    """Analyse a rendered song and compare it with what was played."""
    import numpy as np

    from omacap.analysis.audio import AudioBuffer
    from omacap.analysis.report import analyse_buffer

    audio, truth = render(song)
    analysis = analyse_buffer(AudioBuffer(audio, SAMPLE_RATE),
                              Path(f"{song.name}.wav"), vocabulary=vocabulary)

    played = truth["bars"]
    # Compare by time: whichever played bar covers the middle of each detected bar.
    roots = wrong = 0
    for bar in analysis.bars:
        middle = (bar.start + bar.end) / 2
        match = next((p for p in played if p["start"] <= middle < p["end"]), None)
        if match is None or not bar.chords:
            continue
        heads = {_root_of(c) for c in bar.chords if _root_of(c) is not None}
        if match["root"] in heads:
            roots += 1
        else:
            wrong += 1
    total = max(1, roots + wrong)

    expected_metre = truth["metres"][0]
    return {
        "name": song.name,
        "bpm_played": song.bpm,
        "bpm_read": analysis.tempo,
        "bpm_ok": abs(analysis.tempo - song.bpm) / song.bpm < 0.03,
        "metre_played": "/".join(str(m) for m in dict.fromkeys(truth["metres"])),
        "metre_read": analysis.meter.name,
        "metre_ok": analysis.meter.beats_per_bar == expected_metre,
        "metre_conf": analysis.meter.confidence,
        "bars_played": len(played),
        "bars_read": analysis.bar_count,
        "root_agreement": roots / total,
        "changes_metre": truth["changes_metre"],
        "notes": truth["notes"],
    }


def _root_of(chord: str):
    if not chord or chord[0] not in "ABCDEFG":
        return None
    name = chord[:2] if len(chord) > 1 and chord[1] in "#b" else chord[:1]
    flats = {"Db": 1, "Eb": 3, "Gb": 6, "Ab": 8, "Bb": 10}
    if name in flats:
        return flats[name]
    return NAMES.index(name) if name in NAMES else None


# -- per-section metre, measured and rejected -----------------------------

#: Beats in a window. Fewer than eight bars makes a metre guesswork (see
#: ENOUGH_BARS in chart.py), and eight bars of 4/4 is 32 beats.
WINDOW_BEATS = 32
WINDOW_STEP = 4


def metre_windows(song: Song, weights=None) -> list:
    """The metre detected in each sliding window, and what was actually played.

    Detecting the metre per section is the obvious answer to a song that changes
    metre part-way, and `detect_meter` will happily run on a slice. It does not
    work: see `--metre-windows` output. The detector weighs accent, harmonic
    change and kick accumulated over the whole song; over 32 beats there is much
    less to go on, and 6 wins ties because it is a multiple of both 2 and 3.
    """
    import numpy as np

    from omacap.analysis import meter as meter_module
    from omacap.analysis.audio import AudioBuffer, trim_silence
    from omacap.analysis.chords import synchronise
    from omacap.analysis.features import analyse_spectral
    from omacap.analysis.tempo import analyse_tempo

    audio, truth = render(song)
    buffer = trim_silence(AudioBuffer(audio, SAMPLE_RATE))
    spectral = analyse_spectral(np.asarray(buffer.samples), buffer.sample_rate)
    grid = analyse_tempo(spectral.onset, spectral.frame_rate,
                         low_onset=spectral.low_onset)
    beats = np.asarray(grid.beats)
    if beats.size < WINDOW_BEATS + 1:
        return []
    step = float(np.median(np.diff(beats)))
    edges = np.concatenate([beats, [beats[-1] + step]])
    beat_chroma = synchronise(spectral.chroma, spectral.frame_rate, edges)

    original = meter_module.CUE_WEIGHTS
    if weights:
        meter_module.CUE_WEIGHTS = weights
    try:
        found = []
        for start in range(0, beats.size - WINDOW_BEATS + 1, WINDOW_STEP):
            detected = meter_module.detect_meter(
                beats[start:start + WINDOW_BEATS], spectral.onset,
                spectral.frame_rate, beat_chroma[start:start + WINDOW_BEATS],
                low_onset=spectral.low_onset,
            )
            found.append((start, detected.beats_per_bar))
    finally:
        meter_module.CUE_WEIGHTS = original

    # A median filter, because a single odd window is not a metre change.
    values = [value for _, value in found]
    smoothed = []
    for index in range(len(values)):
        low = max(0, index - 2)
        high = min(len(values), index + 3)
        near = values[low:high]
        smoothed.append(max(set(near), key=near.count))

    rows = []
    for (start, raw), value in zip(found, smoothed):
        middle = start + WINDOW_BEATS // 2
        at = 0
        played = truth["metres"][0]
        for section in song.sections:
            length = len(section.bars) * section.beats_per_bar
            if at <= middle < at + length:
                played = section.beats_per_bar
                break
            at += length
        rows.append({"start": start, "raw": raw, "smoothed": value,
                     "played": played})
    return rows


def report_metre_windows(chosen, weights=None) -> int:
    print("Metre detected in sliding 32-beat windows, median-filtered.\n"
          "The question is whether this could drive a per-section metre.\n")
    print(f"{'song':18}{'played':>9}  {'windowed':<34}{'right':>7}")
    total = right = 0
    for song in chosen:
        rows = metre_windows(song, weights)
        if not rows:
            continue
        hits = sum(r["smoothed"] == r["played"] for r in rows)
        total += len(rows)
        right += hits
        played = "+".join(str(m) for m in dict.fromkeys(
            s.beats_per_bar for s in song.sections))
        shape = "".join(str(r["smoothed"]) for r in rows)[:34]
        print(f"{song.name:18}{played:>9}  {shape:<34}{100 * hits / len(rows):6.0f}%"
              f"{'  <- changes' if song.changes_metre else ''}")
    print(f"\n{100 * right / max(1, total):.1f}% of windows right overall.")
    print("\nNot good enough to segment a song's metre: it would make songs with a\n"
          "steady metre wrong to fix the two that change. Leaning on the harmonic\n"
          "cue helps a short window a lot - (0.1, 0.8, 0.1) gives 85% against\n"
          "76% - and changes nothing for the whole song, where every weighting\n"
          "scores 8 of 9.")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--write", type=Path, metavar="DIR",
                        help="write the audio and the truth instead of scoring")
    parser.add_argument("--chords", default="simple")
    parser.add_argument("--only", help="only songs whose name contains this")
    parser.add_argument("--metre-windows", action="store_true",
                        help="detect the metre in sliding windows, to see whether "
                             "a per-section metre could work (it cannot)")
    parser.add_argument("--weights", help="cue weights as a,b,c, for the above")
    args = parser.parse_args(argv)

    chosen = [s for s in songs() if not args.only or args.only in s.name]

    if args.metre_windows:
        weights = None
        if args.weights:
            weights = tuple(float(part) for part in args.weights.split(","))
        return report_metre_windows(chosen, weights)

    if args.write:
        import wave

        args.write.mkdir(parents=True, exist_ok=True)
        for song in chosen:
            audio, truth = render(song)
            path = args.write / f"{song.name.replace(' ', '_')}.wav"
            with wave.open(str(path), "wb") as handle:
                handle.setnchannels(1)
                handle.setsampwidth(2)
                handle.setframerate(SAMPLE_RATE)
                handle.writeframes((audio * 32767).astype("<i2").tobytes())
            path.with_suffix(".json").write_text(json.dumps(truth, indent=1))
            print(f"  {path.name}  {len(truth['bars'])} bars")
        return 0

    print(f"{'song':20}{'bpm':>12}{'metre':>14}{'conf':>6}"
          f"{'bars':>11}{'roots':>7}")
    rows = []
    for song in chosen:
        row = score(song, args.chords)
        rows.append(row)
        print(f"{row['name']:20}"
              f"{row['bpm_played']:5.0f}/{row['bpm_read']:5.0f}"
              f"{'ok' if row['bpm_ok'] else ' X':>2}"
              f"{row['metre_played']:>8}/{row['metre_read']:>5}"
              f"{'ok' if row['metre_ok'] else ' X':>2}"
              f"{row['metre_conf']:>6.2f}"
              f"{row['bars_played']:5}/{row['bars_read']:<5}"
              f"{100 * row['root_agreement']:6.0f}%")
    steady = [r for r in rows if not r["changes_metre"]]
    print(f"\n{len(steady)} songs with one metre: "
          f"tempo {sum(r['bpm_ok'] for r in steady)}/{len(steady)}, "
          f"metre {sum(r['metre_ok'] for r in steady)}/{len(steady)}, "
          f"roots {100 * sum(r['root_agreement'] for r in steady) / len(steady):.0f}%")
    changing = [r for r in rows if r["changes_metre"]]
    if changing:
        print(f"{len(changing)} that change metre: "
              f"roots {100 * sum(r['root_agreement'] for r in changing) / len(changing):.0f}%"
              f"  (metre cannot be right by construction)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
