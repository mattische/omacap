"""Musical analysis of a recording: tempo, metre, key, bars and chords.

The whole package is optional. It needs numpy, which the base install does not
require; ``pip install 'omacap[analyze]'`` pulls it in.
"""

from __future__ import annotations


class AnalysisUnavailable(RuntimeError):
    """Raised when the analysis extras are not installed."""


def require_numpy():
    """Import numpy, or explain how to install it."""
    try:
        import numpy
    except ImportError as exc:  # pragma: no cover - exercised without numpy
        raise AnalysisUnavailable(
            "Musical analysis needs numpy. Install it with: "
            "pip install 'omacap[analyze]'"
        ) from exc
    return numpy


def analysis_available() -> bool:
    """Whether the analysis extras are installed.

    Checks for the module without importing it, so asking the question stays
    cheap enough to do on every start.
    """
    import importlib.util

    try:
        return importlib.util.find_spec("numpy") is not None
    except (ImportError, ValueError):
        return False


__all__ = ["AnalysisUnavailable", "analysis_available", "require_numpy"]
