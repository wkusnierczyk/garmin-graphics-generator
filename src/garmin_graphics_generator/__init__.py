"""
Garmin Graphics Generator
-------------------------
A library and CLI tool for generating hero images from watch face screenshots.
"""
import os

# --- SILENCE OMP WARNINGS ---
# This must be set here because __init__.py runs before cli.py
os.environ["KMP_WARNINGS"] = "0"
os.environ["OMP_DISPLAY_ENV"] = "FALSE"

# pylint: disable=wrong-import-position
from .core import WatchHeroGenerator

__all__ = ["WatchHeroGenerator"]
