# omacap

Record whatever your computer is playing, straight to an audio file.

`omacap` taps the *monitor* of your sound output — the same mix that reaches your
speakers — so it captures any application: a browser tab, a DAW, a video call, a
music player. No cables, no microphone, no re-routing.

It gives you a small terminal interface where **space** starts and stops the
recording, and a plain CLI for scripts. If what you recorded is music, it can
also work out the key, tempo, time signature and the chords in every bar, and
write that out as a chord chart.

```
┌ omacap ────────────────────────────────────────────────────── v0.1.0 ┐
│                                                                      │
│  ● REC                                                 02:14   3.1 MB│
│  ████████████████████████████████████████████┄┄┄┄┄┄┄┄┄┄┄┄  -12.4 dB  │
│                                                                      │
│  Source  Monitor of USB Audio Analog Stereo                          │
│  Format  mp3  ·  192k                                                │
│          Lossy, universally playable. Good default for sharing.      │
│  Folder  ~/Recordings/omacap                                         │
│  Chart   .md                                                         │
│                                                                      │
├ saved this session ──────────────────────────────────────────────────┤
│  omacap_2026-09-25_08-52-01.mp3   00:42   1.1 MB                     │
├──────────────────────────────────────────────────────────────────────┤
│  space stop · f format · b bitrate · d source                        │
│  a analyse · n name · t chart · ? help · q quit                      │
└──────────────────────────────────────────────────────────────────────┘
```

## Install

Linux with PipeWire or PulseAudio, plus `ffmpeg` and `pactl`. The installer
checks for them and tells you what is missing; see
[Requirements](#requirements) for the details.

```bash
curl -fsSL https://raw.githubusercontent.com/mattische/omacap/main/install.sh | bash
```

That clones omacap to `~/.local/share/omacap`, builds a virtualenv beside it and
links `omacap` into `~/.local/bin`. Run it again any time to update, or use
`omacap update`.

If `~/.local/bin` is not on your `PATH`, the installer says so and tells you what
to add.

Installer options, as environment variables:

| Variable | Effect |
| --- | --- |
| `OMACAP_HOME` | where to install (default `~/.local/share/omacap`) |
| `OMACAP_BIN` | where to link the command (default `~/.local/bin`) |
| `OMACAP_REF` | branch or tag to install (default `main`) |
| `OMACAP_NO_ANALYZE` | set to skip numpy and install recording only |

### By hand

```bash
git clone https://github.com/mattische/omacap.git
cd omacap
python3 -m venv .venv && source .venv/bin/activate
pip install '.[analyze]'        # drop [analyze] for recording only
```

A manual install still updates itself with `omacap update`, as long as it was
installed from a git clone.

### Updating

```bash
omacap update           # fetch and install the latest
omacap update --check   # just say whether anything is waiting
```

omacap checks for updates at most once a day, in the background, and never
delays startup: a notice appears the next time you run it. The check only talks
to this project's git remote. Turn it off entirely with:

```bash
export OMACAP_NO_UPDATE_CHECK=1
```

Updating refuses to run if the checkout has uncommitted changes, so a clone you
have been editing is never overwritten.

### Check it works

```bash
omacap doctor
```

```
omacap 0.1.0

[ok] ffmpeg    /usr/bin/ffmpeg
[ok] formats   all 6 formats available
[ok] meter     live level meter available
[ok] pactl     /usr/bin/pactl
[ok] monitors  4 playback monitor(s) found
[ok] default   alsa_output.usb-Generic_USB_Audio-00.analog-stereo.monitor
[ok] folder    /home/you/Recordings/omacap is writable
[ok] install   managed at /home/you/.local/share/omacap/src (4c7c930)
[ok] analysis  numpy 2.5.3 - chord charts available

Everything looks good.
```

### Already installed it once?

`omacap doctor` says which copy your shell would actually run, and warns if a
second one is shadowing it:

```
[ok] install   managed at /home/you/.local/share/omacap/src (eb1431a)
[ok] command   /home/you/.local/bin/omacap
```

If you installed earlier by cloning by hand, that clone already updates itself
with `omacap update` — there is nothing to redo. Reinstall with the script only
if you want the managed layout and the `~/.local/bin/omacap` command; then remove
the old copy so two do not compete:

```bash
rm -rf /path/to/the/old/clone          # or just its .venv
hash -r                                # let the shell forget the old path
omacap doctor
```

## Requirements

| What | Why | Notes |
| --- | --- | --- |
| Linux with PipeWire or PulseAudio | provides the monitor source that carries playback audio | PipeWire's PulseAudio layer works as-is |
| `ffmpeg` | captures and encodes the audio | must be on your `PATH` |
| `pactl` | lists the available sources | ships with `libpulse` / `pulseaudio-utils` |
| Python 3.10+ | runs omacap | recording needs no third-party packages |
| numpy *(optional)* | chord, key and tempo detection | only for `omacap analyze`; `pip install 'omacap[analyze]'` |

Install the system packages:

```bash
# Arch / Omarchy
sudo pacman -S ffmpeg libpulse python

# Debian / Ubuntu
sudo apt install ffmpeg pulseaudio-utils python3 python3-venv

# Fedora
sudo dnf install ffmpeg pulseaudio-utils python3
```

macOS and Windows are not supported: both lack the PulseAudio monitor source
that omacap records from.

### On Omarchy

Nothing to install. `ffmpeg` comes in as a dependency of `omacut` and `mpv`, and
`pactl` comes in with `pipewire-pulse` via `libpulse`, so a stock Omarchy machine
already has everything. Run `omacap doctor` to confirm.

### Moving between machines

omacap looks its environment up at runtime rather than assuming anything: the
audio server socket (falling back to `/run/user/$UID` when `XDG_RUNTIME_DIR` is
unset), the current default sink and its monitor, the output folder, and which
encoders and filters the local ffmpeg actually supports.

A slimmer ffmpeg build is the one thing that changes behaviour, and it degrades
rather than breaking:

- a **missing encoder** (no `libmp3lame`, no `libopus`) makes that one format
  unavailable. `omacap formats` marks it, `omacap doctor` lists it, and trying to
  use it fails immediately with the encoder's name rather than an ffmpeg error.
  The TUI's `f` key skips it.
- an **ffmpeg too old for the meter filter** costs you the live level meter and
  nothing else. Recording carries on as normal and `doctor` says so.

If a check ever cannot be answered, omacap assumes the feature works rather than
refusing to record.

## Usage

### Interactive

```bash
omacap
```

Start whatever you want to record, press **space**, press **space** again when
you are done. The file is written to `~/Recordings/omacap`.

As soon as a take is saved, omacap asks what to do with it. If the media player
reported more than one track while you were recording, it offers to split the
take into one file per track first; then it offers a chord chart — for each piece
if you split, for the whole recording if you did not. Press **y** to accept, **n**
to skip, or just carry on: any other key dismisses the question and does what you
pressed it for. **a** analyses at any time.

The panel shows what the player is playing, and how many tracks have gone by, so
you can see it working while it records.

To have a long recording stop by itself when the music ends:

```bash
omacap --stop-after-silence 30
```

It only arms once something has actually been recorded, so opening omacap before
pressing play will not stop it straight away.

| Key | Action |
| --- | --- |
| `space` or `r` | start / stop recording |
| `s` | stop recording |
| `f` / `F` | next / previous output format |
| `b` | cycle the bitrate (lossy formats only) |
| `d` | cycle the capture source |
| `n` | name the next recording |
| `a` | analyse the last take into a chord chart |
| `y` / `n` | answer the question asked after a recording |
| `t` | chart format: markdown or plain text |
| `?` or `h` | show the key list |
| `q` | quit |

Settings are locked while a recording runs, so you cannot change the format
half-way through a take.

The bar under the timer is a peak meter in dBFS. If it stays at `silent` while
audio is playing, you are recording the wrong source — press `d` to cycle.

If the level reaches the top of the scale, omacap says so — while recording, and
again when the file is saved:

```
⚠ clipping  turn the application's own volume down, not the speakers
```

That wording is deliberate. On a hardware output the volume is applied in the
device, *after* omacap taps the monitor, so turning your speakers down makes it
quieter to listen to and does not change the recording at all. The application's
own stream is the one that reaches the file:

```bash
pactl list short sink-inputs
pactl set-sink-input-volume <id> 80%
```

`omacap record` prints the peak level of every take for the same reason.

### Non-interactive

```bash
omacap record                      # record until Ctrl-C
omacap record -d 30                # record 30 seconds, then stop
omacap record -f wav -n "take 3"   # WAV, named "take 3.wav"
omacap record -o ~/mix.flac        # exact path; the extension picks the format
omacap record -d 10 -q             # print only the resulting path
omacap record -d 60 --analyze      # record, then chart it straight away
```

`--analyze` (or `-A`) runs the analysis as soon as recording stops and writes the
chart next to the recording. It takes the same `--chords`, `--chart-format` and
`--bars-per-line` options as `omacap analyze`:

```bash
omacap record -d 60 -A -c simple -t txt
```

```
saved   /home/you/Recordings/omacap/omacap_2026-09-25_10-17-02.wav (1:00, 11.0 MB)
analysing…
key     C major (no sharps or flats)
tempo   120 BPM
metre   4/4
bars    30
chart   /home/you/Recordings/omacap/omacap_2026-09-25_10-17-02.txt
```

If the analysis fails the recording is still saved; only the chart is lost.

`omacap record` stops cleanly on `Ctrl-C` (or `SIGTERM`) and always finalises the
file, so containers such as `.m4a` stay playable.

### Other commands

```bash
omacap devices     # list capture sources, with the default marked *
omacap formats     # list output formats
omacap doctor      # check the installation
omacap update      # update to the latest version
```

### What is playing

`omacap nowplaying` shows what omacap can read from your media player — which is
what it uses to name the pieces of a split recording:

```bash
omacap nowplaying
```

```
player   org.mpris.MediaPlayer2.spotify
status   Playing
artist   trampe|strandberg
title    Jag vill vara (en del av din morgondag)
album    det är din stund på jorden
position 118 s of 227 s (109 s remaining)
trackid  /com/spotify/track/5vSEGQVLbUtOUaQCNQ9WcX
filename 01 - trampe_strandberg - Jag vill vara (en del av din morgondag)
```

`--watch` keeps running and prints a line each time the track changes, which is
the quickest way to tell whether your player will give named files:

```bash
omacap nowplaying --watch
```

`--player NAME` picks one when several are running; the command lists the exact
names if the one you asked for is not there.

### Options

| Option | Meaning |
| --- | --- |
| `-f, --format` | `wav`, `flac`, `mp3`, `m4a`, `opus`, `ogg` (default `mp3`) |
| `-s, --source` | capture source or sink name (default: the current output's monitor) |
| `-D, --dir` | folder for recordings (default `~/Recordings/omacap`) |
| `-b, --bitrate` | bitrate for lossy formats, e.g. `192k` |
| `-o, --output` | *(record)* exact output file |
| `-d, --duration` | *(record)* stop after this many seconds |
| `-n, --name` | *(record)* basename for the generated filename |
| `-q, --quiet` | *(record)* print only the saved path |
| `-A, --analyze` | *(record)* chart the recording as soon as it stops |

Set `OMACAP_OUTPUT_DIR` to change the default folder permanently.

## Splitting a recording into tracks

Recording a playlist? `--split` cuts it into one file per track as soon as it
stops, and takes the names from whatever the media player says it is playing:

```bash
omacap record --split
```

```
source  Monitor of Apple Audio Device Internal Speakers
format  wav
player  org.mpris.MediaPlayer2.spotify
saved   /home/you/Recordings/omacap/omacap_2026-09-25_15-49-58.wav (1:10, 12.8 MB)

split into 2 piece(s), named from the player:
  01 - trampe_strandberg - Det är din stund på jorden.wav
  02 - trampe_strandberg - Mitt liv, min tid (Albert Carlsons memoarer).wav

omacap_2026-09-25_15-49-58.wav is untouched.
```

It reads the player over MPRIS, which every desktop media player publishes, so
`omacap nowplaying` will tell you whether yours is visible. With no player
running the pieces are still cut on the silences, just numbered rather than named.
Add `--analyze` and each piece gets its own chord chart.

This is not Spotify-specific. Anything that publishes MPRIS works — mpv, VLC,
Rhythmbox, Strawberry, Elisa — and so do **Firefox and Chromium**, which publish
whatever is playing in a tab. Spotify, YouTube, Bandcamp or SoundCloud in a
browser all name their tracks. Where several players are running, Spotify is
preferred; `--player` picks a different one and `omacap nowplaying` lists the
exact names.

**A track change decides whether to cut; a silence decides where.** That matters:
pausing or seeking inside a track makes silence but does not change the track, so
neither splits the file. A track change with no silence around it — crossfade,
gapless — still splits, just without a gap to aim at.

### Splitting a recording you already have

Cut an existing file where the music stopped:

```bash
omacap split ~/Recordings/omacap/omacap_2026-09-25_08-52-01.wav
```

```
source   /home/you/Recordings/omacap/omacap_2026-09-25_08-52-01.wav
length   23:41
silences 6 found at or above 0.8s

6 piece(s):
  01    03:47  track 01
  02    03:54  track 02
  ...

written to /home/you/Recordings/omacap
```

The original is never touched. `--dry-run` shows the plan without writing
anything, and `--analyze` writes a chord chart for each piece.

| Option | Meaning |
| --- | --- |
| `-D, --dir` | where to write the pieces (default: beside the recording) |
| `--min-gap` | silence this long counts as a track boundary (default 0.8 s) |
| `--min-track` | anything shorter is not kept (default 20 s) |
| `--pad` | keep this much either side of a cut (default 0.25 s) |
| `--threshold` | level below which audio counts as silence (default −60 dB) |
| `-n, --dry-run` | show what would be written, and write nothing |
| `-A, --analyze` | chart each piece |

Cuts are made with a stream copy, so nothing is re-encoded and a lossy
recording does not lose a second generation. The cut lands inside the silence,
where a few tens of milliseconds either way cannot be heard.

**What it can and cannot tell.** Splitting on silence only works when the player
leaves a gap. Measured against Spotify with crossfade off, the gap between tracks
was 2.45 s — plenty. But crossfade, gapless albums and Automix each remove the gap
by design, and a long silence *inside* a piece will split it. Raise `--min-gap` if
a track is being cut in half; lower it if a boundary is being missed.

## Chord charts

Point `omacap analyze` at any recording and it writes a chord chart next to it:

```bash
omacap analyze ~/Recordings/omacap/omacap_2026-09-25_08-52-01.mp3
```

It takes as many files as you like, and a directory means every audio file in it:

```bash
omacap analyze take1.wav take2.wav
omacap analyze ~/Recordings/omacap          # the whole folder
omacap analyze ~/Music/session/*.flac -c simple
```

Each chart is written beside its recording. One file that cannot be analysed does
not stop the rest; the run only fails if none of them could be.

```
key     G major (1 sharp)
tempo   124 BPM
metre   4/4
bars    139
chart   /home/you/Recordings/omacap/omacap_2026-09-25_08-52-01.md
```

The chart itself:

````markdown
# omacap_2026-09-25_08-52-01

| | |
| --- | --- |
| **Key** | G major (1 sharp) |
| **Tempo** | 124 BPM |
| **Time signature** | 4/4 |
| **Bars** | 139 |
| **Length** | 4:30 |
| **Chords used** | D, C, Em, D7 |
| **Confidence** | key high, tempo high, time signature high |

## Chart

Bars read left to right, 4 per line.

```
 1 | D      | C      | Em     | D      |
 5 | D      | C      | Em     | D      |
 9 | D      | C      | C Em   | D      |
```
````

In the interactive interface, press **a** after a take to do the same thing, and
**t** to switch between `.md` and `.txt`.

### Analysis options

| Option | Meaning |
| --- | --- |
| `-o, --output` | chart file to write; only for a single input (default: beside each recording) |
| `-t, --chart-format` | `md`, `txt` or `chordgrid` (default: `md`, or taken from `--output`) |
| `-c, --chords` | vocabulary: `simple` (default), `standard` or `full` |
| `--bars-per-line` | bars per line in the grid (default: 4) |
| `--no-collapse` | write every bar out instead of collapsing repeated phrases |
| `--no-sections` | one chordgrid block for the whole song, not one per section |
| `-p, --print` | print the chart instead of writing a file |

The chord vocabulary is the setting worth knowing about:

| Value | Chords written | Good for |
| --- | --- | --- |
| `simple` | major and minor triads only | **the default**, and the most accurate |
| `standard` | adds sevenths (`7`, `m7`) | pop and rock with a bit more colour |
| `full` | adds `maj7`, `sus4`, `dim` | jazz, or when you want every detail |

A narrower vocabulary means fewer chords to second-guess, and it is not only a
question of taste. Measured against three recordings whose players wrote their
own charts, `simple` agrees 92% of the time, `standard` 85% and `full` 67% - and
the colourings break up the repeats too, taking the longest loop the analysis
reproduces from 45 bars down to 6. A root chord you can play beats a seventh
that might be wrong.

### Bars worth a second listen

A `?` marks a bar the audio matched less well than the rest of the song:

```
 9 | D      | C      | Em?    | D      |
```

It is not decoration. Against three recordings whose players wrote their own
charts, the bars marked this way are about a seventh of the total and contain over
half of the places the analysis disagreed with the chart — four times what picking
bars at random would manage. If you are going to check the chart against your ears,
these are the bars to start with.

### Repeated phrases are written once

Songs repeat, and a chart that writes a verse out three times is three times as
long as it needs to be. omacap looks for phrases of eight or four bars played
more than once in a row and writes them once, with a letter and the number of
times to play them:

```
Form: A×3 B×2 C×2 C×2 B×2 A×2 B×2 D×3.

  6 | C       | Em      | D       | D       |
    | C       | Em      | D       | D       |  A×3
 30 | D?      |
 31 | Em      | C       | Am      | C       |
    | D       | D       | D       | Am      |  B×2
```

On one 139-bar recording that turns 35 rows into 22, and the `B` it finds is the
chorus its players wrote down. In a `chordgrid` chart the same phrases come out bracketed by repeat marks with
the count on the closing one, `||: ... :||x3`, which is both how a chart says it
and the syntax the plugin parses. The letters stay in the form line, since the
plugin has no notation for a section label.

A phrase starts a new line, so a short one can cost more rows than writing it out
twice would. When that happens omacap writes it out — the chart never comes back
longer than it was. `--no-collapse` turns it off entirely, and the form line then
goes away with it.

### Writing into Obsidian

`--chart-format chordgrid` writes the chart as `chordgrid` blocks, which Obsidian
renders as a chart rather than as text:

````bash
omacap analyze take.flac -t chordgrid
````

````markdown
**A** · bars 6–30 · played 3 times

```chordgrid
4/4

||: C | Em | D | D |
| C | Em | D | D :||x3
| D |
```
````

The file is still Markdown, with the same summary above it. `grid` and `obsidian`
are accepted as names for it too.

omacap writes what the plugin actually parses, which is stricter than it looks.
A bar is read as chords only if *all* of it matches the plugin's chord grammar,
and as **rhythm** otherwise - so an invalid bar is not ignored, it is drawn as
something else entirely. Three consequences:

| Where a plain chart writes | a chordgrid gets | because |
| --- | --- | --- |
| `Em?` | `Em` | `?` is not in the grammar. The uncertain bars are named in the text above the grids instead. |
| `N.C.` | `-1` | there is no "no chord" notation, so the bar is written as the rest it is. |
| `C G` | `C / G` | two chords in a bar are separated by a slash *with* spaces. Without them, `C/G` means C with G in the bass. |

The slash form draws two chords as half a bar each, which is a lie if the chord
changed on the last beat. Where a bar's chords do not divide it evenly, their
real lengths are written as note values - `C[2.] G[4]` is three beats then one -
and that is checked against the metre, so `Am[4] D[2]` is what the same split
looks like in 3/4.

### Sections

Each part of the song gets its own grid, with a label above it in plain markdown,
because the plugin has no notation for a section label:

````markdown
**Intro** · bars 1–5
**A** · bars 6–30 · played 3 times
**Bars 56–63**
````

The letters say which parts are the same as each other. They are **not** named
"verse" and "chorus": that was tried and does not survive contact with real
songs - see `CLAUDE.md`. `--no-sections` writes one grid for the whole song.

### What it can and cannot do

Detected: **time signature** (4/4, 3/4, 6/8, 5/4, 7/8), **key** (all 24 major and
minor keys, with the key signature), **tempo** in BPM, the **feel** — whether the
beat is played straight or swung, and where the off-beat sits — the **number of
bars**, and the **chords in each bar**.

Not detected: **syncopation**. The scoring works - it reads zero for straight
eighths and high for a displaced downbeat on material written to test it - but
the bar-level onset profile it needs is not yet trustworthy on real recordings.
`tools/syncopation_probe.py` has the numbers.

Where a judgement was a close call, the chart says so on the line itself:

```
| **Key**            | G major (1 sharp) — or E minor, its relative              |
| **Time signature** | 7/8 — a close call, so the bars may be grouped wrongly    |
```

A key and its relative minor share every note, so when the evidence cannot separate
them both are named rather than one being asserted. And a time signature chosen on a
thin margin is worth knowing about, because the bar grouping is built on it: if it is
wrong, every bar is, however good the chords are.

Every chart also carries a confidence line — `key high, tempo high, time signature
low` — because some of this is genuinely ambiguous:

- **Relative keys.** A minor and C major use identical notes. omacap decides
  between them from the chord sequence, which is usually right but not always.
- **3/4 against 6/8.** These are the same pulse grouped differently; only how
  strongly the middle of the bar is accented separates them.
- **2/4.** Indistinguishable from 4/4 in audio, and reported as 4/4 — which is
  how popular music writes it anyway.
- **Sevenths and suspensions.** A melody note passing over a triad looks a lot
  like an extension. Use `--chords simple` if that gets noisy.
- **Tempo doubling.** A tempo and half that tempo fit the same beats; omacap
  picks the one that accounts for more of the onsets.

Treat it as a good first draft of a chart, not a transcription. It is accurate on
material with a steady pulse and clear harmony, and vaguer on free time, solo
melody, speech or heavy distortion.

**How accurate, measured.** On synthesised material with known ground truth: tempo
exact on 12 of 12 click tracks from 60 to 200 BPM, time signature right on 11 of
12, chords 93% per bar. Against three real band recordings whose players wrote
their own chord charts: **90% of bars on average carry a chord the chart uses**
(82–95% across the three), the time signature was right for all three, and the key
for two — the third is called as the relative major, which shares every chord, and
is reported as medium confidence rather than as certain.

`tools/score_against_charts.py` does that scoring against your own material, if you
have charts to compare with.

### How the analysis works

No machine learning and no scientific stack — just numpy and ffmpeg:

1. **Decode** to mono 22 kHz through ffmpeg, then trim leading and trailing silence.
2. **Chromagram**: an STFT mapped onto semitone bands and folded into twelve
   pitch classes. Each band is weighted by how well the transform can resolve it —
   at C2 there are only 1.4 FFT bins across a semitone, so the bottom of the range
   cannot separate neighbouring notes and is believed proportionally less.
3. **Onset strength**: spectral flux, with a local median removed so a loud
   chorus does not drown out a quiet verse.
4. **Tempo**: autocorrelation of the onset envelope under a log-normal prior,
   then beats placed by dynamic programming, then the tempo refined by fitting a
   line through the beat times.
5. **Metre**: beats are grouped by testing each candidate against how accented the
   candidate downbeats are, how often chords change on them, and how strong the
   40–120 Hz band is — the kick drum, which is what says "one" on a dense mix
   where compression has evened the accents out.
6. **Chords**: chroma averaged per beat and correlated against chord templates,
   then smoothed by a Viterbi pass that charges a fixed cost per chord change.
7. **Key**: Krumhansl-Schmuckler profiles, with the chord sequence casting the
   deciding vote between a key and its relative.
8. **Chords again**: now that the key is known, the chords are recognised a second
   time with the diatonic ones favoured slightly, which settles the close calls.
9. **Feel**: the onset strength is averaged over every beat, which says where the
   off-beat falls. Half way is straight; two thirds is triplet swing. Measured
   against patterns played on purpose, that position is recovered to within 0.01.

## Formats

| Format | Extension | Kind | Use it for |
| --- | --- | --- | --- |
| `wav` | `.wav` | lossless, uncompressed | editing, maximum compatibility |
| `flac` | `.flac` | lossless, compressed | archiving — about half the size of WAV |
| `mp3` | `.mp3` | lossy, 192k default | sharing with anyone, anywhere |
| `m4a` | `.m4a` | lossy AAC, 192k default | Apple devices; this is "mp4 audio" |
| `opus` | `.opus` | lossy, 128k default | best quality per byte, modern players |
| `ogg` | `.ogg` | lossy Vorbis, 192k default | fully open formats |

`mp4` is accepted as an alias for `m4a`. There is no video in a screen recording
of audio, so an audio-only MP4 is correctly named `.m4a`.

Recording a lossy source (Spotify, YouTube) to WAV or FLAC does not restore the
quality that was already lost — but it does avoid adding a second round of
compression, which matters if you plan to edit afterwards.

## How it works

PulseAudio and PipeWire expose a **monitor source** for every output device. It
carries the final mixed signal that goes to the speakers. omacap picks the
monitor of your current default output and hands it to ffmpeg:

```bash
ffmpeg -f pulse -i <sink>.monitor -c:a libmp3lame -b:a 192k out.mp3
```

Live duration, file size and peak level are read back from ffmpeg while it runs.
Stopping sends `SIGINT`, which makes ffmpeg flush its encoder and write the
container trailer instead of leaving a truncated file.

Splitting works on one continuous recording rather than switching files mid-take,
which would risk losing audio at every boundary. While recording, omacap collects
two things: the gaps, reported by ffmpeg on the same channel as the peak level,
and the track changes, read from the media player once a second. A **track change
decides whether to cut; a gap decides where**. Pausing or seeking inside a track
makes a gap without changing the track, so neither splits the file; a track change
with no gap around it — crossfade, gapless — still splits, just without a gap to
aim at. The cutting happens when the recording stops, on a file already safely on
disk, with a stream copy so nothing is re-encoded.

## Troubleshooting

**"No monitor sources found" / "pactl failed"**
Your audio server is not reachable. If you are in a shell without a desktop
session, `XDG_RUNTIME_DIR` may be unset — omacap tries `/run/user/$UID`
automatically, but the audio server does need to be running.

**The meter stays at `silent`**
You are capturing a different output than the one that is playing. Press `d` to
cycle sources, or run `omacap devices` and pass the right one with `--source`.

**Recording is silent after switching headphones**
The default sink changed. Restart omacap, or press `d` to pick the new monitor.

**`ffmpeg was not found on PATH`**
Install ffmpeg with your package manager (see [Requirements](#requirements)).

**The meter says `silent` but the application is definitely playing**
An application can be routed to an output that is not your default one, and
omacap records the default. `pactl list sink-inputs` shows where each application
is going; move it, or point omacap at that output with `--source`.

**The pieces came out numbered instead of named**
omacap saw fewer than two tracks. Run `omacap nowplaying --watch` while the music
plays: if the lines do not change when the track does, the player is not
reporting it and only the gaps are available. Splitting still works, without names.

**It did not split at all, or split a track in half**
`--min-gap` is the dial. A gap has to last that long to count as a boundary, so
raise it if a quiet moment inside a piece is being treated as a track break, and
lower it if a real break is being missed. `--dry-run` shows the plan without
writing anything.

**The recording is clipping**
Lower the application's own stream volume, not the speakers — on a hardware
output the speaker volume is applied after omacap taps the monitor, so turning it
down changes nothing in the file. See [Interactive](#interactive).

## Development

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pytest
```

The test suite needs no sound card and no music files. Recording is tested
against a stub `ffmpeg` on `PATH`, so process handling, progress parsing and
shutdown behaviour are exercised for real while staying reproducible; reading the
media player is tested against a scripted `busctl`. The analysis is tested against
synthesised audio with a known tempo, key, metre and chord progression, so every
claim it makes is checked against ground truth.

For the part that cannot be faked there is a live check, which plays a generated
chord progression out of the sound card, records it back, drives the real
interface, and confirms the analysis returns what was played:

```bash
python tools/live_check.py            # makes sound
python tools/live_check.py --music song.mp3
```

## License

MIT. See [LICENSE](LICENSE).
