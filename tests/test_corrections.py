"""Corrections: a person's chords win, and a hand-edited chart is never lost."""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from omacap import cli, corrections
from omacap.analysis.audio import AudioBuffer
from omacap.analysis.report import analyse_buffer
from omacap.chart import render
from omacap.corrections import CorrectionError, parse_bar, read_chart, written_beats

from synth import SR, song


class Meter:
    beats_per_bar = 4
    name = "4/4"
    is_compound = False


FOUR = [(0, ""), (7, ""), (9, "m"), (5, "")]


def _render(analysis):
    return render(analysis, "chordgrid")


@pytest.fixture(scope="module")
def analysis():
    return analyse_buffer(AudioBuffer(song(FOUR, bars=16), SR), Path("Take.wav"))


# -- reading one bar ------------------------------------------------------

@pytest.mark.parametrize("text,beats", [
    ("C", ["C"] * 4),
    ("C / G", ["C", "C", "G", "G"]),
    ("D · · G", ["D", "D", "D", "G"]),
    ("D . . G", ["D", "D", "D", "G"]),
    ("D[2.] G[4]", ["D", "D", "D", "G"]),
    ("Am[4] D[2] G[4]", ["Am", "D", "D", "G"]),
    ("-1", ["N.C."] * 4),
    ("-4 C[2.]", ["N.C.", "C", "C", "C"]),
    ("N.C.", ["N.C."] * 4),
    ("C/E", ["C/E"] * 4),
])
def test_every_way_a_bar_is_written_reads_back(text, beats):
    assert parse_bar(text, Meter()) == beats


def test_percent_repeats_the_bar_before():
    assert parse_bar("%", Meter(), ["C", "C", "G", "G"]) == ["C", "C", "G", "G"]
    with pytest.raises(CorrectionError):
        parse_bar("%", Meter())


@pytest.mark.parametrize("text", ["", "H", "C G A", "D[2] G[4]", "· C · ·", "C · G"])
def test_a_bar_that_does_not_add_up_is_refused_rather_than_guessed(text):
    with pytest.raises(CorrectionError):
        parse_bar(text, Meter())


# -- reading a chart back -------------------------------------------------

def test_every_chart_omacap_writes_reads_back_bar_for_bar(analysis):
    for collapse in (True, False):
        for sections in (True, False):
            text = render(analysis, "chordgrid", collapse=collapse, sections=sections)
            signature, cells = read_chart(text)
            assert signature == "4/4"
            assert sorted(cells) == [bar.number for bar in analysis.bars]
            previous = None
            for bar in analysis.bars:
                beats = parse_bar(cells[bar.number], analysis.meter, previous)
                assert beats == written_beats(bar, analysis.chords, analysis.meter)
                previous = beats


def test_an_unclosed_repeat_is_an_error():
    with pytest.raises(CorrectionError):
        read_chart("```chordgrid\n4/4\n||: C | G |\n```\n")


# -- applying them --------------------------------------------------------

def test_a_correction_changes_its_bar_and_nothing_else(analysis):
    fixed = corrections.apply(analysis, {"3": {"beats": ["D", "D", "D", "G"],
                                              "start": analysis.bars[2].start}})
    assert fixed.bars[2].chords == ["D", "G"] and fixed.bars[2].corrected
    assert written_beats(fixed.bars[2], fixed.chords, fixed.meter) == ["D", "D", "D", "G"]
    for before, after in zip(analysis.bars, fixed.bars):
        if after.number != 3:
            assert before.chords == after.chords
    assert 3 not in fixed.uncertain_bars
    assert analysis.bars[2].chords != ["D", "G"]       # the original is untouched


def test_a_correction_follows_its_bar_when_the_numbering_moves(analysis):
    bar = analysis.bars[5]
    # Saved under the wrong number, as if a newer analysis had found a bar more.
    fixed = corrections.apply(analysis, {"5": {"beats": ["E"] * 4, "start": bar.start}})
    assert fixed.bars[5].chords == ["E"] and not fixed.bars[4].corrected


# -- the chart on disk ----------------------------------------------------

def _chart(analysis, audio, target):
    fixed, write, notes = corrections.prepare(analysis, audio, target, _render, chordgrid=True)
    if write:
        target.write_text(render(fixed, "chordgrid"), encoding="utf-8")
        corrections.remember(target)
    return fixed, write, notes


def test_a_hand_edit_in_the_chart_is_kept_through_a_new_analysis(analysis, tmp_path):
    audio, target = tmp_path / "Take.wav", tmp_path / "Take.md"
    _chart(analysis, audio, target)
    bar = analysis.bars[6]
    target.write_text(_replace_bar(target.read_text(encoding="utf-8"), bar.number, "Em"),
                      encoding="utf-8")

    fixed, write, notes = _chart(analysis, audio, target)
    assert write and notes and "kept" in notes[0]
    saved = json.loads(corrections.corrections_path(audio).read_text())
    assert any(v["beats"] == ["Em"] * 4 for v in saved["bars"].values())
    _, cells = read_chart(target.read_text(encoding="utf-8"))
    assert cells[bar.number] == "Em"


def test_a_chart_edited_beyond_reading_back_is_left_alone(analysis, tmp_path):
    audio, target = tmp_path / "Take.wav", tmp_path / "Take.md"
    _chart(analysis, audio, target)
    mangled = target.read_text(encoding="utf-8").replace(":||", "", 1)
    target.write_text(mangled, encoding="utf-8")
    _, write, notes = _chart(analysis, audio, target)
    assert not write and "left as it is" in notes[0]
    assert target.read_text(encoding="utf-8") == mangled


def test_an_unchanged_chart_is_rewritten_freely(analysis, tmp_path):
    audio, target = tmp_path / "Take.wav", tmp_path / "Take.md"
    _chart(analysis, audio, target)
    _, write, notes = _chart(analysis, audio, target)
    assert write and not notes
    assert not corrections.corrections_path(audio).exists()


def _replace_bar(text: str, number: int, chord: str) -> str:
    """Rewrite the chart with bar ``number`` changed, without collapsing repeats."""
    signature, cells = read_chart(text)
    cells[number] = chord
    head = text[: text.index("```chordgrid")]
    rows = [" | ".join(cells[n] for n in sorted(cells)[i:i + 4])
            for i in range(0, len(cells), 4)]
    return head + "```chordgrid\nshow% measure-num count\n\n4/4\n\n" + \
        "\n".join(f"| {row} |" for row in rows) + "\n```\n"


def test_analyze_twice_keeps_an_edit_made_in_between(tmp_path, analysis, monkeypatch):
    audio = tmp_path / "Take.wav"
    audio.write_bytes(b"")
    monkeypatch.setattr("omacap.cli._load_analysis", lambda source, vocabulary: analysis)
    assert cli.main(["analyze", str(audio)]) == 0
    chart = audio.with_suffix(".md")
    chart.write_text(_replace_bar(chart.read_text(encoding="utf-8"), 2, "F#m"),
                     encoding="utf-8")
    assert cli.main(["analyze", str(audio)]) == 0
    _, cells = read_chart(chart.read_text(encoding="utf-8"))
    assert cells[2] == "F#m"
    assert corrections.corrections_path(audio).is_file()


# -- the play server ------------------------------------------------------

@pytest.fixture
def served(analysis, tmp_path):
    from omacap.playserver import Session, serve

    audio = tmp_path / "Take.mp3"
    audio.write_bytes(bytes(range(256)) * 40)
    target = tmp_path / "Take.md"

    def write(a):
        fixed, ok, notes = corrections.prepare(a, audio, target, _render, chordgrid=True)
        if ok:
            target.write_text(render(fixed, "chordgrid"), encoding="utf-8")
            corrections.remember(target)
        return fixed, notes

    session = Session(analysis, audio, write)
    server = serve(session)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield session, f"http://127.0.0.1:{server.server_address[1]}", audio, target
    server.shutdown()
    server.server_close()


def _post(url, body, token):
    request = urllib.request.Request(url + "/bar", data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json",
                                              "X-Omacap-Token": token})
    try:
        with urllib.request.urlopen(request) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def test_the_server_seeks_in_the_audio(served):
    _, url, audio, _ = served
    request = urllib.request.Request(url + "/audio", headers={"Range": "bytes=100-199"})
    with urllib.request.urlopen(request) as response:
        assert response.status == 206
        assert response.read() == audio.read_bytes()[100:200]
        assert response.headers["Content-Range"] == f"bytes 100-199/{audio.stat().st_size}"


def test_a_correction_on_the_page_reaches_the_chart(served):
    session, url, audio, target = served
    with urllib.request.urlopen(url + "/") as response:
        assert session.token in response.read().decode()
    status, data = _post(url, {"n": 4, "text": "D · · G"}, session.token)
    assert status == 200
    bar = next(b for b in data["bars"] if b["n"] == 4)
    assert bar["corrected"] and bar["beats"] == "D · · G" and bar["notes"] == "D[2.] G[4]"
    assert "D[2.] G[4]" in target.read_text(encoding="utf-8")
    # And empty means back to what the analysis heard.
    status, data = _post(url, {"n": 4, "text": ""}, session.token)
    assert status == 200 and not next(b for b in data["bars"] if b["n"] == 4)["corrected"]
    assert not corrections.corrections_path(audio).exists()


def test_correcting_every_bar_written_the_same(served):
    session, url, _, _ = served
    first = session.analysis.bars[0]
    alike = [b.number for b in session.analysis.bars if b.label == first.label]
    status, data = _post(url, {"n": first.number, "text": "E", "same": True}, session.token)
    assert status == 200
    assert {b["n"] for b in data["bars"] if b["corrected"]} == set(alike)


def test_the_server_refuses_what_it_cannot_trust(served):
    session, url, _, _ = served
    assert _post(url, {"n": 1, "text": "C"}, "wrong")[0] == 403
    status, data = _post(url, {"n": 1, "text": "H7x"}, session.token)
    assert status == 422 and "not a chord" in data["error"]


def test_a_repeat_written_once_is_not_mistaken_for_an_edit(analysis, tmp_path):
    """Two bars with the same chords but the change on a different beat.

    The phrase collapses on chord names, so the chart writes the second like the
    first. Reading that back must not look like somebody changed the second.
    """
    a, b = analysis.bars[0], analysis.bars[4]
    shifted = corrections.apply(analysis, {
        str(a.number): {"beats": ["C", "C", "G", "G"], "start": a.start},
        str(b.number): {"beats": ["C", "C", "C", "G"], "start": b.start},
    })
    text = _render(shifted)
    assert corrections.edited_bars(shifted, text, _render) == {}


def test_the_page_says_when_the_chart_was_left_alone(served):
    """A correction still saves, but a chart edited beyond reading back is not
    rewritten - and the person correcting on the page has to be told, not only
    the terminal."""
    session, url, audio, target = served
    mangled = target.read_text(encoding="utf-8").replace(":||", "", 1)
    target.write_text(mangled, encoding="utf-8")
    status, data = _post(url, {"n": 2, "text": "Em"}, session.token)
    assert status == 200
    assert data["notes"] and "left as it is" in data["notes"][0]
    assert target.read_text(encoding="utf-8") == mangled
    assert corrections.corrections_path(audio).is_file()
