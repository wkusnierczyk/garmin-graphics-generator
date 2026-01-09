"""
Core logic for the Garmin Graphics Generator.
Contains the WatchHeroGenerator class which handles the fluent API pipeline.
"""
import json
import random
import os
import logging
from io import BytesIO
from typing import List, Tuple
from PIL import Image
from rembg import remove

# Correct relative import
from .constants import (
    DEFAULT_CONFIG_PATH,
    EXTENSION_PNG,
    MODE_RGBA
)

# Load defaults from JSON to separate data from logic
with open(DEFAULT_CONFIG_PATH, "r", encoding="utf-8") as _f:
    _DEFAULTS = json.load(_f)


class WatchHeroGenerator:
    """
    A fluent API class to process watch images, remove backgrounds,
    resize them, and generate a hero composition.
    """

    # pylint: disable=too-many-instance-attributes
    def __init__(self):
        self._input_paths: List[str] = []
        self._output_directory: str = _DEFAULTS["output_directory"]
        self._hero_file_name: str = _DEFAULTS["hero_filename"]
        self._hero_size: Tuple[int, int] = (
            _DEFAULTS["hero_width"],
            _DEFAULTS["hero_height"]
        )
        self._resized_suffix: str = _DEFAULTS["resized_suffix"]
        self._resized_width: int = _DEFAULTS["resized_width"]
        self._size_variation: int = _DEFAULTS["size_variation"]
        self._orientation_variation: int = _DEFAULTS["orientation_variation"]

        # Internal state
        self._processed_images: List[Image.Image] = []

    def set_input_paths(self, file_paths: List[str]) -> "WatchHeroGenerator":
        """Sets the list of input image paths."""
        self._input_paths = file_paths
        return self

    def set_output_directory(self, path: str) -> "WatchHeroGenerator":
        """Sets the directory where output files will be saved."""
        self._output_directory = path
        return self

    def set_hero_filename(self, filename: str) -> "WatchHeroGenerator":
        """Sets the filename for the generated hero image."""
        self._hero_file_name = filename
        return self

    def set_hero_size(self, width: int, height: int) -> "WatchHeroGenerator":
        """Sets the dimensions (width, height) of the hero image."""
        self._hero_size = (width, height)
        return self

    def set_resized_suffix(self, suffix: str) -> "WatchHeroGenerator":
        """Sets the suffix to append to resized individual images."""
        self._resized_suffix = suffix
        return self

    def set_resized_width(self, width: int) -> "WatchHeroGenerator":
        """Sets the target width for resized individual images."""
        self._resized_width = width
        return self

    def set_variations(self, size_var: int, orientation_var: int) -> "WatchHeroGenerator":
        """
        Sets variation parameters.
        :param size_var: 0-10 scale for size randomness.
        :param orientation_var: 0-90 degrees for rotation randomness.
        """
        self._size_variation = size_var
        self._orientation_variation = orientation_var
        return self

    def prepare_output_directory(self) -> "WatchHeroGenerator":
        """Ensures the output directory exists."""
        if not os.path.exists(self._output_directory):
            os.makedirs(self._output_directory)
        return self

    def process_input_images(self) -> "WatchHeroGenerator":
        """
        Loads images and removes background (white -> transparent).
        Stores them in memory for the hero generation step.
        """
        self._processed_images = []
        for file_path in self._input_paths:
            try:
                with open(file_path, "rb") as input_file:
                    input_data = input_file.read()
                    # Remove background using rembg
                    output_data = remove(input_data)

                    image = Image.open(BytesIO(output_data)).convert(MODE_RGBA)
                    self._processed_images.append(image)
            except (IOError, OSError) as error:
                logging.error("Error processing %s: %s", file_path, error)

        return self

    def generate_resized_files(self) -> "WatchHeroGenerator":
        """
        Resizes the original input images (using the transparent version)
        to the specified width and saves them.
        """
        for index, image in enumerate(self._processed_images):
            original_path = self._input_paths[index]
            filename = os.path.basename(original_path)
            name, _ = os.path.splitext(filename)

            aspect_ratio = image.height / image.width
            new_height = int(self._resized_width * aspect_ratio)

            resized_image = image.resize(
                (self._resized_width, new_height),
                Image.Resampling.LANCZOS
            )

            output_filename = f"{name}{self._resized_suffix}{EXTENSION_PNG}"
            output_path = os.path.join(self._output_directory, output_filename)

            resized_image.save(output_path)
            print(f"Saved resized image: {output_path}")

        return self

    def generate_hero_composition(self) -> "WatchHeroGenerator":
        """
        Creates the hero image by scattering watches across the canvas.
        """
        if not self._processed_images:
            return self

        # Transparent background
        hero_image = Image.new(MODE_RGBA, self._hero_size, (255, 255, 255, 0))

        images_to_place = self._processed_images[:]
        random.shuffle(images_to_place)

        for base_image in images_to_place:
            self._process_and_place_image(hero_image, base_image)

        output_path = os.path.join(self._output_directory, self._hero_file_name)
        hero_image.save(output_path)
        print(f"Saved hero image: {output_path}")

        return self

    def _apply_random_transforms(self, image: Image.Image) -> Image.Image:
        """
        Applies random rotation and size scaling to an image based on
        configured variation parameters.
        """
        # 1. Orientation
        angle = 0
        if self._orientation_variation > 0:
            angle = random.uniform(
                -self._orientation_variation, self._orientation_variation
            )

        transformed = image.rotate(
            angle, expand=True, resample=Image.Resampling.BICUBIC
        )

        # 2. Scaling
        scale_factor = 1.0
        if self._size_variation > 0:
            max_scale = 1.0 + (self._size_variation * 0.2)
            min_scale = max(0.2, 1.0 - (self._size_variation * 0.05))
            scale_factor = random.uniform(min_scale, max_scale)

        if scale_factor != 1.0:
            cur_w, cur_h = transformed.size
            new_w = int(cur_w * scale_factor)
            new_h = int(cur_h * scale_factor)

            if new_w > 0 and new_h > 0:
                transformed = transformed.resize(
                    (new_w, new_h), Image.Resampling.LANCZOS
                )

        return transformed

    def _process_and_place_image(self, hero_image: Image.Image, base_image: Image.Image):
        """
        Orchestrates the transformation and placement of a single image onto the canvas.
        """
        # Get transformed image (rotated and scaled)
        final_image = self._apply_random_transforms(base_image)

        img_w, img_h = final_image.size
        canvas_w, canvas_h = self._hero_size

        max_x = canvas_w - img_w
        max_y = canvas_h - img_h

        # 3. Fit to Canvas (if random scaling made it too big)
        if max_x < 0 or max_y < 0:
            ratio = min(canvas_w / img_w, canvas_h / img_h)
            new_w = int(img_w * ratio)
            new_h = int(img_h * ratio)

            # Ensure at least 1px
            new_w = max(1, new_w)
            new_h = max(1, new_h)

            final_image = final_image.resize((new_w, new_h), Image.Resampling.LANCZOS)

            # Recalculate margins
            max_x = canvas_w - new_w
            max_y = canvas_h - new_h

        # 4. Random Position (Scatter)
        # Ensure we don't pass negative range to randint
        pos_x = random.randint(0, max(0, max_x))
        pos_y = random.randint(0, max(0, max_y))

        hero_image.paste(final_image, (pos_x, pos_y), final_image)
