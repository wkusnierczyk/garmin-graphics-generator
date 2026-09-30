"""
Laying captured frames out as one labelled image, for reviewing a face's settings.

A setting is reviewed by seeing its values side by side, and a folder of numbered
PNGs does not show that: which file is which value is in ``index.json``, not in
front of the reader. The sheet puts the label under each frame, a heading over each
row, and, for a grid, the column's value over each column.

The sheet is for review, not for a store listing: plain text on a dark ground,
which is what a watch face's own screen looks against.
"""
from typing import NamedTuple, Optional, Sequence

from PIL import Image, ImageDraw, ImageFont

from .variants import Plan

BACKGROUND = (24, 24, 24, 255)
HEADING_COLOUR = (240, 240, 240, 255)
LABEL_COLOUR = (190, 190, 190, 255)
MARGIN = 24
GAP = 16
# Space between a line of text and what it belongs to.
TEXT_GAP = 6


class Block(NamedTuple):
    """
    One plan's frames, under a heading.

    ``images`` is one image path per combination in ``plan.combinations``, in the
    same order. A capture across several scenes -- woken and always-on, say -- is
    one block per scene.
    """

    heading: str
    plan: Plan
    images: Sequence[str]


def _font(size: int):
    """Pillow's own font at a size, which needs Pillow 10.1; its bitmap font before that."""
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def _text_size(font, text: str):
    if not text:
        return 0, 0
    left, top, right, bottom = ImageDraw.Draw(
        Image.new("RGB", (1, 1))
    ).multiline_textbbox((0, 0), text, font=font)
    return right - left, bottom - top


def compose(
    blocks: Sequence[Block], output_path: str, title: Optional[str] = None
) -> str:
    """Writes the contact sheet for ``blocks`` to ``output_path`` and returns it."""
    blocks = [block for block in blocks if block.plan.rows]
    if not blocks:
        raise ValueError("nothing to lay out")

    with Image.open(blocks[0].images[0]) as first:
        tile_width, tile_height = first.size
    heading_font = _font(max(16, tile_width // 16))
    label_font = _font(max(13, tile_width // 22))

    # Every row is as tall as its tallest label, and every block as wide as its
    # widest row; the sheet is as wide as its widest block.
    layout = []
    width = 0
    height = MARGIN
    title_height = _text_size(heading_font, title or "")[1]
    if title:
        height += title_height + GAP
    for block in blocks:
        entry = {"block": block, "top": height}
        heading_height = _text_size(heading_font, block.heading)[1]
        if block.heading:
            height += heading_height + GAP
        columns_height = 0
        if block.plan.columns:
            columns_height = max(
                _text_size(label_font, column)[1] for column in block.plan.columns
            )
            height += columns_height + TEXT_GAP
        rows = []
        for row in block.plan.rows:
            row_heading_height = _text_size(label_font, row.heading)[1]
            label_height = max(
                (_text_size(label_font, tile.label)[1] for tile in row.tiles), default=0
            )
            top = height
            height += (row_heading_height + TEXT_GAP) if row.heading else 0
            tiles_top = height
            height += tile_height + ((TEXT_GAP + label_height) if label_height else 0)
            height += GAP
            rows.append((row, top, tiles_top))
            width = max(width, len(row.tiles) * (tile_width + GAP) - GAP)
        entry.update(
            rows=rows, heading_height=heading_height, columns_height=columns_height
        )
        layout.append(entry)
        height += GAP
    width = max(width, _text_size(heading_font, title or "")[0])
    width += 2 * MARGIN
    height += MARGIN - 2 * GAP

    sheet = Image.new("RGBA", (width, height), BACKGROUND)
    draw = ImageDraw.Draw(sheet)
    if title:
        draw.text((MARGIN, MARGIN), title, font=heading_font, fill=HEADING_COLOUR)

    for entry in layout:
        block = entry["block"]
        y = entry["top"]
        if block.heading:
            draw.text(
                (MARGIN, y), block.heading, font=heading_font, fill=HEADING_COLOUR
            )
            y += entry["heading_height"] + GAP
        if block.plan.columns:
            for column, text in enumerate(block.plan.columns):
                draw.multiline_text(
                    (MARGIN + column * (tile_width + GAP), y),
                    text,
                    font=label_font,
                    fill=HEADING_COLOUR,
                )
        for row, top, tiles_top in entry["rows"]:
            if row.heading:
                draw.text(
                    (MARGIN, top), row.heading, font=label_font, fill=HEADING_COLOUR
                )
            for column, tile in enumerate(row.tiles):
                x = MARGIN + column * (tile_width + GAP)
                with Image.open(block.images[tile.combination]) as frame:
                    frame = frame.convert("RGBA")
                    if frame.size != (tile_width, tile_height):
                        frame = frame.resize((tile_width, tile_height))
                    sheet.alpha_composite(frame, (x, tiles_top))
                if tile.label:
                    draw.multiline_text(
                        (x, tiles_top + tile_height + TEXT_GAP),
                        tile.label,
                        font=label_font,
                        fill=LABEL_COLOUR,
                    )

    sheet.convert("RGB").save(output_path)
    return output_path
