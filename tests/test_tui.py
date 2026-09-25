"""State-machine tests for the interactive app, driven by synthetic keystrokes."""

from __future__ import annotations

from pathlib import Path

import pytest

from omacap import tui
from omacap.formats import get_format
from omacap.recorder import METER_FLOOR_DB, State
from omacap.tui import BITRATE_CHOICES, TuiApp, shorten_home


class FakeRecorder:
    """Stands in for :class:`omacap.recorder.Recorder` without any subprocess."""

    instances: list["FakeRecorder"] = []
    fail_on_start: str | None = None
    fail_on_stop: str | None = None

    def __init__(self, config):
        self.config = config
        self.state = State.IDLE
        self.error = None
        self.peak_db = METER_FLOOR_DB
        self.duration = 0.0
        self.size_bytes = 0
        self.is_running = False
        self.stopped = False
        FakeRecorder.instances.append(self)

    def start(self):
        if FakeRecorder.fail_on_start:
            from omacap.recorder import RecorderError

            raise RecorderError(FakeRecorder.fail_on_start)
        self.state = State.RECORDING
        self.is_running = True

    def stop(self, timeout=8.0):
        from omacap.recorder import RecordingResult

        self.stopped = True
        self.is_running = False
        self.duration = self.duration or 12.0
        self.size_bytes = self.size_bytes or 240_000
        if FakeRecorder.fail_on_stop:
            self.error = FakeRecorder.fail_on_stop
            self.state = State.FAILED
        else:
            self.state = State.FINISHED
            self.config.output_path.parent.mkdir(parents=True, exist_ok=True)
            self.config.output_path.write_bytes(b"\0" * 16)
        return RecordingResult(
            path=self.config.output_path,
            duration=self.duration,
            size_bytes=self.size_bytes,
            format_name=self.config.audio_format.name,
        )


@pytest.fixture
def app(monkeypatch, tmp_path, monitor_source):
    FakeRecorder.instances = []
    FakeRecorder.fail_on_start = None
    FakeRecorder.fail_on_stop = None
    monkeypatch.setattr(tui, "Recorder", FakeRecorder)
    monkeypatch.setattr(TuiApp, "draw", lambda self, force=False: None)
    instance = TuiApp(
        source=monitor_source,
        audio_format=get_format("mp3"),
        output_dir=tmp_path / "recordings",
        bitrate="192k",
    )
    return instance


def press(app: TuiApp, keys: str) -> None:
    for key in keys:
        app.handle_key(key)


# -- recording lifecycle --------------------------------------------------

def test_space_starts_and_stops_recording(app):
    press(app, " ")
    assert app.state == "recording"
    assert len(FakeRecorder.instances) == 1

    press(app, " ")
    assert app.state == "idle"
    assert FakeRecorder.instances[0].stopped
    assert app.recorder is None


def test_r_also_toggles_recording(app):
    press(app, "r")
    assert app.state == "recording"
    press(app, "r")
    assert app.state == "idle"


def test_s_stops_without_starting_a_new_take(app):
    press(app, " ")
    press(app, "s")
    assert app.state == "idle"
    assert len(FakeRecorder.instances) == 1


def test_a_saved_take_is_listed_and_announced(app):
    press(app, " ")
    press(app, " ")
    assert len(app.recordings) == 1
    assert ".mp3" in app.recordings[0]
    assert app.message.startswith("Saved")
    assert app.message_kind == "success"


def test_each_take_gets_its_own_file(app):
    for _ in range(3):
        press(app, " ")
        press(app, " ")
    paths = {r.config.output_path for r in FakeRecorder.instances}
    assert len(paths) == 3
    assert len(app.recordings) == 3


def test_a_start_failure_is_surfaced_and_leaves_no_recorder(app):
    FakeRecorder.fail_on_start = "ffmpeg was not found on PATH."
    press(app, " ")
    assert app.state == "idle"
    assert app.recorder is None
    assert app.message_kind == "error"
    assert "ffmpeg" in app.message


def test_a_stop_failure_is_surfaced_and_not_listed_as_saved(app):
    FakeRecorder.fail_on_stop = "ffmpeg exited with code 1"
    press(app, " ")
    press(app, " ")
    assert app.message_kind == "error"
    assert app.recordings == []


# -- settings -------------------------------------------------------------

def test_format_cycles_forward_and_backward(app):
    press(app, "f")
    assert app.audio_format.name == "m4a"
    press(app, "F")
    assert app.audio_format.name == "mp3"


def test_switching_format_adopts_its_default_bitrate(app):
    press(app, "ff")          # mp3 -> m4a -> opus
    assert app.audio_format.name == "opus"
    assert app.bitrate == get_format("opus").default_bitrate


def test_bitrate_cycles_through_the_choices(app):
    start = BITRATE_CHOICES.index("192k")
    press(app, "b")
    assert app.bitrate == BITRATE_CHOICES[(start + 1) % len(BITRATE_CHOICES)]


def test_bitrate_is_refused_for_lossless_formats(app):
    while app.audio_format.name != "wav":
        press(app, "f")
    press(app, "b")
    assert "lossless" in app.message
    assert app.view_model().bitrate is None


def test_settings_are_locked_while_recording(app):
    press(app, " ")
    before = (app.audio_format.name, app.bitrate, app.source.name)
    press(app, "fbd")
    assert (app.audio_format.name, app.bitrate, app.source.name) == before
    assert app.message_kind == "error"


def test_source_cycles_through_the_monitors(app, monkeypatch, monitor_source):
    from omacap.devices import Source

    others = [monitor_source, Source("other.monitor", "Other Output", True)]
    monkeypatch.setattr(tui, "list_monitors", lambda: others)
    press(app, "d")
    assert app.source.name == "other.monitor"
    press(app, "d")
    assert app.source.name == monitor_source.name


def test_a_source_lookup_failure_is_reported(app, monkeypatch):
    from omacap.devices import AudioSystemError

    def boom():
        raise AudioSystemError("audio server unreachable")

    monkeypatch.setattr(tui, "list_monitors", boom)
    press(app, "d")
    assert app.message_kind == "error"


# -- naming ---------------------------------------------------------------

def test_naming_a_take_changes_the_filename(app):
    press(app, "n")
    assert app.name_prompt == ""
    press(app, "Take 7")
    press(app, "\r")
    assert app.next_name == "Take 7"

    press(app, " ")
    assert FakeRecorder.instances[0].config.output_path.name == "Take 7.mp3"


def test_the_name_applies_only_to_the_next_take(app):
    press(app, "n")
    press(app, "solo\r")
    press(app, " ")
    press(app, " ")
    assert app.next_name is None
    press(app, " ")
    assert FakeRecorder.instances[1].config.output_path.name.startswith("omacap_")


def test_escape_cancels_the_name_prompt(app):
    press(app, "n")
    press(app, "abc")
    app.handle_key("\x1b")
    assert app.name_prompt is None
    assert app.next_name is None


def test_backspace_edits_the_name(app):
    press(app, "n")
    press(app, "abcd")
    app.handle_key("\x7f")
    assert app.name_prompt == "abc"


def test_an_empty_name_restores_automatic_naming(app):
    press(app, "n")
    press(app, "x\r")
    press(app, "n")
    app.handle_key("\x7f")
    press(app, "\r")
    assert app.next_name is None


def test_names_are_sanitised_and_length_capped(app):
    press(app, "n")
    press(app, "../escape")
    press(app, "\r")
    assert "/" not in (app.next_name or "")

    press(app, "n")
    press(app, "z" * 100)
    assert len(app.name_prompt) == 60


def test_keys_do_not_leak_out_of_the_name_prompt(app):
    press(app, "n")
    press(app, "q f b")
    assert app.running is True
    assert app.audio_format.name == "mp3"
    assert app.name_prompt == "q f b"


# -- help and quitting ----------------------------------------------------

def test_help_toggles(app):
    press(app, "?")
    assert app.show_help is True
    press(app, "?")
    assert app.show_help is False
    press(app, "h")
    assert app.show_help is True


def test_q_quits(app):
    press(app, "q")
    assert app.running is False


def test_ctrl_c_quits(app):
    app.handle_key("\x03")
    assert app.running is False


def test_unknown_keys_are_ignored(app):
    press(app, "xyzZ123")
    assert app.running is True
    assert app.state == "idle"


# -- meter ----------------------------------------------------------------

def test_meter_sits_at_the_floor_when_idle(app):
    app.update_meter(0.1)
    assert app.meter_db == METER_FLOOR_DB


def test_meter_jumps_up_instantly(app):
    press(app, " ")
    app.recorder.peak_db = -6.0
    app.update_meter(0.05)
    assert app.meter_db == -6.0


def test_meter_falls_back_gradually(app):
    press(app, " ")
    app.recorder.peak_db = -6.0
    app.update_meter(0.05)
    app.recorder.peak_db = METER_FLOOR_DB
    app.update_meter(0.05)
    assert METER_FLOOR_DB < app.meter_db < -6.0
    for _ in range(50):
        app.update_meter(0.05)
    assert app.meter_db == METER_FLOOR_DB


def test_meter_never_overshoots_the_target(app):
    press(app, " ")
    app.recorder.peak_db = -20.0
    app.update_meter(0.05)
    app.recorder.peak_db = -25.0
    app.update_meter(10.0)
    assert app.meter_db == -25.0


# -- view model -----------------------------------------------------------

def test_view_model_reflects_a_running_take(app):
    press(app, " ")
    app.recorder.duration = 42.0
    app.recorder.size_bytes = 99_000
    vm = app.view_model()
    assert vm.state == "recording"
    assert vm.elapsed == 42.0 and vm.size_bytes == 99_000
    assert vm.source_label == "Monitor of Test Output"


def test_view_model_is_zeroed_when_idle(app):
    vm = app.view_model()
    assert vm.state == "idle"
    assert vm.elapsed == 0.0 and vm.size_bytes == 0


def test_home_directory_is_abbreviated():
    assert shorten_home(Path.home() / "Recordings") == "~/Recordings"
    assert shorten_home(Path("/srv/audio")) == "/srv/audio"


# -- analysis -------------------------------------------------------------

def test_analysing_before_recording_explains_itself(app):
    press(app, "a")
    assert app.message_kind == "error"
    assert "Record something first" in app.message


def test_analysis_is_offered_once_something_was_recorded(app):
    assert app.view_model().can_analyse is False
    press(app, " ")
    press(app, " ")
    assert app.last_recording is not None
    assert app.view_model().can_analyse is True


def test_analysing_writes_a_chart_and_reports_it(app, monkeypatch):
    press(app, " ")
    press(app, " ")

    class FakeAnalysis:
        key = type("K", (), {"name": "C major", "short_name": "C"})()
        meter = type("M", (), {"name": "4/4"})()
        tempo = 120.0
        bar_count = 16

    written = {}

    def fake_analyse(path, **kwargs):
        written["source"] = path
        return FakeAnalysis()

    def fake_write(analysis, target, chart_format, *args, **kwargs):
        written["target"] = target
        written["format"] = chart_format
        return target

    monkeypatch.setattr("omacap.analysis.report.analyse_file", fake_analyse)
    monkeypatch.setattr(tui, "write_chart", fake_write)

    press(app, "a")
    assert written["source"] == app.last_recording
    assert written["target"].suffix == ".md"
    assert app.message_kind == "success"
    assert "C major" in app.message and "120 BPM" in app.message
    assert any("4/4" in entry for entry in app.recordings)


def test_an_analysis_failure_is_shown_not_raised(app, monkeypatch):
    press(app, " ")
    press(app, " ")

    def boom(path, **kwargs):
        raise RuntimeError("numpy is not installed")

    monkeypatch.setattr("omacap.analysis.report.analyse_file", boom)
    press(app, "a")
    assert app.message_kind == "error"
    assert "numpy" in app.message
    assert app.running is True


def test_analysis_is_refused_while_recording(app):
    press(app, " ")
    press(app, "a")
    assert app.message_kind == "error"
    assert "Stop recording" in app.message


def test_the_chart_format_cycles(app):
    assert app.chart_format == "md"
    press(app, "t")
    assert app.chart_format == "txt"
    press(app, "t")
    assert app.chart_format == "md"


# -- ffmpeg capability handling -------------------------------------------

def test_format_cycling_skips_what_ffmpeg_cannot_write(app, monkeypatch):
    from omacap.formats import get_format as lookup

    usable = {"mp3", "wav"}
    monkeypatch.setattr(
        tui, "format_is_available", lambda fmt: fmt.name in usable
    )
    seen = set()
    for _ in range(6):
        press(app, "f")
        seen.add(app.audio_format.name)
    assert seen <= usable
    assert lookup("m4a").name not in seen


def test_cycling_reports_when_nothing_is_usable(app, monkeypatch):
    monkeypatch.setattr(tui, "format_is_available", lambda fmt: False)
    before = app.audio_format.name
    press(app, "f")
    assert app.audio_format.name == before
    assert app.message_kind == "error"
    assert "No usable output formats" in app.message


# -- the question asked after a recording ---------------------------------

def test_a_finished_take_offers_analysis(app):
    press(app, " ")
    press(app, " ")
    vm = app.view_model()
    assert vm.analyse_prompt is not None
    assert ".mp3" in vm.analyse_prompt
    assert "y analyse" in "\n".join(tui.ui.render(vm, 74, use_color=False))


def test_pressing_y_analyses_the_take(app, monkeypatch):
    called = {}

    def record_call(path, **kwargs):
        called["path"] = path
        return _FakeAnalysis()

    monkeypatch.setattr("omacap.analysis.report.analyse_file", record_call)
    monkeypatch.setattr(tui, "write_chart", lambda a, target, fmt, *rest: target)

    press(app, " ")
    press(app, " ")
    press(app, "y")
    assert called["path"] == app.last_recording
    assert app.analyse_prompt is None
    assert app.message_kind == "success"


def test_enter_also_accepts(app, monkeypatch):
    monkeypatch.setattr(
        "omacap.analysis.report.analyse_file", lambda path, **kwargs: _FakeAnalysis()
    )
    monkeypatch.setattr(tui, "write_chart", lambda a, target, fmt, *rest: target)
    press(app, " ")
    press(app, " ")
    app.handle_key("\r")
    assert app.analyse_prompt is None
    assert app.message_kind == "success"


def test_pressing_n_skips_and_says_how_to_do_it_later(app, monkeypatch):
    monkeypatch.setattr(
        "omacap.analysis.report.analyse_file",
        lambda path, **kwargs: pytest.fail("analysis should not run"),
    )
    press(app, " ")
    press(app, " ")
    press(app, "n")
    assert app.analyse_prompt is None
    assert "press a" in app.message.lower()


def test_escape_also_skips(app):
    press(app, " ")
    press(app, " ")
    app.handle_key("\x1b")
    assert app.analyse_prompt is None


def test_another_key_dismisses_the_question_and_still_acts(app):
    """Space should start the next take, not just clear the question."""
    press(app, " ")
    press(app, " ")
    assert app.analyse_prompt is not None
    press(app, " ")
    assert app.analyse_prompt is None
    assert app.state == "recording"


def test_the_question_is_withdrawn_when_a_new_take_starts(app):
    press(app, " ")
    press(app, " ")
    app.start_recording()
    assert app.analyse_prompt is None


def test_no_question_when_analysis_is_unavailable(app, monkeypatch):
    monkeypatch.setattr(tui, "analysis_available", lambda: False)
    press(app, " ")
    press(app, " ")
    assert app.analyse_prompt is None


def test_no_question_when_the_take_failed(app):
    FakeRecorder.fail_on_stop = "ffmpeg exited with code 1"
    press(app, " ")
    press(app, " ")
    assert app.analyse_prompt is None


def test_the_question_does_not_block_quitting(app):
    press(app, " ")
    press(app, " ")
    press(app, "q")
    assert app.running is False


class _FakeAnalysis:
    key = type("K", (), {"name": "C major", "short_name": "C"})()
    meter = type("M", (), {"name": "4/4"})()
    tempo = 120.0
    bar_count = 16


# -- the update notice -----------------------------------------------------

def test_an_available_update_is_shown_in_the_interface(app, monkeypatch):
    from omacap.updater import UpdateStatus

    monkeypatch.setattr(
        tui, "pending_update",
        lambda: UpdateStatus(available=True, local="aaaaaaa", remote="bbbbbbb"),
    )
    app.refresh_update_notice()
    assert "omacap update" in app.update_notice
    assert "omacap update" in "\n".join(
        tui.ui.render(app.view_model(), 74, use_color=False)
    )


def test_a_failing_update_check_is_invisible(app, monkeypatch):
    """A broken network must not stop the interface starting."""

    def boom():
        raise RuntimeError("no network")

    monkeypatch.setattr(tui, "pending_update", boom)
    app.refresh_update_notice()
    assert app.update_notice == ""
