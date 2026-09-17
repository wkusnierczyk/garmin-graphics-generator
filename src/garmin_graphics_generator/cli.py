"""
CLI entry point for the Garmin Graphics Generator.
"""
import argparse
import logging
import sys
from importlib.metadata import PackageNotFoundError, version

from .constants import DEFAULT_DEVICES_DIRECTORY
from .launcher_icons import LauncherIconError, LauncherIconGenerator, load_renderer

COMMANDS = ("hero", "icons")

# Flags the top-level parser handles itself, so they are not mistaken for the start
# of a legacy flat invocation.
TOP_LEVEL_FLAGS = ("-h", "--help", "--about")


def parse_dimensions(dim_str: str) -> tuple:
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


def setup_logging(verbose: bool, silent: bool):
    """
    Configures the root logger to print to stderr.
    """
    if silent:
        level = logging.ERROR
    elif verbose:
        level = logging.DEBUG
    else:
        level = logging.INFO

    logging.basicConfig(
        level=level, format="%(levelname)s: %(message)s", stream=sys.stderr
    )


def add_verbosity(parser: argparse.ArgumentParser):
    """Adds the mutually exclusive verbosity flags to a parser."""
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "-v", "--verbose", action="store_true", help="Enable verbose output"
    )
    group.add_argument(
        "-q", "--silent", action="store_true", help="Suppress all output except errors"
    )


def add_hero_arguments(parser: argparse.ArgumentParser):
    """Adds the hero image pipeline's arguments to a parser."""
    parser.add_argument(
        "-o", "--output-directory", help="Directory to save output files"
    )

    parser.add_argument(
        "-n",
        "--hero-file-name",
        default="hero.png",
        help="Name of the compound hero file",
    )
    parser.add_argument(
        "-H",
        "--hero-file-size",
        default="1440x720",
        type=parse_dimensions,
        help="Size of hero file (WxH)",
    )
    parser.add_argument(
        "-l",
        "--overlap",
        type=int,
        default=0,
        help="0..100, percentage of allowed overlap between images",
    )

    parser.add_argument(
        "-s", "--size-variation", type=int, default=0, help="0..10, variance in size"
    )
    parser.add_argument(
        "-r",
        "--orientation-variation",
        type=int,
        default=0,
        help="0..90, max rotation angle in degrees",
    )

    parser.add_argument(
        "--resized-file-suffix",
        default="_resized",
        help="Suffix for resized input files",
    )
    parser.add_argument(
        "-w",
        "--resized-file-width",
        type=int,
        default=200,
        help="Width of resized files",
    )

    add_verbosity(parser)
    parser.add_argument("input_files", nargs="*", help="Input image files")


def add_icons_arguments(parser: argparse.ArgumentParser):
    """Adds the launcher icon generator's arguments to a parser."""
    parser.add_argument(
        "-p",
        "--project-directory",
        default=".",
        help="Watch face project directory, the one holding manifest.xml",
    )
    parser.add_argument(
        "-d",
        "--devices-directory",
        default=DEFAULT_DEVICES_DIRECTORY,
        help="SDK directory holding one compiler.json per device",
    )
    parser.add_argument(
        "-R",
        "--renderer",
        help=(
            "How to draw one icon: 'resample:<master.png>' to resample a master "
            "image, or '<file>.py[:<name>]' / '<module>:<name>' for a callable "
            "taking the edge in pixels and returning a square image of that size"
        ),
    )
    parser.add_argument(
        "--no-fallback-icon",
        action="store_true",
        help="Do not rewrite resources/drawables/launcher_icon.png",
    )

    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--check",
        action="store_true",
        help="Verify the committed icons and mapping; exit non-zero on a problem",
    )
    mode.add_argument(
        "--table",
        action="store_true",
        help="Print the product to icon size table as markdown",
    )

    add_verbosity(parser)


def print_about():
    """Prints tool information."""
    try:
        tool_version = version("garmin_graphics_generator")
    except PackageNotFoundError:
        tool_version = "unknown"

    # Using print here (stdout) as this is requested data, not log info
    print(
        "garmin-graphics-generator: "
        "A CLI tool to generate watch face hero images and launcher icons"
    )
    print(f"├─ version:   {tool_version}")
    print("├─ developer: mailto:waclaw.kusnierczyk@gmail.com")
    print("├─ source:    https://github.com/wkusnierczyk/garmin_graphics_generator")
    print("└─ licence:   MIT https://opensource.org/licenses/MIT")


def run_hero(args, parser: argparse.ArgumentParser) -> int:
    """Runs the hero image pipeline."""
    if not args.output_directory or not args.input_files:
        parser.error(
            "the following arguments are required: "
            "-o/--output-directory, input_files (unless using --about)"
        )

    # Imported here rather than at module level: the hero pipeline pulls in rembg and
    # onnxruntime, which the icons command has no use for.
    from . import WatchHeroGenerator  # pylint: disable=import-outside-toplevel

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
    return 0


def run_icons(args, parser: argparse.ArgumentParser) -> int:
    """Generates, checks or tabulates the per-device launcher icons."""
    generator = (
        LauncherIconGenerator()
        .set_project_directory(args.project_directory)
        .set_devices_directory(args.devices_directory)
        .set_fallback_icon(not args.no_fallback_icon)
    )

    if args.table:
        sys.stdout.write(generator.table())
        return 0

    if args.check:
        report = generator.check()
        # Under --silent only the failures are printed, and a clean run says nothing
        # at all: the flag promises all output except errors suppressed. The exit
        # status carries the verdict either way.
        for passed, message in report:
            if not (passed and args.silent):
                print(f"  {'OK  ' if passed else 'FAIL'}  {message}")
        failures = sum(1 for passed, _ in report if not passed)
        if failures or not args.silent:
            print(f"\n{'ALL CONSISTENT' if not failures else f'{failures} PROBLEM(S)'}")
        return 1 if failures else 0

    if not args.renderer:
        parser.error("-R/--renderer is required to generate icons")

    generator.set_renderer(load_renderer(args.renderer))
    generator.generate_icons().write_mapping()
    return 0


def normalize(argv):
    """
    Routes a legacy flat invocation to the hero command.

    The CLI was flat before the icons command existed, so
    `garmin-graphics-generator -o out a.png` has to keep working. Anything not
    starting with a known command or a top-level flag is that older form.
    """
    if argv and argv[0] not in COMMANDS and argv[0] not in TOP_LEVEL_FLAGS:
        return ["hero"] + list(argv)
    return list(argv)


def main(argv=None) -> int:
    """
    Main function to parse arguments and execute the requested command.
    """
    argv = normalize(sys.argv[1:] if argv is None else argv)

    parser = argparse.ArgumentParser(
        description="Generate watch face hero images and per-device launcher icons."
    )
    parser.add_argument(
        "--about", action="store_true", help="Print tool information and exit"
    )
    subparsers = parser.add_subparsers(dest="command")

    hero_parser = subparsers.add_parser(
        "hero", help="Generate a hero image from watch face screenshots"
    )
    add_hero_arguments(hero_parser)

    icons_parser = subparsers.add_parser(
        "icons", help="Generate per-device launcher icons and their jungle mapping"
    )
    add_icons_arguments(icons_parser)

    args = parser.parse_args(argv)

    # Configure logging immediately after parsing
    setup_logging(getattr(args, "verbose", False), getattr(args, "silent", False))

    if args.about:
        print_about()
        return 0

    if args.command == "icons":
        try:
            return run_icons(args, icons_parser)
        except LauncherIconError as error:
            icons_parser.error(str(error))

    if args.command == "hero":
        return run_hero(args, hero_parser)

    parser.error("a command is required: " + ", ".join(COMMANDS))
    return 2


if __name__ == "__main__":
    sys.exit(main())
