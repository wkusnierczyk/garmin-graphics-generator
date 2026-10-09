"""
CLI entry point for the Garmin Graphics Generator.
"""
import argparse
import json
import logging
import sys
import tempfile
from importlib.metadata import PackageNotFoundError, version

from .capture import CaptureError
from .compose import (
    DEFAULT_IMAGE_SIZE,
    DEFAULT_MAX_KB,
    DEFAULT_MODEL,
    DEFAULT_SCREEN_MODEL,
    DEFAULT_SIZE,
    FORMATS,
    IMAGE_SIZES,
    KEY_VARIABLE,
    ComposeError,
    Gemini,
    Request,
    compose,
    prompt_for,
    read_key,
    read_truths,
)
from .constants import (
    DEFAULT_DEVICES_DIRECTORY,
    FALLBACK_ICON_PATH,
    JUNGLE_NAME,
    MANIFEST_NAME,
)
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
from .survey import DEFAULT_SURVEY_COUNT, Scene, make_plan, parse_scene, run_survey
from .variants import ALL_CAP, VariantsError, read_resources

COMMANDS = ("compose", "hero", "icons", "shots")

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
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"Dimensions must be in WxH format, got {dim_str}"
        ) from exc
    if width < 1 or height < 1:
        raise argparse.ArgumentTypeError(f"Dimensions must be positive, got {dim_str}")
    return width, height


def counted(minimum: int):
    """An argparse type for a whole number of at least ``minimum``."""

    def parse(text: str) -> int:
        try:
            value = int(text)
        except ValueError:
            value = None
        if value is None or value < minimum:
            raise argparse.ArgumentTypeError(
                f"expected a whole number of at least {minimum}, got {text}"
            )
        return value

    return parse


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
        help=(
            "Watch face project directory; the manifest, jungle, icon root and "
            "fallback icon paths are relative to it, the renderer is not"
        ),
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
        "--manifest",
        default=MANIFEST_NAME,
        help=f"Manifest the products are read from (default: {MANIFEST_NAME})",
    )
    parser.add_argument(
        "--jungle",
        help=(
            "The one jungle file the mapping is spliced into, not a build list as "
            f"for shots (default: {JUNGLE_NAME}); give it with --icon-root"
        ),
    )
    parser.add_argument(
        "--icon-root",
        help=(
            "Directory holding the resources-icon-<size>/ directories (default: the "
            "project directory); give it with --jungle"
        ),
    )
    fallback = parser.add_mutually_exclusive_group()
    fallback.add_argument(
        "--fallback-icon",
        metavar="PATH",
        help=(
            "Where to write the fallback icon, drawn at the largest size "
            f"(default: {FALLBACK_ICON_PATH})"
        ),
    )
    fallback.add_argument(
        "--no-fallback-icon",
        action="store_true",
        help="Do not write the fallback icon",
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
        help=(
            f"How many frames to capture (default: {DEFAULT_COUNT}, or "
            f"{DEFAULT_SURVEY_COUNT} per build when varying settings)"
        ),
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
        help="Jungle file to build, relative to the project directory; several "
        "separated by ';', later ones overriding earlier ones, as monkeyc takes them",
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
        help=(
            "Time zone the captured face shows: a name such as Asia/Tokyo, or a POSIX "
            "TZ rule such as JST-9"
        ),
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

    add_settings_arguments(parser)
    add_verbosity(parser)


def add_settings_arguments(parser: argparse.ArgumentParser):
    """Adds the arguments that capture a face across its settings."""
    group = parser.add_argument_group(
        "settings",
        "Capture one build per combination of settings, and lay the frames out on "
        "a labelled contact-sheet.png with an index.json. Any of these turns it on.",
    )
    strategy = group.add_mutually_exclusive_group()
    strategy.add_argument(
        "--sweep",
        dest="strategy",
        action="store_const",
        const="sweep",
        help="Vary one setting at a time, the others at their defaults (the default)",
    )
    strategy.add_argument(
        "--grid",
        nargs=2,
        metavar=("ACROSS", "DOWN"),
        help="Every pair of values of two settings, as columns by rows",
    )
    strategy.add_argument(
        "--cases",
        metavar="FILE",
        help='A JSON list of combinations, e.g. [{"timeSize": "6"}, {}]',
    )
    strategy.add_argument(
        "--all",
        dest="strategy",
        action="store_const",
        const="all",
        help=f"Every combination of the varied settings; refused above {ALL_CAP}",
    )
    group.add_argument(
        "--force",
        action="store_true",
        help=f"Allow --all above {ALL_CAP} combinations",
    )
    group.add_argument(
        "--vary",
        action="append",
        default=[],
        metavar="KEY",
        help="A property to vary; repeat for more. Default, with no --set "
        "either: every list and boolean setting",
    )
    group.add_argument(
        "--set",
        dest="assignments",
        action="append",
        default=[],
        metavar="KEY=VALUE[,VALUE...]",
        help="The values to try for a property, replacing those its setting lists",
    )
    group.add_argument(
        "--resources",
        action="append",
        metavar="DIR",
        help="A resource directory the build uses, relative to the project; repeat "
        "for more (default: resources)",
    )
    group.add_argument(
        "--scene",
        action="append",
        default=[],
        metavar="NAME=JUNGLE",
        help="Capture every combination with this jungle list, under NAME, e.g. "
        "always-on='monkey.jungle;aod.jungle'; repeat for more; replaces --jungle",
    )


def add_compose_arguments(parser: argparse.ArgumentParser):
    """Adds the model-composed hero command's arguments to a parser."""
    parser.add_argument(
        "input_files",
        nargs="+",
        help="The watch captures the hero is composed from, e.g. shots' watch-*.png",
    )
    parser.add_argument(
        "-p",
        "--prompt",
        required=True,
        metavar="FILE",
        help="Prompt template; $count, $count_word, $width and $height are filled "
        "in, and any other $name from --var",
    )
    parser.add_argument(
        "-o", "--output-directory", help="Where to write the candidates"
    )
    source = parser.add_mutually_exclusive_group()
    source.add_argument(
        "--print-prompt",
        action="store_true",
        help="Print the filled-in prompt, to paste into the Gemini app, and stop",
    )
    source.add_argument(
        "-c",
        "--candidate",
        dest="candidates",
        action="append",
        default=[],
        metavar="FILE",
        help="An image generated by hand from the prompt; repeat for more",
    )
    source.add_argument(
        "-g",
        "--generate",
        type=counted(1),
        metavar="N",
        help="Generate N candidates through the API instead. Paid: image "
        "generation has no free tier",
    )
    parser.add_argument(
        "--var",
        dest="assignments",
        action="append",
        default=[],
        metavar="NAME=VALUE",
        help="A value for a $name in the prompt; repeat for more",
    )
    parser.add_argument(
        "--vars",
        metavar="FILE",
        help="A JSON object of prompt values; --var overrides it",
    )
    parser.add_argument(
        "--reference",
        metavar="FILE",
        help="With --generate: an image showing what the screens' glyphs look like",
    )
    parser.add_argument(
        "-s",
        "--size",
        dest="sizes",
        action="append",
        type=parse_dimensions,
        metavar="WxH",
        help=f"Output size; repeat for more. The first is the one screened "
        f"(default: {DEFAULT_SIZE[0]}x{DEFAULT_SIZE[1]})",
    )
    parser.add_argument(
        "--max-kb",
        type=counted(0),
        default=DEFAULT_MAX_KB,
        help=f"Reject a candidate whose first size is larger; 0 for no limit "
        f"(default: {DEFAULT_MAX_KB}, the Connect IQ hero limit)",
    )
    parser.add_argument(
        "--format", choices=sorted(FORMATS), default="png", help="Output format"
    )
    parser.add_argument(
        "--checks",
        metavar="FILE",
        help='The face\'s screen truths, a JSON list of {"name", "question", '
        '"expect"}, put to the screening model as yes/no questions',
    )
    parser.add_argument(
        "--no-screen",
        action="store_true",
        help="Skip the screening model; only the size checks run, and no key is needed",
    )
    parser.add_argument(
        "--screen-model",
        default=DEFAULT_SCREEN_MODEL,
        help=f"Model that screens each candidate (default: {DEFAULT_SCREEN_MODEL})",
    )
    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help=f"Image model for --generate (default: {DEFAULT_MODEL})",
    )
    parser.add_argument(
        "--image-size",
        choices=IMAGE_SIZES + ("auto",),
        default=DEFAULT_IMAGE_SIZE,
        help="Size to ask --generate's model for; auto leaves it to the model, for "
        f"models that take no size (default: {DEFAULT_IMAGE_SIZE})",
    )
    parser.add_argument(
        "--key-file",
        metavar="FILE",
        help=f"File holding the API key (default: ${KEY_VARIABLE})",
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
    print("├─ source:    https://github.com/wkusnierczyk/garmin-graphics-generator")
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
        .set_fallback_path(args.fallback_icon or FALLBACK_ICON_PATH)
        .set_manifest(args.manifest)
        .set_jungle(args.jungle or JUNGLE_NAME)
        .set_icon_root(args.icon_root or "")
    )

    # An edition is a jungle and an icon root together. Either one alone falls back
    # to the shared default for the other, and generating then overwrites the shared
    # edition's icons or its mapping.
    edition = args.jungle is not None or args.icon_root is not None
    if edition and (args.jungle is None or args.icon_root is None):
        parser.error("--jungle and --icon-root are given together, or not at all")

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
    # The default fallback path is the shared edition's.
    if edition and args.fallback_icon is None and not args.no_fallback_icon:
        parser.error(
            "generating for an edition needs --fallback-icon PATH or --no-fallback-icon"
        )

    generator.set_renderer(load_renderer(args.renderer))
    generator.generate_icons().write_mapping()
    return 0


def surveying(args) -> bool:
    """Whether any settings argument was given, which makes this a survey."""
    return bool(
        args.strategy
        or args.grid
        or args.cases
        or args.vary
        or args.assignments
        or args.scene
        or args.resources
        or args.force
    )


def run_survey_command(args, work_directory: str) -> int:
    """Captures the face across its settings and writes the contact sheet."""
    if args.prg:
        raise VariantsError("--prg cannot be varied; the survey builds each variant")
    if args.force and args.strategy != "all":
        raise VariantsError("--force only lifts the cap on --all")
    if args.grid:
        strategy = "grid"
    elif args.cases:
        strategy = "cases"
    else:
        strategy = args.strategy or "sweep"
    project = args.project_directory
    resources = read_resources(project, args.resources or ["resources"])
    plan = make_plan(
        resources,
        strategy=strategy,
        vary=args.vary,
        assignments=args.assignments,
        grid=args.grid,
        cases=args.cases,
        force=args.force,
    )
    scenes = [parse_scene(text) for text in args.scene] or [Scene("", args.jungle)]
    result = run_survey(
        project=project,
        product=args.device,
        output_directory=args.output_directory,
        work_directory=work_directory,
        plan=plan,
        resources=resources,
        scenes=scenes,
        force=args.force,
        count=DEFAULT_SURVEY_COUNT if args.count is None else args.count,
        interval=args.interval,
        settle=args.settle,
        image=args.image,
        screen=args.screen,
        platform=args.platform,
        timezone=args.timezone,
        timeout=args.timeout,
        ready_timeout=args.ready_timeout,
        prefix=args.prefix,
    )
    if not args.silent:
        print(f"Captured {result.builds} build(s) of {args.device}:")
        print(f"  {result.sheet_path}")
        print(f"  {result.index_path}")
    return 0


def run_shots(args, parser: argparse.ArgumentParser) -> int:
    """Captures frames from the simulator running headlessly in a container."""

    # The counts and timings are checked by run_simulator, before it starts
    # anything, and reach the caller here as a ShotsError like any other. One home
    # for the rules, rather than a copy that has to be kept in step.
    def capture(work_directory: str) -> int:
        if surveying(args):
            return run_survey_command(args, work_directory)
        shots = take_shots(
            project=args.project_directory,
            product=args.device,
            output_directory=args.output_directory,
            work_directory=work_directory,
            count=DEFAULT_COUNT if args.count is None else args.count,
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


def compose_variables(args) -> dict:
    """The prompt values from --vars, then --var, later ones winning."""
    variables = {}
    if args.vars:
        try:
            with open(args.vars, encoding="utf-8") as stream:
                loaded = json.load(stream)
        except (OSError, ValueError) as error:
            raise ComposeError(f"cannot read {args.vars}: {error}") from error
        if not isinstance(loaded, dict):
            raise ComposeError(f"{args.vars}: expected a JSON object")
        for key, value in loaded.items():
            # A number reads in the prompt as written; true, null or a list would
            # come out as Python's spelling of it, which the template never meant.
            if isinstance(value, bool) or not isinstance(value, (str, int, float)):
                raise ComposeError(
                    f"{args.vars}: {key} must be a string or a number, got {value!r}"
                )
            variables[key] = str(value)
    for assignment in args.assignments:
        name, equals, value = assignment.partition("=")
        if not equals or not name:
            raise ComposeError(f"--var takes NAME=VALUE, got {assignment!r}")
        variables[name] = value
    return variables


def run_compose(args, parser: argparse.ArgumentParser) -> int:
    """Sizes and screens hand-made or generated hero candidates."""
    try:
        with open(args.prompt, encoding="utf-8") as stream:
            template = stream.read()
    except OSError as error:
        raise ComposeError(f"cannot read {args.prompt}: {error.strerror}") from error
    request = Request(
        inputs=args.input_files,
        prompt_template=template,
        output_directory=args.output_directory or "",
        variables=compose_variables(args),
        sources=args.candidates,
        generate=args.generate is not None,
        candidates=args.generate or 0,
        reference=args.reference,
        sizes=args.sizes or [DEFAULT_SIZE],
        max_kb=args.max_kb or None,
        image_format=args.format,
        model=args.model,
        image_size=None if args.image_size == "auto" else args.image_size,
        screen_model=None if args.no_screen else args.screen_model,
        truths=read_truths(args.checks),
    )
    if args.print_prompt:
        sys.stdout.write(prompt_for(request))
        return 0
    if not args.candidates and args.generate is None:
        parser.error("give -c/--candidate images, or --generate N, or --print-prompt")
    if not args.output_directory:
        parser.error("the following arguments are required: -o/--output-directory")
    if args.checks and args.no_screen:
        parser.error("--checks are put to the screening model, which --no-screen skips")
    if args.reference and args.generate is None:
        parser.error("--reference is sent to the image model, so needs --generate")

    client = None
    if request.generate or request.screen_model:
        client = Gemini(read_key(args.key_file))
    results = compose(request, client)

    accepted = [result for result in results if result.accepted]
    if not args.silent:
        for result in results:
            print(
                f"candidate {result.index:02d}: "
                f"{'accepted' if result.accepted else 'REJECTED'}"
            )
            if result.error:
                print(f"  {result.error}")
            for check in result.checks:
                print(
                    f"  {'OK  ' if check.passed else 'FAIL'}  {check.name}: {check.detail}"
                )
            for path in list(result.paths) + [result.sidecar]:
                print(f"  {path}")
        print(f"\n{len(accepted)} of {len(results)} accepted")
    return 0 if accepted else 1


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

    compose_parser = subparsers.add_parser(
        "compose",
        help="Size and screen hero candidates composed by an image model",
    )
    add_compose_arguments(compose_parser)

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
        except (ShotsError, CaptureError, VariantsError) as error:
            shots_parser.error(str(error))

    if args.command == "compose":
        try:
            return run_compose(args, compose_parser)
        except ComposeError as error:
            compose_parser.error(str(error))

    if args.command == "hero":
        return run_hero(args, hero_parser)

    parser.error("a command is required: " + ", ".join(COMMANDS))
    return 2


if __name__ == "__main__":
    sys.exit(main())
