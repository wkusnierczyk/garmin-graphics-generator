# Garmin Graphics Generator

A CLI tool and library for Garmin watch face imagery.   
It:
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

* **Background removal**  
  Automatically strips white backgrounds from input images.
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

* Build your watch face, start the simulator, capture screenshots of all relevant variants of your watch face.
* Run `garmin-graphics-generator` to process the screenshots.
* Use the generated hero image when uploading your watch face to the Garmin Connect IQ Developer portal.
* Use the resized images in your `README.md` file to detail the different variants.

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

The CLI has two commands, `hero` and `icons`. An invocation naming neither is treated as `hero`, so
the flat form the tool had before `icons` existed keeps working.

For details about the available command line options, see `garmin-graphics-generator --help`, and
`garmin-graphics-generator <command> --help`:

```bash
garmin-graphics-generator --help

# Output
usage: garmin-graphics-generator [-h] [--about] {hero,icons} ...

Generate watch face hero images and per-device launcher icons.

positional arguments:
  {hero,icons}
    hero        Generate a hero image from watch face screenshots
    icons       Generate per-device launcher icons and their jungle mapping

options:
  -h, --help    show this help message and exit
  --about       Print tool information and exit
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

garmin-graphics-generator: A CLI tool to generate watch face hero images and launcher icons
├─ version:   0.3.0
├─ developer: mailto:waclaw.kusnierczyk@gmail.com
├─ source:    https://github.com/wkusnierczyk/garmin_graphics_generator
└─ licence:   MIT https://opensource.org/licenses/MIT
```