# Plan: split a playlist capture into one file per track

Status: **done.** All four phases are built and verified against real playback:
`omacap record --split`, `omacap split`, `omacap nowplaying`, the interface's
split and analysis questions, and stopping on a long silence.
Written 2026-09-25.

The idea: while recording a streaming playlist, read what the player says is
playing, and at the end cut the single capture into one file per track, named
after the track, with a chord chart each. A long trailing silence, or stopping
the recording, triggers the split.

This document exists so the work can be picked up without re-deriving any of it.
The measurements below were taken on an Omarchy machine with PipeWire; the
Spotify metadata was read from a real Spotify client.

## What is already proven

| Question | Answer | Evidence |
| --- | --- | --- |
| Can we read what Spotify is playing? | Yes | MPRIS gave `mpris:trackid`, `xesam:title`, `xesam:artist`, `xesam:album`, `xesam:trackNumber`, `mpris:length` |
| Does reading it need GVariant parsing? | **No** | `busctl --user --json=short get-property …` returns JSON that `json.load()` accepts directly |
| What does polling cost? | 2.0 ms median, 2.8 ms max per call → **0.2 % of one core at 1 Hz** | 15 timed calls |
| Any new dependency? | **None** | `busctl` ships with systemd; `gdbus` (glib2) is a fallback |
| Can silence boundaries be captured live? | Yes, for free | `silencedetect` writes `silence_start` / `silence_end` to ffmpeg's log — the same stderr channel the level meter already parses |
| Do the meter and silence detection coexist? | Yes | one filter chain produced 203 peak lines and 4 silence events together |
| Is a post-hoc detection pass affordable? | Yes | 881× realtime; a 1-hour capture scans in ~4 s |
| Is cutting lossless? | Yes | `-c copy` works for wav/flac/mp3/m4a/opus, landing within ~80 ms |

### Silence thresholds, measured

Inter-track gaps are **true digital silence**, because the whole path is digital
(app → PipeWire → monitor). Nothing musical comes close:

| Material | Peak |
| --- | --- |
| Inter-track gap | **−91 dB** |
| Deliberately quiet passage inside a track | −50.8 dB |
| Quietest real musical moment | −30.2 dB |
| Ordinary music | −0.9 dB |

A threshold sweep against a purpose-built file (three tracks, gaps of 2.0 s and
0.8 s, a quiet passage inside track 2, 20 s trailing silence) gave:

- **`noise=-60dB:d=0.3`–`0.5` is exact**: both gaps and the tail, zero false positives.
- `-50 dB` and looser: splits inside the quiet passage.
- `d=1.0`: misses the 0.8 s gap.

So `-60 dB` with a minimum duration of 0.3–0.5 s, and the margin is ~40 dB.

## Architecture: record once, cut at the end

The recorder is left alone. Switching output files live risks glitches and lost
audio for no benefit. Instead a **timeline** is built during the recording, and
the cut happens on stop. The original is kept until the segments verify.

```
ffmpeg (one process, as now)
  ├─ stderr ──► level meter (exists) + silence_start/end (new, same parser)
  └─ file ────► one continuous recording

MPRIS poller (1 Hz, thread) ──► track changes, timestamped with recorder.duration
                                          │
                                      timeline
                                          ▼
                      boundary resolution → cutting → chord chart per segment
```

Timestamping events with `recorder.duration`, which is already exposed live, puts
the timeline directly in *file* time. No wall-clock mapping is needed.

## New modules

| Module | Responsibility | Rough size |
| --- | --- | --- |
| `nowplaying.py` | find the MPRIS player, poll metadata, normalise it | 120 lines |
| `timeline.py` | collect events, resolve boundaries — **pure logic, no I/O** | 150 lines |
| `splitter.py` | cut the file with ffmpeg, validate the segments | 130 lines |
| `recorder.py` | `silencedetect` in the chain, its events parsed, `silences` and `silent_for` exposed | done |
| `capture.py` | recorder and watcher on one timeline, then the cut | done |
| `cli.py` / `tui.py` | `split`, `nowplaying`, `record --split`, the interface's questions | done |

`timeline.py` is deliberately I/O-free: every awkward decision lives there, so it
is entirely table-testable without any audio.

## Boundary resolution

```
for each new trackid on the timeline:
    t = the observation's time within the recording
    gap = the silence interval containing t          (the measured common case)
          else the nearest silence within ±3 s of t
    boundary = the middle of gap, if there is one
               otherwise t                            (crossfade or gapless)
segment = previous boundary .. next boundary, trimmed to the silence edges
```

The containment test comes first because that is what actually happens: the
measurement below found the signal arriving 1.78 s into a 2.45 s gap.

**The rule that matters: silence never splits on its own. Only a changed
`trackid` does.** Silence is used solely to snap a boundary into place. That is
what protects the two common cases:

| Situation | Signals | Outcome |
| --- | --- | --- |
| Paused mid-track | silence, same trackid | no split — correct |
| Seeking within a track | silence, same trackid | no split — correct |
| Crossfade between tracks | new trackid, no silence | split anyway, on the MPRIS time |

## Naming

`01 - trampe|strandberg - Jag vill vara (en del av din morgondag).flac`

**`sanitize_basename()` has been fixed** (it used to mangle ordinary track
titles). It previously produced:

| Title | Current result |
| --- | --- |
| `Jag vill vara (en del av din morgondag)` | `Jag vill vara _en del av din morgondag_` |
| `Kärlek & Kaos` | `Kärlek _ Kaos` |
| `Sång nr. 3 [Live]` | `Sång nr. 3 _Live_` |
| `What's Going On?` | `What_s Going On_` |

Swedish letters were always fine (`\w` is Unicode-aware). The allow-list now keeps
`-.,&'()[]!+` and a space, so `Jag vill vara (en del av din morgondag)` survives
intact and `What's Going On?` becomes `What's Going On`. Path separators and shell
globs are still replaced (`../../etc/passwd` → `_.._etc_passwd`, `a/b\c` → `a_b_c`),
and a replacement left at the end is trimmed while one at the start is kept, so a
traversal attempt cannot come back as a name beginning with a dot.

Without MPRIS, fall back to `track 01`.

## Cutting

`ffmpeg -ss A -to B -i file -c copy out.ext` for every format, with one exception.

**FLAC gotcha:** `-c copy` cuts the audio correctly but writes the *source's*
duration into the header — a 13.5 s segment reported 58.8 s while decoding 13.48 s.
Players would show the wrong length. Re-encoding FLAC instead is lossless and
gives the correct duration (verified: 13.40 s). So: stream-copy everything,
re-encode FLAC.

Because the cut lands inside digital silence, the ~80 ms frame imprecision is
inaudible, so no format needs a re-encode for accuracy.

## Trailing silence stops the recording

A `silence_start` with no matching `silence_end`, lasting longer than N seconds,
stops the recording and triggers the split. It must only arm **after the first
real audio**, or starting omacap before pressing play would stop it immediately.
Suggested default with `--split`: 30 s, with `0` to disable.

## Surface

```bash
omacap record --split                     # names from MPRIS, cut on stop
omacap record --split --analyze           # plus one chord chart per track
omacap record --split --stop-after-silence 30
omacap split RECORDING.wav                # after the fact; silence only, the MPRIS history is gone
omacap nowplaying                         # diagnostics: show what is being read
```

In the TUI: the number of detected tracks in the session list while recording,
and on stop the same y/n question the analysis already uses — *"Split into 7
tracks?"*

## Edge cases that must be handled

| Case | Handling |
| --- | --- |
| Adverts on a free account | `trackid` contains `/ad/` → drop that segment |
| A player that never moves its `trackid` | Chromium publishes one id per session and changes only the title, so a track's identity is `(trackid, title, artist)`, not the id alone |
| The same track twice in a row | `trackid` unchanged → no split, which is correct |
| Rapid skipping | segments shorter than `--min-track` (default 20 s) are dropped |
| Spotify started mid-recording | the poller tolerates the player being absent and appearing later |
| `busctl` missing | degrade to silence-only splitting and `track NN`; never crash |
| Podcasts or local files | different fields, but a title is present — works |
| Crossfade enabled | no silence found at any boundary → **warn** |

## Test strategy

The project's existing patterns fit this directly.

- **`nowplaying`**: a stub `busctl` on `PATH`, exactly like the stub `ffmpeg` in
  `tests/conftest.py`. Scripted sequences: player absent, player appears, paused,
  malformed JSON, `busctl` missing entirely.
- **`timeline`**: table-driven — snap to silence, no silence, pause, seek, events
  before the first audio, out-of-order events.
- **`splitter`**: real ffmpeg against synthesised audio. Assert segment count,
  durations, **that the FLAC header is right**, and that stream copy is used
  wherever it is safe. One case found while writing these: asking ffmpeg for a
  span past the end of a file exits 0 and writes a bare container with no audio,
  so a cut is only accepted once its duration has been read back.
- **End to end**: a three-track file with 2.0 s and 0.8 s gaps → expect three
  files with the right lengths and names.
- **`tools/live_check.py`**: add gaps to `tone_filter` so silence splitting is
  covered against real audio through the sound card.

## Phases, in this order for a reason

| Phase | Delivers | De-risks |
| --- | --- | --- |
| **1** ✅ | `nowplaying.py` + `omacap nowplaying` | Done. The D-Bus-to-audio delay is measured; see below. |
| **2** ✅ | silence events + `omacap split FILE` | Done. Useful on its own, independent of MPRIS |
| **3** ✅ | live timeline + `record --split` with names | Done. Verified against Spotify: two correctly named files either side of a real track change. |
| **4** ✅ | per-segment analysis, TUI prompt, auto-stop | Done. Verified by driving the real interface across three track changes. |

Phase 1 comes first deliberately: it measures the delay *before* anything is
built on top of assuming it.

## The offset, measured

This was the open risk. It is now settled, against a real Spotify client playing a
playlist across a track boundary, captured with `tools/measure_mpris_offset.py`:

```
[silence]  89.79s  start
[track ]   91.57s  Mister Jensens evangelium
[silence]  92.24s  end
```

| | |
| --- | --- |
| Gap Spotify left between tracks | **2.45 s of silence** |
| MPRIS signal, relative to the audio stopping | 1.78 s after |
| MPRIS signal, relative to the next track starting | 0.67 s before |

**The signal lands inside the gap.** That makes the boundary unambiguous and
simplifies the rule: rather than "the nearest silence within ±3 s", prefer **the
silence interval that contains the signal**, and fall back to the nearest one only
when the signal falls outside every gap (crossfade, gapless).

Two things worth knowing that the synthetic tests did not show:

- **Spotify does leave a gap** — 2.45 s here, with no crossfade configured. That is
  far more generous than the 0.8 s the threshold sweep was tuned against.
- **The gap is not perfect digital silence.** It measured **−70 dB**, not the −91 dB
  a synthetic gap gives, because a filter-chain sink sat in the path. Music either
  side was −28.8 dB and −15.3 dB. So −60 dB still separates them cleanly, but the
  real margin is nearer 10 dB than 40, and the threshold should not be raised.

This is one boundary on one playlist. Crossfade, Automix and gapless albums remain
untested, and each removes the gap by design.

## Estimated size

Roughly 600 lines of source and 800 lines of tests, on top of the current 480
tests.
