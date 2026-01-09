# Garmin Graphics Generator

A CLI tool and library to process lists of watch face screenshots.   
It:
* removes white backgrounds;
* generates a composite "hero" pixel image with watch faces scatterred randomly;
* creates copies of input files resized to a common width.

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
garmin-graphics-generator \
   --output-directory ./output \
   --size-variation 5 \
   --orientation-variation 45 \
   --hero-file-name hero_banner.png \
   --hero-file-size 1440x720 \
   --resized-file-suffix _thumb \
   --resized-file-width 200 \
   my_watch_1.jpg my_watch_2.jpg
```

For details about the available command line options, see `garmin-graphics-generator --help`:
```bash
garmin-graphics-generator --help

# Output
usage: garmin-graphics-generator [-h] [--about] [-o OUTPUT_DIRECTORY] [-n HERO_FILE_NAME] [-H HERO_FILE_SIZE] [-l OVERLAP] [-s SIZE_VARIATION] [-r ORIENTATION_VARIATION]
                                 [--resized-file-suffix RESIZED_FILE_SUFFIX] [-w RESIZED_FILE_WIDTH] [-v | -q]
                                 [input_files ...]

Generate a hero image from watch face screenshots.

positional arguments:
  input_files           Input image files

options:
  -h, --help            show this help message and exit
  --about               Print tool information and exit
  -o OUTPUT_DIRECTORY, --output-directory OUTPUT_DIRECTORY
                        Directory to save output files
  -n HERO_FILE_NAME, --hero-file-name HERO_FILE_NAME
                        Name of the compound hero file
  -H HERO_FILE_SIZE, --hero-file-size HERO_FILE_SIZE
                        Size of hero file (WxH)
  -l OVERLAP, --overlap OVERLAP
                        0..100, percentage of allowed overlap between images
  -s SIZE_VARIATION, --size-variation SIZE_VARIATION
                        0..10, variance in size
  -r ORIENTATION_VARIATION, --orientation-variation ORIENTATION_VARIATION
                        0..90, max rotation angle in degrees
  --resized-file-suffix RESIZED_FILE_SUFFIX
                        Suffix for resized input files
  -w RESIZED_FILE_WIDTH, --resized-file-width RESIZED_FILE_WIDTH
                        Width of resized files
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

garmin-graphics-generator: A CLI tool to generate hero images from watch face screenshots
├─ version:   0.2.3
├─ developer: mailto:waclaw.kusnierczyk@gmail.com
├─ source:    https://github.com/wkusnierczyk/garmin_graphics_generator
└─ licence:   MIT https://opensource.org/licenses/MIT
```