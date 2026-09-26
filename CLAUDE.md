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
| `analysis/features.py` | `ONSET_LEAD = 0.027` | The onset envelope **leads** the sound it describes. The window is 93 ms and centred, so a transient at t already raises energy in frames centred from t-46 ms, and the flux peaks before the attack rather than on it. Measured twice independently: -27 ms on isolated clicks at six positions, and -31 ms across eleven arrangements' bar lines, with only 12 ms of scatter round it. Uncorrected, **every beat, bar and cut omacap reported was early by that much**. Correcting it took bar lines within 30 ms from 44% to 95% and within 15 ms from 10% to 74%, synthesised chord roots from 94% to 95%, and the longest exactly-correct run on a real song from 45 bars to 48. |
| *what "92% agreement" measures* | `tools/score_against_charts.py` counts a bar as agreeing when its chord appears **anywhere** in that song's chart. It does not check the chord is right *in that bar* - a song using C, Em, D and Am would score 100% with those four scrambled. The order-checking figure is the `loop` column, `longest_run`, which is the longest stretch exactly reproducing a known repeating figure: 24, 8 and 48 bars on the three real songs. Quote both, and do not let the first stand in for accuracy. |
| `analysis/features.py` | `RESOLUTION_KNEE = 2.5` | A semitone needs about this many FFT bins across it before its neighbours separate. At C2 there are only 1.4, so each band is weighted by how well it is resolved rather than the low end being cut off. A hard cutoff at C3 was tried too and was worse; the taper is also stable for a knee anywhere from 2 to 3. |
| `analysis/chords.py` | `CHANGE_PENALTY = 0.25`, `SAME_ROOT_FRACTION = 0.3` | Harmonic rhythm is slower than the beat. Agreement is flat from 0.20 to 0.30 and a progression that genuinely changes every beat survives to 0.30, so the middle is taken. Changing quality on the same root costs a fraction, or the decoder refuses to hear a suspension resolve. |
| `analysis/meter.py` | `CUE_WEIGHTS = (0.3, 0.4, 0.3)` | Accent, harmonic change, kick band. The kick shares the work rather than replacing anything; adding it took one recording's metre confidence from 0.05 to 1.00 without unseating any time signature that was already right. |
| `analysis/report.py` | `UNCERTAIN_FRACTION = 0.8` | A bar matching this much worse than the song's median gets a `?`. Relative rather than a fixed percentile, so a song the analysis handled well is marked nowhere. Measured: marks ~14% of bars and catches ~57% of the disagreements with a real chart, against 14% for picking at random. |
| `analysis/rhythm.py` | `STRAIGHT_BELOW = 0.56`, `SWUNG_BELOW = 0.72` | Where the off-beat sits. Straight reads 0.51 where 0.50 was played and triplet swing reads 0.67 where 0.667 was played, so the bands sit clear of both. |
| `analysis/key.py` | a relative pair is decided by the chords alone | A key and its relative hold identical notes, so whatever chroma correlation one gets, the other gets by construction - it is a blind witness, not a weak one. When the top two candidates are a relative pair the chroma is dropped and only `chord_evidence` votes. Took the keys from 2 of 3 real songs to 3 of 3 and raised MEDS's confidence from 0.46 to 0.86, with chord agreement unchanged. Two alternatives were tried and rejected: choosing the key signature from chroma *first* and the tonic after (picks D major for MEDS, 1 of 3 wrong), and section-start chords as a cue (1 right, 1 wrong, 1 tie over three songs). |
| `analysis/chords.py` | `DEFAULT_VOCABULARY = "simple"` | Measured against three real charts: simple 92% agreement, standard 85%, full 67%. Colourings also break up the repeated phrases the chart collapses - the longest reproduced loop falls from 45 bars to 18 to 6. The root chord is what a player needs; a seventh it guessed wrong costs more than a seventh it missed. |
| `analysis/key.py` | `KEY_BONUS = 0.05` | A second decoding pass favours chords that belong to the detected key. 0.04-0.08 all gave the same gain, so the middle was taken. |
| `analysis/features.py` | `sigma_semitones = 0.3` | 0.6 leaked into neighbouring pitch classes badly (a lone A4 scored A, G#, A# nearly equally). 0.2 is too narrow for real, slightly detuned instruments. |
| `analysis/features.py` | *no log compression on chroma* | Log compression flattened contrast so far that a C major triad ranked G, G#, C. Measured: contrast 3.2 without it, 1.1 with it. |
| `analysis/chords.py` | correlation, not cosine | Mean-subtracting makes the *absence* of a pitch count as evidence. With plain cosine every triad loses to the seventh chord containing it. |
| `analysis/chords.py` | `CHANGE_PENALTY = 0.06` | Expressed in correlation units so it is comparable with the scores. The original formulation used log-probabilities and was so sticky it merged chord pairs into a compromise chord. |
| `analysis/chords.py` | `EDGE_MARGIN = 0.2` | The chroma window is 371 ms, so frames near a chord change contain both chords. Skipping the edges stopped F reading as Fmaj7. |
| `analysis/chords.py` | `NO_CHORD_SCORE = 0.38` | 0.30 turned an honest `N.C.` intro into a wrong `Cm`. Higher starts rejecting real quiet chords. |
| `analysis/tempo.py` | the octave is judged on the **kick band**, `OCTAVE_GAIN = 1.10` | A doubled beat grid is a *superset* of the true one: every real onset still lands on a beat, so any measure of how well the beats explain the full band must prefer the double or tie. Hi-hats on every eighth **are** the doubled grid. That is not a tuning problem, it is the shape of the question, and it is why the shipped check could only ever double - it wrongly doubled 104 to 208 on one test song. The kick plays on beats, so on the low band a genuinely halved tempo loses badly (measured 0.20 against 1.00) while a correct tempo ties with its double (1.00-1.02). The threshold sits between those measured groups. Took `tools/ground_truth.py` from 7/9 to 8/9 on tempo with no change to the real songs. |
| `analysis/tempo.py` | the 0.5 branch is unreachable in practice | Same reason: coverage can never argue for the slower tempo. A tempo whose first estimate is already an octave high therefore stays there - one test song does. Fixing it needs evidence coverage does not carry. `tests/test_analysis_tempo.py::test_coverage_cannot_argue_for_halving` pins this so it is not read as a bug later. |
| *no per-section metre* | The obvious answer to a song that changes metre, and `detect_meter` runs on a slice happily. Measured with `tools/ground_truth.py --metre-windows`: **76% of 32-beat windows right**, and it makes songs with a *steady* metre wrong - 80% on one, 72% on another, 0% on the 6/8 - to fix the two that change. Segmenting on that would introduce more errors than it removes. The detector accumulates accent, harmonic change and kick over the whole song; over 32 beats there is far less to go on, and 6 wins ties because it is a multiple of both 2 and 3. Fewer than eight bars is already known to make a metre guesswork, and eight bars of 4/4 *is* 32 beats, so there is no window that is both long enough to decide and short enough to sit inside a section. |
| `analysis/meter.py` | `CUE_WEIGHTS` left at (0.3, 0.4, 0.3) | Leaning on the harmonic cue helps a *short* window a great deal - (0.1, 0.8, 0.1) takes windowed accuracy from 76% to 85% - and changes **nothing** for the whole song: every weighting tried scores 8 of 9 on the synthesised set and leaves the three real songs identical at 92%. Only the confidence moves, from 0.83 to 0.93, which makes it *less* informative rather than more, since the same song is still wrong. No evidence to change them. |
| *rejected octave discriminators* | Four were measured and none works: **beat support** (of the beats, how many carry an onset) saturates at 1.00 at every octave because hats fill every subdivision; **onset strength at the beats** falls monotonically with tempo, a selection effect from having fewer beats to cherry-pick; **strong-weak alternation** between consecutive beats picks the halved tempo on 5 of 6; and a **tempo-proportional tolerance** barely moves coverage (0.52 to 0.93 became 0.52 to 0.93) because the onsets are on the grid, not random, so the window width is not the mechanism. |
| `analysis/tempo.py` | `PRIOR_WIDTH_OCTAVES = 1.0` | Log-normal prior around 120 BPM. Necessary but not sufficient: see octave handling below. |
| `analysis/meter.py` | `COMPOUND_MIDBAR_RATIO = 0.90` | Measured accent medians: true 3/4 gives 0.99–1.00, true 6/8 gives 0.78–0.81. Computed on accents *only* — a chord lasting two bars makes the harmony look like six either way. |
| `analysis/report.py` | `DOMINANT_SHARE = 0.62` | A chord holding more than this much of a bar is written alone. |
| `analysis/structure.py` | `PHRASE_LENGTHS = (8, 4)` | Phrase lengths looked for, longest first. Adding 2 made charts *longer*: a two-bar phrase saves one row and costs a line break at each end. Measured on five recordings, `(8, 4)` turned 35 rows into 22 on one and never made any of them worse. |
| `chart.py` | collapsing falls back to flat | A phrase starts its own line, so a short one can cost more rows than writing it out twice. `_layout` builds both and returns the shorter, which makes "never longer" a property of the code rather than of the tuning. |
| `chordgrid.py` | the plugin's grammar is transcribed, not guessed | A bar is chords only if *all* of it matches `chord( / chord)*`; anything else is parsed as **rhythm**, so an invalid bar is drawn as something else rather than ignored. The patterns come from `src/parser/ChordGridParser.ts` at plugin 2.2.0. `tests/test_chordgrid.py` holds omacap's output to them. |
| `chordgrid.py` | uneven chord splits become note values | `C / G` is drawn as half a bar each. Where the chords do not divide the bar evenly that is wrong, so the real lengths are written instead: `C[2.] G[4]` for 3+1 in 4/4, `Am[4] D[2]` for 1+2 in 3/4. A test checks the values sum to the bar. |
| `chart.py` | frontmatter is written by hand, and quoted | omacap depends on nothing at runtime, so there is no YAML library. Every value is double-quoted and `\\` and `"` escaped: a chord `C#` written bare loses everything after the hash to a comment, and `4:30` bare is a mapping. PyYAML is a **dev** dependency only, so `tests/test_chart.py` can round-trip every awkward value through a real parser. No field name may be a YAML 1.1 boolean (`yes`, `on`, `no`...); a test holds that too. |
| `chart.py` | `ENOUGH_BARS = 8` | Below this the metre is guesswork however wide the winning margin, and the chart says so. Found by analysing a 15-second recording: it reported 7/8 at **high** confidence from 2 bars. Confidence is the margin over the runner-up and says nothing about how much evidence there was. |
| `recorder.py` | tags go in at **both** container and stream level | MP3, MP4, FLAC and WAV take `-metadata`; Ogg and Opus keep Vorbis comments on the stream and **silently ignore** the container form - ffmpeg exits 0 having written nothing. Measured on all six formats. `write_tags` therefore reads the tags back and reports what landed rather than what was asked for. |
| `recorder.py` | WAV tags are written but not widely read | They go in a RIFF INFO chunk. `cliamp` ignores them and falls back to the filename; it reads mp3, flac, m4a, opus and ogg correctly. Nothing to fix in omacap - worth knowing when advising a format. |
| `capture.py` | a take is named from MPRIS only when **one** track played | `TrackSession.only_track` returns None for none, several, or an advert. A single file named after one of two tracks would be wrong - that case is what `--split` handles. The rename happens after stopping, not before starting, so "open omacap, then press play" still gets a name. |
| `chart.py` | nothing in frontmatter varies between runs | No analysis date, no timestamp. These files live in a synced Obsidian vault, and re-analysing should not churn every one of them with a diff that says nothing. |
| *no rhythm written into the chart* | The chordgrid plugin notates rhythm, and filling it in from the bar profile looks like the obvious next step. Measured over twenty sections of six recordings, **18 give one of exactly two patterns** - all eighths or all quarters - which the Feel row already says in words. The other two do not survive a threshold sweep; one gives five different patterns between 0.20 and 0.45. The reason is what the profile is made of: onset strength over the **whole mix**, so a drummer on hi-hats puts energy on every eighth whatever the guitar strums. A strum pattern with rests cannot be recovered because the drums fill the rests. `tools/syncopation_probe.py --patterns` shows it. |
| `analysis/rhythm.py` | `SYNCOPATED = 0.30`, no floor in the measure | Syncopation is scored as how far a weak position stands **above** the stronger one after it, so a quiet off-beat contributes nothing and no threshold decides what counts as played. A fixed floor was tried first and dense material cleared it at every position. Anchored on written-out patterns: four-on-the-floor and straight eighths both 0.00 (right - their off-beats are followed by strong positions that *are* played), displaced patterns 0.37-0.62. Every real recording sits at 0.00-0.10, so 0.30 comes from the patterns, not from the recordings. |
| `analysis/report.py` | syncopation is measured per section | Averaged over a song a syncopated chorus and a straight verse cancel out. One recording here reads 0.001 whole-song and 0.283 for its one repeated section. `MIN_SECTION_BARS = 4` because a shorter section has too few bars to average a bar profile over. |
| *bar-relative measurements must use trimmed audio* | `analyse_file` trims leading silence and every bar time is measured from the **trimmed** buffer. A tool that loads the file with `load_audio` alone and uses `analysis.bars` is out by the leading trim - 0.35 s on MEDS, about three sixteenths, and 1.39 s on the worst file here. Small, but enough to move a bar profile by a whole subdivision, which is what it did. `Analysis.audio_start` now reports it. This produced two wrong conclusions before it was found: that the bar onset profile was unusable, and that the bass tracker was 5-8 points worse than it is. Always `trim_silence(load_audio(path))`. |
| *no multi-band onset* | Splitting onset into kick, snare and hat bands was measured against the one problem it was proposed for - dense material where every metrical position clears the floor - and does not help: all three bands stay full. It does confirm that the kick band alone (40-120 Hz) puts the peak on the downbeat where the full band peaks on the backbeat, which is the band `meter.py` already uses. No new code earned its place. |
| *no bass line* | Recovering it is nearly within reach and was measured, not guessed. The bass is monophonic, so a time-domain method sidesteps the resolution limit that stops the chroma path down there: autocorrelation on a low-passed signal with a **93 ms** window gets 12 of 13 written notes exactly, including E1 at 41 Hz, with no bias and no octave errors, and holds 100% down to 125 ms notes. Window length is the whole game - 186 ms drops to 38% on 250 ms notes, 46 ms falls apart. What is not settled is whether it is the bass being followed: agreement with the chord root is 78/73/58% on three studio mixes but 26/35% on two other recordings, with errors smeared across every interval rather than one fixable mode. `tools/bass_probe.py` has it all. |
| *no syncopation figure* | The Longuet-Higgins & Lee score works: on synthesised patterns it gives 0.00 for four-on-the-floor **and** for straight eighths (correctly - every off-beat there is followed by a played stronger position) and 1.08-1.57 for genuinely displaced ones. The bar-level onset profile it needs does not: on MEDS it peaks 1.75 beats into the bar rather than on the downbeat, and on two of five recordings every position clears the floor because dense material flattens the profile. `tools/syncopation_probe.py` holds the measurements and what it would take. |
| `chart.py` | sections are letters, never "verse" or "chorus" | Naming them was tried and rejected. Loudness picks the wrong section: on MEDS the outro is -11.1 dB against the real chorus at -12.3. Recurrence gets MEDS right but has nothing to work with on The Last Song (every section occurs once) and on So Gung Ho picks a one-chord vamp that recurs 5 times. One of three, with a confident error. |
| `chordgrid.py` | every block opens `show% measure-num count` | Asked for by the user. `measure-num` also takes a start number, which the plugin reads with `/measure-num(?::\s*(\d+)(?:[,\-](\d+))?)?/i`, so a section is written `measure-num: 31` - otherwise every block numbers from 1 and contradicts the heading above it. A test holds the spelling against that pattern. |
| `chart.py` | chordgrid repeat count is `:||x3` | The plugin parses it with `/^(:?\|\|)x(\d+)/` - anchored, lowercase `x`, no space. Any other spelling is dropped silently and the phrase renders as if played once, which is why `tests/test_chart.py` pins the regex. The plugin has no notation for a section label, so the A/B letters stay in the form line above the block. |
| `chart.py` | short rows padded with space, not empty cells | A phrase boundary leaves a row holding fewer than four bars. Padding it with `\|` cells would read as bars that are not there. |

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
- **What you hear is not what you record.** On a hardware output the sink's
  volume is applied in the device, after the monitor is tapped, so a recording can
  clip while the speakers sound quiet. A null sink behaves the opposite way, which
  makes it a misleading thing to test clipping against. This is why the clipping
  advice names the *application's* stream volume, not the sink's.
- **`mpris:trackid` is not an identity.** Chromium publishes one id for a whole
  browser session and changes only the title, so keying track changes on the id
  saw a browser playlist as a single endless track. A track is identified by
  `(trackid, title, artist)`. Spotify's desktop client does move the id, which is
  why this went unnoticed until the same playlist was played in a browser.

## About the real-chart ground truth

The band's own charts are the best ground truth available, but they are what the
band *plays*, not a transcription of the take: a chart may write `D` where the
recording has `D/B`, simplify a passing chord, or predate an arrangement change.
So the agreement figures below are a **lower bound**, and disagreement is not
always omacap being wrong.

That matters for how to use them. They are sound for judging whether a change
helps or hurts, since the same bias applies before and after. They are not sound
as a target to maximise: a change that raises agreement without a reason grounded
in how the audio or the maths works is more likely to be fitting the charts'
quirks than hearing the music better.

## Ideas that were tried and did not help

**Letting the user pin a key they already know.** The thought was that someone who
knows their own song could correct the key and have the chords improve with it.
Measured: forcing the right key changes the chords not at all. A key and its
relative share their diatonic set, so the prior the second pass uses is the same
either way, and where the key was already right there was nothing to fix. The
override would only change a label.

**A bass chroma to settle the relative-key question.** The idea was that the
lowest register would say which of a relative pair is home. It does not survive
contact with the transform: on a recording in E minor the loudest bass pitch
classes came back as D 19%, C 16%, **C# 14%**, D# 11%, and neither C# nor D# is in
the song. They are the smear between the real notes, because the bass register
cannot resolve semitones. Anything built on a bass chroma from this STFT is built
on that.

**Falling back to 4/4 when the metre was a close call.** The idea was that a metre
chosen on a thin margin should defer to the commonest one. Measuring the margin as
best-over-runner-up ratio killed it: correct synthetic 3/4 scores 1.13 and correct
6/8 scores 1.78, while the real recording whose 7/8 looks wrong scores 1.68. No
threshold separates them, so the rule would trade working 3/4 and 6/8 detection for
a 4/4 bias. The metre is instead reported as a close call, which cannot make the
detection worse.

## Chroma ideas that were tried and did not help

Measured against a real band recording with the band's own chord chart as ground
truth, so these are not guesses. None of them is worth trying again without a
better reason than "it is what the literature does":

- **Percussive suppression** (a median filter over time on the semitone bands, to
  keep what is sustained): no change at all. The drums were not what was confusing
  it.
- **Harmonic suppression** (subtracting a shifted copy of the spectrum, since a
  note's third harmonic is a fifth above it): actively worse, 91% down to 85%. It
  removes the real fifths of real chords along with the imaginary ones.
- **Spectral whitening**: mixed. Slightly worse agreement, slightly longer runs.

What did work was narrowing where the evidence comes from, and using the key.

## Measured accuracy

From the test suite, against synthesised material with known ground truth:

- Tempo: exact on 12/12 click tracks, 60–200 BPM; within 3% with 12 ms jitter.
- Time signature: 11/12. The miss is fast 3/4 (~160 BPM), which correctly reports
  low confidence.
- Chords: 93% per bar; 100% on the standard progressions even with heavy noise.
- Key: 8/8 on resolving progressions; 5/6 on deliberately ambiguous loops, versus
  2/6 for chroma alone without the chord evidence.

### Against real charts

The development machine has three band recordings for which the band wrote their
own chord charts, which makes a small but real ground truth. `tools/score_against_charts.py`
scores against them; the charts are private, so the tool takes a JSON file
pointing at them rather than carrying any of it in the repo.

Where it stands, as the share of bars carrying a chord the chart uses:

| song | agreement | longest correct loop |
| --- | --- | --- |
| a 4:33 mix in E minor | 94% | 24 bars |
| a 3:44 mix in G major | 95% | 8 bars |
| a 4:44 mix in D major | 86% | 45 bars |

92% on average. What each change bought, measured the same way:

| change | mean agreement |
| --- | --- |
| before any of it | 84.3% |
| resolution taper, key-informed second pass | 90.3% |
| holding a chord unless the harmony moved | **92.0%** |

Time signature was right for all three, and the tempo plausible. The key was right
for two; the third is called G major where the band calls it E minor - the relative
major, with identical diatonic chords - and it names both rather than claiming one.

Two of the five recordings have no chart, so they are scored only on how settled the
output is: two-chord bars fell from 62% and 45% to 32% and 29%, and distinct chords
from 17 and 11 to 15 and 11.

The weakest of the three is nearly one chord throughout, and most of its
disagreement is in an intro the chart marks as rests. That is worth knowing before
chasing the number: a low score is not always a wrong chord.
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
    rhythm.py     subdivision profile; straight, swung or shuffled feel
    structure.py  repeated phrases, so a chart writes a verse once
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
