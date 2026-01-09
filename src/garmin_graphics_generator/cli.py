"""
CLI entry point for the Garmin Graphics Generator.
"""
import argparse
import sys
from importlib.metadata import PackageNotFoundError, version

from garmin_graphics_generator import WatchHeroGenerator


def parse_dimensions(dim_str: str) -> tuple[int, int]:
    """
    Parses a string in the format WxH into a tuple of integers.
    Example: '1440x720' -> (1440, 720)
    """
    try:
        width, height = map(int, dim_str.lower().split("x"))
        return width, height
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"Dimensions must be in WxH format, got {dim_str}"
        ) from exc


def main():
    """
    Main function to parse arguments and execute the generator pipeline.
    """
    parser = argparse.ArgumentParser(
        description="Generate a hero image from watch face screenshots."
    )

    parser.add_argument(
        "--about", action="store_true", help="Print tool information and exit"
    )

    parser.add_argument("--output-directory", help="Directory to save output files")
    parser.add_argument(
        "--size-variation", type=int, default=0, help="0..10, variance in size"
    )
    parser.add_argument(
        "--orientation-variation",
        type=int,
        default=0,
        help="0..90, max rotation angle in degrees",
    )
    parser.add_argument(
        "--overlap",
        type=int,
        default=0,
        help="0..100, percentage of allowed overlap between images",
    )
    parser.add_argument(
        "--hero-file-name", default="hero.png", help="Name of the compound hero file"
    )
    parser.add_argument(
        "--hero-file-size",
        default="1440x720",
        type=parse_dimensions,
        help="Size of hero file (WxH)",
    )
    parser.add_argument(
        "--resized-file-suffix",
        default="_resized",
        help="Suffix for resized input files",
    )
    parser.add_argument(
        "--resized-file-width", type=int, default=200, help="Width of resized files"
    )
    parser.add_argument("input_files", nargs="*", help="Input image files")

    args = parser.parse_args()

    if args.about:
        try:
            tool_version = version("garmin_graphics_generator")
        except PackageNotFoundError:
            tool_version = "unknown"

        print(
            "garmin-graphics-generator: "
            "A CLI tool to generate hero images from watch face screenshots"
        )
        print(f"├─ version: {tool_version}")
        print("├─ developer: mailto:waclaw.kusnierczyk@gmail.com")
        print("├─ source: https://github.com/wkusnierczyk/garmin_graphics_generator")
        print("└─ licence: MIT https://opensource.org/licenses/MIT")
        sys.exit(0)

    if not args.output_directory or not args.input_files:
        parser.error(
            "the following arguments are required: "
            "--output-directory, input_files (unless using --about)"
        )

    generator = WatchHeroGenerator()

    (
        generator.set_output_directory(args.output_directory)
        .set_input_paths(args.input_files)
        .set_hero_filename(args.hero_file_name)
        .set_hero_size(args.hero_file_size[0], args.hero_file_size[1])
        .set_resized_suffix(args.resized_file_suffix)
        .set_resized_width(args.resized_file_width)
        .set_variations(args.size_variation, args.orientation_variation)
        .set_max_overlap(args.overlap)
        .prepare_output_directory()
        .process_input_images()
        .generate_hero_composition()
        .generate_resized_files()
    )


if __name__ == "__main__":
    main()
