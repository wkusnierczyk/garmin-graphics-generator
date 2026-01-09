"""
Core logic for the Garmin Graphics Generator.
Contains the WatchHeroGenerator class which handles the fluent API pipeline.
"""
import json
import logging
import math
import os
import random
from io import BytesIO
from typing import List, Optional, Tuple

from PIL import Image
from rembg import remove

from .constants import DEFAULT_CONFIG_PATH, EXTENSION_PNG, MODE_RGBA

# Load defaults from JSON to separate data from logic
with open(DEFAULT_CONFIG_PATH, "r", encoding="utf-8") as _f:
    _DEFAULTS = json.load(_f)

# Configure logger for this module
logger = logging.getLogger(__name__)


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
            _DEFAULTS["hero_height"],
        )
        self._resized_suffix: str = _DEFAULTS["resized_suffix"]
        self._resized_width: int = _DEFAULTS["resized_width"]
        self._size_variation: int = _DEFAULTS["size_variation"]
        self._orientation_variation: int = _DEFAULTS["orientation_variation"]
        self._max_overlap: int = _DEFAULTS["max_overlap"]

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

    def set_variations(
        self, size_var: int, orientation_var: int
    ) -> "WatchHeroGenerator":
        """
        Sets variation parameters.
        :param size_var: 0-10 scale for size randomness.
        :param orientation_var: 0-90 degrees for rotation randomness.
        """
        self._size_variation = size_var
        self._orientation_variation = orientation_var
        return self

    def set_max_overlap(self, overlap_percent: int) -> "WatchHeroGenerator":
        """
        Sets the allowed overlap percentage (0-100).
        """
        self._max_overlap = max(0, min(100, overlap_percent))
        return self

    def prepare_output_directory(self) -> "WatchHeroGenerator":
        """Ensures the output directory exists."""
        if not os.path.exists(self._output_directory):
            logger.info("Creating output directory: %s", self._output_directory)
            os.makedirs(self._output_directory)
        return self

    def process_input_images(self) -> "WatchHeroGenerator":
        """
        Loads images and removes background (white -> transparent).
        Stores them in memory for the hero generation step.
        """
        self._processed_images = []
        total_files = len(self._input_paths)

        logger.info("Starting background removal for %d images...", total_files)

        for index, file_path in enumerate(self._input_paths):
            logger.info("[%d/%d] Processing: %s", index + 1, total_files, file_path)
            try:
                with open(file_path, "rb") as input_file:
                    input_data = input_file.read()
                    output_data = remove(input_data)

                    image = Image.open(BytesIO(output_data)).convert(MODE_RGBA)
                    self._processed_images.append(image)
            except (IOError, OSError) as error:
                logger.error("Failed to process %s: %s", file_path, error)

        return self

    def generate_resized_files(self) -> "WatchHeroGenerator":
        """
        Resizes the original input images (using the transparent version)
        to the specified width and saves them.
        """
        if not self._processed_images:
            return self

        logger.info("Generating individual resized images...")

        for index, image in enumerate(self._processed_images):
            original_path = self._input_paths[index]
            filename = os.path.basename(original_path)
            name, _ = os.path.splitext(filename)

            aspect_ratio = image.height / image.width
            new_height = int(self._resized_width * aspect_ratio)

            resized_image = image.resize(
                (self._resized_width, new_height), Image.Resampling.LANCZOS
            )

            output_filename = f"{name}{self._resized_suffix}{EXTENSION_PNG}"
            output_path = os.path.join(self._output_directory, output_filename)

            resized_image.save(output_path)
            logger.debug("Saved resized image: %s", output_path)

        return self

    def generate_hero_composition(self) -> "WatchHeroGenerator":
        """
        Creates the hero image by scattering watches across the canvas with collision detection.
        """
        if not self._processed_images:
            return self

        logger.info("Generating hero composition (%dx%d)...", *self._hero_size)

        hero_image = Image.new(MODE_RGBA, self._hero_size, (255, 255, 255, 0))

        images_to_place = self._processed_images[:]
        random.shuffle(images_to_place)

        # Calculate a safe scale factor to ensure all images can actually fit
        base_scale = self._calculate_auto_scale_factor(len(images_to_place))

        placed_rects: List[Tuple[int, int, int, int]] = []

        for i, base_image in enumerate(images_to_place):
            logger.debug("Placing image %d/%d...", i + 1, len(images_to_place))

            # Prepare image (rotate, scale, fit to canvas bounds)
            final_image = self._prepare_image_for_canvas(base_image, base_scale)

            # Attempt to find a non-colliding position
            position = self._find_valid_position(final_image.size, placed_rects)

            if position:
                pos_x, pos_y = position
                hero_image.paste(final_image, (pos_x, pos_y), final_image)
                placed_rects.append(
                    (pos_x, pos_y, final_image.width, final_image.height)
                )
            else:
                logger.warning(
                    "Could not place image %d after multiple attempts (too crowded). "
                    "Try increasing --overlap or reducing variations.",
                    i + 1,
                )

        output_path = os.path.join(self._output_directory, self._hero_file_name)
        hero_image.save(output_path)
        logger.info("Saved hero image: %s", output_path)

        return self

    def _calculate_auto_scale_factor(self, num_images: int) -> float:
        """
        Determines a scaling factor for input images to ensure they fit
        on the canvas without excessive overcrowding.
        """
        if num_images <= 1:
            return 1.0

        canvas_w, canvas_h = self._hero_size
        canvas_area = canvas_w * canvas_h

        # Heuristic: Aim for total image area to cover roughly 60% of canvas
        # to allow for spacing and rotation buffers.
        target_total_area = canvas_area * 0.6
        target_area_per_image = target_total_area / num_images

        # We assume images are roughly square for this estimation
        target_dim = math.sqrt(target_area_per_image)

        return target_dim

    def _prepare_image_for_canvas(
        self, base_image: Image.Image, target_dim_heuristic: float
    ) -> Image.Image:
        """
        Applies transforms and ensures the image fits within canvas dimensions
        and the heuristic target size.
        """
        # 1. First, scale down if the raw image is vastly larger than our heuristic
        # This solves the "input images larger than target" issue for batches.
        current_max = max(base_image.width, base_image.height)

        # Use chained comparison 0 < target < max
        if 0 < target_dim_heuristic < current_max:
            ratio = target_dim_heuristic / current_max
            new_w = int(base_image.width * ratio)
            new_h = int(base_image.height * ratio)
            base_image = base_image.resize((new_w, new_h), Image.Resampling.LANCZOS)

        # 2. Apply random variations
        final_image = self._apply_random_transforms(base_image)
        img_w, img_h = final_image.size
        canvas_w, canvas_h = self._hero_size

        # 3. Hard cap: Ensure it fits in canvas (100%)
        if img_w > canvas_w or img_h > canvas_h:
            ratio = min(canvas_w / img_w, canvas_h / img_h)
            img_w = int(img_w * ratio)
            img_h = int(img_h * ratio)
            # Ensure at least 1px
            img_w = max(1, img_w)
            img_h = max(1, img_h)
            final_image = final_image.resize((img_w, img_h), Image.Resampling.LANCZOS)

        return final_image

    def _find_valid_position(
        self, image_size: Tuple[int, int], placed_rects: List[Tuple[int, int, int, int]]
    ) -> Optional[Tuple[int, int]]:
        """
        Attempts to find a random position for the given image size that does not
        violate collision constraints.
        """
        img_w, img_h = image_size
        canvas_w, canvas_h = self._hero_size
        max_attempts = 100

        max_x = max(0, canvas_w - img_w)
        max_y = max(0, canvas_h - img_h)

        for _ in range(max_attempts):
            pos_x = random.randint(0, max_x)
            pos_y = random.randint(0, max_y)

            new_rect = (pos_x, pos_y, img_w, img_h)

            if not self._check_collision(new_rect, placed_rects):
                return (pos_x, pos_y)

        return None

    def _check_collision(
        self,
        new_rect: Tuple[int, int, int, int],
        placed_rects: List[Tuple[int, int, int, int]],
    ) -> bool:
        """
        Checks if new_rect overlaps with any existing rect beyond the allowed
        _max_overlap threshold.
        """
        if self._max_overlap >= 100:
            return False  # Overlap fully allowed

        for placed_rect in placed_rects:
            overlap_pct = self._calculate_overlap_percentage(new_rect, placed_rect)

            # If overlap is 0 (strict), any intersection > 0 is a collision
            if self._max_overlap == 0 and overlap_pct > 0:
                return True

            if overlap_pct > self._max_overlap:
                return True

        return False

    def _calculate_overlap_percentage(
        self, rect1: Tuple[int, int, int, int], rect2: Tuple[int, int, int, int]
    ) -> float:
        """
        Calculates the overlap percentage relative to the smaller of the two rectangles.
        Rect format: (x, y, w, h)
        """
        # Calculate intersection dimensions directly using tuple indices
        inter_x = max(rect1[0], rect2[0])
        inter_y = max(rect1[1], rect2[1])

        inter_w = min(rect1[0] + rect1[2], rect2[0] + rect2[2]) - inter_x
        inter_h = min(rect1[1] + rect1[3], rect2[1] + rect2[3]) - inter_y

        if inter_w <= 0 or inter_h <= 0:
            return 0.0

        intersection_area = inter_w * inter_h

        area1 = rect1[2] * rect1[3]
        area2 = rect2[2] * rect2[3]

        min_area = min(area1, area2)

        if min_area == 0:
            return 0.0

        return (intersection_area / min_area) * 100.0

    def _apply_random_transforms(self, image: Image.Image) -> Image.Image:
        """
        Applies random rotation and size scaling to an image.
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
