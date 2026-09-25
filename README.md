# omacap

Record whatever your computer is playing, straight to an audio file.

`omacap` taps the *monitor* of your sound output — the same mix that reaches your
speakers — so it captures any application: a browser tab, a DAW, a video call, a
music player. No cables, no microphone, no re-routing.

It gives you a small terminal interface where **space** starts and stops the
recording, and a plain CLI for scripts.

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
│                                                                      │
├ saved this session ──────────────────────────────────────────────────┤
│  omacap_2026-09-25_08-52-01.mp3   00:42   1.1 MB                     │
├──────────────────────────────────────────────────────────────────────┤
│  space stop · f format · b bitrate · d source                        │
│  n name · ? help · q quit                                            │
└──────────────────────────────────────────────────────────────────────┘
```

## Requirements

| What | Why | Notes |
| --- | --- | --- |
| Linux with PipeWire or PulseAudio | provides the monitor source that carries playback audio | PipeWire's PulseAudio layer works as-is |
| `ffmpeg` | captures and encodes the audio | must be on your `PATH` |
| `pactl` | lists the available sources | ships with `libpulse` / `pulseaudio-utils` |
| Python 3.10+ | runs omacap | no third-party Python packages needed |

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

## Install

```bash
git clone https://github.com/mattische/omacap.git
cd omacap
python3 -m venv .venv
source .venv/bin/activate
pip install .
```

That puts an `omacap` command on your `PATH` (inside the virtualenv). To get it
everywhere without activating anything, use [pipx](https://pipx.pypa.io):

```bash
pipx install /path/to/omacap
```

Check that everything is in place:

```bash
omacap doctor
```

```
omacap 0.1.0

[ok] ffmpeg    /usr/bin/ffmpeg
[ok] pactl     /usr/bin/pactl
[ok] monitors  4 playback monitor(s) found
[ok] default   alsa_output.usb-Generic_USB_Audio-00.analog-stereo.monitor
[ok] folder    /home/you/Recordings/omacap is writable

Everything looks good.
```

## Usage

### Interactive

```bash
omacap
```

Start whatever you want to record, press **space**, press **space** again when
you are done. The file is written to `~/Recordings/omacap`.

| Key | Action |
| --- | --- |
| `space` or `r` | start / stop recording |
| `s` | stop recording |
| `f` / `F` | next / previous output format |
| `b` | cycle the bitrate (lossy formats only) |
| `d` | cycle the capture source |
| `n` | name the next recording |
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
```

`omacap record` stops cleanly on `Ctrl-C` (or `SIGTERM`) and always finalises the
file, so containers such as `.m4a` stay playable.

### Other commands

```bash
omacap devices    # list capture sources, with the default marked *
omacap formats    # list output formats
omacap doctor     # check the installation
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

Set `OMACAP_OUTPUT_DIR` to change the default folder permanently.

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

The test suite needs no sound card: a stub `ffmpeg` on `PATH` stands in for the
real one, so process handling, progress parsing and shutdown behaviour are all
exercised for real while staying reproducible.

## Roadmap

Step 2 will analyse recorded music and write out a chord chart — time signature,
key, tempo, bar count and the chords per bar — as `.txt` or Markdown.

## License

MIT. See [LICENSE](LICENSE).
