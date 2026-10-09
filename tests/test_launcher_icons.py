import json
import os
import shlex
import sys

import pytest
from PIL import Image

from garmin_graphics_generator.cli import main
from garmin_graphics_generator.constants import BEGIN_MARKER, END_MARKER
from garmin_graphics_generator.launcher_icons import (
    LauncherIconError,
    LauncherIconGenerator,
    load_renderer,
    mapping_block,
    mapping_pattern,
    read_png_size,
    rebase_specification,
    resample_renderer,
    splice,
    table,
)
from garmin_graphics_generator.readme_table import (
    ReadmeTableError,
    Table,
    anchor_line,
    read_table,
    splice_table,
)

MANIFEST = """<?xml version="1.0"?>
<iq:manifest version="3" xmlns:iq="http://www.garmin.com/xml/connectiq">
    <iq:application id="x" type="watchface">
        <iq:products>
            <iq:product id="venu3"/>
            <iq:product id="venu3s"/>
            <iq:product id="tiny"/>
        </iq:products>
    </iq:application>
</iq:manifest>
"""

SIZES = {"venu3": 70, "venu3s": 70, "tiny": 38}


def make_project(tmp_path, jungle="project.manifest = manifest.xml\n"):
    """Writes a minimal watch face project and the SDK definitions it needs."""
    project = tmp_path / "project"
    project.mkdir()
    (project / "manifest.xml").write_text(MANIFEST)
    (project / "monkey.jungle").write_text(jungle)

    devices = tmp_path / "devices"
    for product, size in SIZES.items():
        directory = devices / product
        directory.mkdir(parents=True)
        (directory / "compiler.json").write_text(
            json.dumps({"launcherIcon": {"width": size, "height": size}})
        )
    return project, devices


def flat(renderer_size):
    """A renderer drawing a plain square, so tests do not depend on artwork."""

    def render(size):
        return Image.new("RGB", (renderer_size or size, renderer_size or size), "green")

    return render


def generator(project, devices, renderer=None):
    return (
        LauncherIconGenerator()
        .set_project_directory(str(project))
        .set_devices_directory(str(devices))
        .set_renderer(renderer or flat(None))
    )


# --------------------------------------------------------------------- free helpers


def test_table_is_ordered_by_size_then_product():
    rendered = table({"b": 38, "a": 70, "c": 70}).splitlines()
    assert [line.split("|")[1].strip() for line in rendered[2:]] == ["a", "c", "b"]
    assert rendered[2].split("|")[2].strip() == "70 x 70"


def test_splice_appends_when_no_block_is_present():
    spliced = splice("project.manifest = manifest.xml\n", mapping_block({"venu3": 70}))
    assert spliced.startswith("project.manifest = manifest.xml\n")
    assert "venu3.resourcePath = $(venu3.resourcePath);resources-icon-70" in spliced


def test_splice_replaces_an_existing_block_in_place():
    first = splice("head = 1\n", mapping_block({"venu3": 70}))
    second = splice(first, mapping_block({"venu3": 60}))
    assert second.startswith("head = 1\n")
    assert "resources-icon-60" in second
    assert "resources-icon-70" not in second
    assert second.count(BEGIN_MARKER) == 1


def test_splice_replaces_a_block_written_by_another_tool():
    foreign = (
        "head = 1\n\n"
        "# BEGIN generated launcher icon mapping -- some/other/tool.py\n"
        "venu3.resourcePath = $(venu3.resourcePath);resources-icon-99\n"
        f"{END_MARKER}\n"
    )
    spliced = splice(foreign, mapping_block({"venu3": 70}))
    assert "resources-icon-99" not in spliced
    assert spliced.count("# BEGIN generated launcher icon mapping") == 1


def test_read_png_size(tmp_path):
    path = tmp_path / "x.png"
    Image.new("RGB", (38, 38)).save(path)
    assert read_png_size(str(path)) == (38, 38)


def test_read_png_size_rejects_a_non_png(tmp_path):
    path = tmp_path / "x.png"
    path.write_bytes(b"not a png at all really")
    assert read_png_size(str(path)) is None


def test_resample_renderer_returns_the_requested_size(tmp_path):
    master = tmp_path / "master.png"
    Image.new("RGB", (100, 100), "green").save(master)
    assert resample_renderer(str(master))(38).size == (38, 38)


def test_load_renderer_from_a_file(tmp_path):
    module = tmp_path / "renderer.py"
    module.write_text(
        "from PIL import Image\n"
        "def render(size):\n"
        "    return Image.new('RGB', (size, size), 'green')\n"
    )
    assert load_renderer(f"{module}")(40).size == (40, 40)


def test_load_renderer_names_the_attribute(tmp_path):
    module = tmp_path / "renderer.py"
    module.write_text(
        "from PIL import Image\n"
        "def icon(size):\n"
        "    return Image.new('RGB', (size, size), 'red')\n"
    )
    assert load_renderer(f"{module}:icon")(40).size == (40, 40)


def test_load_renderer_rejects_a_missing_file(tmp_path):
    with pytest.raises(LauncherIconError):
        load_renderer(str(tmp_path / "absent.py"))


def test_load_renderer_rejects_a_missing_attribute(tmp_path):
    module = tmp_path / "renderer.py"
    module.write_text("value = 1\n")
    with pytest.raises(LauncherIconError):
        load_renderer(f"{module}:nope")


def test_resample_renderer_on_a_master_that_is_not_square(tmp_path, caplog):
    master = tmp_path / "master.png"
    Image.new("RGB", (100, 50), "green").save(master)
    resample_renderer(str(master))
    assert "not square" in caplog.text


# ------------------------------------------------------------------------ pipeline


def test_resolve_sizes_reads_the_sdk_definitions(tmp_path):
    project, devices = make_project(tmp_path)
    assert generator(project, devices).resolve_sizes().sizes == SIZES


def test_resolve_sizes_reports_a_product_with_no_definition(tmp_path):
    project, devices = make_project(tmp_path)
    (devices / "tiny" / "compiler.json").unlink()
    with pytest.raises(LauncherIconError, match="tiny"):
        generator(project, devices).resolve_sizes()


def test_resolve_sizes_reports_a_missing_devices_directory(tmp_path):
    project, _ = make_project(tmp_path)
    with pytest.raises(LauncherIconError, match="device definitions"):
        generator(project, tmp_path / "absent").resolve_sizes()


def test_generate_writes_one_icon_per_distinct_size(tmp_path):
    project, devices = make_project(tmp_path)
    generator(project, devices).generate_icons().write_mapping()

    assert read_png_size(
        str(project / "resources-icon-70/drawables/launcher_icon.png")
    ) == (70, 70)
    assert read_png_size(
        str(project / "resources-icon-38/drawables/launcher_icon.png")
    ) == (38, 38)
    # two products share 70x70, and get one directory between them
    assert sorted(
        name for name in os.listdir(project) if name.startswith("resources-icon-")
    ) == ["resources-icon-38", "resources-icon-70"]

    declaration = (project / "resources-icon-38/drawables/drawables.xml").read_text()
    assert 'id="LauncherIcon"' in declaration


def test_generate_writes_the_fallback_at_the_largest_size(tmp_path):
    project, devices = make_project(tmp_path)
    generator(project, devices).generate_icons()
    assert read_png_size(str(project / "resources/drawables/launcher_icon.png")) == (
        70,
        70,
    )


def test_fallback_can_be_turned_off(tmp_path):
    project, devices = make_project(tmp_path)
    generator(project, devices).set_fallback_icon(False).generate_icons()
    assert not (project / "resources/drawables/launcher_icon.png").exists()


def test_generate_rejects_a_renderer_returning_the_wrong_size(tmp_path):
    project, devices = make_project(tmp_path)
    with pytest.raises(LauncherIconError, match="returned"):
        generator(project, devices, flat(12)).generate_icons()


def test_generate_rejects_a_project_without_a_manifest(tmp_path):
    project, devices = make_project(tmp_path)
    (project / "manifest.xml").unlink()
    with pytest.raises(LauncherIconError, match="manifest.xml"):
        generator(project, devices).generate_icons()


def test_mapping_is_read_back_out_of_the_jungle(tmp_path):
    project, devices = make_project(tmp_path)
    generator(project, devices).generate_icons().write_mapping()
    assert generator(project, devices).mapping() == SIZES


def test_regeneration_does_not_duplicate_the_mapping(tmp_path):
    project, devices = make_project(tmp_path)
    generator(project, devices).generate_icons().write_mapping()
    generator(project, devices).generate_icons().write_mapping()
    jungle = (project / "monkey.jungle").read_text()
    assert jungle.count(BEGIN_MARKER) == 1
    assert [
        line.split(".")[0] for line in jungle.splitlines() if ".resourcePath" in line
    ] == [
        "tiny",
        "venu3",
        "venu3s",
    ]
    assert jungle.startswith("project.manifest = manifest.xml\n")


def test_check_passes_on_a_freshly_generated_project(tmp_path):
    project, devices = make_project(tmp_path)
    generator(project, devices).generate_icons().write_mapping()
    assert all(passed for passed, _ in generator(project, devices).check())


def test_check_catches_an_icon_of_the_wrong_size(tmp_path):
    project, devices = make_project(tmp_path)
    generator(project, devices).generate_icons().write_mapping()
    Image.new("RGB", (12, 12), "green").save(
        project / "resources-icon-38/drawables/launcher_icon.png"
    )
    failures = [
        message for passed, message in generator(project, devices).check() if not passed
    ]
    assert any("promises 38x38" in message for message in failures)


def test_check_catches_an_unmapped_product(tmp_path):
    project, devices = make_project(tmp_path)
    generator(project, devices).generate_icons().write_mapping()
    jungle = project / "monkey.jungle"
    jungle.write_text(
        "\n".join(
            line
            for line in jungle.read_text().splitlines()
            if not line.startswith("tiny.")
        )
        + "\n"
    )
    failures = [
        message for passed, message in generator(project, devices).check() if not passed
    ]
    assert any("tiny" in message for message in failures)


def test_check_catches_a_declaration_naming_the_wrong_file(tmp_path):
    project, devices = make_project(tmp_path)
    generator(project, devices).generate_icons().write_mapping()
    declaration = project / "resources-icon-38/drawables/drawables.xml"
    # Still contains "LauncherIcon", so a substring check would pass it.
    declaration.write_text(
        declaration.read_text().replace("launcher_icon", "wrong_icon")
    )
    failures = [
        message for passed, message in generator(project, devices).check() if not passed
    ]
    assert any("declares LauncherIcon" in message for message in failures)


def test_check_catches_an_orphaned_icon_directory(tmp_path):
    project, devices = make_project(tmp_path)
    generator(project, devices).generate_icons().write_mapping()
    (project / "resources-icon-99" / "drawables").mkdir(parents=True)
    failures = [
        message for passed, message in generator(project, devices).check() if not passed
    ]
    assert any("resources-icon-99" in message for message in failures)


def test_check_works_without_the_sdk(tmp_path):
    project, devices = make_project(tmp_path)
    generator(project, devices).generate_icons().write_mapping()
    without_sdk = (
        LauncherIconGenerator()
        .set_project_directory(str(project))
        .set_devices_directory(str(tmp_path / "absent"))
    )
    report = without_sdk.check()
    assert all(passed for passed, _ in report)
    assert not any("launcherIcon" in message for _, message in report)


def test_table_falls_back_to_the_sdk_before_anything_is_generated(tmp_path):
    project, devices = make_project(tmp_path)
    rendered = (
        LauncherIconGenerator()
        .set_project_directory(str(project))
        .set_devices_directory(str(devices))
        .table()
    )
    assert "| tiny" in rendered
    assert len(rendered.splitlines()) == 2 + len(SIZES)


def test_table_reports_a_project_with_no_products(tmp_path):
    project, devices = make_project(tmp_path)
    (project / "manifest.xml").write_text("<iq:manifest/>")
    with pytest.raises(LauncherIconError, match="no products"):
        generator(project, devices).table()


def test_resample_renderer_does_not_hold_the_master_open(tmp_path):
    master = tmp_path / "master.png"
    Image.new("RGB", (100, 100), "green").save(master)
    renderer = resample_renderer(str(master))
    # Removing the master must not disturb a renderer already built from it, and on
    # Windows would fail outright if the handle were still open.
    os.remove(master)
    assert renderer(38).size == (38, 38)


def test_table_from_the_committed_mapping(tmp_path):
    project, devices = make_project(tmp_path)
    generator(project, devices).generate_icons().write_mapping()
    rendered = generator(project, devices).table()
    assert "| tiny" in rendered
    assert "38 x 38" in rendered


# ------------------------------------------------------------------------ editions


def make_edition(project):
    """Adds a second edition to a project: its own manifest, and a jungle layered last."""
    (project / "manifest-premium.xml").write_text(MANIFEST)
    (project / "premium.jungle").write_text("project.manifest = manifest-premium.xml\n")
    return project


def edition(project, devices, renderer=None):
    return (
        generator(project, devices, renderer)
        .set_manifest("manifest-premium.xml")
        .set_jungle("premium.jungle")
        .set_icon_root("premium")
        .set_fallback_path("premium/resources-base/drawables/launcher_icon.png")
    )


def test_mapping_block_prefixes_the_icon_root():
    block = mapping_block({"venu3": 70}, "premium")
    assert (
        "venu3.resourcePath = $(venu3.resourcePath);premium/resources-icon-70" in block
    )


def test_mapping_pattern_reads_only_its_own_root():
    shared = "venu3.resourcePath = $(venu3.resourcePath);resources-icon-70\n"
    premium = "venu3.resourcePath = $(venu3.resourcePath);premium/resources-icon-70\n"
    assert mapping_pattern().findall(shared) == [("venu3", "70")]
    assert mapping_pattern().findall(premium) == []
    assert mapping_pattern("premium").findall(premium) == [("venu3", "70")]
    assert mapping_pattern("premium").findall(shared) == []


def test_icon_root_is_normalised(tmp_path):
    project, devices = make_project(tmp_path)
    make_edition(project)
    edition(project, devices).set_icon_root(
        "./premium/"
    ).generate_icons().write_mapping()
    assert ";premium/resources-icon-70\n" in (project / "premium.jungle").read_text()


def test_edition_writes_only_under_its_own_paths(tmp_path):
    project, devices = make_project(tmp_path)
    make_edition(project)
    jungle = (project / "monkey.jungle").read_text()
    edition(project, devices).generate_icons().write_mapping()

    assert read_png_size(
        str(project / "premium/resources-icon-38/drawables/launcher_icon.png")
    ) == (38, 38)
    assert read_png_size(
        str(project / "premium/resources-base/drawables/launcher_icon.png")
    ) == (70, 70)
    assert not any(name.startswith("resources-icon-") for name in os.listdir(project))
    assert not (project / "resources").exists()
    assert (project / "monkey.jungle").read_text() == jungle
    assert (
        "tiny.resourcePath = $(tiny.resourcePath);premium/resources-icon-38"
        in (project / "premium.jungle").read_text()
    )


def test_edition_reads_products_from_its_own_manifest(tmp_path):
    project, devices = make_project(tmp_path)
    make_edition(project)
    (project / "manifest.xml").unlink()
    assert edition(project, devices).resolve_sizes().sizes == SIZES


def test_editions_coexist_and_check_independently(tmp_path):
    project, devices = make_project(tmp_path)
    make_edition(project)
    generator(project, devices).generate_icons().write_mapping()
    edition(project, devices).generate_icons().write_mapping()

    assert generator(project, devices).mapping() == SIZES
    assert edition(project, devices).mapping() == SIZES
    # Neither reports the other's icon directories as orphans.
    assert all(passed for passed, _ in generator(project, devices).check())
    assert all(passed for passed, _ in edition(project, devices).check())


def test_edition_check_rejects_an_entry_into_the_shared_root(tmp_path):
    project, devices = make_project(tmp_path)
    make_edition(project)
    generator(project, devices).generate_icons().write_mapping()
    edition(project, devices).generate_icons().write_mapping()
    jungle = project / "premium.jungle"
    jungle.write_text(
        jungle.read_text().replace(
            "$(tiny.resourcePath);premium/resources-icon-38",
            "$(tiny.resourcePath);resources-icon-38",
        )
    )
    failures = [
        message for passed, message in edition(project, devices).check() if not passed
    ]
    assert any("tiny" in message for message in failures)


def test_edition_check_names_an_orphan_by_its_root(tmp_path):
    project, devices = make_project(tmp_path)
    make_edition(project)
    edition(project, devices).generate_icons().write_mapping()
    (project / "premium" / "resources-icon-99" / "drawables").mkdir(parents=True)
    failures = [
        message for passed, message in edition(project, devices).check() if not passed
    ]
    assert any("premium/resources-icon-99" in message for message in failures)


def test_cli_targets_an_edition(tmp_path):
    project, devices = make_project(tmp_path)
    make_edition(project)
    module = tmp_path / "renderer.py"
    module.write_text(
        "from PIL import Image\n"
        "def render(size):\n"
        "    return Image.new('RGB', (size, size), 'gold')\n"
    )
    options = [
        "icons",
        "-p",
        str(project),
        "-d",
        str(devices),
        "--manifest",
        "manifest-premium.xml",
        "--jungle",
        "premium.jungle",
        "--icon-root",
        "premium",
    ]
    assert main(options + ["-R", str(module), "--no-fallback-icon", "-q"]) == 0
    assert (project / "premium/resources-icon-70/drawables/launcher_icon.png").exists()
    assert not (project / "resources").exists()
    assert main(options + ["--check", "-q"]) == 0


def test_helpers_normalise_the_directory():
    block = mapping_block({"venu3": 70}, "./premium/")
    assert ";premium/resources-icon-70\n" in block
    assert mapping_pattern("premium/").findall(block) == [("venu3", "70")]


def test_the_default_block_names_the_plain_command(tmp_path):
    project, devices = make_project(tmp_path)
    generator(project, devices).generate_icons().write_mapping()
    jungle = (project / "monkey.jungle").read_text()
    assert "# Regenerate with: garmin-graphics-generator icons\n" in jungle


def test_an_edition_block_names_the_command_that_regenerates_it(tmp_path):
    project, devices = make_project(tmp_path)
    make_edition(project)
    edition(project, devices).generate_icons().write_mapping()
    assert (
        "# Regenerate with: garmin-graphics-generator icons"
        " --manifest manifest-premium.xml --jungle premium.jungle --icon-root premium"
        " --fallback-icon premium/resources-base/drawables/launcher_icon.png\n"
    ) in (project / "premium.jungle").read_text()


def test_entries_are_relative_to_a_jungle_in_a_subdirectory(tmp_path):
    # monkeyc resolves a jungle's resource paths against the jungle's own directory.
    project, devices = make_project(tmp_path)
    make_edition(project)
    (project / "premium").mkdir()
    (project / "premium.jungle").rename(project / "premium" / "premium.jungle")
    premium = edition(project, devices).set_jungle("premium/premium.jungle")
    premium.generate_icons().write_mapping()

    jungle = (project / "premium" / "premium.jungle").read_text()
    assert "tiny.resourcePath = $(tiny.resourcePath);resources-icon-38\n" in jungle
    assert (project / "premium/resources-icon-38/drawables/launcher_icon.png").exists()
    assert premium.mapping() == SIZES
    assert all(passed for passed, _ in premium.check())


def test_a_jungle_in_a_subdirectory_reaches_the_project_root(tmp_path):
    project, devices = make_project(tmp_path)
    (project / "sub").mkdir()
    (project / "sub" / "icons.jungle").write_text("")
    generator(project, devices).set_jungle("sub/icons.jungle").set_fallback_icon(
        False
    ).write_mapping()
    jungle = (project / "sub" / "icons.jungle").read_text()
    assert "tiny.resourcePath = $(tiny.resourcePath);../resources-icon-38\n" in jungle


def test_an_absolute_icon_root_is_taken_as_it_is(tmp_path):
    project, devices = make_project(tmp_path)
    make_edition(project)
    edition(project, devices).set_icon_root(
        str(project / "premium")
    ).generate_icons().write_mapping()
    assert (project / "premium/resources-icon-38/drawables/launcher_icon.png").exists()
    assert ";premium/resources-icon-38\n" in (project / "premium.jungle").read_text()


def test_generation_refuses_to_replace_another_roots_mapping(tmp_path):
    # --icon-root given, --jungle forgotten: the shared block would be overwritten.
    project, devices = make_project(tmp_path)
    make_edition(project)
    generator(project, devices).generate_icons().write_mapping()
    shared = (project / "monkey.jungle").read_text()
    forgetful = edition(project, devices).set_jungle("monkey.jungle")
    with pytest.raises(LauncherIconError, match="would replace"):
        forgetful.generate_icons()
    assert not (project / "premium").exists()
    assert (project / "monkey.jungle").read_text() == shared


def test_a_build_list_as_the_jungle_fails_before_anything_is_written(tmp_path):
    project, devices = make_project(tmp_path)
    make_edition(project)
    premium = edition(project, devices).set_jungle("monkey.jungle;premium.jungle")
    with pytest.raises(LauncherIconError, match="not a build list"):
        premium.generate_icons()
    assert not (project / "premium").exists()


@pytest.mark.parametrize("fallback", ["premium/resources-base/drawables", ""])
def test_a_fallback_that_is_not_a_png_fails_before_anything_is_written(
    tmp_path, fallback
):
    project, devices = make_project(tmp_path)
    make_edition(project)
    (project / "premium/resources-base/drawables").mkdir(parents=True)
    premium = edition(project, devices).set_fallback_path(fallback)
    with pytest.raises(LauncherIconError, match="not a .png file"):
        premium.generate_icons()
    assert not (project / "premium/resources-icon-70").exists()


def test_check_names_a_missing_jungle(tmp_path):
    project, devices = make_project(tmp_path)
    make_edition(project)
    report = edition(project, devices).set_jungle("premuim.jungle").check()
    assert len(report) == 1
    assert not report[0][0]
    assert "no jungle at" in report[0][1]


def test_table_rejects_a_missing_jungle(tmp_path):
    project, devices = make_project(tmp_path)
    make_edition(project)
    with pytest.raises(LauncherIconError, match="no jungle at"):
        edition(project, devices).set_jungle("premuim.jungle").table()


def cli_edition(project, devices, *extra):
    return [
        "icons",
        "-p",
        str(project),
        "-d",
        str(devices),
        "--manifest",
        "manifest-premium.xml",
        *extra,
    ]


def test_cli_writes_the_fallback_where_it_is_told(tmp_path):
    project, devices = make_project(tmp_path)
    make_edition(project)
    module = tmp_path / "renderer.py"
    module.write_text(
        "from PIL import Image\n"
        "def render(size):\n"
        "    return Image.new('RGB', (size, size), 'gold')\n"
    )
    options = cli_edition(
        project, devices, "--jungle", "premium.jungle", "--icon-root", "premium"
    )
    fallback = "premium/resources-base/drawables/launcher_icon.png"
    assert main(options + ["-R", str(module), "--fallback-icon", fallback, "-q"]) == 0
    assert read_png_size(str(project / fallback)) == (70, 70)
    assert not (project / "resources").exists()


def test_cli_tabulates_an_edition(tmp_path, capsys):
    project, devices = make_project(tmp_path)
    make_edition(project)
    edition(project, devices).generate_icons().write_mapping()
    options = cli_edition(
        project, devices, "--jungle", "premium.jungle", "--icon-root", "premium"
    )
    assert main(options + ["--table"]) == 0
    assert "| tiny" in capsys.readouterr().out


@pytest.mark.parametrize(
    "extra",
    [
        ["--icon-root", "premium", "--no-fallback-icon"],
        ["--jungle", "premium.jungle", "--no-fallback-icon"],
        ["--jungle", "premium.jungle", "--icon-root", "premium"],
        [
            "--jungle",
            "premium.jungle",
            "--icon-root",
            "premium",
            "--no-fallback-icon",
            "--fallback-icon",
            "x.png",
        ],
    ],
    ids=["root-alone", "jungle-alone", "no-fallback-choice", "both-fallbacks"],
)
def test_cli_refuses_an_incomplete_edition(tmp_path, extra):
    project, devices = make_project(tmp_path)
    make_edition(project)
    with pytest.raises(SystemExit) as raised:
        main(cli_edition(project, devices, "-R", "resample:x.png", *extra))
    assert raised.value.code == 2
    assert not (project / "premium").exists()


def test_an_icon_root_with_a_space_is_quoted_and_read_back(tmp_path):
    # Unquoted, monkeyc reads a different path and builds with the shared icon.
    project, devices = make_project(tmp_path)
    make_edition(project)
    premium = edition(project, devices).set_icon_root("premium edition")
    premium.generate_icons().write_mapping()
    jungle = (project / "premium.jungle").read_text()
    assert (
        'tiny.resourcePath = $(tiny.resourcePath);"premium edition/resources-icon-38"\n'
        in jungle
    )
    assert premium.mapping() == SIZES
    assert all(passed for passed, _ in premium.check())
    # and regenerating over its own quoted block is not refused as foreign
    premium.generate_icons().write_mapping()


def test_an_unquoted_entry_with_a_space_is_not_a_mapping():
    entry = (
        "venu3.resourcePath = $(venu3.resourcePath);premium edition/resources-icon-70\n"
    )
    assert mapping_pattern("premium edition").findall(entry) == []


def test_an_icon_root_a_jungle_cannot_hold_is_refused(tmp_path):
    project, devices = make_project(tmp_path)
    make_edition(project)
    with pytest.raises(LauncherIconError, match="cannot hold"):
        edition(project, devices).set_icon_root('pre"mium').generate_icons()


def test_generation_refuses_a_block_mixing_roots(tmp_path):
    project, devices = make_project(tmp_path)
    make_edition(project)
    jungle = project / "premium.jungle"
    jungle.write_text(
        splice(jungle.read_text(), mapping_block({"venu3": 70}, "premium")).replace(
            "venu3.resourcePath = $(venu3.resourcePath);premium/resources-icon-70\n",
            "venu3.resourcePath = $(venu3.resourcePath);premium/resources-icon-70\n"
            "tiny.resourcePath = $(tiny.resourcePath);resources-icon-38\n",
        )
    )
    before = jungle.read_text()
    with pytest.raises(LauncherIconError, match="would replace"):
        edition(project, devices).generate_icons()
    assert jungle.read_text() == before


def test_a_file_where_the_icon_root_goes_fails_before_anything_is_written(tmp_path):
    project, devices = make_project(tmp_path)
    make_edition(project)
    (project / "premium").write_text("not a directory")
    premium = edition(project, devices).set_fallback_path("fallback/launcher_icon.png")
    with pytest.raises(LauncherIconError, match="is a file"):
        premium.generate_icons()
    assert not (project / "fallback").exists()


def test_a_file_where_the_fallback_goes_fails_before_anything_is_written(tmp_path):
    project, devices = make_project(tmp_path)
    make_edition(project)
    (project / "premium").mkdir()
    (project / "premium" / "resources-base").write_text("not a directory")
    with pytest.raises(LauncherIconError, match="is a file"):
        edition(project, devices).generate_icons()
    assert not (project / "premium" / "resources-icon-70").exists()


def test_the_recorded_command_reruns_for_a_jungle_in_a_subdirectory(tmp_path):
    project, devices = make_project(tmp_path)
    (project / "sub").mkdir()
    (project / "sub" / "icons.jungle").write_text("")
    generator(project, devices).set_jungle("sub/icons.jungle").set_fallback_path(
        "resources/drawables/launcher_icon.png"
    ).generate_icons().write_mapping()
    jungle = (project / "sub" / "icons.jungle").read_text()
    command = next(
        line.split("Regenerate with: ", 1)[1]
        for line in jungle.splitlines()
        if "Regenerate with" in line
    )
    assert command == (
        "garmin-graphics-generator icons --jungle sub/icons.jungle --icon-root ."
        " --fallback-icon resources/drawables/launcher_icon.png"
    )
    words = shlex.split(command)[1:]
    module = tmp_path / "renderer.py"
    module.write_text(
        "from PIL import Image\n"
        "def render(size):\n"
        "    return Image.new('RGB', (size, size), 'green')\n"
    )
    rerun = words + ["-p", str(project), "-d", str(devices), "-R", str(module), "-q"]
    assert main(rerun) == 0
    # A callable set without its specification is left out of the command; the rerun
    # gave one, which the command now records, and nothing else changes.
    assert (project / "sub" / "icons.jungle").read_text() == jungle.replace(
        "icons --jungle", f"icons -R {shlex.quote(str(module))} --jungle"
    )


def regenerate_command(jungle):
    """The command a generated block records, as it would be pasted."""
    return next(
        line.split("Regenerate with: ", 1)[1]
        for line in jungle.splitlines()
        if "Regenerate with" in line
    )


def test_a_renderer_specification_is_rebased_onto_the_project(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert rebase_specification("project/tools/icon.py", "project") == "tools/icon.py"
    assert rebase_specification("project/a.py:draw", "project") == "a.py:draw"
    assert (
        rebase_specification("resample:art/m.png", "project") == "resample:../art/m.png"
    )
    # An absolute path under the project would tie the jungle to one machine.
    inside = str(tmp_path / "project" / "icon.py")
    assert rebase_specification(inside, "project") == "icon.py"


def test_a_renderer_specification_that_reads_the_same_anywhere_is_kept(tmp_path):
    start = str(tmp_path / "project")
    assert rebase_specification("package.module:draw", start) == "package.module:draw"
    outside = str(tmp_path / "icon.py")
    assert rebase_specification(outside, start) == outside
    master = "resample:" + str(tmp_path / "m.png")
    assert rebase_specification(master, start) == master


def test_the_recorded_command_names_a_renderer_given_with_its_specification(tmp_path):
    project, devices = make_project(tmp_path)
    make_edition(project)
    edition(project, devices).set_renderer(
        flat(None), str(project / "tools" / "launcher icon.py")
    ).generate_icons().write_mapping()
    assert regenerate_command((project / "premium.jungle").read_text()) == (
        "garmin-graphics-generator icons -R 'tools/launcher icon.py'"
        " --manifest manifest-premium.xml --jungle premium.jungle --icon-root premium"
        " --fallback-icon premium/resources-base/drawables/launcher_icon.png"
    )


def test_the_recorded_command_reruns_with_its_renderer(tmp_path, monkeypatch):
    project, devices = make_project(tmp_path)
    make_edition(project)
    (project / "tools").mkdir()
    (project / "tools" / "icon.py").write_text(
        "from PIL import Image\n"
        "def render(size):\n"
        "    return Image.new('RGB', (size, size), 'green')\n"
    )
    # Generated from outside the project, with the renderer found from there ...
    monkeypatch.chdir(tmp_path)
    options = ["icons", "-p", "project", "-d", str(devices), "-q"]
    edition_options = [
        "--manifest",
        "manifest-premium.xml",
        "--jungle",
        "premium.jungle",
        "--icon-root",
        "premium",
        "--no-fallback-icon",
    ]
    assert main(options + ["-R", "project/tools/icon.py"]) == 0
    assert main(options + edition_options + ["-R", "project/tools/icon.py"]) == 0
    jungles = {
        name: (project / name).read_text()
        for name in ("monkey.jungle", "premium.jungle")
    }
    # ... and rerun from inside it, pasted as recorded.
    monkeypatch.chdir(project)
    for (name, jungle), icon in zip(
        jungles.items(), ["resources-icon-70", "premium/resources-icon-70"]
    ):
        command = regenerate_command(jungle)
        assert shlex.split(command)[2:4] == ["-R", "tools/icon.py"]
        (project / icon / "drawables" / "launcher_icon.png").unlink()
        assert main(shlex.split(command)[1:] + ["-d", str(devices), "-q"]) == 0
        assert (project / icon / "drawables" / "launcher_icon.png").exists()
        assert (project / name).read_text() == jungle


def test_the_recorded_command_quotes_a_path_with_a_space(tmp_path):
    project, devices = make_project(tmp_path)
    make_edition(project)
    edition(project, devices).set_icon_root(
        "premium edition"
    ).generate_icons().write_mapping()
    assert "--icon-root 'premium edition'" in (project / "premium.jungle").read_text()


# ------------------------------------------------------------------- the README table

ANCHOR = "Each supported product is mapped to the icon its device asks for"

README = f"""# My watch face

## Launcher icon

The size is per device.

{ANCHOR}:

The icons are generated output.

## Fonts

| Font | Size |
| :--- | ---: |
| tiny | 12   |
"""


def failures_of(report):
    return [message for passed, message in report if not passed]


def with_readme(project, devices, text=README, anchor=ANCHOR):
    (project / "README.md").write_text(text)
    return generator(project, devices).set_readme_anchor(anchor)


def with_anchor(project, devices, anchor=ANCHOR):
    return generator(project, devices).set_readme_anchor(anchor)


def test_splice_table_inserts_one_right_after_the_anchor():
    spliced = splice_table(README, ANCHOR, table(SIZES))
    assert f"{ANCHOR}:\n\n| Product | " in spliced
    assert "| tiny    | 38 x 38 |\n\nThe icons are generated output.\n" in spliced
    assert spliced.replace(table(SIZES) + "\n", "") == README


def test_splice_table_inserts_at_the_end_of_the_text():
    spliced = splice_table(f"# Face\n\n{ANCHOR}:\n", ANCHOR, table(SIZES))
    assert spliced == f"# Face\n\n{ANCHOR}:\n\n" + table(SIZES)
    assert read_table(spliced, ANCHOR).rows == {p: (s, s) for p, s in SIZES.items()}


def test_splice_table_replaces_only_the_table_under_the_anchor():
    first = splice_table(README, ANCHOR, table(SIZES))
    second = splice_table(first, ANCHOR, table({"venu3": 60}))
    assert "| venu3   | 60 x 60 |" in second
    assert "tiny    |" not in second
    # The next section's table is not this one, and is left alone.
    assert "| tiny | 12   |" in second
    assert second.count("The icons are generated output.") == 1


def test_splice_table_is_stable_on_a_second_run():
    once = splice_table(README, ANCHOR, table(SIZES))
    assert splice_table(once, ANCHOR, table(SIZES)) == once


def test_read_table_reads_only_a_table_directly_under_the_anchor():
    # Prose comes first, so the table under "## Fonts" is not it.
    assert read_table(README, ANCHOR) == Table(None, [])


def test_read_table_reads_back_what_table_writes():
    text = splice_table(README, ANCHOR, table(SIZES))
    assert read_table(text, ANCHOR) == Table({p: (s, s) for p, s in SIZES.items()}, [])


def test_read_table_reads_the_format_the_local_script_wrote():
    # The table as garmin-matrix-time's tools/make-launcher-icons.py left it.
    text = (
        f"{ANCHOR}:\n\n"
        "| Product                 |    Icon |\n"
        "| :---------------------- | ------: |\n"
        "| venu3                   | 70 x 70 |\n"
        "| instinctcrossoveramoled | 38 x 38 |\n\n"
        "The icons are generated output.\n"
    )
    assert read_table(text, ANCHOR) == Table(
        {"venu3": (70, 70), "instinctcrossoveramoled": (38, 38)}, []
    )


def test_generation_writes_the_readme_table(tmp_path):
    project, devices = make_project(tmp_path)
    with_readme(project, devices).generate_icons().write_mapping().write_readme()
    readme = (project / "README.md").read_text()
    assert table(SIZES) in readme
    assert "| tiny | 12   |" in readme
    report = with_anchor(project, devices).check()
    assert not failures_of(report)
    assert any("icon table agrees with the mapping (3)" in m for _, m in report)


def test_check_reports_each_product_the_table_disagrees_on(tmp_path):
    project, devices = make_project(tmp_path)
    with_readme(project, devices).generate_icons().write_mapping().write_readme()
    readme = project / "README.md"
    readme.write_text(
        readme.read_text()
        .replace("| tiny    | 38 x 38 |", "| tiny    | 40 x 40 |")
        .replace("| venu3s  | 70 x 70 |\n", "| gone    | 54 x 54 |\n")
    )
    failures = failures_of(with_anchor(project, devices).check())
    assert len(failures) == 1
    assert "gone (table 54 x 54, not mapped)" in failures[0]
    assert "tiny (table 40 x 40, mapping 38 x 38)" in failures[0]
    assert "venu3s (not in the table, mapping 70 x 70)" in failures[0]
    assert "venu3 (" not in failures[0]


def test_check_reports_a_missing_table(tmp_path):
    project, devices = make_project(tmp_path)
    generator(project, devices).generate_icons().write_mapping()
    (project / "README.md").write_text(README)
    failures = failures_of(with_anchor(project, devices).check())
    assert failures == [f"README.md has no icon table directly under {ANCHOR!r}"]


def test_check_reports_a_missing_anchor(tmp_path):
    project, devices = make_project(tmp_path)
    generator(project, devices).generate_icons().write_mapping()
    (project / "README.md").write_text(README)
    failures = failures_of(with_anchor(project, devices, "Nowhere").check())
    assert len(failures) == 1
    assert "'Nowhere'" in failures[0]


def test_check_reports_a_missing_readme(tmp_path):
    project, devices = make_project(tmp_path)
    generator(project, devices).generate_icons().write_mapping()
    failures = failures_of(with_anchor(project, devices).check())
    assert len(failures) == 1
    assert "no README at" in failures[0]


def test_without_an_anchor_the_readme_is_left_alone(tmp_path):
    project, devices = make_project(tmp_path)
    (project / "README.md").write_text(README)
    generator(project, devices).generate_icons().write_mapping().write_readme()
    assert (project / "README.md").read_text() == README
    report = generator(project, devices).check()
    assert not failures_of(report)
    assert not any("README" in message for _, message in report)


def test_a_missing_anchor_fails_before_anything_is_written(tmp_path):
    project, devices = make_project(tmp_path)
    (project / "README.md").write_text(README)
    with pytest.raises(LauncherIconError, match="--readme-anchor"):
        with_readme(project, devices, anchor="Nowhere").generate_icons()
    assert not (project / "resources").exists()
    assert not any(name.startswith("resources-icon-") for name in os.listdir(project))


def test_editions_keep_tables_of_their_own(tmp_path):
    project, devices = make_project(tmp_path)
    make_edition(project)
    (project / "README.md").write_text(README + "\n## Premium\n\nPremium's icons:\n")
    with_anchor(project, devices).generate_icons().write_mapping().write_readme()
    edition(project, devices).set_readme_anchor(
        "Premium's icons"
    ).generate_icons().write_mapping().write_readme()
    assert (project / "README.md").read_text().count(table(SIZES)) == 2
    assert not failures_of(with_anchor(project, devices).check())
    assert not failures_of(
        edition(project, devices).set_readme_anchor("Premium's icons").check()
    )


def test_cli_maintains_and_checks_the_readme_table(tmp_path, capsys):
    project, devices = make_project(tmp_path)
    (project / "README.md").write_text(README)
    master = tmp_path / "master.png"
    Image.new("RGB", (100, 100), "green").save(master)
    options = ["icons", "-p", str(project), "-d", str(devices)]
    anchor = ["--readme-anchor", ANCHOR]
    assert main(options + anchor + ["-R", f"resample:{master}", "-q"]) == 0
    assert table(SIZES) in (project / "README.md").read_text()
    # The recorded command keeps the README in step when it is rerun.
    assert f"--readme-anchor '{ANCHOR}'" in (project / "monkey.jungle").read_text()

    capsys.readouterr()
    assert main(options + anchor + ["--check"]) == 0
    assert "README.md's icon table agrees" in capsys.readouterr().out

    readme = project / "README.md"
    readme.write_text(readme.read_text().replace("38 x 38", "40 x 40"))
    assert main(options + anchor + ["--check", "-q"]) == 1
    assert main(options + ["--check", "-q"]) == 0


def test_cli_reads_the_readme_it_is_told(tmp_path):
    project, devices = make_project(tmp_path)
    (project / "docs").mkdir()
    (project / "docs" / "icons.md").write_text(README)
    generator(project, devices).set_readme_anchor(
        ANCHOR, "docs/icons.md"
    ).generate_icons().write_mapping().write_readme()
    assert not (project / "README.md").exists()
    assert "--readme docs/icons.md" in (project / "monkey.jungle").read_text()
    options = ["icons", "-p", str(project), "-d", str(devices), "--check", "-q"]
    assert main(options + ["--readme", "docs/icons.md", "--readme-anchor", ANCHOR]) == 0


# ---------------------------------------------------------------- the fallback icon

FALLBACK = "resources/drawables/launcher_icon.png"


def test_check_passes_the_fallback_at_the_largest_size(tmp_path):
    project, devices = make_project(tmp_path)
    generator(project, devices).generate_icons().write_mapping()
    report = generator(project, devices).check()
    assert any(passed and "is the 70x70 fallback" in m for passed, m in report)


def test_check_catches_a_missing_fallback(tmp_path):
    project, devices = make_project(tmp_path)
    generator(project, devices).generate_icons().write_mapping()
    (project / FALLBACK).unlink()
    failures = failures_of(generator(project, devices).check())
    assert failures == [f"{os.path.join(str(project), FALLBACK)} is missing"]


def test_check_catches_a_stale_fallback(tmp_path):
    # As an SDK update raising the largest size would leave it.
    project, devices = make_project(tmp_path)
    generator(project, devices).generate_icons().write_mapping()
    Image.new("RGB", (60, 60), "green").save(project / FALLBACK)
    failures = failures_of(generator(project, devices).check())
    assert len(failures) == 1
    assert "is 60x60, the largest size mapped is 70x70" in failures[0]


def test_check_catches_a_fallback_that_is_not_a_png(tmp_path):
    project, devices = make_project(tmp_path)
    generator(project, devices).generate_icons().write_mapping()
    (project / FALLBACK).write_bytes(b"version https://git-lfs.github.com/spec/v1\n")
    failures = failures_of(generator(project, devices).check())
    assert len(failures) == 1
    assert "is not a PNG" in failures[0]


def test_check_sizes_the_fallback_from_the_mapping_without_the_sdk(tmp_path):
    project, devices = make_project(tmp_path)
    generator(project, devices).generate_icons().write_mapping()
    Image.new("RGB", (60, 60), "green").save(project / FALLBACK)
    failures = failures_of(generator(project, tmp_path / "absent").check())
    assert len(failures) == 1
    assert "70x70" in failures[0]


def test_check_skips_the_fallback_when_it_is_not_written(tmp_path):
    project, devices = make_project(tmp_path)
    generator(project, devices).set_fallback_icon(
        False
    ).generate_icons().write_mapping()
    report = generator(project, devices).set_fallback_icon(False).check()
    assert not failures_of(report)
    assert not any("largest size mapped" in message for _, message in report)


def test_cli_checks_an_editions_fallback_only_when_named(tmp_path):
    project, devices = make_project(tmp_path)
    make_edition(project)
    edition(project, devices).generate_icons().write_mapping()
    options = cli_edition(
        project, devices, "--jungle", "premium.jungle", "--icon-root", "premium"
    )
    fallback = "premium/resources-base/drawables/launcher_icon.png"
    # Not the shared edition's fallback, which this project does not have.
    assert main(options + ["--check", "-q"]) == 0
    assert main(options + ["--check", "-q", "--fallback-icon", fallback]) == 0
    (project / fallback).unlink()
    assert main(options + ["--check", "-q", "--fallback-icon", fallback]) == 1


# -------------------------------------------------------------------- the renderer


def test_load_renderer_leaves_no_bytecode_beside_the_file(tmp_path):
    tools = tmp_path / "tools"
    tools.mkdir()
    (tools / "icon.py").write_text(
        "from PIL import Image\n"
        "def render(size):\n"
        "    return Image.new('RGB', (size, size), 'green')\n"
    )
    before = sys.dont_write_bytecode
    assert load_renderer(str(tools / "icon.py"))(38).size == (38, 38)
    assert os.listdir(tools) == ["icon.py"]
    assert sys.dont_write_bytecode == before


# ------------------------------------------- which table, and which anchor, is meant

TABLE_TEXT = table(SIZES)
ROWS = {p: (s, s) for p, s in SIZES.items()}


def test_an_unrelated_table_later_in_the_section_is_left_alone():
    text = (
        f"{ANCHOR}:\n\nThe settings, for comparison:\n\n"
        "| Setting | Default |\n| :------ | ------: |\n| size    |      12 |\n"
    )
    spliced = splice_table(text, ANCHOR, TABLE_TEXT)
    assert spliced == f"{ANCHOR}:\n\n" + TABLE_TEXT + "\n" + text[len(ANCHOR) + 3 :]
    assert read_table(spliced, ANCHOR) == Table(ROWS, [])
    # Before generation, the check finds no table rather than that one.
    assert read_table(text, ANCHOR).rows is None


def test_a_fence_and_a_stale_table_below_it_are_not_the_table():
    stale = "| Product | Icon |\n| :--- | ---: |\n| tiny | 40 x 40 |\n"
    text = f"{ANCHOR}:\n\n```bash\n# regenerate\nmake icons\n```\n\n" + stale
    assert read_table(text, ANCHOR).rows is None
    spliced = splice_table(text, ANCHOR, TABLE_TEXT)
    assert spliced == f"{ANCHOR}:\n\n" + TABLE_TEXT + "\n" + text[len(ANCHOR) + 3 :]
    assert read_table(spliced, ANCHOR) == Table(ROWS, [])


def test_the_anchor_inside_a_fence_or_mid_line_does_not_count():
    text = (
        "```bash\n"
        f"{ANCHOR}\n"
        "```\n"
        f'garmin-graphics-generator icons --readme-anchor "{ANCHOR}"\n\n'
        f"{ANCHOR}:\n\n" + TABLE_TEXT
    )
    assert anchor_line(text.split("\n"), ANCHOR) == 5
    assert read_table(text, ANCHOR) == Table(ROWS, [])


def test_the_anchor_may_be_a_heading():
    text = "# Face\n\n## Icon sizes\n\n" + TABLE_TEXT
    assert read_table(text, "Icon sizes") == Table(ROWS, [])


def test_an_anchor_beginning_two_lines_is_refused():
    text = f"{ANCHOR}:\n\n{TABLE_TEXT}\n{ANCHOR}, again.\n"
    with pytest.raises(ReadmeTableError, match=r"2 lines begin with .*\(lines 1, 9\)"):
        splice_table(text, ANCHOR, TABLE_TEXT)
    with pytest.raises(ReadmeTableError, match="make the anchor unique"):
        read_table(text, ANCHOR)


def test_check_refuses_an_ambiguous_anchor(tmp_path):
    project, devices = make_project(tmp_path)
    generator(project, devices).generate_icons().write_mapping()
    (project / "README.md").write_text(f"{ANCHOR}:\n\n{TABLE_TEXT}\n{ANCHOR}.\n")
    failures = failures_of(with_anchor(project, devices).check())
    assert len(failures) == 1
    assert "make the anchor unique" in failures[0]


def test_check_reports_unreadable_and_repeated_rows(tmp_path):
    project, devices = make_project(tmp_path)
    with_readme(project, devices).generate_icons().write_mapping().write_readme()
    readme = project / "README.md"
    readme.write_text(
        readme.read_text().replace(
            "| venu3s  | 70 x 70 |\n",
            "| venu3s  | 70 x 70 |\n| venu3s  | 70 x 70 |\n| `old`   | 40 × 40 |\n",
        )
    )
    failures = failures_of(with_anchor(project, devices).check())
    assert len(failures) == 1
    assert "venu3s is listed more than once" in failures[0]
    assert "unreadable row '| `old`   | 40 × 40 |'" in failures[0]


def test_a_table_without_a_separator_is_reported():
    text = f"{ANCHOR}:\n\n| Product | Icon |\n| tiny | 38 x 38 |\n"
    assert read_table(text, ANCHOR).problems == ["no header and separator row"]


def test_the_readme_keeps_its_line_endings(tmp_path):
    project, devices = make_project(tmp_path)
    (project / "README.md").write_bytes(README.replace("\n", "\r\n").encode())
    with_anchor(project, devices).generate_icons().write_mapping().write_readme()
    written = (project / "README.md").read_bytes()
    assert b"\r\n" in written
    assert written.count(b"\n") == written.count(b"\r\n")
    assert (TABLE_TEXT.replace("\n", "\r\n")).encode() in written
    assert not failures_of(with_anchor(project, devices).check())


# ------------------------------------------------------ the edition fallback default


def test_a_library_edition_has_no_fallback_until_given_one(tmp_path):
    project, devices = make_project(tmp_path)
    make_edition(project)
    unset = (
        generator(project, devices)
        .set_manifest("manifest-premium.xml")
        .set_jungle("premium.jungle")
        .set_icon_root("premium")
    )
    with pytest.raises(LauncherIconError, match="--fallback-icon PATH"):
        unset.generate_icons()
    assert not (project / "premium").exists()

    edition(project, devices).generate_icons().write_mapping()
    # A shared fallback of another size is not the edition's, and is not checked.
    (project / "resources" / "drawables").mkdir(parents=True)
    Image.new("RGB", (12, 12)).save(project / FALLBACK)
    report = unset.check()
    assert not failures_of(report)
    assert not any("largest size mapped" in message for _, message in report)


def test_a_shared_fallback_elsewhere_is_checked_where_it_is(tmp_path):
    project, devices = make_project(tmp_path)
    elsewhere = "resources/icons/launcher_icon.png"
    generator(project, devices).set_fallback_path(
        elsewhere
    ).generate_icons().write_mapping()
    failures = failures_of(generator(project, devices).check())
    assert failures == [f"{os.path.join(str(project), FALLBACK)} is missing"]
    report = generator(project, devices).set_fallback_path(elsewhere).check()
    assert not failures_of(report)


# ------------------------------------------------------------------- line endings


def test_mixed_line_endings_are_kept_outside_the_table(tmp_path):
    project, devices = make_project(tmp_path)
    original = b"# A\nline lf\r\nIcon sizes:\nafter crlf\r\nafter lf\n"
    (project / "README.md").write_bytes(original)
    with_anchor(project, devices, "Icon sizes").generate_icons().write_mapping()
    with_anchor(project, devices, "Icon sizes").write_readme()
    written = (project / "README.md").read_bytes()
    # The anchor's line ends in "\n", and so do the table's.
    inserted = b"\n" + TABLE_TEXT.encode() + b"\n"
    assert written == original.replace(b"Icon sizes:\n", b"Icon sizes:\n" + inserted)
    assert not failures_of(with_anchor(project, devices, "Icon sizes").check())


def test_the_table_ends_its_lines_as_the_anchor_does():
    text = "# A\nIcon sizes:\r\n\r\n| old |\r\nafter\n"
    spliced = splice_table(text, "Icon sizes", TABLE_TEXT)
    assert spliced == (
        "# A\nIcon sizes:\r\n\r\n" + TABLE_TEXT.replace("\n", "\r\n") + "after\n"
    )
    assert read_table(spliced, "Icon sizes") == Table(ROWS, [])


def test_a_fence_closes_in_crlf_text():
    text = "```\r\nIcon sizes in code\r\n```\r\nIcon sizes per device:\r\n"
    spliced = splice_table(text, "Icon sizes", TABLE_TEXT)
    assert spliced == (text + "\r\n" + TABLE_TEXT.replace("\n", "\r\n"))
    assert read_table(spliced, "Icon sizes") == Table(ROWS, [])


# ------------------------------------------------------------------ an empty anchor


@pytest.mark.parametrize("anchor", ["", "   "], ids=["empty", "blank"])
def test_an_empty_anchor_is_refused_not_ignored(anchor):
    with pytest.raises(LauncherIconError, match="is empty"):
        LauncherIconGenerator().set_readme_anchor(anchor)
    with pytest.raises(ReadmeTableError, match="is empty"):
        read_table(f"{ANCHOR}:\n\n{TABLE_TEXT}", anchor)


@pytest.mark.parametrize("anchor", ["", "   "], ids=["empty", "blank"])
def test_cli_fails_on_an_empty_anchor_over_a_stale_table(tmp_path, anchor):
    project, devices = make_project(tmp_path)
    with_readme(project, devices).generate_icons().write_mapping().write_readme()
    readme = project / "README.md"
    readme.write_text(readme.read_text().replace("38 x 38", "40 x 40"))
    options = ["icons", "-p", str(project), "-d", str(devices), "--check", "-q"]
    assert main(options + ["--readme-anchor", ANCHOR]) == 1
    with pytest.raises(SystemExit) as raised:
        main(options + ["--readme-anchor", anchor])
    assert raised.value.code != 0
