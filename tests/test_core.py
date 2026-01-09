import pytest
from unittest.mock import MagicMock, patch
from PIL import Image
from garmin_graphics_generator.core import WatchHeroGenerator


@pytest.fixture
def mock_image():
    # Returns a real PIL Image to start with
    return Image.new("RGB", (100, 100), color="red")


def test_fluent_api_configuration():
    gen = WatchHeroGenerator()
    # Test that methods return self and set state
    res = (
        gen.set_output_directory("test_dir")
        .set_resized_width(300)
        .set_max_overlap(50)
    )

    assert res == gen
    # pylint: disable=protected-access
    assert gen._output_directory == "test_dir"
    assert gen._resized_width == 300
    assert gen._max_overlap == 50


def test_collision_logic():
    """
    Verifies the _check_collision logic against specific geometric scenarios.
    """
    gen = WatchHeroGenerator()

    # 1. Strict Mode (0% overlap allowed)
    gen.set_max_overlap(0)

    # Existing rect: x=0, y=0, w=100, h=100 (Area 10000)
    placed = [(0, 0, 100, 100)]

    # Case A: Totally separate (x=200) -> No collision
    assert gen._check_collision((200, 0, 100, 100), placed) is False

    # Case B: Slight overlap (x=90) -> Collision
    assert gen._check_collision((90, 0, 100, 100), placed) is True

    # 2. Lenient Mode (50% overlap allowed)
    gen.set_max_overlap(50)

    # Case C: 10% overlap
    # Intersection width = 10 (from x=90 to x=100), height=100. Area=1000.
    # Total area=10000. Overlap = 10%.
    # 10% <= 50% -> No collision (allowed)
    assert gen._check_collision((90, 0, 100, 100), placed) is False

    # Case D: 60% overlap
    # Intersection width = 60 (from x=40 to x=100). Area=6000.
    # 60% > 50% -> Collision
    assert gen._check_collision((40, 0, 100, 100), placed) is True


@patch("garmin_graphics_generator.core.remove")
@patch("builtins.open")
@patch("os.path.exists")
@patch("os.makedirs")
def test_pipeline_execution(mock_makedirs, mock_exists, mock_open, mock_remove, mock_image):
    """
    Test the full chain without actually doing heavy image processing.
    """
    mock_exists.return_value = False

    # Mock file read
    mock_file = MagicMock()
    mock_open.return_value.__enter__.return_value = mock_file
    mock_file.read.return_value = b"fake_image_data"

    # Mock rembg output
    mock_remove.return_value = b"fake_transparent_data"

    # Mock .convert() to return the same object (self).
    mock_image.convert = MagicMock(return_value=mock_image)

    # Mock .resize() to return the same object.
    mock_image.resize = MagicMock(return_value=mock_image)

    # Mock .save() to prevent disk writes.
    mock_image.save = MagicMock()

    with patch("PIL.Image.open") as mock_pil_open:
        mock_pil_open.return_value = mock_image

        gen = WatchHeroGenerator()
        gen.set_input_paths(["watch1.jpg"]) \
            .set_output_directory("out") \
            .set_max_overlap(20) \
            .prepare_output_directory() \
            .process_input_images() \
            .generate_resized_files()

        # Verify directory creation
        mock_makedirs.assert_called_with("out")

        # Verify resize logic was triggered
        assert mock_image.resize.called
