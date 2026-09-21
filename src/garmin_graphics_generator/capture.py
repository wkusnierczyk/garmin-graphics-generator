"""
Turning a raw simulator framebuffer into device screenshots and watch renders.

This module is the image half of the ``shots`` command and knows nothing about
containers: it takes a framebuffer dump and a device's SDK definition, and returns
the two images worth keeping. Everything it needs is published by the SDK, so the
result is a crop at known coordinates rather than an estimate.

``Devices/<product>/simulator.json`` gives ``display.location``, the screen
rectangle within the device render -- ``{x: 122, y: 238, 416x416}`` for
``epix2pro47mm``. ``Devices/<product>/<product>.png`` is the render itself, and its
alpha channel is ``0`` exactly over the screen aperture and ``255`` everywhere else,
including the flat white surround the simulator draws the watch on.

That alpha channel does two jobs. Masking the framebuffer crop with it reproduces
the device screen as the panel shows it, corners of a round display included: those
pixels are covered by the render's own rim, and a round display never lights them.
Compositing the render over the crop puts the frame back inside the watch.

The watch silhouette is a third thing, and the one place a naive approach goes
wrong. It is computed by flooding the render's white surround inwards from the
corners, on the artwork alone, before any frame is composited -- so a bright pixel
in the watch face cannot be mistaken for background. A global white threshold, the
usual shortcut, punches holes in exactly the content worth showing: the lead glyph
of a digital-rain column is very nearly white.
"""
import json
import logging
import os
import struct
from typing import Dict, Optional, Tuple

from PIL import Image, ImageChops, ImageDraw

logger = logging.getLogger(__name__)

# X Window Dump, the format "Xvfb -fbdir" maps its framebuffer onto. The header is
# 25 unsigned 32-bit fields, always most-significant byte first whatever the machine
# that wrote it, followed by the window name and then one 12-byte colour map entry
# per "ncolors". Pixel data starts after all of that.
XWD_HEADER_FIELDS = 25
XWD_HEADER_FORMAT = ">25I"
XWD_COLORMAP_ENTRY_SIZE = 12
XWD_FILE_VERSION = 7

# Field offsets within the unpacked header, by index rather than by name: the
# format is fixed and ancient, and naming all 25 would be more noise than help.
_HEADER_SIZE = 0
_FILE_VERSION = 1
_PIXMAP_WIDTH = 4
_PIXMAP_HEIGHT = 5
_BYTE_ORDER = 7
_BITS_PER_PIXEL = 11
_BYTES_PER_LINE = 12
_RED_MASK = 14
_GREEN_MASK = 15
_BLUE_MASK = 16
_NCOLORS = 19

# Pillow raw decoder modes for the pixel layouts Xvfb and xwd actually produce,
# keyed by (bits per pixel, byte order, red mask). Byte order 0 is LSBFirst. A
# 32-bit LSBFirst pixel whose red mask is 0x00FF0000 arrives as B, G, R, unused --
# which is what a depth-24 Xvfb screen gives.
_RAW_MODES = {
    (32, 0, 0x00FF0000): "BGRX",
    (32, 1, 0x00FF0000): "XRGB",
    (32, 0, 0x000000FF): "RGBX",
    (24, 0, 0x00FF0000): "BGR",
    (24, 1, 0x00FF0000): "RGB",
}

SIMULATOR_DEFINITION = "simulator.json"

# How often to sample rows when choosing one to match the device render on. Every
# row would be wasted work: neighbouring rows of a watch render are near-identical,
# so a stride costs nothing in the quality of the row chosen.
PROBE_ROW_STRIDE = 4

# How much of each render edge to disregard when matching, as a floor; see
# DeviceRender._inset. The simulator clips its own artwork against its window.
RENDER_EDGE_INSET = 8


class CaptureError(Exception):
    """Raised when a framebuffer or a device definition is not what it claims."""


def read_xwd(data: bytes) -> Image.Image:
    """
    Decodes an X Window Dump into an RGB image.

    Accepts the layouts ``Xvfb -fbdir`` and ``xwd`` produce; anything else is
    reported rather than guessed at, because a wrong guess here yields a plausible
    image with the colour channels swapped.
    """
    if len(data) < struct.calcsize(XWD_HEADER_FORMAT):
        raise CaptureError(
            f"framebuffer is {len(data)} bytes, too short to hold an XWD header"
        )

    header = struct.unpack(
        XWD_HEADER_FORMAT, data[: struct.calcsize(XWD_HEADER_FORMAT)]
    )

    if header[_FILE_VERSION] != XWD_FILE_VERSION:
        raise CaptureError(
            f"XWD file version {header[_FILE_VERSION]}, expected {XWD_FILE_VERSION}; "
            "this is not an X Window Dump"
        )

    width = header[_PIXMAP_WIDTH]
    height = header[_PIXMAP_HEIGHT]
    bits_per_pixel = header[_BITS_PER_PIXEL]
    bytes_per_line = header[_BYTES_PER_LINE]
    byte_order = header[_BYTE_ORDER]
    red_mask = header[_RED_MASK]

    mode = _RAW_MODES.get((bits_per_pixel, byte_order, red_mask))
    if mode is None:
        raise CaptureError(
            f"unsupported XWD pixel layout: {bits_per_pixel} bits per pixel, "
            f"byte order {byte_order}, masks "
            f"{red_mask:#010x}/{header[_GREEN_MASK]:#010x}/{header[_BLUE_MASK]:#010x}"
        )

    offset = header[_HEADER_SIZE] + header[_NCOLORS] * XWD_COLORMAP_ENTRY_SIZE
    needed = bytes_per_line * height
    if len(data) - offset < needed:
        raise CaptureError(
            f"framebuffer holds {len(data) - offset} bytes of pixel data, "
            f"{needed} needed for {width}x{height}"
        )

    logger.debug(
        "decoding %dx%d XWD, %d bits per pixel, raw mode %s",
        width,
        height,
        bits_per_pixel,
        mode,
    )
    return Image.frombytes(
        "RGB",
        (width, height),
        data[offset : offset + needed],
        "raw",
        mode,
        bytes_per_line,
    )


class DeviceRender:
    """
    A product's simulator artwork, and the geometry needed to cut frames out of it.

    Built from the two files the SDK ships per device, so a product the SDK knows
    about needs no configuration here at all.
    """

    def __init__(self, product: str, devices_directory: str):
        self.product = product
        directory = os.path.join(os.path.expanduser(devices_directory), product)
        if not os.path.isdir(directory):
            raise CaptureError(
                f"no SDK definition for {product} in {devices_directory}; "
                "check the product name against manifest.xml, or pass "
                "--devices-directory"
            )

        definition_path = os.path.join(directory, SIMULATOR_DEFINITION)
        try:
            with open(definition_path, "r", encoding="utf-8") as definition_file:
                definition = json.load(definition_file)
        except (IOError, OSError, ValueError) as error:
            raise CaptureError(f"cannot read {definition_path}: {error}") from error

        try:
            location = definition["display"]["location"]
            self.screen = (
                int(location["x"]),
                int(location["y"]),
                int(location["width"]),
                int(location["height"]),
            )
            image_name = definition.get("image", f"{product}.png")
        except (KeyError, TypeError, ValueError) as error:
            raise CaptureError(
                f"{definition_path} has no usable display.location: {error}"
            ) from error

        render_path = os.path.join(directory, image_name)
        try:
            with Image.open(render_path) as opened:
                self.render = opened.convert("RGBA")
        except (IOError, OSError) as error:
            raise CaptureError(f"cannot read {render_path}: {error}") from error

        self.silhouette = _watch_silhouette(self.render)
        logger.debug(
            "%s: render %dx%d, screen %s",
            product,
            self.render.width,
            self.render.height,
            self.screen,
        )

    @property
    def screen_box(self) -> Tuple[int, int, int, int]:
        """The screen rectangle within the render, as a Pillow crop box."""
        x, y, width, height = self.screen
        return (x, y, x + width, y + height)

    def locate(self, frame: Image.Image) -> Tuple[int, int]:
        """
        Finds where this device's render sits within a whole-screen framebuffer.

        Matched on the render's own pixels rather than computed from window
        geometry, so a simulator that changes its menu bar, its status bar or its
        window placement does not silently shift every capture by a few rows. A
        row below the screen aperture is searched for first -- the watch face
        cannot write there, so the row is the artwork's alone -- and the result is
        then verified against further rows before it is believed.

        Matched on the middle of each row rather than the whole of it. The
        simulator sizes its window to the render and then draws a border inside
        that, so the last couple of columns and rows are clipped and never appear
        in the framebuffer: measured as two of each on SDK 9.2.0. Insetting is
        what makes the match independent of how much gets clipped.
        """
        probe_y = self._probe_row()
        render_rgb = self.render.convert("RGB")
        inset = self._inset(render_rgb)
        strip = render_rgb.crop(
            (inset, probe_y, render_rgb.width - inset, probe_y + 1)
        ).tobytes()

        haystack = frame.tobytes()
        index = haystack.find(strip)
        while index >= 0:
            if index % 3 == 0:
                pixel = index // 3
                top = pixel // frame.width
                left = pixel % frame.width
                origin = (left - inset, top - probe_y)
                if self._verify(frame, render_rgb, origin):
                    logger.debug("located %s render at %s", self.product, origin)
                    return origin
            index = haystack.find(strip, index + 1)

        raise CaptureError(
            f"could not find the {self.product} render in a "
            f"{frame.width}x{frame.height} framebuffer; the simulator may have "
            "been showing another product, or a dialog over the device"
        )

    def _probe_row(self) -> int:
        """
        Picks a render row clear of the screen aperture, and rich enough to be unique.

        Chosen by looking rather than by rule. Only rows the watch face cannot
        write to are eligible -- above and below the aperture -- and of those the
        one with the most distinct pixels wins, because that is the row least
        likely to occur twice in a framebuffer. Sampled every few rows so the scan
        stays cheap on a render several hundred pixels tall.
        """
        render_rgb = self.render.convert("RGB")
        inset = self._inset(render_rgb)
        _, screen_y, _, screen_height = self.screen
        below = screen_y + screen_height

        # The last rows go with the last columns: the simulator clips them, so a row
        # matched there would never be found in a frame.
        bands = [
            range(below, max(below, render_rgb.height - inset)),
            range(0, max(0, screen_y)),
        ]

        best = None
        best_variety = 1
        for band in bands:
            for row in band[::PROBE_ROW_STRIDE]:
                raw = render_rgb.crop(
                    (inset, row, render_rgb.width - inset, row + 1)
                ).tobytes()
                variety = len({raw[i : i + 3] for i in range(0, len(raw), 3)})
                if variety > best_variety:
                    best, best_variety = row, variety

        if best is None:
            raise CaptureError(
                f"the {self.product} render has no row outside the screen with "
                "enough detail to match on"
            )
        logger.debug(
            "%s: matching on row %d, %d distinct pixels",
            self.product,
            best,
            best_variety,
        )
        return best

    @staticmethod
    def _inset(render_rgb: Image.Image) -> int:
        """
        How much of each render edge to disregard when matching.

        A fraction of the width rather than the two columns measured, so the
        clipping does not have to be predicted exactly -- only bounded. Well
        inside the white surround, so nothing distinguishing is given up.
        """
        return max(RENDER_EDGE_INSET, render_rgb.width // 32)

    def _verify(
        self, frame: Image.Image, render_rgb: Image.Image, origin: Tuple[int, int]
    ) -> bool:
        """Confirms a candidate origin against further render rows clear of the screen."""
        left, top = origin
        if left < 0 or top < 0:
            return False

        # The render may run past the right and bottom edges of what was captured,
        # because the simulator clips it. What must be wholly present is the screen
        # rectangle -- that is the part being cut out.
        screen_x, screen_y, screen_width, screen_height = self.screen
        if left + screen_x + screen_width > frame.width:
            return False
        if top + screen_y + screen_height > frame.height:
            return False

        inset = self._inset(render_rgb)
        below = screen_y + screen_height
        # A handful of rows spread over both bands, not every row: the strip match
        # has already done the discriminating, and this only has to rule out a
        # coincidence that spans a scanline boundary. The last rows of the render
        # are left out for the same reason the columns are inset -- they are
        # clipped, and never reach the framebuffer.
        rows = [
            screen_y // 4,
            screen_y // 2,
            below + (render_rgb.height - below) // 3,
        ]
        for row in rows:
            if not 0 <= row < render_rgb.height:
                continue
            if top + row >= frame.height:
                return False
            expected = render_rgb.crop((inset, row, render_rgb.width - inset, row + 1))
            actual = frame.crop(
                (
                    left + inset,
                    top + row,
                    left + render_rgb.width - inset,
                    top + row + 1,
                )
            )
            if expected.tobytes() != actual.tobytes():
                return False
        return True

    def extract(
        self, frame: Image.Image, origin: Optional[Tuple[int, int]] = None
    ) -> Tuple[Image.Image, Image.Image]:
        """
        Cuts one framebuffer into the device screen and the watch render.

        Returns ``(screen, watch)``: the screen at the device's native resolution
        with the render's rim masked back to black, and the frame set into the
        watch with everything outside the watch transparent.
        """
        if origin is None:
            origin = self.locate(frame)
        left, top = origin
        screen_x, screen_y, width, height = self.screen

        captured = frame.crop(
            (
                left + screen_x,
                top + screen_y,
                left + screen_x + width,
                top + screen_y + height,
            )
        ).convert("RGB")

        # The rim of the render overlaps the corners of a round display. Those
        # pixels are not the panel's, so they are put back to the black the panel
        # would show rather than left carrying artwork.
        rim = self.render.getchannel("A").crop(self.screen_box)
        screen = captured.copy()
        screen.paste((0, 0, 0), (0, 0), rim)

        watch = Image.new("RGBA", self.render.size, (0, 0, 0, 0))
        watch.paste(captured.convert("RGBA"), (screen_x, screen_y))
        watch.alpha_composite(self.render)
        watch.putalpha(self.silhouette)

        return screen, watch


def _watch_silhouette(render: Image.Image) -> Image.Image:
    """
    Builds the alpha channel that cuts the watch out of its flat white surround.

    Flooded inwards from all four corners rather than thresholded: the surround is
    one connected region of pure white, while white pixels inside the watch -- a
    button highlight, a near-white glyph in the face once a frame is composited --
    are not reachable from outside and are kept.
    """
    flooded = render.convert("RGB").copy()
    sentinel = (255, 0, 255)
    for corner in (
        (0, 0),
        (flooded.width - 1, 0),
        (0, flooded.height - 1),
        (flooded.width - 1, flooded.height - 1),
    ):
        if flooded.getpixel(corner) != sentinel:
            ImageDraw.floodfill(flooded, corner, sentinel, thresh=8)

    # point() over the flooded copy: the sentinel is the only fully saturated
    # magenta in a render made of greys, so a per-channel test identifies it
    # without walking the image in Python. darker() is a pixel-wise minimum, which
    # over three 0-or-255 masks is their conjunction.
    red, green, blue = flooded.split()
    outside = ImageChops.darker(
        ImageChops.darker(
            red.point(lambda v: 255 if v == 255 else 0),
            green.point(lambda v: 255 if v == 0 else 0),
        ),
        blue.point(lambda v: 255 if v == 255 else 0),
    )
    return ImageChops.invert(outside)


def device_geometry(product: str, devices_directory: str) -> Dict[str, object]:
    """Reports what the SDK says about a product's screen, for diagnostics."""
    device = DeviceRender(product, devices_directory)
    return {
        "product": product,
        "render": device.render.size,
        "screen": device.screen,
    }
