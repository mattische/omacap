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

## Install

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

## Usage

### Interactive

```bash
omacap
```

Start whatever you want to record, press **space**, press **space** again when
you are done. The file is written to `~/Recordings/omacap`.

As soon as a take is saved, omacap asks whether to analyse it. Press **y** to get
a chord chart straight away, **n** to skip, or just carry on — any other key
dismisses the question and does what you pressed it for. You can always analyse
later with **a**.

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
omacap devices    # list capture sources, with the default marked *
omacap formats    # list output formats
omacap doctor     # check the installation
omacap update     # update to the latest version
```

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

## Chord charts

Point `omacap analyze` at any recording and it writes a chord chart next to it:

```bash
omacap analyze ~/Recordings/omacap/omacap_2026-09-25_08-52-01.mp3
```

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
| `-o, --output` | chart file to write (default: beside the recording) |
| `-t, --chart-format` | `md` or `txt` (default: `md`, or taken from `--output`) |
| `-c, --chords` | vocabulary: `simple`, `standard` (default) or `full` |
| `--bars-per-line` | bars per line in the grid (default: 4) |
| `-p, --print` | print the chart instead of writing a file |

The chord vocabulary is the setting worth knowing about:

| Value | Chords written | Good for |
| --- | --- | --- |
| `simple` | major and minor triads only | the most readable chart; what most people want |
| `standard` | adds sevenths (`7`, `m7`) | pop and rock with a bit more colour |
| `full` | adds `maj7`, `sus4`, `dim` | jazz, or when you want every detail |

A narrower vocabulary means fewer chords to second-guess. `simple` often turns a
busy chart into an obvious four-bar loop.

### What it can and cannot do

Detected: **time signature** (4/4, 3/4, 6/8, 5/4, 7/8), **key** (all 24 major and
minor keys, with the key signature), **tempo** in BPM, the **number of bars**, and
the **chords in each bar**.

Every chart carries a confidence line — `key high, tempo high, time signature
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

### How the analysis works

No machine learning and no scientific stack — just numpy and ffmpeg:

1. **Decode** to mono 22 kHz through ffmpeg, then trim leading and trailing silence.
2. **Chromagram**: an STFT mapped onto semitone bands and folded into twelve
   pitch classes.
3. **Onset strength**: spectral flux, with a local median removed so a loud
   chorus does not drown out a quiet verse.
4. **Tempo**: autocorrelation of the onset envelope under a log-normal prior,
   then beats placed by dynamic programming, then the tempo refined by fitting a
   line through the beat times.
5. **Metre**: beats are grouped by testing each candidate against how accented
   the candidate downbeats are and how often chords change on them.
6. **Chords**: chroma averaged per beat and correlated against chord templates,
   then smoothed by a Viterbi pass that charges a fixed cost per chord change.
7. **Key**: Krumhansl-Schmuckler profiles, with the chord sequence casting the
   deciding vote between a key and its relative.

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

## Development

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pytest
```

The test suite needs no sound card and no music files. Recording is tested
against a stub `ffmpeg` on `PATH`, so process handling, progress parsing and
shutdown behaviour are exercised for real while staying reproducible. The
analysis is tested against synthesised audio with a known tempo, key, metre and
chord progression, so every claim it makes is checked against ground truth.

## License

MIT. See [LICENSE](LICENSE).
