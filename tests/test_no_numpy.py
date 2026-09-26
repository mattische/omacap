"""Recording must not need numpy.

Recording works with nothing but Python and ffmpeg; numpy is an extra, for the
analysis only. That is a promise to anyone who installs omacap to capture audio
and never charts a thing, and it is easy to break by accident - one import added
to a module the TUI touches is enough.

Each check runs in a fresh interpreter with numpy blocked at the import hook, so
an already-imported numpy in this test session cannot hide a breakage.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

SRC = str(Path(__file__).resolve().parents[1] / "src")

#: Everything the recording side reaches, and nothing from `analysis`.
RECORDING_MODULES = [
    "omacap",
    "omacap.ui",
    "omacap.tui",
    "omacap.cli",
    "omacap.chart",
    "omacap.chordgrid",
    "omacap.recorder",
    "omacap.devices",
    "omacap.formats",
    "omacap.updater",
    "omacap.nowplaying",
    "omacap.timeline",
    "omacap.splitter",
]

BLOCKER = '''
import sys


class Blocked:
    """Refuse numpy the way a machine without it would.

    This uses find_spec, the only finder protocol current Python consults - the
    older find_module/load_module pair is gone, and a finder written against it
    is silently ignored, which is exactly what
    test_the_blocker_really_blocks_numpy is here to catch.
    """

    def find_spec(self, name, path=None, target=None):
        if name == "numpy" or name.startswith("numpy."):
            raise ImportError("No module named 'numpy'")
        return None


sys.meta_path.insert(0, Blocked())
sys.path.insert(0, {src!r})
'''


def run(body: str) -> subprocess.CompletedProcess:
    script = BLOCKER.format(src=SRC) + textwrap.dedent(body)
    return subprocess.run([sys.executable, "-c", script],
                          capture_output=True, text=True, timeout=120)


def test_the_blocker_really_blocks_numpy():
    """Guard the guard: a test that cannot fail proves nothing."""
    result = run("""
        try:
            import numpy
        except ImportError:
            print("blocked")
        else:
            print("NOT BLOCKED")
    """)
    assert result.stdout.strip() == "blocked", result.stderr


@pytest.mark.parametrize("module", RECORDING_MODULES)
def test_a_recording_module_imports_without_numpy(module):
    result = run(f"""
        import {module}
        print("ok")
    """)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ok", result.stderr


def test_the_chart_format_names_work_without_numpy():
    # chart.py is reached from the TUI's format key, so its pure helpers have to
    # stay usable with no numpy behind them.
    result = run("""
        from omacap.chart import CHART_FORMATS, chart_format_label
        print(" ".join(chart_format_label(f) for f in CHART_FORMATS))
    """)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == ".md .txt chordgrid (.md)", result.stdout


def test_the_command_line_runs_without_numpy():
    result = run("""
        from omacap import cli
        raise SystemExit(cli.main(["--help"]))
    """)
    assert result.returncode == 0, result.stderr
    assert "omacap" in result.stdout


def test_analysing_without_numpy_says_so_rather_than_crashing():
    result = run("""
        from omacap.analysis import require_numpy
        try:
            require_numpy()
        except Exception as exc:
            print(type(exc).__name__, "-", exc)
    """)
    assert result.returncode == 0, result.stderr
    assert "numpy" in result.stdout.lower()
