"""Every input in the composition, every time (#12)."""
import random

from PIL import Image

from garmin_graphics_generator.core import WatchHeroGenerator

# Big enough relative to the canvas that a single greedy pass fails often: this is
# the shape of the case that found the defect, four watch renders on a store hero.
INPUT_SIZE = (646, 887)
HERO_SIZE = (1440, 720)


def generator(count, **settings):
    """A generator holding `count` distinct opaque images, ready to compose."""
    composed = (
        WatchHeroGenerator()
        .set_hero_size(*HERO_SIZE)
        .set_variations(settings.get("size", 5), settings.get("orientation", 20))
        .set_max_overlap(settings.get("overlap", 20))
    )
    # Set directly rather than through process_input_images: the layout is what is
    # under test, and loading from disk would only slow it down.
    # pylint: disable=protected-access
    composed._processed_images = [
        Image.new("RGBA", INPUT_SIZE, (10, 10 + index * 20, 10, 255))
        for index in range(count)
    ]
    return composed


def placed(composed, seed):
    """Lays out once under a fixed seed and reports how many images landed."""
    random.seed(seed)
    # pylint: disable=protected-access
    layout = None
    scale = composed._calculate_auto_scale_factor(len(composed._processed_images))
    from garmin_graphics_generator import core

    for _ in range(core.LAYOUT_ATTEMPTS):
        layout = composed._attempt_layout(scale)
        if layout is not None:
            return len(layout)
        scale *= core.LAYOUT_SHRINK
    return len(composed._attempt_layout(scale, partial=True))


def test_every_image_is_placed_whatever_the_seed():
    """The defect was that this depended on luck: two images on one run, four on the next."""
    composed = generator(4)
    counts = {placed(composed, seed) for seed in range(30)}
    assert counts == {4}


def test_a_crowded_canvas_still_places_everything():
    composed = generator(8)
    assert placed(composed, 1) == 8


# Full size, so nothing is scaled down: six of these cannot sit on the canvas
# without touching, which is the situation an attempt has to report rather than
# quietly truncate.
UNSCALED = max(INPUT_SIZE)


def test_an_attempt_reports_failure_rather_than_a_short_layout():
    """A layout that cannot seat everything is None, so the caller can retry it whole."""
    composed = generator(6, overlap=0, orientation=0, size=0)
    random.seed(0)
    # pylint: disable=protected-access
    assert composed._attempt_layout(UNSCALED) is None


def test_a_partial_attempt_keeps_what_fits():
    composed = generator(6, overlap=0, orientation=0, size=0)
    random.seed(0)
    # pylint: disable=protected-access
    layout = composed._attempt_layout(UNSCALED, partial=True)
    assert layout is not None
    assert 0 < len(layout) < 6


def test_the_composition_contains_every_input(tmp_path):
    """End to end: the written file carries all four, not an arbitrary subset."""
    # No rotation and no size variation, so each input stays the flat colour it was
    # given: rotating or rescaling interpolates the edges into shades of it, and the
    # colours are what identify the inputs here.
    composed = (
        generator(4, orientation=0, size=0, overlap=0)
        .set_output_directory(str(tmp_path))
        .set_hero_filename("hero.png")
    )
    random.seed(3)
    composed.prepare_output_directory().generate_hero_composition()

    hero = Image.open(tmp_path / "hero.png").convert("RGBA")
    # Read as bytes rather than through getdata(), which Pillow 12 deprecates and
    # Pillow 10 -- the floor this package supports -- has no replacement for.
    raw = hero.tobytes()
    greens = {raw[index + 1] for index in range(0, len(raw), 4) if raw[index + 3]}
    assert greens == {10, 30, 50, 70}
