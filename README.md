       # Garmin Graphics Generator

A CLI tool and library to process lists of watch images. It removes white backgrounds, generates a composite "hero" image with scattered watches, and creates resized individual files.

## Features

* **Background Removal:** Automatically strips white backgrounds from input images.
* **Hero Generation:** Creates a 1440x720 (configurable) composite image with randomized placement, rotation, and sizing.
* **Batch Resizing:** Resizes all processed images to a standard width.
* **Fluent API:** Easy to use Python API for integration into other scripts.

## Installation

```bash
git clone <repository-url>
cd garmin_graphics_generator
pip install .
```

## Usage

### CLI

```bash
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

**View About Information:**
```bash
garmin-graphics-generator --about
```

### Python Library

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

1.  **Install dev dependencies:**
    ```bash
    make install
    ```
2.  **Run tests:**
    ```bash
    make test
    ```
3.  **Run linting:**
    ```bash
    make lint
    ```