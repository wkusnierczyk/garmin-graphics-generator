import os

import pytest
from PIL import Image

from garmin_graphics_generator import shots
from garmin_graphics_generator.capture import DeviceRender
from garmin_graphics_generator.shots import ShotsError, cut_frames, run_simulator

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
        second = Image.open(cut[1].screen_path)
        assert second.getpixel((SCREEN["width"] // 2, SCREEN["height"] // 2)) == (
            0,
            90,
            0,
        )


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
        assert "JUNGLE=pro.jungle" in recorded["command"]


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
