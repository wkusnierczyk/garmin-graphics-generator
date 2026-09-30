"""
Capturing a watch face across its settings, as a labelled contact sheet.

This is ``shots`` run over a `variants.Plan`: one build per combination of settings
(and per scene), all in one container, then one sheet with every frame labelled by
what it shows, and ``index.json`` saying which file is which, so a face's own
tooling can pick frames out without parsing directory names.

A *scene* is a jungle list, named. A face that draws a separate always-on screen
usually needs a build-time switch to show it in the simulator -- a jungle that
forces the low-power path -- and the tool should not have to know any face's
gating to capture it. So each scene is captured with every combination, and the
sheet has a block per scene, headed with its name.
"""
import json
import os
import shutil
from typing import Dict, List, NamedTuple, Optional, Sequence

from . import sheet, variants
from .shots import Build, take_builds

SHEET_NAME = "contact-sheet.png"
INDEX_NAME = "index.json"
# A survey captures one frame per combination unless asked for more: the sheet
# shows the first, and every extra frame is more simulator time per build.
DEFAULT_SURVEY_COUNT = 1


class Scene(NamedTuple):
    """A named jungle list; ``name`` is ``""`` for a survey of a single scene."""

    name: str
    jungle: str


def parse_scene(text: str) -> Scene:
    """``NAME=JUNGLE[;JUNGLE...]`` as a scene."""
    name, separator, jungle = text.partition("=")
    name, jungle = name.strip(), jungle.strip()
    if not separator or not name or not jungle:
        raise variants.VariantsError(f"expected NAME=JUNGLE for a scene, not {text!r}")
    if os.sep in name or name.startswith("."):
        raise variants.VariantsError(f"a scene name is a directory name, not {name!r}")
    return Scene(name, jungle)


class Survey(NamedTuple):
    """Where a survey's results went."""

    sheet_path: str
    index_path: str
    builds: int


def make_plan(
    resources: variants.Resources,
    strategy: str = variants.SWEEP,
    vary: Sequence[str] = (),
    assignments: Sequence[str] = (),
    grid: Optional[Sequence[str]] = None,
    cases: Optional[str] = None,
    force: bool = False,
) -> variants.Plan:
    """The plan for a strategy, from the settings the project declares."""
    if strategy == variants.CASES:
        if not cases:
            raise variants.VariantsError("the cases strategy needs a cases file")
        if vary or assignments:
            # A case names its values itself; a --vary or --set beside it would
            # look like it applied and be ignored.
            raise variants.VariantsError("--cases takes its values from the file only")
        return variants.plan_cases(resources, cases)
    if strategy == variants.GRID:
        if not grid or len(grid) != 2:
            raise variants.VariantsError("a grid names two settings: ACROSS DOWN")
        # --vary and --set may narrow the grid's own two settings, and nothing
        # else: a third setting has no place on two axes, and would be dropped.
        extra = [
            key
            for key in list(vary)
            + [variants.parse_assignment(a)[0] for a in assignments]
            if key not in grid
        ]
        if extra:
            raise variants.VariantsError(
                f"a grid varies {grid[0]} and {grid[1]} only, not {', '.join(extra)}"
            )
        chosen = variants.choose_settings(resources, list(grid), assignments)
        return variants.plan_grid(chosen, across=grid[0], down=grid[1])
    chosen = variants.choose_settings(resources, vary, assignments)
    if strategy == variants.ALL:
        return variants.plan_all(chosen, force=force)
    if strategy == variants.SWEEP:
        return variants.plan_sweep(chosen)
    raise variants.VariantsError(f"no strategy {strategy!r}")


def _build_name(scene: Scene, number: int, combination: Dict[str, str]) -> str:
    name = f"{number:02d}-{variants.slug(combination)}"
    return os.path.join(scene.name, name) if scene.name else name


def _clear_previous_survey(output_directory: str) -> None:
    """
    Removes the directories an earlier survey listed in its index.

    Names carry a combination's values, so a survey of different settings writes
    different directories, and the old ones would sit beside the new ones looking
    like part of this run. Only what the old index names is removed.
    """
    index_path = os.path.join(output_directory, INDEX_NAME)
    try:
        with open(index_path, "r", encoding="utf-8") as index_file:
            previous = json.load(index_file)
    except (OSError, ValueError):
        return
    root = os.path.abspath(output_directory)
    if not isinstance(previous, dict):
        return
    for entry in previous.get("captures", []):
        if not isinstance(entry, dict):
            continue
        directory = os.path.abspath(os.path.join(root, entry.get("directory", "")))
        # Never outside the output directory, whatever a stale index says.
        if directory.startswith(root + os.sep) and os.path.isdir(directory):
            shutil.rmtree(directory)
    for scene in previous.get("scenes", []):
        if not isinstance(scene, str):
            continue
        directory = os.path.join(output_directory, scene)
        if scene and os.path.isdir(directory) and not os.listdir(directory):
            os.rmdir(directory)


def run_survey(
    project: str,
    product: str,
    output_directory: str,
    work_directory: str,
    plan: variants.Plan,
    resources: variants.Resources,
    scenes: Sequence[Scene],
    force: bool = False,
    **options,
) -> Survey:
    """
    Captures every combination of ``plan`` in every scene, and lays them out.

    `--all`'s cap is checked here as well as in the plan, against the builds
    rather than the combinations, because every scene multiplies them.
    """
    names = [scene.name for scene in scenes]
    if len(set(names)) != len(names):
        raise variants.VariantsError(
            "two scenes share a name, and would write over each other's frames"
        )
    total = len(plan.combinations) * len(scenes)
    if plan.strategy == variants.ALL and total > variants.ALL_CAP and not force:
        raise variants.VariantsError(
            f"{total} builds across {len(scenes)} scenes is more than "
            f"{variants.ALL_CAP}; narrow them with --vary, or pass --force"
        )
    project = os.path.abspath(os.path.expanduser(project))
    output_directory = os.path.abspath(os.path.expanduser(output_directory))
    os.makedirs(output_directory, exist_ok=True)
    _clear_previous_survey(output_directory)

    overlays = variants.overlay_files(project, resources, plan.combinations)
    builds: List[Build] = []
    for scene in scenes:
        for number, (combination, files) in enumerate(
            zip(plan.combinations, overlays), start=1
        ):
            builds.append(
                Build(
                    _build_name(scene, number, combination),
                    scene.jungle,
                    files or None,
                )
            )

    options.setdefault("count", DEFAULT_SURVEY_COUNT)
    captured = take_builds(
        project=project,
        product=product,
        output_directory=output_directory,
        work_directory=work_directory,
        builds=builds,
        **options,
    )

    blocks = []
    captures = []
    per_scene = len(plan.combinations)
    for position, scene in enumerate(scenes):
        shots = captured[position * per_scene : (position + 1) * per_scene]
        scene_builds = builds[position * per_scene : (position + 1) * per_scene]
        blocks.append(
            sheet.Block(scene.name, plan, [frames[0].screen_path for frames in shots])
        )
        for combination, build, frames in zip(plan.combinations, scene_builds, shots):
            values = {
                key: combination.get(key, resources.properties[key].default)
                for key in resources.properties
            }
            captures.append(
                {
                    "scene": scene.name,
                    "jungle": scene.jungle,
                    "directory": build.name,
                    "changed": combination,
                    "values": values,
                    "screens": [
                        os.path.relpath(f.screen_path, output_directory) for f in frames
                    ],
                    "watches": [
                        os.path.relpath(f.watch_path, output_directory) for f in frames
                    ],
                }
            )

    sheet_path = sheet.compose(
        blocks,
        os.path.join(output_directory, SHEET_NAME),
        title=f"{product}: {plan.strategy} of "
        + ", ".join(setting.key for setting in plan.settings),
    )
    index_path = os.path.join(output_directory, INDEX_NAME)
    with open(index_path, "w", encoding="utf-8") as index_file:
        json.dump(
            {
                "device": product,
                "strategy": plan.strategy,
                "settings": [setting.key for setting in plan.settings],
                "scenes": [scene.name for scene in scenes],
                "sheet": SHEET_NAME,
                "captures": captures,
            },
            index_file,
            indent=2,
        )
        index_file.write("\n")
    return Survey(sheet_path, index_path, len(builds))
