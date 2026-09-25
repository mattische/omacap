import json

import pytest

from omacap import devices
from omacap.devices import (
    AudioSystemError,
    Source,
    parse_sources_json,
    parse_sources_short,
    resolve_source,
)

JSON_PAYLOAD = json.dumps([
    {"name": "alsa_output.speakers.monitor", "description": "Monitor of Speakers"},
    {"name": "alsa_input.mic", "description": "Built-in Mic"},
    {"name": "virtual_sink", "description": "Virtual", "monitor_of_sink": "sink-1"},
    {"description": "nameless entry should be skipped"},
])

SHORT_PAYLOAD = (
    "40\talsa_output.speakers.monitor\tPipeWire\ts16le 2ch 48000Hz\tSUSPENDED\n"
    "41\talsa_input.mic\tPipeWire\ts16le 2ch 48000Hz\tSUSPENDED\n"
    "\n"
)


def test_json_parsing_flags_monitors():
    sources = parse_sources_json(JSON_PAYLOAD)
    assert [s.name for s in sources] == [
        "alsa_output.speakers.monitor", "alsa_input.mic", "virtual_sink"
    ]
    assert [s.is_monitor for s in sources] == [True, False, True]


def test_json_parsing_keeps_descriptions():
    assert parse_sources_json(JSON_PAYLOAD)[0].description == "Monitor of Speakers"


def test_short_parsing_flags_monitors():
    sources = parse_sources_short(SHORT_PAYLOAD)
    assert [(s.name, s.is_monitor) for s in sources] == [
        ("alsa_output.speakers.monitor", True), ("alsa_input.mic", False)
    ]


def test_label_falls_back_to_the_name():
    assert Source("x.monitor", "", True).label == "x.monitor"
    assert Source("x.monitor", "Nice Name", True).label == "Nice Name"


def test_pactl_env_supplies_a_runtime_dir(monkeypatch, tmp_path):
    monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
    monkeypatch.setattr(devices.os.path, "isdir", lambda _: True)
    assert devices._pactl_env()["XDG_RUNTIME_DIR"].startswith("/run/user/")


def test_pactl_env_keeps_an_existing_runtime_dir(monkeypatch):
    monkeypatch.setenv("XDG_RUNTIME_DIR", "/custom/run")
    assert devices._pactl_env()["XDG_RUNTIME_DIR"] == "/custom/run"


@pytest.fixture
def stub_sources(monkeypatch):
    sources = parse_sources_json(JSON_PAYLOAD)
    monkeypatch.setattr(devices, "list_sources", lambda: sources)
    return sources


def test_list_monitors_drops_microphones(monkeypatch, stub_sources):
    assert all(s.is_monitor for s in devices.list_monitors())
    assert "alsa_input.mic" not in [s.name for s in devices.list_monitors()]


def test_resolve_source_accepts_an_exact_name(stub_sources):
    assert resolve_source("alsa_input.mic").name == "alsa_input.mic"


def test_resolve_source_upgrades_a_sink_to_its_monitor(stub_sources):
    assert resolve_source("alsa_output.speakers").name == "alsa_output.speakers.monitor"


def test_resolve_source_rejects_an_unknown_name(stub_sources):
    with pytest.raises(AudioSystemError, match="Unknown audio source"):
        resolve_source("nope")


def test_resolve_source_defaults_to_the_current_output(monkeypatch, stub_sources):
    monkeypatch.setattr(devices, "default_sink", lambda: "alsa_output.speakers")
    assert resolve_source(None).name == "alsa_output.speakers.monitor"


def test_default_monitor_falls_back_when_the_sink_has_none(monkeypatch, stub_sources):
    monkeypatch.setattr(devices, "default_sink", lambda: "headphones-without-monitor")
    assert devices.default_monitor().name == "alsa_output.speakers.monitor"


def test_default_monitor_complains_when_there_are_none(monkeypatch):
    monkeypatch.setattr(devices, "list_sources", lambda: [])
    with pytest.raises(AudioSystemError, match="No monitor sources"):
        devices.default_monitor()


def test_missing_pactl_is_explained(monkeypatch):
    monkeypatch.setattr(devices.shutil, "which", lambda _: None)
    with pytest.raises(AudioSystemError, match="pactl"):
        devices._run_pactl(["info"])
