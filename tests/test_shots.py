import os
import shutil
import sys
from types import SimpleNamespace

import pytest
from PIL import Image

from garmin_graphics_generator import shots
from garmin_graphics_generator.capture import DeviceRender
from garmin_graphics_generator.shots import (
    ShotsError,
    cut_frames,
    resolve_timezone,
    run_simulator,
)

from .test_capture import SCREEN, make_device, make_xwd, place


def make_frames(tmp_path, devices, count=3, product="testwatch"):
    """Writes `count` framebuffer dumps of the device, each showing a different face."""
    device = DeviceRender(product, str(devices))
    directory = tmp_path / "frames"
    directory.mkdir()
    paths = []
    for index in range(1, count + 1):
        frame = place(device.render, origin=(7, 13), screen_fill=(0, 40 * index, 0))
        path = directory / f"frame-{index}.xwd"
        path.write_bytes(make_xwd(frame))
        paths.append(str(path))
    return paths


def make_project(tmp_path, jungle="monkey.jungle"):
    project = tmp_path / "project"
    project.mkdir()
    (project / jungle).write_text("project.manifest = manifest.xml\n")
    return project


class TestCutFrames:
    def test_writes_a_screen_and_a_watch_for_each_frame(self, tmp_path):
        devices = make_device(tmp_path)
        frames = make_frames(tmp_path, devices)
        output = tmp_path / "out"

        cut = cut_frames(frames, "testwatch", str(devices), str(output))

        assert len(cut) == 3
        for index, shot in enumerate(cut, start=1):
            assert shot.index == index
            assert os.path.basename(shot.screen_path) == f"screen-{index}.png"
            assert os.path.basename(shot.watch_path) == f"watch-{index}.png"
            assert Image.open(shot.screen_path).size == (
                SCREEN["width"],
                SCREEN["height"],
            )
            assert Image.open(shot.watch_path).mode == "RGBA"

    def test_each_frame_keeps_its_own_content(self, tmp_path):
        devices = make_device(tmp_path)
        frames = make_frames(tmp_path, devices)

        cut = cut_frames(frames, "testwatch", str(devices), str(tmp_path / "out"))
        middles = [
            Image.open(shot.screen_path).getpixel(
                (SCREEN["width"] // 2, SCREEN["height"] // 2)
            )
            for shot in cut
        ]
        assert middles == [(0, 40, 0), (0, 80, 0), (0, 120, 0)]

    def test_a_prefix_is_prepended_to_every_name(self, tmp_path):
        devices = make_device(tmp_path)
        frames = make_frames(tmp_path, devices, count=1)

        cut = cut_frames(
            frames, "testwatch", str(devices), str(tmp_path / "out"), prefix="Matrix"
        )
        assert os.path.basename(cut[0].watch_path) == "Matrixwatch-1.png"

    def test_the_output_directory_is_created(self, tmp_path):
        devices = make_device(tmp_path)
        frames = make_frames(tmp_path, devices, count=1)
        output = tmp_path / "deep" / "out"

        cut = cut_frames(frames, "testwatch", str(devices), str(output))
        assert os.path.isfile(cut[0].screen_path)

    def test_a_window_that_moved_is_found_again(self, tmp_path):
        """The offset is reused between frames, but not trusted blindly."""
        devices = make_device(tmp_path)
        device = DeviceRender("testwatch", str(devices))
        directory = tmp_path / "frames"
        directory.mkdir()

        first = place(device.render, origin=(7, 13), screen_fill=(0, 50, 0))
        moved = place(device.render, origin=(20, 30), screen_fill=(0, 90, 0))
        paths = []
        for index, frame in enumerate((first, moved), start=1):
            path = directory / f"frame-{index}.xwd"
            path.write_bytes(make_xwd(frame))
            paths.append(str(path))

        cut = cut_frames(paths, "testwatch", str(devices), str(tmp_path / "out"))

        # Compared against the crop taken at the offset this frame actually has,
        # pixel for pixel. Sampling the middle instead passes either way: a crop at
        # the stale offset is still mostly screen, so its centre is still the fill
        # colour while the picture around it is shifted by however far the window
        # went. That is what this test used to do, and it proved nothing.
        expected, _ = device.extract(moved)
        assert Image.open(cut[1].screen_path).tobytes() == expected.tobytes()


class TestStaleState:
    def test_output_from_a_longer_run_is_not_left_behind(self, tmp_path):
        """Otherwise the documented watch-*.png glob picks up the previous capture."""
        devices = make_device(tmp_path)
        output = tmp_path / "out"

        cut_frames(
            make_frames(tmp_path, devices, count=3),
            "testwatch",
            str(devices),
            str(output),
        )
        assert (output / "watch-3.png").exists()

        shutil.rmtree(tmp_path / "frames")
        cut_frames(
            make_frames(tmp_path, devices, count=1),
            "testwatch",
            str(devices),
            str(output),
        )

        assert (output / "watch-1.png").exists()
        assert not (output / "watch-2.png").exists()
        assert not (output / "watch-3.png").exists()

    def test_other_files_in_the_output_directory_are_left_alone(self, tmp_path):
        devices = make_device(tmp_path)
        output = tmp_path / "out"
        output.mkdir()
        keep = output / "MatrixTimeHero.png"
        keep.write_bytes(b"not mine to remove")
        (output / "watch-9.png").write_bytes(b"mine")

        cut_frames(
            make_frames(tmp_path, devices, count=1),
            "testwatch",
            str(devices),
            str(output),
        )

        assert keep.read_bytes() == b"not mine to remove"
        assert not (output / "watch-9.png").exists()

    def test_a_prefixed_run_does_not_clear_another_prefix(self, tmp_path):
        devices = make_device(tmp_path)
        output = tmp_path / "out"
        frames = make_frames(tmp_path, devices, count=1)

        cut_frames(frames, "testwatch", str(devices), str(output), prefix="a-")
        cut_frames(frames, "testwatch", str(devices), str(output), prefix="b-")

        assert (output / "a-watch-1.png").exists()
        assert (output / "b-watch-1.png").exists()

    def test_a_stale_status_does_not_stand_in_for_this_run(self, tmp_path, monkeypatch):
        """A reused --work-directory would report ready before anything started."""
        monkeypatch.setattr(shots, "docker_available", lambda: True)
        project = make_project(tmp_path)
        work = tmp_path / "work"
        work.mkdir()
        (work / shots.STATUS_NAME).write_text("ready\n")
        (work / "frame-7.xwd").write_bytes(b"stale")
        (work / shots.DEVICE_SUBDIRECTORY).mkdir()

        def stop(*_args, **_kwargs):
            raise RuntimeError("stop here")

        monkeypatch.setattr(shots.subprocess, "run", stop)
        with pytest.raises(RuntimeError):
            run_simulator(str(project), "testwatch", str(work))

        assert not (work / shots.STATUS_NAME).exists()
        assert not (work / "frame-7.xwd").exists()
        assert not (work / shots.DEVICE_SUBDIRECTORY).exists()


class TestRunSimulatorArguments:
    def test_reports_a_missing_docker(self, tmp_path, monkeypatch):
        monkeypatch.setattr(shots, "docker_available", lambda: False)
        with pytest.raises(ShotsError, match="Docker is not available"):
            run_simulator(str(tmp_path), "testwatch", str(tmp_path / "work"))

    def test_reports_a_missing_project(self, tmp_path, monkeypatch):
        monkeypatch.setattr(shots, "docker_available", lambda: True)
        with pytest.raises(ShotsError, match="no such project directory"):
            run_simulator(str(tmp_path / "nosuch"), "testwatch", str(tmp_path / "work"))

    def test_reports_a_directory_that_is_not_a_project(self, tmp_path, monkeypatch):
        monkeypatch.setattr(shots, "docker_available", lambda: True)
        project = tmp_path / "project"
        project.mkdir()
        with pytest.raises(ShotsError, match="has no monkey.jungle"):
            run_simulator(str(project), "testwatch", str(tmp_path / "work"))

    @pytest.mark.parametrize(
        "settings, message",
        [
            ({"count": 0}, "count must be at least 1"),
            ({"interval": -1}, "interval cannot be negative"),
            ({"settle": -0.5}, "settle cannot be negative"),
            ({"timeout": 0}, "timeout must be positive"),
            ({"ready_timeout": -5}, "ready_timeout must be positive"),
        ],
    )
    def test_timings_are_rejected_before_anything_starts(
        self, tmp_path, monkeypatch, settings, message
    ):
        """A negative interval only reaches time.sleep after a build and a launch."""
        started = []
        monkeypatch.setattr(shots, "docker_available", lambda: started.append("docker"))
        monkeypatch.setattr(
            shots.subprocess, "run", lambda *a, **k: started.append("run")
        )
        project = make_project(tmp_path)

        with pytest.raises(ShotsError, match=message):
            run_simulator(str(project), "testwatch", str(tmp_path / "work"), **settings)
        assert started == []

    def test_a_prebuilt_prg_needs_no_jungle(self, tmp_path, monkeypatch):
        """Nothing is compiled, so the directory need not be a project at all."""
        monkeypatch.setattr(shots, "docker_available", lambda: True)
        bare = tmp_path / "bare"
        bare.mkdir()
        recorded = {}

        def fake_run(command, **_):
            recorded["command"] = command
            raise RuntimeError("stop here")

        monkeypatch.setattr(shots.subprocess, "run", fake_run)
        with pytest.raises(RuntimeError):
            run_simulator(
                str(bare), "testwatch", str(tmp_path / "work"), prg="/tmp/built.prg"
            )
        assert "PRG=/tmp/built.prg" in recorded["command"]

    def test_an_alternative_jungle_is_honoured(self, tmp_path, monkeypatch):
        """A repo carrying a second edition names its own jungle."""
        monkeypatch.setattr(shots, "docker_available", lambda: True)
        project = make_project(tmp_path, jungle="pro.jungle")
        recorded = {}

        def fake_run(command, **_):
            recorded["command"] = command
            raise RuntimeError("stop here")

        monkeypatch.setattr(shots.subprocess, "run", fake_run)
        with pytest.raises(RuntimeError):
            run_simulator(
                str(project), "testwatch", str(tmp_path / "work"), jungle="pro.jungle"
            )
        assert "JUNGLE_1=pro.jungle" in recorded["command"]

    def test_a_list_of_jungles_reaches_the_compiler_whole(self, tmp_path, monkeypatch):
        """monkeyc -f takes several files; the value is passed through as one string."""
        monkeypatch.setattr(shots, "docker_available", lambda: True)
        project = make_project(tmp_path)
        (project / "capture.jungle").write_text("base.excludeAnnotations = real\n")
        recorded = {}

        def fake_run(command, **_):
            recorded["command"] = command
            raise RuntimeError("stop here")

        monkeypatch.setattr(shots.subprocess, "run", fake_run)
        with pytest.raises(RuntimeError):
            run_simulator(
                str(project),
                "testwatch",
                str(tmp_path / "work"),
                jungle="monkey.jungle;capture.jungle",
            )
        assert "JUNGLE_1=monkey.jungle;capture.jungle" in recorded["command"]

    def test_every_jungle_in_a_list_is_checked(self, tmp_path, monkeypatch):
        """The missing one is named, not the list: that is what the caller has to fix."""
        monkeypatch.setattr(shots, "docker_available", lambda: True)
        project = make_project(tmp_path)
        with pytest.raises(ShotsError, match="has no capture.jungle"):
            run_simulator(
                str(project),
                "testwatch",
                str(tmp_path / "work"),
                jungle="monkey.jungle;capture.jungle",
            )

    def test_an_empty_jungle_is_rejected(self, tmp_path, monkeypatch):
        """Nothing to build, and no filename to report as missing either."""
        monkeypatch.setattr(shots, "docker_available", lambda: True)
        project = make_project(tmp_path)
        with pytest.raises(ShotsError, match="jungle is empty"):
            run_simulator(
                str(project), "testwatch", str(tmp_path / "work"), jungle="  "
            )


def make_zone(directory, name, rule, version=b"2"):
    """Writes a TZif file whose footer is `rule`, with binary noise before it."""
    path = directory.joinpath(*name.split("/"))
    path.parent.mkdir(parents=True, exist_ok=True)
    body = b"TZif" + version + b"\0" * 15 + b"\x0a\x00\xff\x0a" * 4
    if version != b"\0":
        body += b"\n" + rule.encode("ascii") + b"\n"
    path.write_bytes(body)
    return path


class TestResolveTimezone:
    """The tester image has no zone database, so a name has to become a rule."""

    @pytest.mark.parametrize(
        "name, rule",
        [
            ("Asia/Tokyo", "JST-9"),
            ("America/New_York", "EST5EDT,M3.2.0,M11.1.0"),
            ("Asia/Kolkata", "IST-5:30"),
            ("UTC", "UTC0"),
        ],
    )
    def test_a_name_becomes_its_zone_files_rule(self, tmp_path, name, rule):
        make_zone(tmp_path, name, rule)
        assert resolve_timezone(name, [str(tmp_path)]) == rule

    def test_glibcs_leading_colon_is_a_name_too(self, tmp_path):
        make_zone(tmp_path, "Asia/Tokyo", "JST-9")
        assert resolve_timezone(":Asia/Tokyo", [str(tmp_path)]) == "JST-9"

    def test_the_first_database_that_has_the_zone_wins(self, tmp_path):
        first, second = tmp_path / "first", tmp_path / "second"
        first.mkdir()
        make_zone(second, "Asia/Tokyo", "JST-9")
        assert resolve_timezone("Asia/Tokyo", [str(first), str(second)]) == "JST-9"

    @pytest.mark.parametrize(
        "rule",
        [
            "EST5",
            "MSK-3",
            "EST5EDT,M3.2.0,M11.1.0",
            "<+0530>-5:30",
            "CET-1CEST,M3.5.0,M10.5.0/3",
        ],
    )
    def test_a_rule_passes_through(self, tmp_path, rule):
        assert resolve_timezone(rule, [str(tmp_path)]) == rule

    @pytest.mark.parametrize(
        "value", ["Mars/Olympus", "Asia", "../Asia/Tokyo", "/Asia/Tokyo", "", "Asia/"]
    )
    def test_an_unknown_name_is_an_error_not_utc(self, tmp_path, value):
        make_zone(tmp_path, "Asia/Tokyo", "JST-9")
        with pytest.raises(ShotsError, match="unknown time zone"):
            resolve_timezone(value, [str(tmp_path)])

    @pytest.mark.parametrize(
        "rule, version",
        [("JST-9", b"\0"), ("", b"2"), ("not a rule", b"2"), ("EST\u0665", b"2")],
    )
    def test_a_file_without_a_rule_does_not_resolve(self, tmp_path, rule, version):
        """Version 1 has no footer; nor does an empty one, or one glibc cannot read."""
        path = make_zone(tmp_path, "Odd", "", version)
        if rule:
            path.write_bytes(path.read_bytes()[:-1] + rule.encode("utf-8") + b"\n")
        with pytest.raises(ShotsError, match="unknown time zone"):
            resolve_timezone("Odd", [str(tmp_path)])

    def test_a_stray_file_does_not_resolve(self, tmp_path):
        (tmp_path / "Odd").write_bytes(b"not a zone\n")
        with pytest.raises(ShotsError, match="unknown time zone"):
            resolve_timezone("Odd", [str(tmp_path)])

    def test_a_zone_file_wins_over_the_rule_its_name_spells(self, tmp_path):
        """EST5EDT is both; the file says when daylight time starts, glibc guesses."""
        make_zone(tmp_path, "EST5EDT", "EST5EDT,M3.2.0,M11.1.0")
        assert resolve_timezone("EST5EDT", [str(tmp_path)]) == "EST5EDT,M3.2.0,M11.1.0"

    def test_a_rule_needs_no_database(self, tmp_path):
        assert resolve_timezone("JST-9", [str(tmp_path / "none")]) == "JST-9"

    def test_non_ascii_digits_are_not_a_rule(self, tmp_path):
        """glibc reads only ASCII digits, and runs anything else at UTC."""
        with pytest.raises(ShotsError, match="unknown time zone"):
            resolve_timezone("EST\u0665", [str(tmp_path)])

    @pytest.mark.parametrize("value", ["..\\Asia\\Tokyo", "Asia\\..\\..\\Odd"])
    def test_a_backslash_cannot_leave_the_database(self, tmp_path, value):
        """A separator on Windows: the zone outside the database is not read."""
        (tmp_path / "db").mkdir()
        make_zone(tmp_path, "Asia/Tokyo", "JST-9")
        make_zone(tmp_path, "Odd", "JST-9")
        with pytest.raises(ShotsError, match="unknown time zone"):
            resolve_timezone(value, [str(tmp_path / "db")])

    def test_a_host_without_a_database_says_so(self, tmp_path):
        """Not "unknown": the name may be right, and the fix is to install one."""
        with pytest.raises(ShotsError, match="no zone database"):
            resolve_timezone("Asia/Tokyo", [str(tmp_path / "none")])

    def test_the_tzdata_package_is_searched_last(self, tmp_path, monkeypatch):
        package = tmp_path / "tzdata"
        package.mkdir()
        spec = SimpleNamespace(origin=str(package / "__init__.py"))
        monkeypatch.setattr(shots.importlib.util, "find_spec", lambda _: spec)
        assert shots._zone_directories()[-1] == str(package / "zoneinfo")

    def test_python_3_8_searches_the_default_path(self, monkeypatch):
        """zoneinfo is 3.9's; without it, its default search path is used."""
        monkeypatch.setitem(sys.modules, "zoneinfo", None)
        monkeypatch.setattr(shots.importlib.util, "find_spec", lambda _: None)
        assert shots._zone_directories() == list(shots._FALLBACK_TZPATH)

    def test_the_hosts_own_database_is_searched_by_default(self):
        """Wherever this runs, UTC is in its zone database or in tzdata."""
        if not any(
            os.path.isfile(os.path.join(d, "UTC")) for d in shots._zone_directories()
        ):
            pytest.skip("no zone database on this host")
        assert resolve_timezone("UTC") == "UTC0"

    def test_the_container_is_given_the_rule(self, tmp_path, monkeypatch):
        make_zone(tmp_path / "zones", "Asia/Tokyo", "JST-9")
        monkeypatch.setattr(
            shots, "_zone_directories", lambda: [str(tmp_path / "zones")]
        )
        monkeypatch.setattr(shots, "docker_available", lambda: True)
        project = make_project(tmp_path)
        recorded = {}

        def fake_run(command, **_):
            recorded["command"] = command
            raise RuntimeError("stop here")

        monkeypatch.setattr(shots.subprocess, "run", fake_run)
        with pytest.raises(RuntimeError):
            run_simulator(
                str(project), "testwatch", str(tmp_path / "work"), timezone="Asia/Tokyo"
            )
        assert "TZ=JST-9" in recorded["command"]

    def test_an_unknown_name_stops_before_docker(self, tmp_path, monkeypatch):
        started = []
        monkeypatch.setattr(shots, "_zone_directories", lambda: [str(tmp_path)])
        monkeypatch.setattr(shots, "docker_available", lambda: started.append("docker"))
        with pytest.raises(ShotsError, match="unknown time zone"):
            run_simulator(
                str(make_project(tmp_path)),
                "testwatch",
                str(tmp_path / "work"),
                timezone="Mars/Olympus",
            )
        assert started == []


def test_docker_available_is_false_without_the_client(monkeypatch):
    monkeypatch.setattr(shots.shutil, "which", lambda _: None)
    assert shots.docker_available() is False


# Opt-in: this one builds a real watch face and runs the simulator in a container,
# which needs Docker, a several-gigabyte image and a minute or so. Point
# GARMIN_SHOTS_PROJECT at a Connect IQ project and GARMIN_SHOTS_DEVICE at one of the
# products its manifest names to run it.
INTEGRATION_PROJECT = os.environ.get("GARMIN_SHOTS_PROJECT")
INTEGRATION_DEVICE = os.environ.get("GARMIN_SHOTS_DEVICE")


@pytest.mark.skipif(
    not (INTEGRATION_PROJECT and INTEGRATION_DEVICE),
    reason="set GARMIN_SHOTS_PROJECT and GARMIN_SHOTS_DEVICE to run the capture",
)
def test_captures_a_real_watch_face(tmp_path):
    from garmin_graphics_generator.shots import take_shots

    captured = take_shots(
        project=INTEGRATION_PROJECT,
        product=INTEGRATION_DEVICE,
        output_directory=str(tmp_path / "out"),
        work_directory=str(tmp_path / "work"),
        count=2,
        interval=1.0,
        platform=os.environ.get("GARMIN_SHOTS_PLATFORM"),
    )

    assert len(captured) == 2
    first = Image.open(captured[0].watch_path)
    assert first.mode == "RGBA"
    # The surround is cut away, and the watch is not.
    assert first.getpixel((0, 0))[3] == 0
    assert first.getpixel((first.width // 2, first.height // 2))[3] == 255


def test_the_build_deadline_restarts_on_progress(tmp_path, monkeypatch):
    """--timeout bounds one build, not the sum of a survey's builds."""
    status = tmp_path / shots.STATUS_NAME
    reports = iter(["building 1", "building 2", "building 3", "ready 1"])
    clock = {"now": 0.0}

    def sleep(_seconds):
        # Each poll takes 40 of a 60 s deadline; only a restart keeps it alive.
        clock["now"] += 40
        status.write_text(next(reports))

    monkeypatch.setattr(shots.time, "monotonic", lambda: clock["now"])
    monkeypatch.setattr(shots.time, "sleep", sleep)
    monkeypatch.setattr(shots, "_container_running", lambda _c: True)

    def stop(*_args, **_kwargs):
        raise RuntimeError("reached the screen wait")

    monkeypatch.setattr(shots, "DeviceRender", stop)
    with pytest.raises(RuntimeError, match="screen wait"):
        shots._await_ready("c", str(tmp_path), "testwatch", 60, 60, 1)
