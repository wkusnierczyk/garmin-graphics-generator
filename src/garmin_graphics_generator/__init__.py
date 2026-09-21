"""
Garmin Graphics Generator
-------------------------
A library and CLI tool for watch face imagery: screenshots captured from the
Connect IQ simulator, hero images made from them, and per-device launcher icons.
"""
import os

# --- SILENCE OMP WARNINGS ---
# This must be set here because __init__.py runs before cli.py
os.environ["KMP_WARNINGS"] = "0"
os.environ["OMP_DISPLAY_ENV"] = "FALSE"

# pylint: disable=wrong-import-position
from .launcher_icons import LauncherIconGenerator

__all__ = ["LauncherIconGenerator", "WatchHeroGenerator"]


def __getattr__(name):
    """
    Imports WatchHeroGenerator on first use.

    The hero pipeline pulls in rembg and onnxruntime, which are heavy and entirely
    unrelated to launcher icons. Importing them lazily keeps `icons` -- the command
    a watch face project reruns on every device list change -- to Pillow alone.
    """
    if name == "WatchHeroGenerator":
        from .core import WatchHeroGenerator  # pylint: disable=import-outside-toplevel

        return WatchHeroGenerator
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
