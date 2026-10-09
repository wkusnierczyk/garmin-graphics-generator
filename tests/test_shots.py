import os
import shutil
import sys
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from PIL import Image

from garmin_graphics_generator import shots
from garmin_graphics_generator.capture import DeviceRender
from garmin_graphics_generator.shots import (
    ShotsError,
    TimeZone,
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


def make_zone(directory, name, rule="JST-9"):
    """Writes a TZif file ending in `rule`, with binary noise before it."""
    path = directory.joinpath(*name.split("/"))
    path.parent.mkdir(parents=True, exist_ok=True)
    body = b"TZif2" + b"\0" * 15 + b"\x0a\x00\xff\x0a" * 4
    path.write_bytes(body + b"\n" + rule.encode("ascii") + b"\n")
    return path


def make_database(directory):
    """A zone database: a directory holding UTC, which is how one is told apart."""
    make_zone(directory, "UTC", "UTC0")
    return directory


def capture_command(tmp_path, monkeypatch, **options):
    """The docker run command run_simulator would issue, and its work directory."""
    monkeypatch.setattr(shots, "docker_available", lambda: True)
    project = make_project(tmp_path)
    recorded = {}

    def fake_run(command, **_):
        recorded["command"] = command
        raise RuntimeError("stop here")

    monkeypatch.setattr(shots.subprocess, "run", fake_run)
    work = tmp_path / "work"
    with pytest.raises(RuntimeError):
        run_simulator(str(project), "testwatch", str(work), **options)
    return recorded["command"], work


class TestResolveTimezone:
    """The tester image has no zone database, so a name has to be given its file."""

    @pytest.mark.parametrize(
        "name",
        [
            "Asia/Tokyo",
            "UTC",
            "America/New_York",
            "America/Argentina/Buenos_Aires",
            "Etc/GMT+5",
            "Etc/GMT-14",
            "America/Port-au-Prince",
        ],
    )
    def test_a_name_is_found_in_the_database(self, tmp_path, name):
        path = make_zone(tmp_path, name)
        assert resolve_timezone(name, [str(tmp_path)]) == TimeZone(path=str(path))

    def test_glibcs_leading_colon_is_a_name_too(self, tmp_path):
        path = make_zone(tmp_path, "Asia/Tokyo")
        assert resolve_timezone(":Asia/Tokyo", [str(tmp_path)]).path == str(path)

    def test_the_first_database_that_has_the_zone_wins(self, tmp_path):
        first, second = tmp_path / "first", tmp_path / "second"
        first.mkdir()
        (first / "Asia").mkdir()
        path = make_zone(second, "Asia/Tokyo")
        assert resolve_timezone("Asia/Tokyo", [str(first), str(second)]).path == str(
            path
        )

    @pytest.mark.parametrize(
        "rule",
        [
            "EST5",
            "MSK-3",
            "UTC0",
            "EST5EDT,M3.2.0,M11.1.0",
            "<+0530>-5:30",
            "CET-1CEST,M3.5.0,M10.5.0/3",
            "<-03>3<-02>,M3.5.0/-2,M10.5.0/-1",
            "IST-1GMT0,M10.5.0,M3.5.0/1",
            "XXX3EDT4,0/0,J365/25",
            "<+13>-13<+14>,J1/0,J365/167",
            "AAA-24:59:59",
        ],
    )
    def test_a_rule_passes_through(self, tmp_path, rule):
        assert resolve_timezone(rule, [str(tmp_path)]) == TimeZone(rule=rule)

    @pytest.mark.parametrize(
        "value",
        [
            "ABC999",
            "ABC25",
            "ABC5:60",
            "ABC5:00:60",
            "ABC5DEF,M13.1.0,M11.1.0",
            "ABC5DEF,M0.1.0,M11.1.0",
            "ABC5DEF,M3.6.0,M11.1.0",
            "ABC5DEF,M3.2.7,M11.1.0",
            "ABC5DEF,J999,J300",
            "ABC5DEF,J0,J300",
            "ABC5DEF,366,300",
            "ABC5DEF,M3.2.0/168,M11.1.0",
            "ABC5DEF26,M3.2.0,M11.1.0",
            "EST\u0665",
        ],
    )
    def test_a_rule_glibc_would_misread_is_an_error(self, tmp_path, value):
        """glibc clamps ABC999 to UTC-24 and runs it, rather than refusing."""
        with pytest.raises(ShotsError, match="unknown time zone"):
            resolve_timezone(value, [str(make_database(tmp_path))])

    @pytest.mark.parametrize(
        "value", ["Mars/Olympus", "Asia", "../Asia/Tokyo", "/Asia/Tokyo", "", "Asia/"]
    )
    def test_an_unknown_name_is_an_error_not_utc(self, tmp_path, value):
        make_database(tmp_path)
        make_zone(tmp_path, "Asia/Tokyo")
        with pytest.raises(ShotsError, match="unknown time zone"):
            resolve_timezone(value, [str(tmp_path)])

    def test_a_file_that_is_not_a_zone_does_not_resolve(self, tmp_path):
        make_database(tmp_path)
        (tmp_path / "Odd").write_bytes(b"not a zone\n")
        with pytest.raises(ShotsError, match="unknown time zone"):
            resolve_timezone("Odd", [str(tmp_path)])

    def test_a_zone_file_wins_over_the_rule_its_name_spells(self, tmp_path):
        """EST5EDT is both; the file says when daylight time starts, glibc guesses."""
        path = make_zone(tmp_path, "EST5EDT", "EST5EDT,M3.2.0,M11.1.0")
        assert resolve_timezone("EST5EDT", [str(tmp_path)]).path == str(path)

    def test_a_rule_may_take_glibcs_leading_colon(self, tmp_path):
        """glibc falls back to reading :JST-9 as a rule when no file has that name."""
        assert resolve_timezone(":JST-9", [str(tmp_path)]) == TimeZone(rule="JST-9")

    def test_a_rule_needs_no_database(self, tmp_path):
        assert resolve_timezone("JST-9", [str(tmp_path / "none")]).rule == "JST-9"

    @pytest.mark.parametrize(
        "value",
        [
            "..\\Asia\\Tokyo",
            "Asia\\..\\..\\Odd",
            "C:Odd",
            "C:\\Odd",
            "Asia\\C:\\Tokyo",
            "Asia/C:/Tokyo",
            "Asia/To\0kyo",
        ],
    )
    def test_a_name_cannot_leave_the_database(self, tmp_path, value):
        """Separators and drives on Windows: the zone outside is not read."""
        make_database(tmp_path / "db")
        make_zone(tmp_path, "Asia/Tokyo")
        make_zone(tmp_path, "Odd")
        with pytest.raises(ShotsError, match="unknown time zone"):
            resolve_timezone(value, [str(tmp_path / "db")])

    def test_a_host_without_a_database_says_so(self, tmp_path):
        """Not "unknown": the name may be right, and the fix is to install one."""
        with pytest.raises(ShotsError, match="no zone database"):
            resolve_timezone("Asia/Tokyo", [str(tmp_path / "none")])

    def test_an_empty_directory_on_the_path_is_no_database(self, tmp_path):
        """A directory on TZPATH that exists, but holds no zones, is still none."""
        (tmp_path / "Asia").mkdir()
        with pytest.raises(ShotsError, match="no zone database"):
            resolve_timezone("Asia/Tokyo", [str(tmp_path)])

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
        assert resolve_timezone("UTC").path is not None


class TestTimezoneInTheContainer:
    def test_a_zone_file_is_copied_in_whole_and_named(self, tmp_path, monkeypatch):
        """
        The whole file, transitions and all, not the rule it ends in.

        That rule holds only after the last transition: Africa/Casablanca's ends in
        a permanent +01, yet the file still drops to +00 every Ramadan until 2087.
        """
        zones = tmp_path / "zones"
        path = make_zone(zones, "Africa/Casablanca", "<+01>-1")
        monkeypatch.setattr(shots, "_zone_directories", lambda: [str(zones)])

        command, work = capture_command(
            tmp_path, monkeypatch, timezone="Africa/Casablanca"
        )

        assert "TZ=:/out/zone" in command
        assert (work / "zone").read_bytes() == path.read_bytes()

    def test_the_hosts_casablanca_keeps_its_ramadan_transitions(self, tmp_path):
        """The regression itself, on a real zone, where the host has one."""
        zoneinfo = pytest.importorskip("zoneinfo")
        try:
            zone = resolve_timezone("Africa/Casablanca")
        except ShotsError:
            pytest.skip("no Africa/Casablanca on this host")
        copied = tmp_path / "Casablanca"
        shutil.copyfile(zone.path, copied)
        ramadan = datetime(2026, 2, 25, 12, tzinfo=timezone.utc)
        with open(copied, "rb") as stream:
            local = ramadan.astimezone(zoneinfo.ZoneInfo.from_file(stream))
        assert local.utcoffset() == timedelta(0)

    def test_a_rule_is_given_as_it_is(self, tmp_path, monkeypatch):
        command, work = capture_command(tmp_path, monkeypatch, timezone="JST-9")
        assert "TZ=JST-9" in command
        assert not (work / "zone").exists()

    def test_a_stale_zone_file_is_cleared(self, tmp_path, monkeypatch):
        """A zone left by an earlier run must not be read as this run's."""
        work = tmp_path / "work"
        work.mkdir()
        (work / "zone").write_bytes(b"TZif stale")
        command, _ = capture_command(tmp_path, monkeypatch)
        assert not any(part.startswith("TZ=") for part in command)
        assert not (work / "zone").exists()

    def test_an_unknown_name_stops_before_docker(self, tmp_path, monkeypatch):
        started = []
        make_database(tmp_path)
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
