"""`omacap play`: the play-along page, served locally, with chords you can correct.

A page opened from disk cannot write files, so this serves it from a small HTTP
server on 127.0.0.1 instead. A correction made on the page is saved beside the
recording at once and the chart is rewritten with it, through the same
`corrections` path every other chart write goes through - so a chart edited in
Obsidian meanwhile is read back first rather than overwritten.

Only this one recording and its page are served. Writes need a token that is
only in the page, so another site open in the browser cannot post to it.
"""

from __future__ import annotations

import json
import re
import secrets
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import corrections
from .player import player_data, render_player

_RANGE = re.compile(r"bytes=(\d*)-(\d*)$")
MAX_BODY = 64 * 1024
CHUNK = 256 * 1024
AUDIO_TYPES = {".mp3": "audio/mpeg", ".m4a": "audio/mp4", ".mp4": "audio/mp4",
               ".ogg": "audio/ogg", ".opus": "audio/ogg", ".flac": "audio/flac",
               ".wav": "audio/wav"}


class Session:
    """One recording, its analysis, and the chart its corrections are written to."""

    def __init__(self, analysis, audio: Path, write_chart):
        self.raw = analysis
        self.audio = Path(audio)
        # analysis -> (analysis with corrections, what was said writing the chart)
        self.write_chart = write_chart
        self.token = secrets.token_urlsafe(16)
        self.lock = threading.Lock()
        self.analysis, self.notes = write_chart(analysis)

    def page(self) -> str:
        # Reloading the page picks up anything changed in the chart meanwhile.
        with self.lock:
            self.analysis, self.notes = self.write_chart(self.raw)
            return render_player(self.analysis, "audio", edit=self.token, notes=self.notes)

    def data(self) -> dict:
        return player_data(self.analysis, "audio", edit=self.token, notes=self.notes)

    def correct(self, number: int, text: str, same: bool) -> dict:
        """Correct one bar - and every bar written like it, when ``same``."""
        with self.lock:
            meter = self.analysis.meter
            bars = {bar.number: bar for bar in self.analysis.bars}
            if number not in bars:
                raise corrections.CorrectionError(f"there is no bar {number}")
            fixes = corrections.load(self.audio)
            targets = [bars[number]]
            if same:
                was = corrections.written_beats(bars[number], self.analysis.chords, meter)
                targets = [bar for bar in self.analysis.bars
                           if corrections.written_beats(bar, self.analysis.chords, meter) == was]
            if text.strip():
                previous = bars.get(number - 1)
                before = (corrections.written_beats(previous, self.analysis.chords, meter)
                          if previous else None)
                beats = corrections.parse_bar(text, meter, before)
                for bar in targets:
                    fixes[str(bar.number)] = corrections.correction(bar, beats)
            else:
                # An empty bar means "back to what the analysis heard".
                for bar in targets:
                    fixes.pop(str(bar.number), None)
            corrections.save(self.audio, fixes)
            self.analysis, self.notes = self.write_chart(self.raw)
            return self.data()


def make_handler(session: Session):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):   # the terminal is for the user, not for every request
            pass

        def _send(self, status, body: bytes, kind: str, extra=None):
            self.send_response(status)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            for name, value in (extra or {}).items():
                self.send_header(name, value)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _json(self, status, payload):
            self._send(status, json.dumps(payload, ensure_ascii=False).encode(),
                       "application/json; charset=utf-8")

        def do_HEAD(self):
            self.do_GET()

        def do_GET(self):
            if self.path in ("/", "/index.html"):
                self._send(HTTPStatus.OK, session.page().encode(), "text/html; charset=utf-8")
            elif self.path == "/audio":
                self._audio()
            else:
                self._send(HTTPStatus.NOT_FOUND, b"not found", "text/plain")

        def _audio(self):
            # Browsers seek in audio with Range requests; without them a click on a
            # bar would restart the song instead of jumping to it.
            path = session.audio
            size = path.stat().st_size
            kind = AUDIO_TYPES.get(path.suffix.lower(), "application/octet-stream")
            first, last = 0, size - 1
            status = HTTPStatus.OK
            wanted = _RANGE.match(self.headers.get("Range", ""))
            if wanted and (wanted.group(1) or wanted.group(2)):
                if wanted.group(1):
                    first = int(wanted.group(1))
                    last = min(int(wanted.group(2)), size - 1) if wanted.group(2) else size - 1
                else:
                    first = max(0, size - int(wanted.group(2)))
                if first > last or first >= size:
                    self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
                    self.send_header("Content-Range", f"bytes */{size}")
                    self.end_headers()
                    return
                status = HTTPStatus.PARTIAL_CONTENT
            length = last - first + 1
            self.send_response(status)
            self.send_header("Content-Type", kind)
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Length", str(length))
            if status == HTTPStatus.PARTIAL_CONTENT:
                self.send_header("Content-Range", f"bytes {first}-{last}/{size}")
            self.end_headers()
            if self.command == "HEAD":
                return
            try:
                with path.open("rb") as handle:
                    handle.seek(first)
                    while length > 0:
                        chunk = handle.read(min(CHUNK, length))
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                        length -= len(chunk)
            except (BrokenPipeError, ConnectionResetError):
                pass   # the browser moved on to another range

        def do_POST(self):
            if self.path != "/bar":
                return self._send(HTTPStatus.NOT_FOUND, b"not found", "text/plain")
            if not secrets.compare_digest(self.headers.get("X-Omacap-Token", ""), session.token):
                return self._json(HTTPStatus.FORBIDDEN, {"error": "not this page"})
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                length = -1
            if not 0 < length <= MAX_BODY:
                return self._json(HTTPStatus.BAD_REQUEST, {"error": "bad request"})
            try:
                body = json.loads(self.rfile.read(length))
                number, text = int(body["n"]), str(body.get("text", ""))
                same = bool(body.get("same", False))
            except (ValueError, KeyError, TypeError):
                return self._json(HTTPStatus.BAD_REQUEST, {"error": "bad request"})
            try:
                self._json(HTTPStatus.OK, session.correct(number, text, same))
            except corrections.CorrectionError as exc:
                self._json(HTTPStatus.UNPROCESSABLE_ENTITY, {"error": str(exc)})

    return Handler


def serve(session: Session, port: int = 0) -> ThreadingHTTPServer:
    """A server for the session on 127.0.0.1; ``port`` 0 picks a free one."""
    return ThreadingHTTPServer(("127.0.0.1", port), make_handler(session))
