import json
import os

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
    resample_renderer,
    splice,
    table,
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
    generator(project, devices).set_jungle("sub/icons.jungle").write_mapping()
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
