import json
import os

import pytest
from PIL import Image

from garmin_graphics_generator.constants import BEGIN_MARKER, END_MARKER
from garmin_graphics_generator.launcher_icons import (
    LauncherIconError,
    LauncherIconGenerator,
    load_renderer,
    mapping_block,
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


def test_table_from_the_committed_mapping(tmp_path):
    project, devices = make_project(tmp_path)
    generator(project, devices).generate_icons().write_mapping()
    rendered = generator(project, devices).table()
    assert "| tiny" in rendered
    assert "38 x 38" in rendered
