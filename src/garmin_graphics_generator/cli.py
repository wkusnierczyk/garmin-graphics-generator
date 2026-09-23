"""
CLI entry point for the Garmin Graphics Generator.
"""
import argparse
import logging
import sys
import tempfile
from importlib.metadata import PackageNotFoundError, version

from .capture import CaptureError
from .constants import DEFAULT_DEVICES_DIRECTORY
from .launcher_icons import LauncherIconError, LauncherIconGenerator, load_renderer
from .shots import (
    DEFAULT_COUNT,
    DEFAULT_IMAGE,
    DEFAULT_INTERVAL,
    DEFAULT_READY_TIMEOUT,
    DEFAULT_SCREEN,
    DEFAULT_SETTLE,
    DEFAULT_TIMEOUT,
    ShotsError,
    take_shots,
)

COMMANDS = ("hero", "icons", "shots")

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


def add_shots_arguments(parser: argparse.ArgumentParser):
    """Adds the headless capture command's arguments to a parser."""
    parser.add_argument(
        "-p",
        "--project-directory",
        default=".",
        help="Watch face project directory, the one holding monkey.jungle",
    )
    parser.add_argument(
        "-d",
        "--device",
        required=True,
        help="Product to capture, as named in manifest.xml (e.g. epix2pro47mm)",
    )
    parser.add_argument(
        "-o",
        "--output-directory",
        default=".",
        help="Where to write the screen and watch images",
    )
    parser.add_argument(
        "-n",
        "--count",
        type=int,
        default=DEFAULT_COUNT,
        help=f"How many frames to capture (default: {DEFAULT_COUNT})",
    )
    parser.add_argument(
        "-i",
        "--interval",
        type=float,
        default=DEFAULT_INTERVAL,
        help=(
            "Seconds between frames; what makes an animated face look different "
            f"in each (default: {DEFAULT_INTERVAL})"
        ),
    )
    parser.add_argument(
        "--settle",
        type=float,
        default=DEFAULT_SETTLE,
        help=(
            "Seconds to let the face run before the first frame, so a capture is "
            f"not of its opening state (default: {DEFAULT_SETTLE})"
        ),
    )
    parser.add_argument(
        "--prefix",
        default="",
        help="Prepended to every output filename",
    )
    parser.add_argument(
        "--jungle",
        default="monkey.jungle",
        help="Jungle file to build, relative to the project directory",
    )
    parser.add_argument(
        "--prg",
        help="Path inside the container to a prebuilt .prg, skipping the build",
    )
    parser.add_argument(
        "--image",
        default=DEFAULT_IMAGE,
        help="Container image carrying the SDK and the device definitions",
    )
    parser.add_argument(
        "--platform",
        help="Container platform, e.g. linux/amd64 on an arm64 machine",
    )
    parser.add_argument(
        "--screen",
        default=DEFAULT_SCREEN,
        help=(
            "Virtual display size; must be larger than the device render "
            f"(default: {DEFAULT_SCREEN})"
        ),
    )
    parser.add_argument(
        "--timezone",
        help="TZ for the container, which is the time the captured face shows",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=DEFAULT_TIMEOUT,
        help=(
            "Seconds to allow the build and simulator start, which is where an "
            f"emulated run spends its time (default: {DEFAULT_TIMEOUT})"
        ),
    )
    parser.add_argument(
        "--ready-timeout",
        type=int,
        default=DEFAULT_READY_TIMEOUT,
        help=(
            "Seconds to wait for the pushed face to appear on the simulator's "
            f"screen (default: {DEFAULT_READY_TIMEOUT})"
        ),
    )
    parser.add_argument(
        "--work-directory",
        help=(
            "Where to keep the raw framebuffers and the device definition copied "
            "out of the container; a temporary directory by default"
        ),
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
        "A CLI tool for watch face imagery: simulator screenshots, hero images "
        "and launcher icons"
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

    # Imported here rather than at module level: the hero pipeline can pull in rembg
    # and onnxruntime, which the icons command has no use for.
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


def run_shots(args, parser: argparse.ArgumentParser) -> int:
    """Captures frames from the simulator running headlessly in a container."""

    # The counts and timings are checked by run_simulator, before it starts
    # anything, and reach the caller here as a ShotsError like any other. One home
    # for the rules, rather than a copy that has to be kept in step.
    def capture(work_directory: str) -> int:
        shots = take_shots(
            project=args.project_directory,
            product=args.device,
            output_directory=args.output_directory,
            work_directory=work_directory,
            count=args.count,
            interval=args.interval,
            settle=args.settle,
            image=args.image,
            jungle=args.jungle,
            prg=args.prg,
            screen=args.screen,
            platform=args.platform,
            timezone=args.timezone,
            timeout=args.timeout,
            ready_timeout=args.ready_timeout,
            prefix=args.prefix,
        )
        if not args.silent:
            print(f"Captured {len(shots)} frame(s) of {args.device}:")
            for shot in shots:
                print(f"  {shot.screen_path}")
                print(f"  {shot.watch_path}")
        return 0

    if args.work_directory:
        return capture(args.work_directory)
    # Held only for the life of the capture: the framebuffer dumps are several
    # megabytes each and nothing downstream reads them again.
    with tempfile.TemporaryDirectory(prefix="garmin-shots-") as work_directory:
        return capture(work_directory)


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
        description=(
            "Capture watch face screenshots, and generate hero images and "
            "per-device launcher icons."
        )
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

    shots_parser = subparsers.add_parser(
        "shots", help="Capture watch face screenshots from the simulator, headlessly"
    )
    add_shots_arguments(shots_parser)

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

    if args.command == "shots":
        try:
            return run_shots(args, shots_parser)
        except (ShotsError, CaptureError) as error:
            shots_parser.error(str(error))

    if args.command == "hero":
        return run_hero(args, hero_parser)

    parser.error("a command is required: " + ", ".join(COMMANDS))
    return 2


if __name__ == "__main__":
    sys.exit(main())
