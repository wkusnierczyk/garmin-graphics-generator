import json
import struct

import pytest
from PIL import Image, ImageDraw

from garmin_graphics_generator.capture import (
    CaptureError,
    DeviceRender,
    device_geometry,
    read_xwd,
)

# A device small enough to assert about pixel by pixel, shaped like a real one: a
# round screen aperture inside a square render, on a flat white surround.
RENDER_SIZE = (60, 90)
SCREEN = {"x": 10, "y": 20, "width": 40, "height": 40}


def make_device(tmp_path, product="testwatch", screen=None, render_size=None):
    """Writes an SDK-shaped device definition and returns its devices directory."""
    screen = screen or SCREEN
    render_size = render_size or RENDER_SIZE
    devices = tmp_path / "devices"
    directory = devices / product
    directory.mkdir(parents=True)

    (directory / "simulator.json").write_text(
        json.dumps(
            {
                "display": {"location": screen, "shape": "round"},
                "image": f"{product}.png",
            }
        )
    )

    render = Image.new("RGBA", render_size, (255, 255, 255, 255))
    draw = ImageDraw.Draw(render)
    # A case with some detail in it, so a row outside the screen is distinctive
    # enough to match on, and a strap band below it.
    draw.rectangle(
        (4, 4, render_size[0] - 5, render_size[1] - 5), fill=(90, 90, 95, 255)
    )
    for x in range(6, render_size[0] - 6, 3):
        draw.line((x, 70, x, 80), fill=(30 + x * 2, 40, 50, 255))
    # The aperture: transparent where the panel shows through, opaque rim elsewhere.
    draw.ellipse(
        (
            screen["x"],
            screen["y"],
            screen["x"] + screen["width"] - 1,
            screen["y"] + screen["height"] - 1,
        ),
        fill=(0, 0, 0, 0),
    )
    render.save(directory / f"{product}.png")
    return devices


def make_xwd(image, bits_per_pixel=32, byte_order=0, red_mask=0x00FF0000, version=7):
    """Packs an image into the XWD layout Xvfb writes."""
    width, height = image.size
    bytes_per_pixel = bits_per_pixel // 8
    bytes_per_line = width * bytes_per_pixel
    header_size = 100
    ncolors = 0

    header = [0] * 25
    header[0] = header_size
    header[1] = version
    header[2] = 2
    header[3] = 24
    header[4] = width
    header[5] = height
    header[7] = byte_order
    header[11] = bits_per_pixel
    header[12] = bytes_per_line
    header[14] = red_mask
    header[15] = 0x0000FF00
    header[16] = 0x000000FF
    header[19] = ncolors

    pixels = bytearray()
    for y in range(height):
        for x in range(width):
            red, green, blue = image.getpixel((x, y))[:3]
            if bytes_per_pixel == 4:
                pixels += bytes((blue, green, red, 0))
            else:
                pixels += bytes((blue, green, red))
    return struct.pack(">25I", *header) + bytes(pixels)


def place(render_path_image, canvas_size=(200, 160), origin=(7, 13), screen_fill=None):
    """Draws a device render into a larger frame, as the simulator's window does."""
    frame = Image.new("RGB", canvas_size, (0, 0, 0))
    render = render_path_image.convert("RGB")
    if screen_fill is not None:
        render = render.copy()
        ImageDraw.Draw(render).rectangle(
            (
                SCREEN["x"],
                SCREEN["y"],
                SCREEN["x"] + SCREEN["width"] - 1,
                SCREEN["y"] + SCREEN["height"] - 1,
            ),
            fill=screen_fill,
        )
    frame.paste(render, origin)
    return frame


class TestReadXwd:
    def test_decodes_a_32_bit_dump(self):
        source = Image.new("RGB", (8, 4), (10, 20, 30))
        source.putpixel((3, 2), (200, 100, 50))
        decoded = read_xwd(make_xwd(source))
        assert decoded.size == (8, 4)
        assert decoded.getpixel((3, 2)) == (200, 100, 50)
        assert decoded.getpixel((0, 0)) == (10, 20, 30)

    def test_decodes_a_24_bit_dump(self):
        source = Image.new("RGB", (5, 3), (7, 8, 9))
        decoded = read_xwd(make_xwd(source, bits_per_pixel=24))
        assert decoded.getpixel((1, 1)) == (7, 8, 9)

    def test_rejects_a_short_file(self):
        with pytest.raises(CaptureError, match="too short"):
            read_xwd(b"\x00" * 10)

    def test_rejects_another_format(self):
        source = Image.new("RGB", (4, 4), (0, 0, 0))
        with pytest.raises(CaptureError, match="not an X Window Dump"):
            read_xwd(make_xwd(source, version=6))

    def test_rejects_an_unsupported_pixel_layout(self):
        source = Image.new("RGB", (4, 4), (0, 0, 0))
        with pytest.raises(CaptureError, match="unsupported XWD pixel layout"):
            read_xwd(make_xwd(source, bits_per_pixel=16))

    def test_rejects_truncated_pixel_data(self):
        source = Image.new("RGB", (8, 8), (0, 0, 0))
        data = make_xwd(source)
        with pytest.raises(CaptureError, match="pixel data"):
            read_xwd(data[: len(data) - 40])


class TestDeviceRender:
    def test_reads_the_sdk_definition(self, tmp_path):
        devices = make_device(tmp_path)
        device = DeviceRender("testwatch", str(devices))
        assert device.screen == (10, 20, 40, 40)
        assert device.render.size == RENDER_SIZE

    def test_reports_an_unknown_product(self, tmp_path):
        make_device(tmp_path)
        with pytest.raises(CaptureError, match="no SDK definition for nosuch"):
            DeviceRender("nosuch", str(tmp_path / "devices"))

    def test_reports_a_definition_without_a_display(self, tmp_path):
        devices = make_device(tmp_path)
        (devices / "testwatch" / "simulator.json").write_text(
            json.dumps({"display": {}})
        )
        with pytest.raises(CaptureError, match="display.location"):
            DeviceRender("testwatch", str(devices))

    def test_silhouette_cuts_the_surround_and_keeps_the_watch(self, tmp_path):
        devices = make_device(tmp_path)
        device = DeviceRender("testwatch", str(devices))
        # The white surround is transparent, the case is not.
        assert device.silhouette.getpixel((0, 0)) == 0
        assert (
            device.silhouette.getpixel((RENDER_SIZE[0] // 2, RENDER_SIZE[1] // 2))
            == 255
        )

    def test_silhouette_keeps_white_enclosed_by_the_watch(self, tmp_path):
        """A near-white pixel inside the case is content, not background."""
        devices = make_device(tmp_path)
        render = Image.open(devices / "testwatch" / "testwatch.png").convert("RGBA")
        render.putpixel((30, 75), (255, 255, 255, 255))
        render.save(devices / "testwatch" / "testwatch.png")

        device = DeviceRender("testwatch", str(devices))
        assert device.silhouette.getpixel((30, 75)) == 255


class TestLocate:
    def test_finds_the_render_in_a_frame(self, tmp_path):
        devices = make_device(tmp_path)
        device = DeviceRender("testwatch", str(devices))
        frame = place(device.render, origin=(7, 13), screen_fill=(0, 200, 0))
        assert device.locate(frame) == (7, 13)

    def test_finds_it_whatever_the_face_is_drawing(self, tmp_path):
        """The probe row is outside the aperture, so screen content cannot move it."""
        devices = make_device(tmp_path)
        device = DeviceRender("testwatch", str(devices))
        for fill in ((0, 0, 0), (255, 255, 255), (12, 240, 33)):
            frame = place(device.render, origin=(3, 5), screen_fill=fill)
            assert device.locate(frame) == (3, 5)

    def test_tolerates_a_render_clipped_at_the_right_and_bottom(self, tmp_path):
        """The simulator draws a border inside its window over its own artwork."""
        devices = make_device(tmp_path)
        device = DeviceRender("testwatch", str(devices))
        frame = place(device.render, origin=(7, 13), screen_fill=(0, 90, 0))
        draw = ImageDraw.Draw(frame)
        draw.rectangle(
            (7 + RENDER_SIZE[0] - 2, 0, frame.width, frame.height), fill=(60, 60, 60)
        )
        draw.rectangle(
            (0, 13 + RENDER_SIZE[1] - 2, frame.width, frame.height), fill=(60, 60, 60)
        )
        assert device.locate(frame) == (7, 13)

    def test_reports_a_frame_without_the_device(self, tmp_path):
        devices = make_device(tmp_path)
        device = DeviceRender("testwatch", str(devices))
        empty = Image.new("RGB", (200, 160), (220, 220, 220))
        with pytest.raises(CaptureError, match="could not find the testwatch render"):
            device.locate(empty)


class TestExtract:
    def test_screen_is_the_panel_at_native_resolution(self, tmp_path):
        devices = make_device(tmp_path)
        device = DeviceRender("testwatch", str(devices))
        frame = place(device.render, origin=(7, 13), screen_fill=(0, 200, 0))

        screen, _ = device.extract(frame)
        assert screen.size == (SCREEN["width"], SCREEN["height"])
        # The middle of the aperture is the face; the corners are under the rim and
        # are put back to the black a round display shows there.
        assert screen.getpixel((SCREEN["width"] // 2, SCREEN["height"] // 2)) == (
            0,
            200,
            0,
        )
        assert screen.getpixel((0, 0)) == (0, 0, 0)

    def test_watch_carries_the_frame_and_a_transparent_surround(self, tmp_path):
        devices = make_device(tmp_path)
        device = DeviceRender("testwatch", str(devices))
        frame = place(device.render, origin=(7, 13), screen_fill=(0, 200, 0))

        _, watch = device.extract(frame)
        assert watch.size == RENDER_SIZE
        assert watch.mode == "RGBA"
        assert watch.getpixel((0, 0))[3] == 0
        centre = watch.getpixel((SCREEN["x"] + 20, SCREEN["y"] + 20))
        assert centre[:3] == (0, 200, 0)
        assert centre[3] == 255

    def test_a_supplied_origin_skips_the_search(self, tmp_path):
        devices = make_device(tmp_path)
        device = DeviceRender("testwatch", str(devices))
        frame = place(device.render, origin=(7, 13), screen_fill=(9, 9, 200))

        by_search = device.extract(frame)[0].tobytes()
        by_origin = device.extract(frame, (7, 13))[0].tobytes()
        assert by_search == by_origin


def test_device_geometry_reports_the_sdk_numbers(tmp_path):
    devices = make_device(tmp_path)
    assert device_geometry("testwatch", str(devices)) == {
        "product": "testwatch",
        "render": RENDER_SIZE,
        "screen": (10, 20, 40, 40),
    }
