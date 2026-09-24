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

from .constants import DEFAULT_CONFIG_PATH, EXTENSION_PNG, MODE_RGBA

# Load defaults from JSON to separate data from logic
with open(DEFAULT_CONFIG_PATH, "r", encoding="utf-8") as _f:
    _DEFAULTS = json.load(_f)

# Configure logger for this module
logger = logging.getLogger(__name__)

# How many whole arrangements to try before settling for what fits, and how much to
# shrink the images on each retry. Measured on the case that found the defect -- four
# 646x887 watch renders on a 1440x720 canvas at 20% overlap -- over 60 seeded runs
# each: ten attempts at 4% still came up short 5 times, twenty at 8% not once. Only a
# run that has already failed several times gets anywhere near the small end, and in
# practice the first attempt succeeds, so this costs nothing on the runs that work.
LAYOUT_ATTEMPTS = 20
LAYOUT_SHRINK = 0.92


def remove(data: bytes) -> bytes:
    """
    Cuts an image out of its background, deferring the import until it is needed.

    rembg pulls in onnxruntime, which is heavy and which an already-transparent
    input -- everything `shots` produces -- never touches. Keeping the call behind
    a module-level function of our own also leaves one seam to stub in tests,
    rather than a name bound at import time.
    """
    from rembg import remove as rembg_remove  # pylint: disable=import-outside-toplevel

    return rembg_remove(data)


def has_transparency(image: Image.Image) -> bool:
    """
    Reports whether an image is already cut out of its background.

    An alpha channel alone does not say so -- a screenshot saved as RGBA is fully
    opaque -- so this asks whether anything in it is actually transparent.
    """
    if image.mode == "P" and "transparency" in image.info:
        return True
    if image.mode not in ("RGBA", "LA"):
        return False
    alpha = image.getchannel("A")
    return alpha.getextrema()[0] < 255


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
        Loads images, removing the background from any that still have one.

        An input that already carries transparency is taken as it is. That is not
        an optimisation: `shots` cuts its watch renders against the SDK's own
        artwork, which is exact, and running a segmentation model over an exact
        cutout can only degrade it. It also keeps the `shots` to `hero` path off
        rembg and onnxruntime entirely.
        """
        self._processed_images = []
        total_files = len(self._input_paths)
        pad_width = len(str(total_files))

        logger.info("Preparing %d image(s) for the hero composition...", total_files)

        for index, file_path in enumerate(self._input_paths):
            current_num = index + 1
            # Format: [ 9/39] with space padding
            logger.info(
                "[%*d/%d] Processing: %s",
                pad_width,
                current_num,
                total_files,
                file_path,
            )
            try:
                with Image.open(file_path) as opened:
                    if has_transparency(opened):
                        logger.debug("%s is already cut out", file_path)
                        self._processed_images.append(opened.convert(MODE_RGBA))
                        continue

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
        Creates the hero image by scattering watches across the canvas.

        A layout that cannot fit every image is retried whole rather than written
        short (#12). Placement is random -- in order, size and angle -- so a second
        attempt is a genuinely different arrangement, and each retry also asks for a
        little less area per image, which is what actually resolves a canvas that is
        too crowded. Giving up on one image and carrying on, which is what this did,
        made the output depend on luck: four watch renders on a 1440x720 canvas
        landed two on one run and four on the next with nothing changed between them.
        """
        if not self._processed_images:
            return self

        logger.info("Generating hero composition (%dx%d)...", *self._hero_size)

        layout = self.lay_out()

        hero_image = Image.new(MODE_RGBA, self._hero_size, (255, 255, 255, 0))
        for image, position in layout:
            hero_image.paste(image, position, image)

        output_path = os.path.join(self._output_directory, self._hero_file_name)
        hero_image.save(output_path)
        logger.info("Saved hero image: %s", output_path)

        return self

    def lay_out(self) -> List[Tuple[Image.Image, Tuple[int, int]]]:
        """
        Arranges every image, retrying whole layouts until they all fit.

        Separate from writing the file so that what is arranged can be examined
        without a hero image being saved to look at -- and so this loop exists
        once. A test that reproduced it would be testing its own copy.

        The scale shrinks *between* attempts, never after the last one, so the
        salvage layout below is made at a size that was actually tried and the
        count in the warning is the number of attempts that happened.
        """
        scale = self._calculate_auto_scale_factor(len(self._processed_images))

        for attempt in range(LAYOUT_ATTEMPTS):
            if attempt:
                scale *= LAYOUT_SHRINK
            layout = self._attempt_layout(scale)
            if layout is not None:
                if attempt:
                    logger.debug(
                        "Laid out on attempt %d, at scale %.0f", attempt + 1, scale
                    )
                return layout

        # Every attempt came up short, so take one more at the smallest size tried
        # and keep whatever fits rather than nothing. Said once, and now a
        # statement about the request rather than about a run of bad luck.
        layout = self._attempt_layout(scale, partial=True)
        logger.warning(
            "Could only place %d of %d images in %dx%d after %d attempts. "
            "Try a larger --hero-file-size, a higher --overlap, or fewer images.",
            len(layout),
            len(self._processed_images),
            *self._hero_size,
            LAYOUT_ATTEMPTS,
        )
        return layout

    def _attempt_layout(
        self, scale: float, partial: bool = False
    ) -> Optional[List[Tuple[Image.Image, Tuple[int, int]]]]:
        """
        Tries one whole arrangement of every image at the given scale.

        Returns the placements, or None if any image could not be placed -- unless
        `partial`, which keeps whatever did fit.
        """
        images_to_place = self._processed_images[:]
        random.shuffle(images_to_place)

        placed_rects: List[Tuple[int, int, int, int]] = []
        layout: List[Tuple[Image.Image, Tuple[int, int]]] = []

        for base_image in images_to_place:
            final_image = self._prepare_image_for_canvas(base_image, scale)
            position = self._find_valid_position(final_image.size, placed_rects)

            if position is None:
                if not partial:
                    return None
                continue

            layout.append((final_image, position))
            placed_rects.append(
                (position[0], position[1], final_image.width, final_image.height)
            )

        return layout

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
