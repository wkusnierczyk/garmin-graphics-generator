import json
import os

import pytest
from PIL import Image

from garmin_graphics_generator import cli, sheet, survey, variants
from garmin_graphics_generator.shots import Shot
from garmin_graphics_generator.variants import VariantsError

from .test_variants import PROPERTIES, SETTINGS, STRINGS


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "project"
    (root / "resources" / "properties").mkdir(parents=True)
    (root / "resources" / "settings").mkdir()
    (root / "resources" / "strings").mkdir()
    (root / "resources" / "properties" / "properties.xml").write_text(PROPERTIES)
    (root / "resources" / "settings" / "settings.xml").write_text(SETTINGS)
    (root / "resources" / "strings" / "strings.xml").write_text(STRINGS)
    (root / "monkey.jungle").write_text("project.manifest = manifest.xml\n")
    (root / "aod.jungle").write_text("base.excludeAnnotations = real\n")
    return root


@pytest.fixture
def resources(project):
    return variants.read_resources(str(project), ["resources"])


def fake_take_builds(recorded, shade=40):
    """Stands in for the container: one solid screen per build, a shade apart."""

    def take(project, product, output_directory, work_directory, builds, **options):
        recorded["builds"] = list(builds)
        recorded["options"] = options
        captured = []
        for number, build in enumerate(builds, start=1):
            directory = os.path.join(output_directory, build.name)
            os.makedirs(directory, exist_ok=True)
            screen = os.path.join(directory, "screen-1.png")
            watch = os.path.join(directory, "watch-1.png")
            Image.new("RGBA", (60, 60), (0, shade * number % 256, 0, 255)).save(screen)
            Image.new("RGBA", (80, 80), (0, 0, 0, 0)).save(watch)
            captured.append([Shot(1, screen, watch)])
        return captured

    return take


class TestMakePlan:
    def test_sweep_is_the_default(self, resources):
        plan = survey.make_plan(resources, vary=["size"])
        assert plan.strategy == "sweep"

    def test_a_grid_varies_its_two_settings_without_being_told(self, resources):
        plan = survey.make_plan(resources, "grid", grid=["style", "size"])
        assert [s.key for s in plan.settings] == ["size", "style"]
        assert len(plan.combinations) == 6

    def test_cases_need_a_file(self, resources):
        with pytest.raises(VariantsError, match="cases file"):
            survey.make_plan(resources, "cases")


class TestParseScene:
    def test_a_name_and_a_jungle_list(self):
        assert survey.parse_scene("aod=monkey.jungle;aod.jungle") == survey.Scene(
            "aod", "monkey.jungle;aod.jungle"
        )

    @pytest.mark.parametrize("text", ["aod", "=monkey.jungle", "aod=", "a/b=x"])
    def test_malformed_scenes_are_rejected(self, text):
        with pytest.raises(VariantsError):
            survey.parse_scene(text)


class TestRunSurvey:
    def test_one_build_per_combination_per_scene(
        self, project, resources, tmp_path, monkeypatch
    ):
        recorded = {}
        monkeypatch.setattr(survey, "take_builds", fake_take_builds(recorded))
        plan = survey.make_plan(resources, vary=["style"])
        scenes = [
            survey.Scene("woken", "monkey.jungle"),
            survey.Scene("aod", "monkey.jungle;aod.jungle"),
        ]

        result = survey.run_survey(
            str(project),
            "watch",
            str(tmp_path / "out"),
            str(tmp_path / "work"),
            plan,
            resources,
            scenes,
        )

        names = [build.name for build in recorded["builds"]]
        assert names == [
            "woken/01-defaults",
            "woken/02-style-1",
            "aod/01-defaults",
            "aod/02-style-1",
        ]
        assert recorded["builds"][2].jungle == "monkey.jungle;aod.jungle"
        hollow = recorded["builds"][1].files["resources/properties/properties.xml"]
        assert '<property id="style" type="number">1</property>' in hollow
        assert recorded["options"]["count"] == survey.DEFAULT_SURVEY_COUNT
        assert result.builds == 4

        index = json.loads((tmp_path / "out" / "index.json").read_text())
        assert index["scenes"] == ["woken", "aod"]
        assert index["captures"][3]["values"]["style"] == "1"
        assert index["captures"][3]["values"]["size"] == "1"
        assert index["captures"][3]["screens"] == ["aod/02-style-1/screen-1.png"]
        assert Image.open(result.sheet_path).size[0] > 2 * 60

    def test_a_rerun_removes_what_the_last_one_wrote(
        self, project, resources, tmp_path, monkeypatch
    ):
        monkeypatch.setattr(survey, "take_builds", fake_take_builds({}))
        out = tmp_path / "out"
        scene = [survey.Scene("", "monkey.jungle")]
        survey.run_survey(
            str(project),
            "watch",
            str(out),
            str(tmp_path / "work"),
            survey.make_plan(resources, vary=["size"]),
            resources,
            scene,
        )
        (out / "mine.txt").write_text("keep")
        survey.run_survey(
            str(project),
            "watch",
            str(out),
            str(tmp_path / "work"),
            survey.make_plan(resources, vary=["style"]),
            resources,
            scene,
        )

        assert not (out / "03-size-2").exists()
        assert (out / "02-style-1").is_dir()
        assert (out / "mine.txt").exists()

    def test_a_stale_index_cannot_remove_outside_the_output(self, tmp_path):
        out = tmp_path / "out"
        out.mkdir()
        victim = tmp_path / "victim"
        victim.mkdir()
        (out / "index.json").write_text(
            json.dumps({"captures": [{"directory": "../victim"}, {"directory": ""}]})
        )
        survey._clear_previous_survey(str(out))
        assert victim.is_dir()
        assert out.is_dir()


class TestSheet:
    def test_a_grid_is_as_wide_as_its_columns(self, resources, tmp_path):
        plan = survey.make_plan(resources, "grid", grid=["style", "size"])
        images = []
        for number in range(len(plan.combinations)):
            path = tmp_path / f"{number}.png"
            Image.new("RGBA", (50, 50), (number * 30, 0, 0, 255)).save(path)
            images.append(str(path))

        path = sheet.compose(
            [sheet.Block("", plan, images)], str(tmp_path / "sheet.png"), title="t"
        )
        width, height = Image.open(path).size
        assert width == 2 * sheet.MARGIN + 2 * 50 + sheet.GAP
        assert height > 3 * 50

    def test_each_tile_shows_its_own_frame(self, resources, tmp_path):
        plan = survey.make_plan(resources, vary=["style"])
        images = []
        for number, colour in enumerate([(255, 0, 0, 255), (0, 0, 255, 255)]):
            path = tmp_path / f"{number}.png"
            Image.new("RGBA", (50, 50), colour).save(path)
            images.append(str(path))

        sheet_path = sheet.compose(
            [sheet.Block("", plan, images)], str(tmp_path / "sheet.png")
        )
        image = Image.open(sheet_path)
        # The one row has a heading above it; sample the middle of each tile.
        top = next(
            y
            for y in range(image.height)
            if image.getpixel((sheet.MARGIN + 25, y)) == (255, 0, 0)
        )
        assert image.getpixel((sheet.MARGIN + 50 + sheet.GAP + 25, top + 25)) == (
            0,
            0,
            255,
        )


class TestCommandLine:
    def run(self, monkeypatch, project, tmp_path, *extra):
        recorded = {}

        def fake(**kwargs):
            recorded.update(kwargs)
            return survey.Survey("sheet.png", "index.json", 1)

        monkeypatch.setattr(cli, "run_survey", fake)
        code = cli.main(
            [
                "shots",
                "-q",
                "-p",
                str(project),
                "-d",
                "watch",
                "-o",
                str(tmp_path / "o"),
            ]
            + list(extra)
        )
        return code, recorded

    def test_vary_turns_the_survey_on(self, monkeypatch, project, tmp_path):
        code, recorded = self.run(monkeypatch, project, tmp_path, "--vary", "size")
        assert code == 0
        assert recorded["plan"].strategy == "sweep"
        assert recorded["scenes"] == [survey.Scene("", "monkey.jungle")]
        assert recorded["count"] == survey.DEFAULT_SURVEY_COUNT

    def test_grid_and_scenes(self, monkeypatch, project, tmp_path):
        _, recorded = self.run(
            monkeypatch,
            project,
            tmp_path,
            "--grid",
            "style",
            "size",
            "--scene",
            "aod=monkey.jungle;aod.jungle",
            "-n",
            "2",
        )
        assert recorded["plan"].strategy == "grid"
        assert recorded["scenes"] == [survey.Scene("aod", "monkey.jungle;aod.jungle")]
        assert recorded["count"] == 2

    def test_a_prebuilt_prg_cannot_be_varied(self, monkeypatch, project, tmp_path):
        with pytest.raises(SystemExit):
            self.run(monkeypatch, project, tmp_path, "--vary", "size", "--prg", "x.prg")

    def test_all_is_capped(self, monkeypatch, project, tmp_path, capsys):
        with pytest.raises(SystemExit):
            self.run(
                monkeypatch,
                project,
                tmp_path,
                "--all",
                "--set",
                "hue=" + ",".join(str(n) for n in range(30)),
            )
        assert "more than 24" in capsys.readouterr().err

    def test_without_settings_arguments_it_is_a_plain_capture(
        self, monkeypatch, project, tmp_path
    ):
        called = {}
        monkeypatch.setattr(
            cli, "take_shots", lambda **kwargs: called.update(kwargs) or []
        )
        cli.main(["shots", "-q", "-p", str(project), "-d", "watch"])
        assert called["count"] == cli.DEFAULT_COUNT
