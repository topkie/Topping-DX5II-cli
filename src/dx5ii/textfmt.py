r"""Format the markdown manual as readable terminal text.

The manual is written in markdown so it can live in the repository and be read
on a web page. Printing it verbatim puts ``#``, ``|`` and backtick characters on
the screen, which is noise in a terminal. This module converts the small subset
of markdown the manual actually uses:

``# title``
    The text followed by a line of ``=``.
``## title``
    The text followed by a line of ``-``.
``| a | b |``
    A column-aligned table. Cell widths use the display width, so a CJK
    character counts as two columns.
``- item`` / ``1. item``
    A bullet, or the number kept as is. Continuation lines are joined back into
    one item.
``\`\`\` `` block
    Kept verbatim, indented by four spaces.
inline
    ``**bold**``, ``_italic_`` and ```code``` lose their markers; a link becomes
    ``text (url)``.
paragraphs
    Lines that belong to one paragraph are joined and re-wrapped to the
    terminal width, because the markdown hard-wraps at 80 columns.

No ANSI colour is emitted: the output stays valid when redirected to a file.
"""

from __future__ import annotations

import re
import textwrap
import unicodedata

_BULLET = "- "
_BULLET_MARKS = ("- ", "* ", "+ ")
_ORDERED = re.compile(r"^(\d+)\.\s+(.*)$")
_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
_FENCE = re.compile(r"^\s*```")
_SEPARATOR_CELL = re.compile(r"^[-: ]+$")


def display_width(text: str) -> int:
    """Width of ``text`` in terminal columns (CJK counts as two)."""
    return sum(
        2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1 for ch in text
    )


def _pad(text: str, width: int) -> str:
    return text + " " * max(0, width - display_width(text))


def _inline(text: str) -> str:
    """Strip the inline markup the manual uses."""
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
    text = re.sub(r"(?<![\w*])\*([^*]+)\*(?![\w*])", r"\1", text)
    text = re.sub(r"(?<![\w_])_([^_]+)_(?![\w_])", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"\1 (\2)", text)
    return text


def _is_table_row(line: str) -> bool:
    stripped = line.strip()
    return stripped.startswith("|") and stripped.endswith("|") and len(stripped) > 2


def _table_cells(line: str) -> list[str]:
    inner = line.strip()[1:-1]
    # A cell may escape a pipe as \|; keep it out of the split.
    inner = inner.replace("\\|", "\x00")
    return [cell.replace("\x00", "|").strip() for cell in inner.split("|")]


def _render_table(rows: list[str], width: int) -> list[str]:
    parsed = [[_inline(cell) for cell in _table_cells(row)] for row in rows]
    body = [cells for cells in parsed
            if not all(_SEPARATOR_CELL.match(cell) and cell for cell in cells)]
    if not body:
        return []
    columns = max(len(cells) for cells in body)
    widths = [0] * columns
    for cells in body:
        for index, cell in enumerate(cells):
            widths[index] = max(widths[index], display_width(cell))

    gap = 2 * (columns - 1)
    total = sum(widths)
    if total + gap > width:
        # Shrink every column in proportion instead of sacrificing the widest
        # one, so a long command column does not squeeze its neighbour to nothing.
        room = max(12 * columns, width - gap)
        scaled = [max(12, int(column * room / total)) for column in widths]
        while sum(scaled) > room:
            index_of_widest = scaled.index(max(scaled))
            scaled[index_of_widest] -= 1
        widths = scaled

    wrapped = []
    for cells in body:
        cells = cells + [""] * (columns - len(cells))
        wrapped.append([
            textwrap.wrap(cell, width=max(8, widths[index]),
                          break_long_words=True, break_on_hyphens=False) or [""]
            for index, cell in enumerate(cells)
        ])

    out = []
    for position, cell_lines in enumerate(wrapped):
        height = max(len(lines) for lines in cell_lines)
        for row_index in range(height):
            pieces = []
            for index, lines in enumerate(cell_lines):
                text = lines[row_index] if row_index < len(lines) else ""
                pieces.append(_pad(text, widths[index]))
            out.append("  ".join(pieces).rstrip())
        if position == 0:
            out.append("  ".join("-" * column for column in widths))
    return out


def _join_paragraph(lines: list[str]) -> str:
    return " ".join(line.strip() for line in lines if line.strip())


def render_markdown(
    text: str,
    width: int = 80,
    ascii_only: bool = True,
) -> str:
    """Convert manual markdown into terminal text of at most ``width`` columns."""
    lines = text.splitlines()
    out: list[str] = []
    index = 0
    paragraph: list[str] = []

    def flush() -> None:
        if not paragraph:
            return
        joined = _inline(_join_paragraph(paragraph))
        if not ascii_only:
            joined = joined.replace(" - ", " — ")
        out.extend(textwrap.wrap(
            joined, width=width, break_long_words=False, break_on_hyphens=False,
        ) or [""])
        paragraph.clear()

    def blank() -> None:
        if out and out[-1] != "":
            out.append("")

    while index < len(lines):
        line = lines[index]
        stripped = line.strip()

        if _FENCE.match(line):
            flush()
            blank()
            index += 1
            while index < len(lines) and not _FENCE.match(lines[index]):
                out.append("    " + lines[index].rstrip())
                index += 1
            index += 1
            blank()
            continue

        if not stripped:
            flush()
            blank()
            index += 1
            continue

        heading = _HEADING.match(stripped)
        if heading:
            flush()
            blank()
            title = _inline(heading.group(2)).strip()
            level = len(heading.group(1))
            out.append(title)
            if level == 1:
                out.append("=" * display_width(title))
            elif level == 2:
                out.append("-" * display_width(title))
            blank()
            index += 1
            continue

        if _is_table_row(line):
            flush()
            blank()
            rows = []
            while index < len(lines) and _is_table_row(lines[index]):
                rows.append(lines[index])
                index += 1
            out.extend(_render_table(rows, width))
            blank()
            continue

        ordered = _ORDERED.match(stripped)
        bulleted = stripped.startswith(_BULLET_MARKS)
        if ordered or bulleted:
            flush()
            if ordered:
                marker = ordered.group(1) + ". "
                body = ordered.group(2)
            else:
                marker = ("* " if ascii_only else "• ")
                body = stripped[2:]
            item = [body]
            index += 1
            while index < len(lines):
                follow = lines[index]
                if not follow.strip():
                    break
                if (_HEADING.match(follow.strip()) or _is_table_row(follow)
                        or _FENCE.match(follow)
                        or follow.strip().startswith(_BULLET_MARKS)
                        or _ORDERED.match(follow.strip())
                        or len(follow) - len(follow.lstrip()) < 2):
                    break
                item.append(follow.strip())
                index += 1
            joined = _inline(_join_paragraph(item))
            wrapped = textwrap.wrap(
                joined, width=max(20, width - len(marker) - 2),
                break_long_words=False, break_on_hyphens=False,
            ) or [""]
            out.append("  " + marker + wrapped[0])
            for extra in wrapped[1:]:
                out.append("  " + " " * len(marker) + extra)
            continue

        paragraph.append(stripped)
        index += 1

    flush()
    while out and out[-1] == "":
        out.pop()
    return "\n".join(out) + "\n"
