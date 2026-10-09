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
import importlib.util
import logging
import os
import re
import shutil
import subprocess
import time
from typing import Dict, List, NamedTuple, Optional, Sequence

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
STATUS_NAME = "status"
PROBE_NAME = "probe.xwd"
GO_PREFIX = "go"
VARIANTS_SUBDIRECTORY = "variants"
# The zone file a named --timezone is copied to, for the container's TZ to name.
ZONE_NAME = "zone"


class ShotsError(Exception):
    """Raised when the simulator cannot be run, or produced nothing usable."""


class Build(NamedTuple):
    """
    One build to capture.

    ``name`` is the subdirectory its images go to, ``""`` for the output directory
    itself. ``files`` maps a path relative to the project to the text it has in
    this build, laid over a copy of the project before compiling; that is how a
    variant changes a property's default without the project being written to.
    """

    name: str = ""
    jungle: str = "monkey.jungle"
    files: Optional[Dict[str, str]] = None


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
set -eu -o pipefail

fail() { echo "shots: $*" >&2; echo failed > "$OUT/status"; exit 1; }

command -v "$SDK_BIN/monkeyc" >/dev/null || fail "no monkeyc in $SDK_BIN"

if [ -z "${PRG:-}" ]; then
  openssl genrsa -out /tmp/shots_key.pem 4096 2>/dev/null
  openssl pkcs8 -topk8 -inform PEM -outform DER \
    -in /tmp/shots_key.pem -out /tmp/shots_key -nocrypt
  SOURCE=/project
  if [ -d "$OUT/variants" ]; then
    # Variants are laid over a copy, so the project itself is never written to.
    SOURCE=/tmp/shots_source
    mkdir -p "$SOURCE"
    # The work directory is left out when it sits inside the project: it holds
    # the overlays and framebuffer dumps, which are no part of the build.
    tar -C /project --exclude=./.git ${WORK_IN_PROJECT:+--exclude="./$WORK_IN_PROJECT"} \
      -cf - . | tar -C "$SOURCE" -xf -
  fi
  cd "$SOURCE"
  # Every build before any capture: a build that fails does so before the
  # simulator has been waited for, and the builds share one JVM warm-up in the
  # page cache if nothing else.
  for i in $(seq 1 "$BUILDS"); do
    eval "jungle=\$JUNGLE_$i"
    if [ -d "$OUT/variants/$i" ]; then cp -R "$OUT/variants/$i/." "$SOURCE/"; fi
    echo "shots: building $i/$BUILDS for $PRODUCT"
    # Reported, so the caller's deadline is per build rather than for all of them.
    echo "building $i" > "$OUT/status"
    "$SDK_BIN/monkeyc" -w -y /tmp/shots_key -d "$PRODUCT" -f "$jungle" \
      -o "/tmp/shots-$i.prg" || fail "build $i failed for $PRODUCT"
  done
else
  test -f "$PRG" || fail "no such build: $PRG"
  cp "$PRG" /tmp/shots-1.prg
fi

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

port_open() { (exec 3<>/dev/tcp/127.0.0.1/"$PORT") 2>/dev/null; }
# Whether either process group still has a live member. Zombies do not count:
# this script is the container's init, so an exited process lingers as one until
# it is reaped, and kill -0 would report it alive.
running() {
  ps -eo pgid=,stat= | awk -v a="$1" -v b="$2" \
    '($1 == a || $1 == b) && $2 !~ /^Z/ { live = 1 } END { exit !live }'
}

# One simulator per build, each started fresh. A running simulator keeps the app's
# settings in a .SET file and draws those rather than a new build's defaults, so
# the file is removed and the simulator restarted between builds; a fresh
# simulator is also what makes "the face is on screen" mean this build's face,
# since it comes up showing no device at all. Each is started under setsid so the
# whole of it -- the launcher script and what it runs -- can be stopped as a group.
for i in $(seq 1 "$BUILDS"); do
  if [ "$i" -gt 1 ]; then
    until [ -e "$OUT/go-$i" ]; do sleep 0.5; done
    # Stopped and waited for as process groups, not by the port closing: a
    # simulator can close its port before it has finished writing the app's
    # settings, and a write landing after the removal below would hand this
    # build the last one's settings. TERM first; KILL for what ignores it.
    kill -TERM -- "-$PUSH" "-$SIMULATOR" 2>/dev/null || true
    waited=0
    while running "$SIMULATOR" "$PUSH"; do
      waited=$((waited + 1))
      [ "$waited" -ne 20 ] || kill -KILL -- "-$PUSH" "-$SIMULATOR" 2>/dev/null || true
      [ "$waited" -lt 60 ] || fail "the simulator did not stop within ${waited}s"
      sleep 0.5
    done
  fi
  # The app's settings and its storage, both kept by the simulator between runs
  # of the same app. Settings would override this build's defaults; storage would
  # carry state from one build into the next.
  find /tmp /root -path /tmp/shots_source -prune -o -type f \
    \( -path '*/GARMIN/APPS/SETTINGS/*' -o -path '*/GARMIN/APPS/DATA/*' \) \
    -print -delete 2>/dev/null || true

  setsid "$SDK_BIN/connectiq" >/tmp/simulator.log 2>&1 &
  SIMULATOR=$!

  waited=0
  until port_open; do
    waited=$((waited + 1))
    [ "$waited" -lt 120 ] || { cat /tmp/simulator.log >&2; \
      fail "simulator did not open port $PORT within ${waited}s"; }
    sleep 1
  done
  echo "shots: simulator ready after ${waited}s"

  setsid "$SDK_BIN/monkeydo" "/tmp/shots-$i.prg" "$PRODUCT" >/tmp/monkeydo.log 2>&1 &
  PUSH=$!

  echo "ready $i" > "$OUT/status"
  echo "shots: build $i pushed, holding the simulator open"
done
# monkeydo never returns while the app runs, and the caller decides when enough
# frames have been taken, so this waits to be torn down rather than exiting.
tail -f /dev/null
"""

# Copies the live framebuffer to a regular file the caller can read off the bind
# mount. A plain copy rather than reading the mapping through the mount: what Xvfb
# writes is a memory map, and a host filesystem driver is under no obligation to
# show a mapping's current contents.
_GRAB_COMMAND = "cp /tmp/fb/Xvfb_screen0 "


def _check_timings(
    count: int, interval: float, settle: float, timeout: int, ready_timeout: int
) -> None:
    """
    Rejects timings that cannot work, before anything is started.

    Checked here rather than where they are used: a negative interval only reaches
    `time.sleep` once the image has been pulled, the face compiled and the
    simulator launched, which is a long way to go to be told the arguments were
    wrong.
    """
    if count < 1:
        raise ShotsError(f"count must be at least 1, not {count}")
    for name, value in (("interval", interval), ("settle", settle)):
        if value < 0:
            raise ShotsError(f"{name} cannot be negative, and {value} is")
    for name, value in (("timeout", timeout), ("ready_timeout", ready_timeout)):
        if value <= 0:
            raise ShotsError(f"{name} must be positive, not {value}")


# Where to look for a zone file when the zoneinfo module, which is Python 3.9's,
# cannot say: its own default search path.
_FALLBACK_TZPATH = (
    "/usr/share/zoneinfo",
    "/usr/lib/zoneinfo",
    "/usr/share/lib/zoneinfo",
    "/etc/zoneinfo",
)

# A POSIX TZ rule, which glibc reads without a zone database: a standard name and
# offset, then optionally a daylight name, its offset, and the two dates between
# which it applies. ``EST5``, ``MSK-3``, ``<+0530>-5:30``, ``EST5EDT,M3.2.0,M11.1.0``.
# ASCII only: glibc reads no other digits, and a rule it cannot read is UTC.
_POSIX_NAME = r"(?:[A-Za-z]{3,}|<[A-Za-z0-9+-]{3,}>)"
_POSIX_TIME = r"[+-]?[0-9]{1,3}(?::[0-9]{2}){0,2}"
_POSIX_DATE = r"J[0-9]{1,3}|[0-9]{1,3}|M[0-9]{1,2}\.[0-9]\.[0-9]"
_POSIX_TZ = re.compile(
    rf"{_POSIX_NAME}(?P<std>{_POSIX_TIME})"
    rf"(?:{_POSIX_NAME}(?P<dst>{_POSIX_TIME})?"
    rf"(?:,(?P<start>{_POSIX_DATE})(?:/(?P<start_time>{_POSIX_TIME}))?"
    rf",(?P<end>{_POSIX_DATE})(?:/(?P<end_time>{_POSIX_TIME}))?)?)?"
)
# The largest hour glibc takes in an offset, and in a transition time: TZif v3
# lets a transition fall up to a week either side of its day, ``M3.5.0/-1``.
_OFFSET_HOURS = 24
_TRANSITION_HOURS = 167


# What a component of a tz name is made of, per the tz database's own rules.
_ZONE_PART = re.compile(r"[A-Za-z0-9._+-]+")


class TimeZone(NamedTuple):
    """
    What the container's TZ is made from: a POSIX rule given as it is, or the
    host's file for a named zone, to be copied in.
    """

    rule: Optional[str] = None
    path: Optional[str] = None


def _time_in_range(text: str, hours: int) -> bool:
    fields = [int(field) for field in text.lstrip("+-").split(":")]
    return fields[0] <= hours and all(field <= 59 for field in fields[1:])


def _date_in_range(text: str) -> bool:
    if text.startswith("J"):
        return 1 <= int(text[1:]) <= 365
    if text.startswith("M"):
        month, week, day = (int(field) for field in text[1:].split("."))
        return 1 <= month <= 12 and 1 <= week <= 5 and day <= 6
    return int(text) <= 365


def _is_posix_rule(text: str) -> bool:
    """
    Whether glibc reads ``text`` as the rule it spells.

    The ranges are checked as well as the shape: glibc clamps an offset it finds
    too large rather than refusing it, so ``ABC999`` would run, at UTC-24.
    """
    match = _POSIX_TZ.fullmatch(text)
    if match is None:
        return False
    for group, hours in (
        ("std", _OFFSET_HOURS),
        ("dst", _OFFSET_HOURS),
        ("start_time", _TRANSITION_HOURS),
        ("end_time", _TRANSITION_HOURS),
    ):
        if match[group] is not None and not _time_in_range(match[group], hours):
            return False
    return all(
        _date_in_range(match[group])
        for group in ("start", "end")
        if match[group] is not None
    )


def _zone_directories() -> List[str]:
    """The host's zone databases, in the order zoneinfo would search them."""
    try:
        import zoneinfo  # pylint: disable=import-outside-toplevel

        directories = list(zoneinfo.TZPATH)
    except ImportError:
        directories = list(_FALLBACK_TZPATH)
    # The tzdata package is the database on a host that has none of its own.
    spec = importlib.util.find_spec("tzdata")
    if spec is not None and spec.origin:
        directories.append(os.path.join(os.path.dirname(spec.origin), "zoneinfo"))
    return directories


def _is_zone_file(path: str) -> bool:
    """Whether ``path`` is a TZif file, which is what glibc reads a zone from."""
    try:
        with open(path, "rb") as stream:
            return stream.read(4) == b"TZif"
    except OSError:
        return False


def _is_zone_database(directory: str) -> bool:
    """
    Whether ``directory`` holds a zone database, rather than merely existing.

    Probed with UTC, which every database has: an empty or stray directory on the
    search path is no database, and a name missing from it is not "unknown".
    """
    return _is_zone_file(os.path.join(directory, "UTC"))


def _zone_parts(name: str) -> Optional[List[str]]:
    """
    ``name``'s path components within a zone database, or None if it leaves one.

    Each component may hold only what tz names are made of -- ASCII letters and
    digits, ``.``, ``_``, ``+`` and ``-`` -- and may not be ``.`` or ``..``. That
    leaves nothing for a platform to read as a root, a drive or a separator:
    ``/x``, ``..\\x``, ``C:x`` and ``Asia\\C:\\Tokyo`` all fail, everywhere.
    """
    parts = name.split("/")
    if any(
        part in (os.curdir, os.pardir) or not _ZONE_PART.fullmatch(part)
        for part in parts
    ):
        return None
    return parts


def resolve_timezone(
    value: str, directories: Optional[Sequence[str]] = None
) -> TimeZone:
    """
    Turns ``value`` into a time zone the tester image can honour, or raises.

    The image has no zone database, and glibc meets a zone name it cannot find a
    file for by running at UTC, silently. So a name is looked up here, on the host,
    and its file is what the container is given. The whole file, not just the rule
    it ends in: that rule only holds after the file's last transition, and a zone
    such as Africa/Casablanca has years of explicit transitions still ahead.

    A value that names no zone but is a POSIX rule is given as it is; glibc needs
    no file for one. The zone is tried first: ``EST5EDT`` is both, and its file
    says when daylight time starts where the bare rule leaves it to glibc.
    """
    text = value.strip()
    if directories is None:
        directories = _zone_directories()
    # glibc's own spelling of "this is a file name", which changes nothing here.
    name = text[1:] if text.startswith(":") else text
    parts = _zone_parts(name) if name else None
    if parts is not None:
        for directory in directories:
            path = os.path.join(directory, *parts)
            if os.path.isfile(path) and _is_zone_file(path):
                return TimeZone(path=path)
    # glibc reads ``:JST-9`` as a rule too, once no file by that name is found.
    if _is_posix_rule(name):
        return TimeZone(rule=name)
    if not any(_is_zone_database(directory) for directory in directories):
        raise ShotsError(
            f"cannot look up time zone {value!r}: this host has no zone database; "
            "install the tzdata package, or give a POSIX TZ rule such as JST-9"
        )
    raise ShotsError(
        f"unknown time zone {value!r}: give a zone name such as Asia/Tokyo, or a "
        "POSIX TZ rule such as JST-9"
    )


def _install_timezone(zone: TimeZone, work_directory: str) -> str:
    """
    The container's TZ for ``zone``, copying a zone file into the work directory,
    which the container sees as ``/out``.
    """
    if zone.path is None:
        return zone.rule
    shutil.copyfile(zone.path, os.path.join(work_directory, ZONE_NAME))
    return f":/out/{ZONE_NAME}"


def _clear_previous_run(work_directory: str) -> None:
    """
    Removes what an earlier run left in an explicitly named work directory.

    The default work directory is a fresh temporary one, but `--work-directory`
    keeps its contents between runs, and every one of them would be read as this
    run's: a stale `status` says the simulator is ready before it has started, and
    a stale device definition is artwork that rendered some other capture.
    """
    for name in os.listdir(work_directory):
        path = os.path.join(work_directory, name)
        if (
            name in (STATUS_NAME, PROBE_NAME, ZONE_NAME)
            or name.startswith(GO_PREFIX + "-")
            or (name.startswith(FRAME_PREFIX + "-") and name.endswith(".xwd"))
        ):
            os.remove(path)
        elif name in (DEVICE_SUBDIRECTORY, VARIANTS_SUBDIRECTORY) and os.path.isdir(
            path
        ):
            shutil.rmtree(path)


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


def _jungle_files(jungle: str) -> List[str]:
    """
    The jungle argument as the list of files it is.

    ``monkeyc -f`` is ``--jungles``: it takes several files separated by ``;`` and
    lets later ones override earlier ones, which is how a build variant is selected
    without a second copy of the project's settings -- a one-line jungle that
    re-includes an annotation the project jungle excludes, say. The value is passed
    to the compiler as one string either way, so splitting matters only here, where
    each file is checked to exist before a container is started.
    """
    return [name.strip() for name in jungle.split(";") if name.strip()]


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

    One build; `run_builds` does the work.
    """
    return run_builds(
        project=project,
        product=product,
        work_directory=work_directory,
        builds=[Build(jungle=jungle)],
        count=count,
        interval=interval,
        settle=settle,
        image=image,
        prg=prg,
        screen=screen,
        platform=platform,
        timezone=timezone,
        timeout=timeout,
        ready_timeout=ready_timeout,
    )[0]


def run_builds(
    project: str,
    product: str,
    work_directory: str,
    builds: Sequence[Build],
    count: int = DEFAULT_COUNT,
    interval: float = DEFAULT_INTERVAL,
    settle: float = DEFAULT_SETTLE,
    image: str = DEFAULT_IMAGE,
    prg: Optional[str] = None,
    screen: str = DEFAULT_SCREEN,
    platform: Optional[str] = None,
    timezone: Optional[str] = None,
    timeout: int = DEFAULT_TIMEOUT,
    ready_timeout: int = DEFAULT_READY_TIMEOUT,
) -> List[List[str]]:
    """
    Captures each build in turn, in one container, and returns each one's dumps.

    ``work_directory`` is bind-mounted into the container and receives the frames
    and a copy of the device definition that drew them.

    A build's ``jungle`` is what ``monkeyc -f`` takes: one file, or several
    separated by ``;`` with later files overriding earlier ones. Every one of them
    must exist in the project, and that is checked before Docker is touched.

    Every build is compiled before the first capture, and the simulator is
    restarted between builds rather than the container: the container's start and
    the image's first compile are the slow part, and a fresh simulator is what
    makes each build draw its own defaults.

    Frames are taken once the pushed app is on screen, which is waited for rather
    than assumed: the simulator comes up showing no device at all, and how long
    ``monkeydo`` then takes to push a build into it varies by an order of magnitude
    between a native run and an emulated one. A fixed delay is either a capture of
    an empty window or a long wait for nothing.
    """
    # Arguments first, environment second: a mistyped count is the caller's to fix
    # whether or not Docker happens to be running, and reporting it as a Docker
    # problem sends them after the wrong thing.
    _check_timings(count, interval, settle, timeout, ready_timeout)
    if not builds:
        raise ShotsError("nothing to build")
    if prg is not None and (len(builds) > 1 or builds[0].files):
        raise ShotsError("a prebuilt .prg cannot be varied; build from the project")
    zone = None if timezone is None else resolve_timezone(timezone)
    if zone is not None and zone.path is not None:
        logger.info("Time zone %s is %s", timezone, zone.path)

    if not docker_available():
        raise ShotsError(
            "Docker is not available. The capture runs the Connect IQ simulator in "
            "a container, so a running Docker daemon is required; start Docker and "
            "try again."
        )

    project = os.path.abspath(os.path.expanduser(project))
    if not os.path.isdir(project):
        raise ShotsError(f"no such project directory: {project}")
    # Only when something is going to be built: a prebuilt .prg is compiled
    # already, and a directory holding one need not be a project at all.
    if prg is None:
        for build in builds:
            names = _jungle_files(build.jungle)
            if not names:
                raise ShotsError("no jungle file to build; jungle is empty")
            for name in names:
                if not os.path.isfile(os.path.join(project, name)):
                    raise ShotsError(
                        f"{project} has no {name}; is it a Connect IQ project?"
                    )

    work_directory = os.path.abspath(os.path.expanduser(work_directory))
    os.makedirs(work_directory, exist_ok=True)
    _clear_previous_run(work_directory)
    _write_variants(work_directory, builds)

    environment = {
        "SDK_BIN": "/connectiq/bin",
        "DEVICES": "/root/.Garmin/ConnectIQ/Devices",
        "PRODUCT": product,
        "BUILDS": str(len(builds)),
        "OUT": "/out",
        "SCREEN": f"{screen}x24",
        "SIM_DISPLAY": DEFAULT_DISPLAY,
        "PORT": str(DEFAULT_PORT),
        "HOME": "/root",
    }
    for number, build in enumerate(builds, start=1):
        environment[f"JUNGLE_{number}"] = build.jungle
    relative_work = os.path.relpath(work_directory, project)
    if not relative_work.startswith(os.pardir) and relative_work != os.curdir:
        environment["WORK_IN_PROJECT"] = relative_work
    if prg is not None:
        environment["PRG"] = prg
    if zone is not None:
        environment["TZ"] = _install_timezone(zone, work_directory)

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

    logger.info(
        "Capturing %d frame(s) of %s, %d build(s), in %s",
        count,
        product,
        len(builds),
        image,
    )
    logger.debug("docker command: %s", " ".join(command))
    started = subprocess.run(
        command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False, text=True
    )
    if started.returncode != 0:
        raise ShotsError(f"could not start the container: {started.stderr.strip()}")
    container = started.stdout.strip()

    try:
        captured = []
        for number, build in enumerate(builds, start=1):
            if number > 1:
                # The container holds each simulator open until told to move on.
                _touch(os.path.join(work_directory, f"{GO_PREFIX}-{number}"))
            _await_ready(
                container, work_directory, product, timeout, ready_timeout, number
            )
            if len(builds) > 1:
                logger.info("[build %d/%d] %s", number, len(builds), build.name)
            if settle > 0:
                logger.info("Letting the face run for %ss", settle)
                time.sleep(settle)
            captured.append(
                _grab_frames(container, work_directory, count, interval, number)
            )
        return captured
    finally:
        logger.debug("removing container %s", container[:12])
        subprocess.run(
            ["docker", "rm", "--force", container],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )


def _touch(path: str) -> None:
    with open(path, "w", encoding="utf-8"):
        pass


def _write_variants(work_directory: str, builds: Sequence[Build]) -> None:
    """
    Writes each build's changed files where the container lays them over the project.

    ``variants/<n>/<path>`` for build ``n``. Nothing is written for a build that
    changes nothing, and with no such build at all the container compiles the
    project where it is mounted, as a plain capture always has.
    """
    for number, build in enumerate(builds, start=1):
        for relative, text in (build.files or {}).items():
            target = os.path.normpath(
                os.path.join(
                    work_directory, VARIANTS_SUBDIRECTORY, str(number), relative
                )
            )
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with open(target, "w", encoding="utf-8") as variant_file:
                variant_file.write(text)


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
    number: int = 1,
) -> None:
    """
    Waits for build ``number``'s simulator, then for its app to reach the screen.

    Two waits, because they fail for different reasons and on wildly different
    timescales: a build that takes minutes under emulation is normal, and a push
    that takes minutes is not.
    """
    status_path = os.path.join(work_directory, STATUS_NAME)
    wanted = f"ready {number}"
    deadline = time.monotonic() + build_timeout
    last = _read_status(status_path)
    while last not in (wanted, "failed"):
        # The deadline restarts whenever the container reports progress, so it
        # bounds one build rather than all of them: a survey of forty builds
        # under emulation is normal, and one build taking half an hour is not.
        status = _read_status(status_path)
        if status != last:
            last = status
            deadline = time.monotonic() + build_timeout
            continue
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

    if _read_status(status_path) != wanted:
        raise ShotsError(
            "the container could not start the simulator:\n" + _container_log(container)
        )

    device = DeviceRender(product, os.path.join(work_directory, DEVICE_SUBDIRECTORY))
    probe_path = os.path.join(work_directory, PROBE_NAME)
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


def _read_status(path: str) -> Optional[str]:
    """What the container last reported, or None before it has said anything."""
    try:
        with open(path, "r", encoding="utf-8") as status_file:
            return status_file.read().strip()
    except FileNotFoundError:
        return None


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
    container: str, work_directory: str, count: int, interval: float, number: int = 1
) -> List[str]:
    """Copies `count` framebuffers out of the container, `interval` seconds apart."""
    frames = []
    for index in range(1, count + 1):
        path = os.path.join(work_directory, f"{FRAME_PREFIX}-{number}-{index}.xwd")
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
    rest, which turns a search into a crop for every frame after the first. Reused
    is not assumed, though: `extract` checks the offset against each frame and says
    so if the window has moved, and then it is simply found again. Cropping alone
    would accept any offset that lands inside the image and quietly return a watch
    shifted by however far the window went.
    """
    device = DeviceRender(product, devices_directory)
    os.makedirs(output_directory, exist_ok=True)
    _clear_previous_output(output_directory, prefix)

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
            logger.debug("the render moved; locating again in %s", frame_path)
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


def _clear_previous_output(output_directory: str, prefix: str) -> None:
    """
    Removes the images an earlier capture wrote under these names.

    Without this a rerun at a smaller count leaves the extra frames of the larger
    one behind, and the documented `hero ... shots/watch-*.png` glob picks them up:
    a composition of this capture and the last one, with nothing to show that is
    what it is. Only the names this function itself produces are removed, so
    anything else in the directory is left alone.
    """
    pattern = re.compile(
        rf"^{re.escape(prefix)}({SCREEN_PREFIX}|{WATCH_PREFIX})-\d+\.png$"
    )
    for name in os.listdir(output_directory):
        if pattern.match(name):
            os.remove(os.path.join(output_directory, name))


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
    return take_builds(
        project=project,
        product=product,
        output_directory=output_directory,
        work_directory=work_directory,
        builds=[Build(jungle=jungle)],
        prefix=prefix,
        count=count,
        interval=interval,
        settle=settle,
        image=image,
        prg=prg,
        screen=screen,
        platform=platform,
        timezone=timezone,
        timeout=timeout,
        ready_timeout=ready_timeout,
    )[0]


def take_builds(
    project: str,
    product: str,
    output_directory: str,
    work_directory: str,
    builds: Sequence[Build],
    prefix: str = "",
    **options,
) -> List[List[Shot]]:
    """
    Captures several builds in one container, and cuts each into its own directory.

    Build ``b``'s images go to ``output_directory/b.name``. ``options`` are
    `run_builds`' timings and container settings.
    """
    captured = run_builds(
        project=project,
        product=product,
        work_directory=work_directory,
        builds=builds,
        **options,
    )
    devices_directory = os.path.join(work_directory, DEVICE_SUBDIRECTORY)
    return [
        cut_frames(
            frames=frames,
            product=product,
            devices_directory=devices_directory,
            output_directory=os.path.join(output_directory, build.name),
            prefix=prefix,
        )
        for build, frames in zip(builds, captured)
    ]
