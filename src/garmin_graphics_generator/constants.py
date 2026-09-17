"""
Constants used throughout the Garmin Graphics Generator package.
"""
import os

# Define constants for file paths and string literals
DEFAULT_CONFIG_PATH = os.path.join(os.path.dirname(__file__), "data", "defaults.json")
EXTENSION_PNG = ".png"
MODE_RGBA = "RGBA"
WHITE_PIXEL_THRESHOLD = 240

# Launcher icons. The SDK publishes the required size per device in
# Devices/<product>/compiler.json, which is what DEFAULT_DEVICES_DIRECTORY points at
# on macOS; pass --devices-directory on any other platform.
DEFAULT_DEVICES_DIRECTORY = "~/Library/Application Support/Garmin/ConnectIQ/Devices"
MANIFEST_NAME = "manifest.xml"
JUNGLE_NAME = "monkey.jungle"
LAUNCHER_ICON_NAME = "launcher_icon.png"
ICON_DIRECTORY_TEMPLATE = "resources-icon-{size}"

# The generated mapping is spliced between these, so a jungle can carry hand-written
# entries of its own outside the block.
BEGIN_MARKER = "# BEGIN generated launcher icon mapping -- garmin-graphics-generator"
END_MARKER = "# END generated launcher icon mapping"

DRAWABLES_XML = (
    '<drawables xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
    "xsi:noNamespaceSchemaLocation="
    '"https://developer.garmin.com/downloads/connect-iq/resources.xsd">\n'
    '    <bitmap id="LauncherIcon" filename="launcher_icon.png" dithering="none" />\n'
    "</drawables>\n"
)
