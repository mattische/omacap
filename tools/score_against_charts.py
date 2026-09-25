#!/usr/bin/env python3
"""Score omacap's analysis against charts somebody actually wrote.

Synthesised audio proves the maths; only a real chart written by the people who
played the song proves the result is useful. This scores omacap against a set of
recordings whose chords are known, so a change to the analysis can be judged
rather than guessed at.

The charts are nobody's business but their owner's, so nothing about them lives
here. Point the tool at a JSON file instead:

    {
      "songs": [
        {
          "name":   "Example",
          "audio":  "~/Music/example.mp3",
          "key":    "E minor",
          "chords": ["C", "Em", "D", "Am"],
          "loop":   ["C", "Em", "D", "D"]
        }
      ]
    }

``chords`` is every chord the chart uses, written as omacap would write it - a
slash chord counts as its root triad, since omacap does not write inversions.
``loop`` is an optional repeating figure; the tool reports the longest run it is
reproduced for.

    python tools/score_against_charts.py songs.json
    python tools/score_against_charts.py songs.json --chords standard
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def longest_run(roots: list[str], loop: list[str]) -> int:
    """The longest stretch reproducing ``loop``, starting at any point or phase."""
    if not loop:
        return 0
    best = 0
    for start in range(len(roots)):
        for phase in range(len(loop)):
            run = 0
            while (start + run < len(roots)
                   and roots[start + run] == loop[(run + phase) % len(loop)]):
                run += 1
            best = max(best, run)
    return best


def score(song: dict, vocabulary: str) -> dict:
    from omacap.analysis.report import analyse_file

    path = Path(song["audio"]).expanduser()
    analysis = analyse_file(path, vocabulary=vocabulary)
    roots = [bar.label.split()[0] for bar in analysis.bars]
    wanted = set(song.get("chords", []))
    agree = sum(1 for root in roots if root in wanted)
    return {
        "name": song.get("name", path.stem),
        "key": analysis.key.name,
        "key_ok": analysis.key.name == song.get("key"),
        "confidence": analysis.key.confidence,
        "metre": analysis.meter.name,
        "bpm": analysis.tempo,
        "agreement": agree / len(roots) * 100 if roots else 0.0,
        "run": longest_run(roots, song.get("loop", [])),
        "bars": len(roots),
        "stray": sorted({r for r in roots if r not in wanted and r != "N.C."}),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("spec", help="JSON file describing the songs and their charts")
    parser.add_argument("-c", "--chords", default="simple",
                        help="chord vocabulary to score (default: simple)")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="also list chords found that the chart does not use")
    args = parser.parse_args()

    try:
        spec = json.loads(Path(args.spec).expanduser().read_text())
    except (OSError, ValueError) as exc:
        print(f"score_against_charts: could not read {args.spec}: {exc}", file=sys.stderr)
        return 2
    songs = spec.get("songs") or []
    if not songs:
        print("score_against_charts: the spec has no songs in it", file=sys.stderr)
        return 2

    rows = []
    print(f"{'song':22} {'key':11} {'ok':>3} {'conf':>5} {'metre':>6} {'bpm':>5} "
          f"{'agreement':>10} {'loop':>5} {'bars':>5}")
    for song in songs:
        try:
            row = score(song, args.chords)
        except Exception as exc:
            print(f"  {song.get('name', '?'):22} could not be scored: {exc}")
            continue
        rows.append(row)
        print(f"  {row['name']:20} {row['key']:11} {'yes' if row['key_ok'] else 'no':>3} "
              f"{row['confidence']:5.2f} {row['metre']:>6} {row['bpm']:5.0f} "
              f"{row['agreement']:9.0f}% {row['run']:5} {row['bars']:5}")
        if args.verbose and row["stray"]:
            print(f"  {'':20} not in the chart: {', '.join(row['stray'])}")

    if rows:
        mean = sum(r["agreement"] for r in rows) / len(rows)
        keys = sum(1 for r in rows if r["key_ok"])
        print(f"\n  {len(rows)} songs: agreement {mean:.0f}% on average, "
              f"key right for {keys} of {len(rows)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
