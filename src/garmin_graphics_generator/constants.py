"""
Constants used throughout the Garmin Graphics Generator package.
"""
import os

# Define constants for file paths and string literals
DEFAULT_CONFIG_PATH = os.path.join(os.path.dirname(__file__), "data", "defaults.json")
EXTENSION_PNG = ".png"
MODE_RGBA = "RGBA"
WHITE_PIXEL_THRESHOLD = 240
