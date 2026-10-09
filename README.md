# Garmin Graphics Generator

A CLI tool and library for Garmin watch face imagery.   
It:
* captures watch face screenshots from the Connect IQ simulator, headlessly;
* captures a watch face across its settings, as a labelled contact sheet;
* removes white backgrounds from screenshots;
* generates a composite "hero" pixel image with watch faces scattered randomly;
* sizes and screens a hero composed by an image model (Gemini) from the captures, by hand or
  through the API;
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
* **Settings contact sheet**  
  Reads the settings a project declares, builds the face once per combination worth seeing (one
  setting at a time, a grid of two, a list of cases, or all of them), and lays the frames out on one
  image with each labelled by its values -- for reviewing a setting before it merges.
* **Background removal**  
  Automatically strips white backgrounds from input images. An input that is already cut out -- every
  `watch-*.png` from `shots` -- is taken as it is, so that path never loads `rembg` at all.
* **Hero image generation**  
  Creates a standard 1440x720 (configurable) composite image with randomized placement, rotation, and sizing.
  The default image size is expected for uploading to the Garmin Connect IQ Developer portal.
* **Model-composed hero**  
  Fills in a prompt template for an image model, then crops and resizes what the model made to the
  exact store size, and screens it: watch count, cases inside the frame, the face's own screen
  truths, and the file size limit. Rejected candidates are kept, marked. Generating through the API
  is opt-in and paid; by default the images come from the Gemini app.
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
Where an image model is the better route, `compose` takes the parts of it that have to be done every
time -- filling in the prompt, checking the count and the screens, cutting to the exact size -- and
leaves the judgement to you; see [Composed hero](#composed-hero).

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

### Across settings

A face with settings has to be reviewed at more than its defaults. Any of the settings options below
turns `shots` into a survey: one build per combination of settings, all captured in one container,
laid out as `contact-sheet.png` with each frame labelled, and described in `index.json`.

The options are generic; the settings, resource directories and jungles are the face's own. As a
worked example, here they are for [Matrix Time](https://github.com/wkusnierczyk/garmin-matrix-time),
whose Premium edition has settings on its own resource path and draws its always-on screen from a
build forced by `graphics.jungle`. Clone it and work on the local copy:

```bash
git clone https://github.com/wkusnierczyk/garmin-matrix-time.git
cd garmin-matrix-time

# every value of the time size, the other settings at their defaults
garmin-graphics-generator shots -p . -d epix2pro47mm -o preview/ \
    --resources resources --resources premium/resources-base \
    --jungle "monkey.jungle;premium.jungle" --vary timeSize

# every size in each style, as a grid: styles across, sizes down
garmin-graphics-generator shots -p . -d epix2pro47mm -o preview/ \
    --resources resources --resources premium/resources-base \
    --jungle "monkey.jungle;premium.jungle" --grid timeStyle timeSize

# the time style on the woken screen and on the always-on one
garmin-graphics-generator shots -p . -d epix2pro47mm -o preview/ \
    --resources resources --resources premium/resources-base --vary timeStyle \
    --scene "woken=monkey.jungle;premium.jungle" \
    --scene "always-on=monkey.jungle;graphics.jungle;premium.jungle"
```

For your own face, name the resource directories its jungle puts on the build's resource path, the
properties its `settings.xml` declares, and, if it has one, the jungle that forces its always-on
screen.

**Where the values come from.** The properties, settings and strings under the `--resources`
directories: a `list` setting contributes its `listEntry` values, labelled as the settings screen
labels them, and a `boolean` contributes `true` and `false`. The directories are named rather than
discovered because which ones a build uses is the jungle's business: a file on another edition's
resource path would be varied and then ignored by the build, and every tile would look the same.
`--set KEY=VALUE,VALUE` gives the values of anything else -- a number, a colour -- or narrows a list.

**Which combinations.** A full product is rarely what you want, and grows fast:

| option | captures | answers |
|:--|:--|:--|
| `--sweep` (the default) | one setting at a time, the others at their defaults; the defaults are built once | what does each setting do |
| `--grid ACROSS DOWN` | every pair of values of two settings, as columns by rows | how do these two interact |
| `--cases FILE` | the combinations in a JSON list, e.g. `[{"timeSize": "6", "timeStyle": "1"}, {}]` | the handful that matter |
| `--all` | every combination of the varied settings; refused above 24 without `--force` | everything, when it is small |

`--vary KEY` and `--set KEY=...` both name the settings to vary; with neither, a sweep or `--all`
varies every list and boolean setting. A grid varies its two settings only, and `--cases` takes its
values from the file only: each refuses a `--vary` or `--set` it would otherwise ignore.

**How a setting is applied.** By rewriting the property's default in a copy of the project inside the
container, and building that. A fresh simulator has no settings file for the app, so it draws every
property at the default its build declares. Before each build is pushed the simulator is
stopped, the app's settings and storage under `/tmp/com.garmin.connectiq/GARMIN/APPS` are removed, and
it is started again. On a desktop the simulator keeps an app's settings there between runs, and they
would override a new build's defaults. In the tester image the settings directory has been found empty
between builds -- nothing edits the settings there -- so the removal is a guard, not a step the capture
depends on today. The restart is also what makes "the face is on screen" mean this build's face. Writing the simulator's settings
file directly would save a build per combination, but its format is not documented. The project
itself is never written to.

**Scenes.** `--scene NAME=JUNGLE` captures every combination with that jungle list, under a heading
of its own. `NAME` is also the scene's output directory, so it is one name of letters, digits, `.`,
`_` and `-`, starting with a letter or digit. A face that draws a separate always-on screen usually needs a build-time switch to show it
in the simulator, and a scene is how that switch is passed without this tool knowing any face's gating.
Without `--scene`, `--jungle` is the one scene.

**Output.** One directory per build (under the scene's name when there are scenes), named by the
settings it changes -- `02-timeStyle-1`, `05-timeSize-6_timeStyle-1`, or `01-defaults` -- holding `screen-<i>.png` and `watch-<i>.png` as a plain capture does; `contact-sheet.png`,
made of each build's first screen; and `index.json`, mapping each directory to its scene, its jungle
and the value of every property. A survey takes one frame per build unless `-n` asks for more. A
rerun removes the directories the previous `index.json` lists, and nothing else.

**Cost.** Every build is compiled before the first capture, in the one container, so the image is
started once. `--timeout` bounds each build, not all of them together. Each build then costs a simulator start and `--settle`.
Measured on an arm64 Mac, where the image runs emulated: four builds of a watch face compiled in
3 min 37 s, and each was then captured in about 25 s -- about five and a half minutes for the whole
survey.

**Not solved yet.** The clock moves between builds, so tiles taken a minute apart show different
times; `--timezone` fixes the zone, not the minute, and the simulator has no command-line hook for a
fixed time. An animated face also draws differently in every frame. A setting across several devices is
one run per device.

## Composed hero

A hero that reads as a product shot needs watches seen from several viewpoints, overlapping, under one
light. `hero` cannot make that: a 2D transform has no pixels for a case's side wall, lugs or crown,
and the face-on lighting is baked into each capture. An image model can, given the captures and a
precise prompt. `compose` handles everything around that call.

```bash
# fill in the prompt, to paste into the Gemini app with the captures
garmin-graphics-generator compose -p tools/hero-prompt.txt --print-prompt shots/watch-*.png

# size and screen what Gemini made; a store hero and a README banner from each
garmin-graphics-generator compose -p tools/hero-prompt.txt -o candidates/ \
   --checks tools/hero-checks.json -s 1440x720 -s 900x450 \
   -c ~/Downloads/gemini-1.png -c ~/Downloads/gemini-2.png shots/watch-*.png

# or generate four candidates through the API -- paid, see below
garmin-graphics-generator compose -p tools/hero-prompt.txt -o candidates/ \
   --checks tools/hero-checks.json -g 4 shots/watch-*.png
```

**Hand-made or generated.** Image generation through the Gemini API has no free tier: on a key without
billing, every image model answers with a quota of zero. Generating in the Gemini app is covered by a
subscription. So the default is the app: `--print-prompt` writes the prompt, the images made from it
come back with `-c`, and the command sizes and screens them. `-g N` makes the N calls itself instead.
It sends the prompt, every capture and an optional `--reference` image to `--model`
(`gemini-3-pro-image` by default), asking for the nearest aspect ratio at least as wide as the target
(21:9 for a 2:1 hero; the API offers no 2:1) at `--image-size` (4K by default).

**The prompt** is a template. `$count`, `$count_word`, `$width` and `$height` come from the inputs and
the first `-s`, and cannot be overridden, since screening counts against them; any other `$name` is
passed with `--var name=value` or a `--vars` JSON object of strings and numbers, and a placeholder with
no value is an error rather than sent to the model as it is. Roll, pitch and yaw
limits, the light, overlap, and the face's screen truths are the project's to word: they live in its
template, not here.

**Sizing.** Each candidate is turned upright by its EXIF orientation, flattened onto white where it
is transparent (a store hero must be opaque), cropped about its centre to the target ratio, and
resized to exactly each `-s` (1440x720 by default, which is what Connect IQ requires of a hero). Its
colour profile is kept. Ask the model for clear space at the left and right edges, which is what the
crop removes.

**Screening.** Each candidate is checked before you look at it:
- its first size is exact, and its file is within `--max-kb` (2048 by default, the Connect IQ limit);
- the crop had at least as many pixels across as the first size, so it was not enlarged: a
  1024x1024 image from the app crops to 1024x512 and fails a 1440x720 hero;
- a vision model (`--screen-model`, `gemini-3.8-flash` by default) counts the watch cases, which must
  equal the number of captures, and says whether any case is cut off by the edge;
- the same call answers each yes/no question in `--checks`, a JSON list such as

  ```json
  [{"name": "numerals-in-rain",
    "question": "Apart from the one time readout on each screen, does any screen show an Arabic numeral 0-9?",
    "expect": false}]
  ```

This is one model checking another, so a pass narrows the field and does not replace looking.
`--no-screen` runs only the local checks, needs no key, and cannot be combined with `--checks`.

**Failures.** Hand-made candidates are all read before the first is screened, so a broken file stops
the run before it has paid for anything. A generated image that cannot be read is kept as
`candidate-NN-failed-original.<ext>` beside its `-failed.json`, and the run goes on. Timeouts, dropped
connections and server errors are retried. An error that the next call would repeat -- no quota, no
credit, a refused key, a bad model name -- stops generation, and stops screening for the remaining
candidates, which are rejected and say why.

**Output.** Candidates are numbered on from the highest already in `-o`, so a rerun adds to the set and
nothing is ever overwritten. Each is `candidate-NN.png`, one `candidate-NN-WxH.png` per further size,
`candidate-NN-original.<ext>` as it came from the model, and `candidate-NN.json`: the model and the
version that served it (or the source file and its hash), the prompt's SHA-256, the parameters, every
check with its result, and the time. A rejected candidate has `-rejected` in all its names; a call that
returned no image leaves `candidate-NN-failed.json` saying why. The command exits 0 when at least one
candidate was accepted. It never puts one in place: copying the chosen one over the published hero is
yours to do.

**The key** is read from `--key-file`, or else from `GEMINI_API_KEY`. It is sent in a header, never in
a URL, and is never logged or written into a sidecar.

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

### Editions

A project that builds a second edition from the same tree, by layering an edition jungle last,

```bash
monkeyc -f "monkey.jungle;premium.jungle" ...
```

can give that edition its own icons. Point the command at the edition's manifest and jungle, and at a
directory of its own for the icons:

```bash
garmin-graphics-generator icons -p ../my-watch-face -R ../my-watch-face/tools/premium_icon.py \
    --manifest manifest-premium.xml --jungle premium.jungle --icon-root premium \
    --fallback-icon premium/resources-base/drawables/launcher_icon.png
```

Every path but the renderer's is relative to `-p`; the renderer is found from the current directory.
The icons go into `premium/resources-icon-<size>/`, and the mapping into `premium.jungle`:

```
venu3.resourcePath = $(venu3.resourcePath);premium/resources-icon-70
```

monkeyc resolves a jungle's paths against the jungle's own directory, so that is what the entries are
relative to: a `premium/premium.jungle` gets `;resources-icon-70`. A path with a space is written
quoted, `;"premium edition/resources-icon-70"`, as the jungle syntax requires: unquoted, monkeyc reads
a different path, and builds with the shared icon without a word.

Because the edition jungle comes last, its entry comes after the shared one, and monkeyc takes the
later `LauncherIcon`. A build that does not name the edition jungle never sees these icons.

The fallback icon needs a `drawables.xml` declaring it in the same directory; that file is the
project's, since it may declare other bitmaps, and the command writes only the image. It covers less
than the shared one does: a product the shared jungle maps but the edition's does not yet gets the
**shared** per-size icon, because the shared entry outranks any directory on the base resource path.
The edition's `--check` reports that product as unmapped.

`--jungle` and `--icon-root` go together, and generating for an edition needs `--fallback-icon PATH`
or `--no-fallback-icon`. Either one alone would fall back to the shared default for the rest, and
overwrite the shared edition's icons or mapping. For the same reason, the command refuses to replace a
generated block that maps into a different directory than the one it was given, and checks every path
before it writes anything. Each block's comment carries the command line that regenerates it.

`--check` and `--table` take the same options, so each edition is checked on its own. A mapping entry
pointing into another edition's directory does not count as a mapping for this one.

### Checking

`--check` verifies that the jungle exists, that every product in the manifest has a mapping, that no
mapping names a product outside it, that every icon is the size its directory promises, that each
declares `LauncherIcon`, that no icon directory is left unmapped, and — when the SDK is installed —
that every mapping matches the size the SDK declares for that device. It needs no SDK: the committed
mapping is itself the device-to-size table, and the SDK is used only to cross-check it. Hook it into
the watch face's own `Makefile` and it fails the build when the device list and the icons drift apart.

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

The CLI has four commands, `shots`, `hero`, `compose` and `icons`. An invocation naming none of them is treated
as `hero`, so the flat form the tool had before `icons` existed keeps working.

For details about the available command line options, see `garmin-graphics-generator --help`, and
`garmin-graphics-generator <command> --help`:

```bash
garmin-graphics-generator --help

# Output
usage: garmin-graphics-generator [-h] [--about] {hero,compose,icons,shots} ...

Capture watch face screenshots, and generate hero images and per-device launcher icons.

positional arguments:
  {hero,compose,icons,shots}
    hero                Generate a hero image from watch face screenshots
    compose             Size and screen hero candidates composed by an image model
    icons               Generate per-device launcher icons and their jungle mapping
    shots               Capture watch face screenshots from the simulator, headlessly

options:
  -h, --help            show this help message and exit
  --about               Print tool information and exit
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
                                       [--work-directory WORK_DIRECTORY] [--sweep |
                                       --grid ACROSS DOWN | --cases FILE | --all] [--force]
                                       [--vary KEY] [--set KEY=VALUE[,VALUE...]] [--resources DIR]
                                       [--scene NAME=JUNGLE] [-v | -q]

options:
  -h, --help            show this help message and exit
  -p, --project-directory PROJECT_DIRECTORY
                        Watch face project directory, the one holding monkey.jungle
  -d, --device DEVICE   Product to capture, as named in manifest.xml (e.g. epix2pro47mm)
  -o, --output-directory OUTPUT_DIRECTORY
                        Where to write the screen and watch images
  -n, --count COUNT     How many frames to capture (default: 4, or 1 per build when varying
                        settings)
  -i, --interval INTERVAL
                        Seconds between frames; what makes an animated face look different in each
                        (default: 3.0)
  --settle SETTLE       Seconds to let the face run before the first frame, so a capture is not of
                        its opening state (default: 6.0)
  --prefix PREFIX       Prepended to every output filename
  --jungle JUNGLE       Jungle file to build, relative to the project directory; several separated
                        by ';', later ones overriding earlier ones, as monkeyc takes them
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

settings:
  Capture one build per combination of settings, and lay the frames out on a labelled contact-
  sheet.png with an index.json. Any of these turns it on.

  --sweep               Vary one setting at a time, the others at their defaults (the default)
  --grid ACROSS DOWN    Every pair of values of two settings, as columns by rows
  --cases FILE          A JSON list of combinations, e.g. [{"timeSize": "6"}, {}]
  --all                 Every combination of the varied settings; refused above 24
  --force               Allow --all above 24 combinations
  --vary KEY            A property to vary; repeat for more. Default, with no --set either: every
                        list and boolean setting
  --set KEY=VALUE[,VALUE...]
                        The values to try for a property, replacing those its setting lists
  --resources DIR       A resource directory the build uses, relative to the project; repeat for
                        more (default: resources)
  --scene NAME=JUNGLE   Capture every combination with this jungle list, under NAME, e.g. always-
                        on='monkey.jungle;aod.jungle'; repeat for more; replaces --jungle
```

```bash
garmin-graphics-generator compose --help

# Output
usage: garmin-graphics-generator compose [-h] -p FILE [-o OUTPUT_DIRECTORY] [--print-prompt |
                                         -c FILE | -g N] [--var NAME=VALUE] [--vars FILE]
                                         [--reference FILE] [-s WxH] [--max-kb MAX_KB]
                                         [--format {jpg,png}] [--checks FILE] [--no-screen]
                                         [--screen-model SCREEN_MODEL] [--model MODEL]
                                         [--image-size {512px,1K,2K,4K,auto}] [--key-file FILE]
                                         [-v | -q]
                                         input_files [input_files ...]

positional arguments:
  input_files           The watch captures the hero is composed from, e.g. shots' watch-*.png

options:
  -h, --help            show this help message and exit
  -p, --prompt FILE     Prompt template; $count, $count_word, $width and $height are filled in,
                        and any other $name from --var
  -o, --output-directory OUTPUT_DIRECTORY
                        Where to write the candidates
  --print-prompt        Print the filled-in prompt, to paste into the Gemini app, and stop
  -c, --candidate FILE  An image generated by hand from the prompt; repeat for more
  -g, --generate N      Generate N candidates through the API instead. Paid: image generation has
                        no free tier
  --var NAME=VALUE      A value for a $name in the prompt; repeat for more
  --vars FILE           A JSON object of prompt values; --var overrides it
  --reference FILE      With --generate: an image showing what the screens' glyphs look like
  -s, --size WxH        Output size; repeat for more. The first is the one screened (default:
                        1440x720)
  --max-kb MAX_KB       Reject a candidate whose first size is larger; 0 for no limit (default:
                        2048, the Connect IQ hero limit)
  --format {jpg,png}    Output format
  --checks FILE         The face's screen truths, a JSON list of {"name", "question", "expect"},
                        put to the screening model as yes/no questions
  --no-screen           Skip the screening model; only the size checks run, and no key is needed
  --screen-model SCREEN_MODEL
                        Model that screens each candidate (default: gemini-3.8-flash)
  --model MODEL         Image model for --generate (default: gemini-3-pro-image)
  --image-size {512px,1K,2K,4K,auto}
                        Size to ask --generate's model for; auto leaves it to the model, for
                        models that take no size (default: 4K)
  --key-file FILE       File holding the API key (default: $GEMINI_API_KEY)
  -v, --verbose         Enable verbose output
  -q, --silent          Suppress all output except errors
```

```bash
garmin-graphics-generator icons --help

# Output
usage: garmin-graphics-generator icons [-h] [-p PROJECT_DIRECTORY] [-d DEVICES_DIRECTORY] [-R RENDERER]
                                       [--manifest MANIFEST] [--jungle JUNGLE] [--icon-root ICON_ROOT]
                                       [--fallback-icon PATH | --no-fallback-icon] [--check | --table]
                                       [-v | -q]

options:
  -h, --help            show this help message and exit
  -p, --project-directory PROJECT_DIRECTORY
                        Watch face project directory; the manifest, jungle, icon root and fallback icon
                        paths are relative to it, the renderer is not
  -d, --devices-directory DEVICES_DIRECTORY
                        SDK directory holding one compiler.json per device
  -R, --renderer RENDERER
                        How to draw one icon: 'resample:<master.png>' to resample a master image, or
                        '<file>.py[:<name>]' / '<module>:<name>' for a callable taking the edge in
                        pixels and returning a square image of that size
  --manifest MANIFEST   Manifest the products are read from (default: manifest.xml)
  --jungle JUNGLE       The one jungle file the mapping is spliced into, not a build list as for shots
                        (default: monkey.jungle); give it with --icon-root
  --icon-root ICON_ROOT
                        Directory holding the resources-icon-<size>/ directories (default: the project
                        directory); give it with --jungle
  --fallback-icon PATH  Where to write the fallback icon, drawn at the largest size (default:
                        resources/drawables/launcher_icon.png)
  --no-fallback-icon    Do not write the fallback icon
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

### Releasing

A release PR changes the version in `pyproject.toml` and regenerates the [About](#about) transcript, and its description holds the release notes. After merging it, save the notes as `notes.md` and tag `main`:

```bash
git checkout main && git pull
git tag -a vX.Y.Z -m 'Release X.Y.Z' && git push origin vX.Y.Z
```

The tag push runs `.github/workflows/release.yml`, which refuses a tag that is not `v` plus the version in `pyproject.toml`, builds the sdist and the wheel, and attaches them to a *draft* release. Once `gh release view vX.Y.Z` shows the draft, publish it with the notes:

```bash
gh release edit vX.Y.Z --title 'vX.Y.Z <summary>' --notes-file notes.md --draft=false
```

## About

```bash
garmin-graphics-generator --about

garmin-graphics-generator: A CLI tool for watch face imagery: simulator screenshots, hero images and launcher icons
├─ version:   0.7.0
├─ developer: mailto:waclaw.kusnierczyk@gmail.com
├─ source:    https://github.com/wkusnierczyk/garmin_graphics_generator
└─ licence:   MIT https://opensource.org/licenses/MIT
```
