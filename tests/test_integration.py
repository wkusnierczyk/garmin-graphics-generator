import importlib.util
import os

import pytest
from PIL import Image

from garmin_graphics_generator.core import WatchHeroGenerator

# Only the opaque-input path needs a background removed, and so only it needs rembg.
# That is an optional dependency in practice -- the shots-to-hero path never reaches
# it -- so the test that wants it says why it skipped, and the rest of the module
# still runs.
needs_rembg = pytest.mark.skipif(
    importlib.util.find_spec("rembg") is None, reason="rembg is not installed"
)


@needs_rembg
def test_full_flow_with_generated_dummy_images(tmp_path):
    """
    Create a dummy image, run the generator, check if files exist.
    """
    input_dir = tmp_path / "inputs"
    output_dir = tmp_path / "outputs"
    input_dir.mkdir()

    # Create dummy input image
    img_path = input_dir / "test_watch.png"
    Image.new("RGB", (500, 500), "white").save(img_path)

    # Run Generator
    gen = WatchHeroGenerator()
    (
        gen.set_input_paths([str(img_path)])
        .set_output_directory(str(output_dir))
        .set_hero_filename("hero.png")
        .set_resized_suffix("_small")
        .set_resized_width(100)
        .prepare_output_directory()
        .process_input_images()
        .generate_hero_composition()
        .generate_resized_files()
    )

    # Assertions
    assert (output_dir / "hero.png").exists()
    assert (output_dir / "test_watch_small.png").exists()

    # check resize dimensions
    resized_img = Image.open(output_dir / "test_watch_small.png")
    assert resized_img.width == 100


def test_a_transparent_input_is_not_run_through_rembg(tmp_path, monkeypatch):
    """`shots` output is cut against the SDK's artwork; segmenting it again can only spoil it."""
    from garmin_graphics_generator import core

    input_dir = tmp_path / "inputs"
    input_dir.mkdir()
    cut_out = Image.new("RGBA", (40, 40), (0, 0, 0, 0))
    cut_out.paste((10, 200, 10, 255), (10, 10, 30, 30))
    path = input_dir / "watch-1.png"
    cut_out.save(path)

    def fail(_):
        raise AssertionError("background removal was run on an image that had none")

    monkeypatch.setattr(core, "remove", fail)

    generator = (
        WatchHeroGenerator()
        .set_input_paths([str(path)])
        .set_output_directory(str(tmp_path / "out"))
        .prepare_output_directory()
        .process_input_images()
    )

    # pylint: disable=protected-access
    processed = generator._processed_images
    assert len(processed) == 1
    assert processed[0].getpixel((0, 0))[3] == 0
    assert processed[0].getpixel((20, 20))[:3] == (10, 200, 10)
