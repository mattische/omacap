#!/usr/bin/env python3
"""How far does a player's MPRIS track-change signal lead or lag its audio?

This is the one measurement the per-track splitting plan depends on - see
docs/track-splitting-plan.md. A player announces a track change over D-Bus, but
the audio it has already buffered keeps coming, so the signal and the sound do
not line up. This records the monitor source with silencedetect while polling
MPRIS, then pairs each track change with the nearest silence and prints the gap.

Run it on the machine where the player actually runs, with a playlist playing
across at least one track boundary:

    python tools/measure_mpris_offset.py --duration 90

It records what is already playing and adds no sound of its own. To rehearse it
without disturbing anything, send a player to a throwaway sink and capture that
instead:

    pactl load-module module-null-sink sink_name=probe
    mpv --audio-device=pulse/probe a.mp3 b.mp3 &
    python tools/measure_mpris_offset.py --monitor probe.monitor --duration 30
    pactl unload-module module-null-sink

Measured against mpv, whose buffering is minimal, the signal arrived 50-140 ms
before the audio. A streaming client buffers far more, so expect a larger number.
"""
from __future__ import annotations

import argparse, json, os, re, subprocess, sys, threading, time
from dataclasses import dataclass, field

POLL = 0.25
SILENCE_RE = re.compile(r"silence_(start|end):\s*(-?[\d.]+)")


@dataclass
class Collected:
    started: float = 0.0
    silences: list = field(default_factory=list)   # (kind, stream_time)
    tracks: list = field(default_factory=list)     # (wall_time, trackid, title)
    stop: threading.Event = field(default_factory=threading.Event)


def find_player(preferred: str | None) -> str | None:
    out = subprocess.run(["busctl", "--user", "list", "--no-legend"],
                         capture_output=True, text=True)
    names = sorted({l.split()[0] for l in out.stdout.splitlines()
                    if l.startswith("org.mpris.MediaPlayer2.")})
    if preferred:
        return preferred if preferred in names else None
    for n in names:                       # prefer spotify when several are up
        if "spotify" in n:
            return n
    return names[0] if names else None


def poll_mpris(player: str, c: Collected) -> None:
    last = None
    while not c.stop.is_set():
        r = subprocess.run(
            ["busctl", "--user", "--json=short", "get-property", player,
             "/org/mpris/MediaPlayer2", "org.mpris.MediaPlayer2.Player", "Metadata"],
            capture_output=True, text=True)
        now = time.time()
        try:
            data = json.loads(r.stdout)["data"]
            tid = data.get("mpris:trackid", {}).get("data")
            title = data.get("xesam:title", {}).get("data")
        except Exception:
            tid = title = None
        if tid and tid != last:
            c.tracks.append((now, tid, title))
            last = tid
            print(f"  [track ] {now - c.started:7.2f}s  {title}", flush=True)
        c.stop.wait(POLL)


def record(sink_monitor: str, seconds: float, out: str, c: Collected) -> None:
    proc = subprocess.Popen(
        ["ffmpeg", "-hide_banner", "-nostdin", "-nostats", "-loglevel", "info",
         "-f", "pulse", "-i", sink_monitor,
         "-af", "silencedetect=noise=-60dB:d=0.3",
         "-ac", "2", "-ar", "48000", "-c:a", "pcm_s16le",
         "-t", str(seconds), "-y", out],
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, bufsize=1)
    c.started = time.time()
    for line in proc.stderr:
        m = SILENCE_RE.search(line)
        if m:
            kind, t = m.group(1), float(m.group(2))
            c.silences.append((kind, t))
            print(f"  [silence] {t:7.2f}s  {kind}", flush=True)
    proc.wait()
    c.stop.set()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--player")
    ap.add_argument("--duration", type=float, default=90.0)
    ap.add_argument("--out", default="/tmp/omacap-offset-probe.wav")
    ap.add_argument("--monitor", help="capture source; default is the default sink's monitor")
    args = ap.parse_args()

    os.environ.setdefault("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    player = find_player(args.player)
    if not player:
        print("no MPRIS player on the bus — start one and press play", file=sys.stderr)
        return 2
    if args.monitor:
        monitor = args.monitor
    else:
        sink = subprocess.run(["pactl", "get-default-sink"],
                              capture_output=True, text=True).stdout.strip()
        monitor = f"{sink}.monitor"
    print(f"player : {player}\nmonitor: {monitor}\nrunning {args.duration:.0f}s\n")

    c = Collected()
    t = threading.Thread(target=record, args=(monitor, args.duration, args.out, c))
    t.start()
    while c.started == 0.0:
        time.sleep(0.01)
    p = threading.Thread(target=poll_mpris, args=(player, c), daemon=True)
    p.start()
    t.join()

    print("\n=== correlation ===")
    if not c.tracks:
        print("no track changes seen — play across a track boundary")
        return 1
    # Silence events are in stream time, which starts when recording starts.
    edges = [(kind, st) for kind, st in c.silences]
    rows = 0
    for wall, tid, title in c.tracks:
        rel = wall - c.started
        near = min(edges, key=lambda e: abs(e[1] - rel), default=None)
        if near is None:
            print(f"  {title[:34]:34} at {rel:6.2f}s  no silence at all")
            continue
        delta = rel - near[1]
        flag = "" if abs(delta) < 3 else "   (too far apart to pair)"
        print(f"  {str(title)[:34]:34} track@{rel:6.2f}s  "
              f"nearest {near[0]:13} @{near[1]:6.2f}s  offset {delta:+6.2f}s{flag}")
        rows += 1
    print(f"\n  positive offset = the MPRIS signal arrives AFTER the audio boundary")
    return 0


if __name__ == "__main__":
    sys.exit(main())
