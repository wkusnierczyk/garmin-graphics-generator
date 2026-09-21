"""
Driving the Connect IQ simulator headlessly to capture a watch face.

The simulator is a GUI application and ``monkeydo`` only pushes a ``.prg`` into one
that is already running, so capturing a frame has always meant a developer sitting
in front of it. This module removes the developer: it runs the simulator inside a
container under ``Xvfb``, pushes the build in, and reads frames straight out of the
virtual framebuffer.

The container needs nothing installed into it -- ``Xvfb``, ``openssl``, ``bash`` and
the SDK are all already in the Connect IQ tester image -- which keeps a capture from
depending on a package index being reachable. ``Xvfb -fbdir`` maps the framebuffer
onto a file in X Window Dump format, so reading a frame is a file copy and needs no
screenshot utility either.

The container sets the run up and then blocks, and the frames are taken against it
from outside. That split is not incidental. Whether the face is on screen yet is a
question about pixels, and Pillow is here rather than in the container; a capture
driven from within would have to guess with a fixed delay, which is either a
picture of an empty window or a long wait for nothing -- the push takes under a
second natively and the better part of a minute under emulation.

Two things come back out through the mounted work directory: the frames, and the
SDK's own definition of the device that drew them. Taking the definition from the
container rather than from a local SDK means the artwork a frame is cut against is
always the artwork that rendered it, and that a machine with no Connect IQ SDK
installed can still run this.

The container is what makes the command portable: the same capture runs on a
developer's machine and in CI, and needs no screen-recording permission on either.
"""
import logging
import os
import shutil
import subprocess
import time
from typing import List, NamedTuple, Optional, Sequence

from .capture import CaptureError, DeviceRender, read_xwd

logger = logging.getLogger(__name__)

# The Connect IQ tester image, which carries the SDK and -- unlike the SDK's own
# archive -- the per-product device definitions monkeyc compiles against. Pinned by
# digest rather than by tag so a capture is reproducible; override with --image.
DEFAULT_IMAGE = (
    "ghcr.io/matco/connectiq-tester@sha256:"
    "64958e8fd2925d0c4986d72a9aa9d8e2101297a881354aab0118be2f1dc22105"
)

DEFAULT_COUNT = 4
DEFAULT_INTERVAL = 3.0
# How long to let the face run before the first frame is kept, counted from the
# moment the face is actually on screen. A watch face with an animation opens on its
# initial state -- for digital rain, every column at the top -- which is the one
# frame that does not look like the face in use.
DEFAULT_SETTLE = 6.0
DEFAULT_SCREEN = "1280x1024"
DEFAULT_DISPLAY = ":99"
DEFAULT_PORT = 1234
# Generous because the tester image is built for amd64 and a capture on an arm64
# machine therefore compiles under emulation, where monkeyc takes minutes.
DEFAULT_TIMEOUT = 1800
# How long to wait for the pushed app to appear on the simulator's screen, and how
# often to look. monkeydo returns nothing on success and keeps running, so the only
# report that the push landed is the device showing up in the framebuffer.
DEFAULT_READY_TIMEOUT = 300
READY_POLL_INTERVAL = 2.0

SCREEN_PREFIX = "screen"
WATCH_PREFIX = "watch"
FRAME_PREFIX = "frame"
DEVICE_SUBDIRECTORY = "device"


class ShotsError(Exception):
    """Raised when the simulator cannot be run, or produced nothing usable."""


class Shot(NamedTuple):
    """One captured frame, as the two files worth keeping."""

    index: int
    screen_path: str
    watch_path: str


# Run inside the container to set the capture up, then block. The X server and the
# simulator have to outlive the step that starts them, so they cannot be launched
# from a "docker exec": a backgrounded process there is at the mercy of that exec's
# exit. This script is the container's command instead, and grabbing frames is done
# by exec against the container it leaves running.
#
# The port probe uses bash's own /dev/tcp rather than nc, which ubuntu:jammy does
# not carry: the point of this script is that the image needs nothing added to it.
# It probes rather than sleeping a flat interval because the launcher returns long
# before the simulator accepts connections.
_SETUP_SCRIPT = r"""
set -eu

fail() { echo "shots: $*" >&2; echo failed > "$OUT/status"; exit 1; }

command -v "$SDK_BIN/monkeyc" >/dev/null || fail "no monkeyc in $SDK_BIN"

if [ -z "${PRG:-}" ]; then
  openssl genrsa -out /tmp/shots_key.pem 4096 2>/dev/null
  openssl pkcs8 -topk8 -inform PEM -outform DER \
    -in /tmp/shots_key.pem -out /tmp/shots_key -nocrypt
  PRG=/tmp/shots.prg
  echo "shots: building $PRODUCT"
  "$SDK_BIN/monkeyc" -w -y /tmp/shots_key -d "$PRODUCT" -f "$JUNGLE" -o "$PRG" \
    || fail "build failed for $PRODUCT"
fi
test -f "$PRG" || fail "no such build: $PRG"

# The device definition travels with the frames: what a frame is cut against has to
# be the artwork that drew it, not whatever a local SDK happens to hold.
mkdir -p "$OUT/device/$PRODUCT"
cp "$DEVICES/$PRODUCT/simulator.json" "$OUT/device/$PRODUCT/" \
  || fail "no SDK definition for $PRODUCT in this image"
cp "$DEVICES/$PRODUCT"/*.png "$OUT/device/$PRODUCT/" 2>/dev/null || true

mkdir -p /tmp/fb
export DISPLAY="$SIM_DISPLAY"
Xvfb "$SIM_DISPLAY" -screen 0 "$SCREEN" -fbdir /tmp/fb >/tmp/xvfb.log 2>&1 &

waited=0
until [ -e /tmp/fb/Xvfb_screen0 ]; do
  waited=$((waited + 1))
  [ "$waited" -lt 30 ] || { cat /tmp/xvfb.log >&2; fail "Xvfb wrote no framebuffer"; }
  sleep 1
done

"$SDK_BIN/connectiq" >/tmp/simulator.log 2>&1 &

waited=0
until (exec 3<>/dev/tcp/127.0.0.1/"$PORT") 2>/dev/null; do
  waited=$((waited + 1))
  [ "$waited" -lt 120 ] || { cat /tmp/simulator.log >&2; \
    fail "simulator did not open port $PORT within ${waited}s"; }
  sleep 1
done
echo "shots: simulator ready after ${waited}s"

"$SDK_BIN/monkeydo" "$PRG" "$PRODUCT" >/tmp/monkeydo.log 2>&1 &

echo ready > "$OUT/status"
echo "shots: app pushed, holding the simulator open"
# monkeydo never returns while the app runs, and the caller decides when enough
# frames have been taken, so this waits to be torn down rather than exiting.
tail -f /dev/null
"""

# Copies the live framebuffer to a regular file the caller can read off the bind
# mount. A plain copy rather than reading the mapping through the mount: what Xvfb
# writes is a memory map, and a host filesystem driver is under no obligation to
# show a mapping's current contents.
_GRAB_COMMAND = "cp /tmp/fb/Xvfb_screen0 "


def docker_available() -> bool:
    """Reports whether a Docker daemon is reachable."""
    if shutil.which("docker") is None:
        return False
    probe = subprocess.run(
        ["docker", "info"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return probe.returncode == 0


def run_simulator(
    project: str,
    product: str,
    work_directory: str,
    count: int = DEFAULT_COUNT,
    interval: float = DEFAULT_INTERVAL,
    settle: float = DEFAULT_SETTLE,
    image: str = DEFAULT_IMAGE,
    jungle: str = "monkey.jungle",
    prg: Optional[str] = None,
    screen: str = DEFAULT_SCREEN,
    platform: Optional[str] = None,
    timezone: Optional[str] = None,
    timeout: int = DEFAULT_TIMEOUT,
    ready_timeout: int = DEFAULT_READY_TIMEOUT,
) -> List[str]:
    """
    Runs the simulator in a container and returns the framebuffer dumps it wrote.

    ``work_directory`` is bind-mounted into the container and receives the frames
    and a copy of the device definition that drew them.

    Frames are taken once the pushed app is on screen, which is waited for rather
    than assumed: the simulator comes up showing no device at all, and how long
    ``monkeydo`` then takes to push a build into it varies by an order of magnitude
    between a native run and an emulated one. A fixed delay is either a capture of
    an empty window or a long wait for nothing.
    """
    if not docker_available():
        raise ShotsError(
            "Docker is not available. The capture runs the Connect IQ simulator in "
            "a container, so a running Docker daemon is required; start Docker and "
            "try again."
        )

    project = os.path.abspath(os.path.expanduser(project))
    if not os.path.isdir(project):
        raise ShotsError(f"no such project directory: {project}")
    if not os.path.isfile(os.path.join(project, jungle)):
        raise ShotsError(f"{project} has no {jungle}; is it a Connect IQ project?")

    work_directory = os.path.abspath(os.path.expanduser(work_directory))
    os.makedirs(work_directory, exist_ok=True)

    environment = {
        "SDK_BIN": "/connectiq/bin",
        "DEVICES": "/root/.Garmin/ConnectIQ/Devices",
        "PRODUCT": product,
        "JUNGLE": jungle,
        "OUT": "/out",
        "SCREEN": f"{screen}x24",
        "SIM_DISPLAY": DEFAULT_DISPLAY,
        "PORT": str(DEFAULT_PORT),
        "HOME": "/root",
    }
    if prg is not None:
        environment["PRG"] = prg
    if timezone is not None:
        environment["TZ"] = timezone

    command = ["docker", "run", "--detach"]
    if platform:
        command += ["--platform", platform]
    for name, value in environment.items():
        command += ["-e", f"{name}={value}"]
    command += [
        "-v",
        f"{project}:/project",
        "-v",
        f"{work_directory}:/out",
        "-w",
        "/project",
        "--entrypoint",
        "/bin/bash",
        image,
        "-c",
        _SETUP_SCRIPT,
    ]

    logger.info("Capturing %d frame(s) of %s in %s", count, product, image)
    logger.debug("docker command: %s", " ".join(command))
    started = subprocess.run(
        command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False, text=True
    )
    if started.returncode != 0:
        raise ShotsError(f"could not start the container: {started.stderr.strip()}")
    container = started.stdout.strip()

    try:
        _await_ready(container, work_directory, product, timeout, ready_timeout)
        if settle > 0:
            logger.info("Letting the face run for %ss", settle)
            time.sleep(settle)
        return _grab_frames(container, work_directory, count, interval)
    finally:
        logger.debug("removing container %s", container[:12])
        subprocess.run(
            ["docker", "rm", "--force", container],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )


def _container_log(container: str) -> str:
    """The container's own output, for a failure message worth reading."""
    logs = subprocess.run(
        ["docker", "logs", container],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
        text=True,
    )
    return logs.stdout.strip()


def _await_ready(
    container: str,
    work_directory: str,
    product: str,
    build_timeout: int,
    ready_timeout: int,
) -> None:
    """
    Waits for the build, then for the pushed app to reach the simulator's screen.

    Two waits, because they fail for different reasons and on wildly different
    timescales: a build that takes minutes under emulation is normal, and a push
    that takes minutes is not.
    """
    status_path = os.path.join(work_directory, "status")
    deadline = time.monotonic() + build_timeout
    while not os.path.exists(status_path):
        if time.monotonic() > deadline:
            raise ShotsError(
                f"the container did not finish setting up within {build_timeout}s:\n"
                + _container_log(container)
            )
        if not _container_running(container):
            raise ShotsError(
                "the container exited early:\n" + _container_log(container)
            )
        time.sleep(READY_POLL_INTERVAL)

    with open(status_path, "r", encoding="utf-8") as status_file:
        if status_file.read().strip() != "ready":
            raise ShotsError(
                "the container could not start the simulator:\n"
                + _container_log(container)
            )

    device = DeviceRender(product, os.path.join(work_directory, DEVICE_SUBDIRECTORY))
    probe_path = os.path.join(work_directory, "probe.xwd")
    deadline = time.monotonic() + ready_timeout
    while True:
        frame = _grab(container, work_directory, probe_path)
        try:
            device.locate(frame)
            logger.info("The face is on screen")
            return
        except CaptureError:
            pass
        if time.monotonic() > deadline:
            raise ShotsError(
                f"the {product} face did not appear on the simulator's screen "
                f"within {ready_timeout}s; raise --ready-timeout, or check that "
                "the build runs:\n" + _container_log(container)
            )
        time.sleep(READY_POLL_INTERVAL)


def _container_running(container: str) -> bool:
    """Reports whether the container is still up."""
    state = subprocess.run(
        ["docker", "inspect", "--format", "{{.State.Running}}", container],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=False,
        text=True,
    )
    return state.stdout.strip() == "true"


def _grab(container: str, work_directory: str, path: str):
    """Copies the live framebuffer out of the container and decodes it."""
    name = os.path.basename(path)
    grabbed = subprocess.run(
        ["docker", "exec", container, "bash", "-c", _GRAB_COMMAND + f"/out/{name}"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
        text=True,
    )
    if grabbed.returncode != 0:
        raise ShotsError(
            "could not read the simulator's framebuffer: " + grabbed.stdout.strip()
        )
    with open(path, "rb") as frame_file:
        return read_xwd(frame_file.read())


def _grab_frames(
    container: str, work_directory: str, count: int, interval: float
) -> List[str]:
    """Copies `count` framebuffers out of the container, `interval` seconds apart."""
    frames = []
    for index in range(1, count + 1):
        path = os.path.join(work_directory, f"frame-{index}.xwd")
        _grab(container, work_directory, path)
        logger.info("[%d/%d] captured frame", index, count)
        frames.append(path)
        if index < count:
            time.sleep(interval)
    return frames


def cut_frames(
    frames: Sequence[str],
    product: str,
    devices_directory: str,
    output_directory: str,
    prefix: str = "",
) -> List[Shot]:
    """
    Cuts framebuffer dumps into device screenshots and watch renders.

    The device render is located in the first frame and the offset reused for the
    rest: the simulator does not move its window mid-capture, and locating once
    turns a search into a crop for every frame after the first.
    """
    device = DeviceRender(product, devices_directory)
    os.makedirs(output_directory, exist_ok=True)

    shots: List[Shot] = []
    origin = None
    for index, frame_path in enumerate(frames, start=1):
        with open(frame_path, "rb") as frame_file:
            frame = read_xwd(frame_file.read())
        if origin is None:
            origin = device.locate(frame)
            logger.debug("device render at %s in %s", origin, frame_path)
        try:
            screen, watch = device.extract(frame, origin)
        except CaptureError:
            # A window that did move invalidates the reused offset rather than the
            # capture: look again for this frame and carry on with the new one.
            origin = device.locate(frame)
            screen, watch = device.extract(frame, origin)

        screen_path = os.path.join(
            output_directory, f"{prefix}{SCREEN_PREFIX}-{index}.png"
        )
        watch_path = os.path.join(
            output_directory, f"{prefix}{WATCH_PREFIX}-{index}.png"
        )
        screen.save(screen_path)
        watch.save(watch_path)
        logger.info("[%d/%d] %s", index, len(frames), os.path.basename(watch_path))
        shots.append(Shot(index, screen_path, watch_path))

    return shots


def take_shots(
    project: str,
    product: str,
    output_directory: str,
    work_directory: str,
    count: int = DEFAULT_COUNT,
    interval: float = DEFAULT_INTERVAL,
    settle: float = DEFAULT_SETTLE,
    image: str = DEFAULT_IMAGE,
    jungle: str = "monkey.jungle",
    prg: Optional[str] = None,
    screen: str = DEFAULT_SCREEN,
    platform: Optional[str] = None,
    timezone: Optional[str] = None,
    timeout: int = DEFAULT_TIMEOUT,
    ready_timeout: int = DEFAULT_READY_TIMEOUT,
    prefix: str = "",
) -> List[Shot]:
    """Runs a capture and cuts what it produced. The whole command, in one call."""
    frames = run_simulator(
        project=project,
        product=product,
        work_directory=work_directory,
        count=count,
        interval=interval,
        settle=settle,
        image=image,
        jungle=jungle,
        prg=prg,
        screen=screen,
        platform=platform,
        timezone=timezone,
        timeout=timeout,
        ready_timeout=ready_timeout,
    )
    devices_directory = os.path.join(work_directory, DEVICE_SUBDIRECTORY)
    return cut_frames(
        frames=frames,
        product=product,
        devices_directory=devices_directory,
        output_directory=output_directory,
        prefix=prefix,
    )
