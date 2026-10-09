"""
The product to icon size table in a project README: finding it, and rewriting it.

The table is located by an anchor the project supplies, typically the sentence
introducing it, and is the run of ``|`` lines directly under that line. Both the
writer and the reader find it the same way, so the check never reads a table the
generator would not write.

Texts are split on "\\n" alone, so a line keeps a "\\r" it ends with: every line
outside the table is written back exactly as it was, whatever its ending.
"""
import re
from typing import Dict, List, NamedTuple, Optional, Tuple


class ReadmeTableError(Exception):
    """Raised when the anchor does not pick out exactly one line."""


# One row of the table as launcher_icons.table() writes it. Read as width and height,
# so a row stating a non-square size disagrees with the mapping rather than going
# unread.
TABLE_ROW_PATTERN = re.compile(r"^\|\s*(\S+)\s*\|\s*(\d+)\s*x\s*(\d+)\s*\|\s*$")
TABLE_SEPARATOR_PATTERN = re.compile(r"^\|(\s*:?-+:?\s*\|)+\s*$")
# A fenced code block opens and closes with a run of three or more of one of these.
FENCE_PATTERN = re.compile(r"^\s{0,3}(`{3,}|~{3,})")
# What may precede the anchor on its line: indentation, and a heading's markers.
LINE_LEAD_PATTERN = re.compile(r"^\s*(#+\s+)?")


def anchor_line(lines: List[str], anchor: Optional[str]) -> int:
    """
    The index of the one line the anchor begins, outside fenced code.

    The anchor may follow indentation or a heading's ``#`` markers, and may be the
    whole line or its start, but not text further along it: a command quoting the
    anchor is not where the table goes. Raises when no line, or more than one,
    begins with it.
    """
    if not anchor:
        raise ReadmeTableError("the anchor is empty")
    found: List[int] = []
    fence: Optional[str] = None
    for index, line in enumerate(lines):
        line = line.rstrip("\r")
        opening = FENCE_PATTERN.match(line)
        if fence is not None:
            # Closed by a run of the same character, at least as long.
            if opening and opening.group(1)[0] == fence[0]:
                if len(opening.group(1)) >= len(fence) and not line.strip(
                    fence[0] + " \t"
                ):
                    fence = None
            continue
        if opening:
            fence = opening.group(1)
            continue
        if line[LINE_LEAD_PATTERN.match(line).end() :].startswith(anchor):
            found.append(index)
    if not found:
        raise ReadmeTableError(f"no line begins with {anchor!r} outside code")
    if len(found) > 1:
        raise ReadmeTableError(
            f"{len(found)} lines begin with {anchor!r} outside code (lines "
            + ", ".join(str(index + 1) for index in found)
            + "); make the anchor unique"
        )
    return found[0]


def _table_span(lines: List[str], anchor: int) -> Optional[Tuple[int, int]]:
    """
    Where the table is: the run of ``|`` lines following the anchor's line, with
    nothing but blank lines between. None when anything else comes first.
    """
    start = anchor + 1
    while start < len(lines) and not lines[start].strip():
        start += 1
    if start == len(lines) or not lines[start].startswith("|"):
        return None
    end = start
    while end < len(lines) and lines[end].startswith("|"):
        end += 1
    return start, end


def splice_table(text: str, anchor: str, rendered: str) -> str:
    """
    Replaces the table under ``anchor`` in a markdown text, or inserts one.

    The table under the anchor is the run of ``|`` lines following the anchor's
    line with only blank lines between. When anything else follows, a table is
    inserted directly after the anchor's line, set off by a blank line on either
    side, and nothing further down is touched.

    The table's lines end as the anchor's line does; every other line keeps its
    own ending, byte for byte.
    """
    lines = text.split("\n")
    anchor_index = anchor_line(lines, anchor)
    ending = "\r" if lines[anchor_index].endswith("\r") else ""
    rows = [row + ending for row in rendered.rstrip("\n").split("\n")]
    span = _table_span(lines, anchor_index)
    if span is not None:
        start, end = span
    else:
        start = end = anchor_index + 1
        rows = [ending] + rows
        # The blank lines already after the anchor, if any, set the table off
        # from what follows; otherwise one is added.
        if start < len(lines) and lines[start].strip():
            rows.append(ending)
    return "\n".join(lines[:start] + rows + lines[end:])


class Table(NamedTuple):
    """The README table as read back: its rows, and what in it did not read."""

    # product -> (width, height); None when there is no table under the anchor.
    rows: Optional[Dict[str, Tuple[int, int]]]
    problems: List[str]


def read_table(text: str, anchor: str) -> Table:
    """
    Reads the table under ``anchor`` back: the same lines :func:`splice_table` writes.

    Every line but the header and the separator has to be a product and a size; a
    line that is not, and a product listed twice, are reported as problems.
    """
    lines = text.split("\n")
    span = _table_span(lines, anchor_line(lines, anchor))
    if span is None:
        return Table(None, [])
    start, end = span
    rows: Dict[str, Tuple[int, int]] = {}
    problems: List[str] = []
    if end - start < 2 or not TABLE_SEPARATOR_PATTERN.match(lines[start + 1]):
        problems.append("no header and separator row")
        body = lines[start + 1 : end]
    else:
        body = lines[start + 2 : end]
    for line in body:
        match = TABLE_ROW_PATTERN.match(line)
        if not match:
            problems.append(f"unreadable row {line.strip()!r}")
            continue
        product = match.group(1)
        if product in rows:
            problems.append(f"{product} is listed more than once")
            continue
        rows[product] = (int(match.group(2)), int(match.group(3)))
    return Table(rows, problems)


__all__ = [
    "ReadmeTableError",
    "Table",
    "anchor_line",
    "read_table",
    "splice_table",
]
