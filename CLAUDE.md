# omacap — working notes

Context for picking this project back up. Usage lives in [README.md](README.md);
this file is the things that are *not* obvious from the code, and the reasoning
behind decisions that look arbitrary until you know why.

## What it is

A Linux tool that records whatever the computer is playing, by reading the
PulseAudio/PipeWire **monitor source** of the current output sink — the same mix
that reaches the speakers. ffmpeg does capture and encoding; omacap drives it and
reads progress back.

On top of that it can analyse a recording as music: time signature, key, tempo,
bar count and the chords in each bar, written out as a chord chart.

## Status

Both planned stages are finished, tested and pushed.

- **Stage 1 — recording.** TUI and CLI, six output formats, live peak meter.
- **Stage 2 — musical analysis.** `omacap analyze`, `record --analyze`, `a` in
  the TUI, and a y/n question offered as soon as a take is saved.
- **Distribution.** `install.sh` for a one-line install, `omacap update` to
  update in place, and a cached background update check that posts a notice.

466 tests pass (`pytest`). Nothing is known to be broken.

## Design decisions that matter

**ffmpeg is the engine, not a library.** No Python audio bindings. ffmpeg was
already needed for encoding, so it also does decoding for the analysis. That
keeps the dependency list at *zero* for recording and *numpy only* for analysis.

**Stopping means SIGINT, never SIGKILL.** ffmpeg flushes its encoder and writes
the container trailer on SIGINT. Killing it truncates the file — an `.m4a` with no
`moov` atom will not play. ffmpeg exits 255 when interrupted, so 255 counts as
success in `_CLEAN_EXIT_CODES`.

**The level meter reads ffmpeg's log, not a pipe.** `ametadata`'s own `file=-`
output is block-buffered: every level line arrives in one burst *after* recording
ends. Printing to the log instead flushes line by line. This is why the recorder
runs at `-loglevel info` when metering, and why `_NOISE_RE` exists to keep the
resulting info chatter out of error messages. Do not "tidy" this back into a pipe.

**Capabilities are probed, never assumed.** An unsupported filter option makes
ffmpeg refuse to start and write *nothing*, so a slimmer ffmpeg build would
silently cost a whole recording. `metering_supported()` and `available_encoders()`
probe once and are cached. A failed probe means "assume it works", never "refuse".

**The update check must never cost anything.** It reads a cache written by a
background thread, at most once a day, and every failure path is silent: no git,
no network, no remote, a detached HEAD or a corrupt cache all mean "say nothing"
rather than "raise". The notice goes to **stderr**, so `omacap record -q | ...`
still pipes a bare path. `OMACAP_NO_UPDATE_CHECK=1` disables it outright, and the
test suite sets that automatically so no test ever reaches the network.

**Updating never overwrites local work.** `apply_update()` refuses on a dirty
working tree or a detached HEAD, and only ever fast-forwards. The install script
refuses on a dirty tree too. Someone's clone with uncommitted changes is not ours
to discard.

**The installer installs editable.** `pip install --editable` means the code that
runs *is* the checkout, so `omacap update` is a `git pull` plus a reinstall for
entry points and dependencies, and `find_installation()` can locate the checkout
by walking up from the imported package.

**Analysis is numpy-only by choice.** librosa would have been faster to write but
pulls in ~24 packages (numba, llvmlite, scikit-learn). The DSP here is a few
hundred lines against numpy directly. If you ever reconsider, note that the
current accuracy is measured — see below — so any swap has a bar to clear.

## Tuned constants, and the measurements behind them

These numbers were arrived at by experiment, not taste. If you change one, re-run
the relevant tests; they encode the ground truth.

| Where | Constant | Why this value |
| --- | --- | --- |
| `analysis/features.py` | `N_FFT_CHROMA = 8192` | 2.7 Hz resolution, enough to separate semitones down to C2. 4096 could not resolve the bass. |
| `analysis/features.py` | `N_FFT_ONSET = 2048` | Onsets need time resolution, not frequency resolution. Both share `HOP_LENGTH` so the time axes line up. |
| `analysis/features.py` | `sigma_semitones = 0.3` | 0.6 leaked into neighbouring pitch classes badly (a lone A4 scored A, G#, A# nearly equally). 0.2 is too narrow for real, slightly detuned instruments. |
| `analysis/features.py` | *no log compression on chroma* | Log compression flattened contrast so far that a C major triad ranked G, G#, C. Measured: contrast 3.2 without it, 1.1 with it. |
| `analysis/chords.py` | correlation, not cosine | Mean-subtracting makes the *absence* of a pitch count as evidence. With plain cosine every triad loses to the seventh chord containing it. |
| `analysis/chords.py` | `CHANGE_PENALTY = 0.06` | Expressed in correlation units so it is comparable with the scores. The original formulation used log-probabilities and was so sticky it merged chord pairs into a compromise chord. |
| `analysis/chords.py` | `EDGE_MARGIN = 0.2` | The chroma window is 371 ms, so frames near a chord change contain both chords. Skipping the edges stopped F reading as Fmaj7. |
| `analysis/chords.py` | `NO_CHORD_SCORE = 0.38` | 0.30 turned an honest `N.C.` intro into a wrong `Cm`. Higher starts rejecting real quiet chords. |
| `analysis/tempo.py` | `PRIOR_WIDTH_OCTAVES = 1.0` | Log-normal prior around 120 BPM. Necessary but not sufficient: see octave handling below. |
| `analysis/meter.py` | `COMPOUND_MIDBAR_RATIO = 0.90` | Measured accent medians: true 3/4 gives 0.99–1.00, true 6/8 gives 0.78–0.81. Computed on accents *only* — a chord lasting two bars makes the harmony look like six either way. |
| `analysis/report.py` | `DOMINANT_SHARE = 0.62` | A chord holding more than this much of a bar is written alone. |

## Traps already hit and fixed

Worth knowing so they are not reintroduced:

- **Bar one used to be dropped.** The beat tracker reliably misses the beat at
  t=0, so the first downbeat landed a bar late and *every* chart was shifted.
  `bar_boundaries()` extrapolates backwards for this, and trims a trailing bar
  that is more than half empty.
- **Tempo octaves.** Autocorrelation peaks just as hard at half speed; 180 BPM
  came out as 90. Resolved by `onset_coverage()` — the faster grid explains more
  of the onsets. The prior alone is not enough.
- **Tempo precision.** Beat times are quantised to 23 ms frames, so the median
  interval is coarse (120 BPM read as 117.5). `refine_tempo()` fits a line through
  beat index against beat time, which averages the error away. Now exact on
  12/12 click tracks.
- **A chord and its own extension flickering.** D and Dmaj7 alternating made a
  steady bar look as if it changed. `chord_family()` groups them and the
  best-supported variant wins.
- **argparse subparser defaults.** `omacap -f flac record` silently dropped the
  format, because the subparser's `None` default overwrote the parent's value.
  Shared options use `argparse.SUPPRESS` in subparsers for this reason.
- **git may simply not be there.** `doctor` crashed with `FileNotFoundError` on a
  PATH without git. `_git()` now returns a failed `CompletedProcess` instead of
  raising; git is only needed to self-update, so its absence must cost nothing
  else.
- **A stale update notice.** The cache can outlive the update it warned about, so
  `pending_update()` re-reads the local revision before showing anything.
- **`mpris:trackid` is not an identity.** Chromium publishes one id for a whole
  browser session and changes only the title, so keying track changes on the id
  saw a browser playlist as a single endless track. A track is identified by
  `(trackid, title, artist)`. Spotify's desktop client does move the id, which is
  why this went unnoticed until the same playlist was played in a browser.

## Measured accuracy

From the test suite, against synthesised material with known ground truth:

- Tempo: exact on 12/12 click tracks, 60–200 BPM; within 3% with 12 ms jitter.
- Time signature: 11/12. The miss is fast 3/4 (~160 BPM), which correctly reports
  low confidence.
- Chords: 93% per bar; 100% on the standard progressions even with heavy noise.
- Key: 8/8 on resolving progressions; 5/6 on deliberately ambiguous loops, versus
  2/6 for chroma alone without the chord evidence.

On the private reference track used during development (a 4:30 band mix) it
reports G major, 124 BPM, 4/4, 139 bars, and a clear repeating `D | C | Em | D`.
A separate live recording of the same track through the sound card agrees — a
useful end-to-end check that the recording path and the analysis path are
consistent. Keep a real piece of music around for this: the synthetic tests catch
correctness, but only real music catches output that is technically right and
practically unreadable.

## Working on it

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'
pytest                              # 407 tests, ~17s, no sound card needed
python tools/live_check.py          # real audio through the real sound card
```

`pytest` never touches the audio server: recording runs against a stub `ffmpeg`
installed on `PATH` by `tests/conftest.py`, and the analysis runs against audio
synthesised by `tests/synth.py` with a known tempo, key, metre and progression.

`tools/live_check.py` also exercises the y/n question asked after a take. It is
the part that cannot be faked. It plays a generated
C–G–Am–F progression out of the default sink, records it back through the monitor,
drives the real TUI over a pty, and analyses the result — then checks the answer
is C major, 120 BPM, 4/4. Run it after touching recording, the TUI or the audio
plumbing, and on any new machine. Pass `--music FILE` to use real music instead,
and `--keep` to keep the recordings.

**The stub ffmpeg must answer capability probes.** It gets asked `-encoders` and
gets a `-f null` metering probe before any recording. If you extend it and forget
those branches, it treats `-encoders` as an output filename and records into it
until the 15 s probe timeout — which shows up as the whole suite crawling.

## Two installs on one machine

The update cache lives once per user (`~/.cache/omacap/`) but installs are
per-directory, so a cached status records which checkout it was about and is
discarded when it belongs to another. `doctor` reports which launcher PATH would
pick, flags a second one shadowing it, and flags running a different copy than
PATH would choose — this is the confusion people actually hit after installing
twice.

## Layout

```
src/omacap/
  cli.py          argparse entry point; every subcommand
  tui.py          the interactive app: terminal handling, key loop, state
  ui.py           pure rendering — ViewModel in, list of lines out, no I/O
  recorder.py     the ffmpeg process, progress parsing, capability probes
  devices.py      pactl: monitor source discovery
  formats.py      output formats and their encoder arguments
  chart.py        chord chart rendering (markdown and plain text)
  updater.py      update detection, the cached check, and applying an update
  analysis/
    audio.py      decode to mono float32 via ffmpeg; silence trimming
    features.py   STFT, semitone filterbank, chroma, onset strength
    tempo.py      tempo estimation and beat tracking
    meter.py      time signature, downbeat phase, bar grid
    chords.py     templates, beat-synchronous matching, Viterbi
    key.py        Krumhansl-Schmuckler plus chord evidence
    report.py     orchestration; bars and their chords
tools/
  live_check.py   manual end-to-end check against the real sound card
install.sh        one-line installer; also the update path
```

`ui.py` is deliberately free of I/O so the whole screen can be rendered and
asserted on in tests. Keep it that way: if a change needs terminal state, it
belongs in `tui.py`.

## Things that were considered and not done

- **2/4 detection.** Genuinely indistinguishable from 4/4 in audio. Reported as
  4/4, which is how popular music writes it anyway. Do not add it back without a
  real discriminator.
- **Pause and resume.** ffmpeg cannot pause cleanly; `SIGSTOP` would leave a gap
  in the timeline rather than a shorter file.
- **Filtering white noise out of chord detection.** Noise-only input produces
  spurious chords. Real recordings are not white noise, and the honest fix (a
  spectral peakiness test) was not worth the risk of rejecting quiet real chords.

## If you want to take it further

**Splitting a playlist capture into one file per track** is planned in detail in
[docs/track-splitting-plan.md](docs/track-splitting-plan.md): read what the player
reports over MPRIS, cut the capture on track changes, name each file after its
track and chart each one. The plan records what was already measured (silence
thresholds, `busctl --json` polling cost, the FLAC stream-copy header bug, a
`sanitize_basename()` bug that mangles ordinary track titles) so none of it has to
be re-derived, and it phases the work so the one unmeasured risk is settled first.

The rest are smaller and unplanned.

- **Song sections.** Repeated chord patterns are already visible in the charts;
  detecting and labelling them (verse, chorus) would shorten a 139-bar chart a
  lot. Self-similarity over bar-level chroma is the usual approach.
- **Key changes.** `detect_key_with_chords` assumes one key for the whole
  recording. A windowed version would handle modulation.
- **Bass-aware chords.** Slash chords (`C/E`) need the bass note separately,
  which means a low-band chroma alongside the full-range one.
- **Release tags.** `install.sh` takes `OMACAP_REF`, so tagging releases and
  defaulting to the latest tag rather than `main` would give people something
  more stable than the tip of the branch.
- **An AUR package.** The natural next step after tags for Arch and Omarchy.
