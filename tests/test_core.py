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
    res = gen.set_output_directory("test_dir") \
        .set_resized_width(300)

    assert res == gen
    # pylint: disable=protected-access
    assert gen._output_directory == "test_dir"
    assert gen._resized_width == 300


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

    # --- FIXES ---
    # 1. Mock .convert() to return the same object (self).
    mock_image.convert = MagicMock(return_value=mock_image)

    # 2. Mock .resize() to return the same object.
    mock_image.resize = MagicMock(return_value=mock_image)

    # 3. Mock .save() to prevent disk writes.
    mock_image.save = MagicMock()

    with patch("PIL.Image.open") as mock_pil_open:
        mock_pil_open.return_value = mock_image

        gen = WatchHeroGenerator()
        gen.set_input_paths(["watch1.jpg"]) \
            .set_output_directory("out") \
            .prepare_output_directory() \
            .process_input_images() \
            .generate_resized_files()

        # Verify directory creation
        mock_makedirs.assert_called_with("out")

        # Verify resize logic was triggered
        assert mock_image.resize.called
