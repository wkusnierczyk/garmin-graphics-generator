# Garmin Graphics Generator

A CLI tool and library for Garmin watch face imagery.   
It:
* captures watch face screenshots from the Connect IQ simulator, headlessly;
* removes white backgrounds from screenshots;
* generates a composite "hero" pixel image with watch faces scattered randomly;
* creates copies of input files resized to a common width;
* generates a launcher icon for every size the supported devices ask for, and the jungle
  mapping that serves them.

Implementation language: Python.

> **Note**  
> Garmin Graphics Generator is a **Build What You Need** (BWYN) project.
> 
> It came into existence as a hacky script to automate the process of generating images for 
> upload to the Garmin Connect IQ Developer portal, and images for inclusion in a watch face README.md file.

## Features

* **Headless screenshot capture**  
  Runs the Connect IQ simulator in a container under `Xvfb`, pushes the project's build into it, and
  writes both the device screen at native resolution and the frame set into the SDK's own watch
  render, background already transparent. No GUI, and no screen-recording permission.
* **Background removal**  
  Automatically strips white backgrounds from input images. An input that is already cut out -- every
  `watch-*.png` from `shots` -- is taken as it is, so that path never loads `rembg` at all.
* **Hero image generation**  
  Creates a standard 1440x720 (configurable) composite image with randomized placement, rotation, and sizing.
  The default image size is expected for uploading to the Garmin Connect IQ Developer portal.
* **Batch resizing**  
  Resizes the input images to a common width (200 pixels by default) for inclusion in a watch face `README.md` file.
* **Per-device launcher icons**  
  Reads the required launcher icon size of every supported device from the SDK, renders one icon per
  distinct size, and writes the per-product `resourcePath` mapping into `monkey.jungle`.
* **Command line interface**  
  Easy to use CLI tool for quick processing of a list of images.
  See `garmin-graphics-generator --help` for more information.
* **Python Library**  
  The CLI is backed by a Python module that can be integrate the functionality into your own Python scripts.
* **Fluent API**  
  Easy to use fluent Python API.

## Workflow

The recommended workflow is as follows:

* Run `garmin-graphics-generator shots` to build your watch face and capture it from the simulator.
* Run `garmin-graphics-generator hero` to process the captures.
* Use the generated hero image when uploading your watch face to the Garmin Connect IQ Developer portal.
* Use the resized images in your `README.md` file to detail the different variants.

```bash
garmin-graphics-generator shots -p ../my-watch-face -d epix2pro47mm -n 4 -o shots/
garmin-graphics-generator hero -o graphics/ shots/watch-*.png
```

Capturing used to be the manual step: start the simulator, wait for a frame worth keeping, *File ->
Save Screenshot*, repeat, then composite each capture onto a watch render by hand. That is the step
that made the output drift, because a screenshot is derived from the app but is not generated output,
so nothing reports it stale. Both halves are now a command.

But can't you do all this using an AI, like Gemini with Nano Banana?
Yes, you can, and in most contexts that solution might be better and preferred.
Sometimes, however, forcing an AI agent to fulfil your expectations proves difficult; for example, when you have several watch faces that look much alike, it may consistently fail to include all of them in the generated image, instead replicating only one of them.
Another limitation is that of being able to upload only a limited number of input images (through the chat-based web interface, at least).
The Garmin Graphics Generator does not aim at replacing any other solution you may find more useful, but rather complementing them in a niche selection of tasks.

## Screenshots

`shots` runs the simulator where it can be driven: inside a container, under a virtual display.

```bash
# four frames, three seconds apart, of one product
garmin-graphics-generator shots -p ../my-watch-face -d epix2pro47mm -n 4 -o shots/

# on an arm64 machine, where the tester image runs emulated
garmin-graphics-generator shots -p ../my-watch-face -d epix2pro47mm --platform linux/amd64 -o shots/

# a fixed clock, so the captured face reads the same every time
garmin-graphics-generator shots -p ../my-watch-face -d venu3 --timezone Asia/Tokyo -o shots/
```

Two files come out per frame:

| file | what it is | use |
|:--|:--|:--|
| `screen-<i>.png` | the device framebuffer at native resolution, nothing composited over it | a raw capture |
| `watch-<i>.png` | the frame set into the SDK's watch render, surround already transparent | input to `hero` |

`watch-*.png` needs no background removal, so `hero` takes it as it is.

### Why a container

`monkeydo` only pushes a `.prg` into a simulator that is already running, and the simulator is a GUI
application. The alternatives on macOS both need a permission granted by hand, per terminal
application: window capture needs Screen Recording -- and without it returns the desktop wallpaper
rather than failing -- and driving *File -> Save Screenshot* with AppleScript needs Accessibility.
Neither runs in CI.

The container needs nothing installed into it. `Xvfb`, `openssl`, `bash` and the SDK are all already
in `ghcr.io/matco/connectiq-tester`, which also carries the per-product device definitions that the
SDK's own archive does not. `Xvfb -fbdir` maps the framebuffer onto a file in X Window Dump format,
so reading a frame is a file copy: no `xwd`, `scrot` or ImageMagick either.

### How a frame is cut

Everything needed is published by the SDK, so this is a crop at known coordinates rather than an
estimate:

* `Devices/<product>/simulator.json` gives `display.location`, the screen rectangle within the device
  render -- `{x: 122, y: 238, 416x416}` for `epix2pro47mm`;
* `Devices/<product>/<product>.png` **is** the watch render the simulator draws, and its alpha channel
  is `0` exactly over the screen aperture. Masking the crop with it reproduces the device screen
  including the corners a round display never lights;
* the watch silhouette comes from a flood fill of that render's flat white surround, computed on the
  artwork alone before any frame is composited -- so a bright pixel in the watch face cannot be
  mistaken for background. A global white threshold punches holes in exactly the content worth
  showing: the lead glyph of a digital-rain column is very nearly white;
* the render is located in the framebuffer by matching its own pixels, on a row the watch face cannot
  write to, rather than by assuming where the simulator puts its window. A simulator that changes its
  menu bar or its status bar then shifts nothing.

The device definition is copied out of the container along with the frames, so the artwork a frame is
cut against is always the artwork that rendered it -- and a machine with no Connect IQ SDK installed
can still run this.

### Timing

Frames are taken once the face is actually on screen, which is waited for rather than assumed: the
simulator comes up showing no device at all, and `monkeydo` takes under a second to push a build
natively against the better part of a minute under emulation. `--settle` then lets the face run
before the first frame is kept, so a capture is not of an animation's opening state, and `--interval`
spaces the rest so each frame differs.

## Launcher icons

Garmin sets the launcher icon size **per device**, not per resolution: it is `launcherIcon` in the
SDK's `Devices/<product>/compiler.json`. A watch face supporting a few dozen products typically needs
half a dozen different sizes — `garmin-matrix-time` needs eight, from 38x38 to 70x70 — and shipping a
single icon means the compiler warns and rescales for every device it does not fit.

The size is **not** a function of `deviceFamily` either: `round-390x390` alone spans 38x38 to 70x70.
So the qualified `resources-<family>/drawables/` directories that serve the fonts cannot express it,
and the mapping has to be per product:

```
venu3.resourcePath = $(venu3.resourcePath);resources-icon-70
```

`garmin-graphics-generator icons` writes all of that: one icon per distinct size into
`resources-icon-<size>/drawables/`, the `drawables.xml` declaring each, and the per-product mapping
spliced into `monkey.jungle` between generated markers. Anything outside those markers is left alone.

```bash
# regenerate, resampling a master image
garmin-graphics-generator icons -p ../my-watch-face -R resample:art/icon-512.png

# regenerate, with the project drawing its own artwork at each size
garmin-graphics-generator icons -p ../my-watch-face -R ../my-watch-face/tools/icon.py

# verify what is committed, without needing an SDK
garmin-graphics-generator icons -p ../my-watch-face --check

# print the product to icon size table for the project README
garmin-graphics-generator icons -p ../my-watch-face --table
```

`--check` and `--table` read the committed mapping, so they need no SDK; `--table` falls back to the
SDK when nothing has been generated yet, which is when the project README is usually being written.
Under `-q` a passing `--check` prints nothing and says so through its exit status alone.

### The renderer

The sizing, the directory layout, the mapping and the checks are the same for every watch face. The
artwork is not, so `icons` takes a **renderer**: a callable given the target edge in pixels, returning
a square image of exactly that size.

`resample:<master.png>` is built in and resamples one master image. That is the right answer for
artwork made of a few bold shapes. It is the wrong answer for fine strokes at a high spatial
frequency — dense glyphs, hairlines, digital rain — which downscaling turns into noise long before
38x38. Such a project supplies a file of its own:

```python
# my-watch-face/tools/icon.py
import os
from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))


def render(size):
    image = Image.new("RGB", (size, size), (0, 0, 0))
    draw = ImageDraw.Draw(image)
    # ... draw at `size`, not at some master size ...
    return image
```

and points at it with `-R tools/icon.py`. `-R tools/icon.py:name` picks a differently named function,
and `-R package.module:name` loads one from an installed module.

### Checking

`--check` verifies that every product in `manifest.xml` has a mapping, that no mapping names a product
outside it, that every icon is the size its directory promises, that each declares `LauncherIcon`,
that no icon directory is left unmapped, and — when the SDK is installed — that every mapping matches
the size the SDK declares for that device. It needs no SDK: the committed mapping is itself the
device-to-size table, and the SDK is used only to cross-check it. Hook it into the watch face's own
`Makefile` and it fails the build when the device list and the icons drift apart.

## Installation

```bash
git clone <repository-url>
cd garmin_graphics_generator
pip install .
```

## Usage

### CLI

Use the tool as a command line executable.

```bash
# Example usage
garmin-graphics-generator hero \
   --output-directory ./output \
   --size-variation 5 \
   --orientation-variation 45 \
   --hero-file-name hero_banner.png \
   --hero-file-size 1440x720 \
   --resized-file-suffix _thumb \
   --resized-file-width 200 \
   my_watch_1.jpg my_watch_2.jpg
```

The CLI has three commands, `shots`, `hero` and `icons`. An invocation naming none of them is treated
as `hero`, so the flat form the tool had before `icons` existed keeps working.

For details about the available command line options, see `garmin-graphics-generator --help`, and
`garmin-graphics-generator <command> --help`:

```bash
garmin-graphics-generator --help

# Output
usage: garmin-graphics-generator [-h] [--about] {hero,icons,shots} ...

Capture watch face screenshots, and generate hero images and per-device launcher icons.

positional arguments:
  {hero,icons,shots}
    hero              Generate a hero image from watch face screenshots
    icons             Generate per-device launcher icons and their jungle mapping
    shots             Capture watch face screenshots from the simulator, headlessly

options:
  -h, --help          show this help message and exit
  --about             Print tool information and exit
```

```bash
garmin-graphics-generator shots --help

# Output
usage: garmin-graphics-generator shots [-h] [-p PROJECT_DIRECTORY] -d DEVICE [-o OUTPUT_DIRECTORY]
                                       [-n COUNT] [-i INTERVAL] [--settle SETTLE]
                                       [--prefix PREFIX] [--jungle JUNGLE] [--prg PRG]
                                       [--image IMAGE] [--platform PLATFORM] [--screen SCREEN]
                                       [--timezone TIMEZONE] [--timeout TIMEOUT]
                                       [--ready-timeout READY_TIMEOUT]
                                       [--work-directory WORK_DIRECTORY] [-v | -q]

options:
  -h, --help            show this help message and exit
  -p, --project-directory PROJECT_DIRECTORY
                        Watch face project directory, the one holding monkey.jungle
  -d, --device DEVICE   Product to capture, as named in manifest.xml (e.g. epix2pro47mm)
  -o, --output-directory OUTPUT_DIRECTORY
                        Where to write the screen and watch images
  -n, --count COUNT     How many frames to capture (default: 4)
  -i, --interval INTERVAL
                        Seconds between frames; what makes an animated face look different in each
                        (default: 3.0)
  --settle SETTLE       Seconds to let the face run before the first frame, so a capture is not of
                        its opening state (default: 6.0)
  --prefix PREFIX       Prepended to every output filename
  --jungle JUNGLE       Jungle file to build, relative to the project directory
  --prg PRG             Path inside the container to a prebuilt .prg, skipping the build
  --image IMAGE         Container image carrying the SDK and the device definitions
  --platform PLATFORM   Container platform, e.g. linux/amd64 on an arm64 machine
  --screen SCREEN       Virtual display size; must be larger than the device render (default:
                        1280x1024)
  --timezone TIMEZONE   TZ for the container, which is the time the captured face shows
  --timeout TIMEOUT     Seconds to allow the build and simulator start, which is where an emulated
                        run spends its time (default: 1800)
  --ready-timeout READY_TIMEOUT
                        Seconds to wait for the pushed face to appear on the simulator's screen
                        (default: 300)
  --work-directory WORK_DIRECTORY
                        Where to keep the raw framebuffers and the device definition copied out of
                        the container; a temporary directory by default
  -v, --verbose         Enable verbose output
  -q, --silent          Suppress all output except errors
```

```bash
garmin-graphics-generator icons --help

# Output
usage: garmin-graphics-generator icons [-h] [-p PROJECT_DIRECTORY] [-d DEVICES_DIRECTORY] [-R RENDERER]
                                       [--no-fallback-icon] [--check | --table] [-v | -q]

options:
  -h, --help            show this help message and exit
  -p, --project-directory PROJECT_DIRECTORY
                        Watch face project directory, the one holding manifest.xml
  -d, --devices-directory DEVICES_DIRECTORY
                        SDK directory holding one compiler.json per device
  -R, --renderer RENDERER
                        How to draw one icon: 'resample:<master.png>' to resample a master image, or
                        '<file>.py[:<name>]' / '<module>:<name>' for a callable taking the edge in
                        pixels and returning a square image of that size
  --no-fallback-icon    Do not rewrite resources/drawables/launcher_icon.png
  --check               Verify the committed icons and mapping; exit non-zero on a problem
  --table               Print the product to icon size table as markdown
  -v, --verbose         Enable verbose output
  -q, --silent          Suppress all output except errors
```

### Python Library

Use the tool as a Python library.

```python
from garmin_graphics_generator import WatchHeroGenerator

(
    WatchHeroGenerator()
    .set_input_paths(["watch1.jpg", "watch2.jpg"])
    .set_output_directory("./output")
    .set_variations(size_var=2, orientation_var=30)
    .prepare_output_directory()
    .process_input_images()
    .generate_hero_composition()
    .generate_resized_files()
)
```

```python
from garmin_graphics_generator import LauncherIconGenerator

(
    LauncherIconGenerator()
    .set_project_directory("../my-watch-face")
    .set_renderer(my_render)          # size -> a square PIL image of that size
    .generate_icons()
    .write_mapping()
)
```

`WatchHeroGenerator` is imported lazily, so a project that only generates launcher icons pays for
Pillow alone and not for `rembg` and `onnxruntime`.

## Development

If you'd like to clone the repository and contribute to or experiment with the code, check out the included `Makefile` for convenience targets. 
The typical workflow to run after updating the code is as follows:

```bash
# Clean up the project
make clean 

# Lint the code 
make format lint

# Build and test
# Note: you may need to run make install first to install dependencies 
make build test

# Install the package locally
make install

# Install the package globally
pip install .
```

## About

```bash
garmin-graphics-generator --about

garmin-graphics-generator: A CLI tool for watch face imagery: simulator screenshots, hero images and launcher icons
├─ version:   0.5.0
├─ developer: mailto:waclaw.kusnierczyk@gmail.com
├─ source:    https://github.com/wkusnierczyk/garmin_graphics_generator
└─ licence:   MIT https://opensource.org/licenses/MIT
```
