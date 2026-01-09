import os
import pytest
from PIL import Image
from garmin_graphics_generator.core import WatchHeroGenerator


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
        gen
        .set_input_paths([str(img_path)])
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
