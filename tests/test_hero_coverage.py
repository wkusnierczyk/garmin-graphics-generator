"""The canvas coverage target, exposed as --coverage (#17)."""
import math
import random

import pytest
from PIL import Image

from garmin_graphics_generator import cli
from garmin_graphics_generator.core import WatchHeroGenerator

HERO_SIZE = (1440, 720)
INPUT_SIZE = (646, 887)


def old_scale_factor(generator, num_images):
    """The scale factor as it was computed before the target was a parameter."""
    if num_images <= 1:
        return 1.0
    canvas_w, canvas_h = generator._hero_size  # pylint: disable=protected-access
    return math.sqrt(canvas_w * canvas_h * 0.6 / num_images)


def write_inputs(directory, count):
    """Writes `count` distinct cut-out images, so the hero needs no rembg."""
    paths = []
    for index in range(count):
        path = directory / f"watch-{index}.png"
        image = Image.new("RGBA", INPUT_SIZE, (0, 0, 0, 0))
        image.paste((10, 10 + index * 20, 10, 255), (40, 40, 600, 840))
        image.save(path)
        paths.append(str(path))
    return paths


def hero(tmp_path, name, flags, seed=7):
    """Runs the hero command under a fixed seed and returns the written image's bytes."""
    inputs = tmp_path / "inputs"
    if not inputs.exists():
        inputs.mkdir()
        write_inputs(inputs, 4)
    out = tmp_path / name
    random.seed(seed)
    status = cli.main(
        ["hero", "-o", str(out), "-q", "-l", "20", "-s", "3", "-r", "15"]
        + flags
        + sorted(str(path) for path in inputs.iterdir())
    )
    assert status == 0
    return (out / "hero.png").read_bytes()


class TestDefault:
    """Nothing changes for a caller that does not set the coverage."""

    def test_the_default_is_sixty_percent(self):
        # pylint: disable=protected-access
        assert WatchHeroGenerator()._coverage == 60

    @pytest.mark.parametrize("count", [1, 2, 4, 5, 9])
    def test_the_default_scale_factor_is_the_old_one_exactly(self, count):
        generator = WatchHeroGenerator().set_hero_size(*HERO_SIZE)
        # pylint: disable=protected-access
        assert generator._calculate_auto_scale_factor(count) == old_scale_factor(
            generator, count
        )

    def test_the_default_hero_is_the_old_hero_to_the_byte(self, tmp_path, monkeypatch):
        current = hero(tmp_path, "current", [])
        monkeypatch.setattr(
            WatchHeroGenerator, "_calculate_auto_scale_factor", old_scale_factor
        )
        assert hero(tmp_path, "old", []) == current

    def test_sixty_given_explicitly_is_the_default(self, tmp_path):
        assert hero(tmp_path, "given", ["--coverage", "60"]) == hero(
            tmp_path, "default", []
        )


class TestScale:
    """The coverage decides how large the images are drawn."""

    def factor(self, coverage, count=5):
        generator = (
            WatchHeroGenerator().set_hero_size(*HERO_SIZE).set_coverage(coverage)
        )
        # pylint: disable=protected-access
        return generator._calculate_auto_scale_factor(count)

    def test_a_larger_coverage_gives_a_larger_scale_factor(self):
        factors = [self.factor(coverage) for coverage in (1, 30, 60, 80, 100)]
        assert all(later > earlier for earlier, later in zip(factors, factors[1:]))

    def test_the_area_scales_with_the_coverage(self):
        """Twice the coverage is twice the area per image, so sqrt(2) the edge."""
        assert self.factor(80) / self.factor(40) == pytest.approx(math.sqrt(2))

    def test_a_single_image_is_unaffected(self):
        assert self.factor(100, count=1) == self.factor(10, count=1) == 1.0

    def test_the_cli_passes_the_coverage_through(self, tmp_path, monkeypatch):
        seen = []
        real = WatchHeroGenerator.set_coverage

        def spy(generator, coverage):
            seen.append(coverage)
            return real(generator, coverage)

        monkeypatch.setattr(WatchHeroGenerator, "set_coverage", spy)
        hero(tmp_path, "dense", ["--coverage", "85"])
        assert seen == [85]

    def test_the_legacy_flat_form_takes_it_too(self, tmp_path):
        inputs = tmp_path / "inputs"
        inputs.mkdir()
        paths = write_inputs(inputs, 2)
        out = tmp_path / "out"
        assert cli.main(["-o", str(out), "-q", "--coverage", "90"] + paths) == 0
        assert (out / "hero.png").exists()


class TestValidation:
    """A coverage outside 1..100 is refused, not clamped."""

    @pytest.mark.parametrize("coverage", [1, 100])
    def test_the_bounds_are_accepted(self, coverage):
        # pylint: disable=protected-access
        assert WatchHeroGenerator().set_coverage(coverage)._coverage == coverage

    @pytest.mark.parametrize("coverage", [0, -5, 101, 250])
    def test_the_setter_refuses_out_of_range(self, coverage):
        with pytest.raises(
            ValueError, match=r"coverage must be a whole percentage from 1 to 100"
        ):
            WatchHeroGenerator().set_coverage(coverage)

    @pytest.mark.parametrize("coverage", [60.5, 60.0, True, "60", None])
    def test_the_setter_refuses_anything_but_an_int(self, coverage):
        """True is an int to Python, and would otherwise pass as a coverage of 1."""
        with pytest.raises(
            ValueError, match=r"got " + repr(coverage).replace(".", r"\.")
        ):
            WatchHeroGenerator().set_coverage(coverage)

    @pytest.mark.parametrize("value", ["0", "101", "-1", "60.5", "dense"])
    def test_the_cli_refuses_out_of_range(self, tmp_path, capsys, value):
        with pytest.raises(SystemExit) as raised:
            cli.main(["hero", "-o", str(tmp_path / "o"), "--coverage", value, "w.png"])
        assert raised.value.code == 2
        error = capsys.readouterr().err
        assert "--coverage" in error
        assert f"expected a whole number from 1 to 100, got {value}" in error
        assert not (tmp_path / "o").exists()


class TestLowerBound:
    """The lowest coverage on a small canvas asks for images under a pixel (PR #37)."""

    def test_the_lowest_coverage_on_a_small_canvas_still_renders(self, tmp_path):
        inputs = tmp_path / "inputs"
        inputs.mkdir()
        paths = write_inputs(inputs, 20)
        out = tmp_path / "out"
        random.seed(0)
        status = cli.main(
            [
                "hero",
                "-q",
                "-o",
                str(out),
                "--hero-file-size",
                "64x32",
                "--coverage",
                "1",
            ]
            + paths
        )
        assert status == 0
        assert Image.open(out / "hero.png").size == (64, 32)

    def test_a_sub_pixel_target_keeps_every_dimension_positive(self):
        generator = WatchHeroGenerator().set_hero_size(64, 32)
        image = Image.new("RGBA", INPUT_SIZE, (10, 10, 10, 255))
        # pylint: disable=protected-access
        prepared = generator._prepare_image_for_canvas(image, 1.01)
        assert prepared.width >= 1 and prepared.height >= 1
