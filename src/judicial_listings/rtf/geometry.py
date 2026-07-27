"""Extract table geometry and typography from a judicial-listings RTF."""

from __future__ import annotations

import re
from pathlib import Path

from ..roster_types import ImmutableModel, JudicialSection, clean_space
from .text import TableOccurrence, find_table_body

TWIPS_PER_POINT = 20
HALF_POINTS_PER_POINT = 2


class FontGeometry(ImmutableModel):
    number: int
    name: str
    size_half_points: int

    @property
    def size_points(self) -> float:
        return self.size_half_points / HALF_POINTS_PER_POINT


class CellGeometry(ImmutableModel):
    width_twips: int
    padding_left_twips: int
    padding_right_twips: int

    @property
    def content_width_twips(self) -> int:
        return (
            self.width_twips
            - self.padding_left_twips
            - self.padding_right_twips
        )

    @property
    def content_width_points(self) -> float:
        return self.content_width_twips / TWIPS_PER_POINT


class TableGeometry(ImmutableModel):
    section: JudicialSection
    font: FontGeometry
    left: CellGeometry
    right: CellGeometry


class RtfGeometry(ImmutableModel):
    tables: tuple[TableGeometry, ...]


def _group_end(rtf: str, start: int) -> int:
    depth = 0
    index = start
    while index < len(rtf):
        if rtf[index] == "\\" and rtf[index + 1 : index + 2] in "{}\\":
            index += 2
            continue
        if rtf[index] == "{":
            depth += 1
        elif rtf[index] == "}":
            depth -= 1
            if depth == 0:
                return index + 1
        index += 1
    raise ValueError("RTF group is not closed")


def _child_groups(group: str) -> list[str]:
    children: list[str] = []
    child_start: int | None = None
    depth = 0
    index = 0
    while index < len(group):
        if group[index] == "\\" and group[index + 1 : index + 2] in "{}\\":
            index += 2
            continue
        if group[index] == "{":
            depth += 1
            if depth == 2:
                child_start = index
        elif group[index] == "}":
            if depth == 2 and child_start is not None:
                children.append(group[child_start : index + 1])
                child_start = None
            depth -= 1
        index += 1
    return children


def _group_text(group: str) -> str:
    text: list[str] = []
    depth = 0
    index = 0
    while index < len(group):
        character = group[index]
        if character == "{":
            depth += 1
            index += 1
            continue
        if character == "}":
            depth -= 1
            index += 1
            continue
        if depth != 1:
            index += 1
            continue
        if character == ";":
            break
        if character != "\\":
            text.append(character)
            index += 1
            continue

        next_character = group[index + 1 : index + 2]
        if next_character.isalpha():
            index += 1
            while index < len(group) and group[index].isalpha():
                index += 1
            if group[index : index + 1] == "-":
                index += 1
            while index < len(group) and group[index].isdigit():
                index += 1
            if group[index : index + 1] == " ":
                index += 1
            continue
        if next_character == "'" and index + 3 < len(group):
            text.append(
                bytes([int(group[index + 2 : index + 4], 16)]).decode(
                    "cp1252",
                    "replace",
                )
            )
            index += 4
            continue
        if next_character in "{}\\":
            text.append(next_character)
        index += 2
    return clean_space("".join(text))


def _font_names(rtf: str) -> dict[int, str]:
    start = rtf.find(r"{\fonttbl")
    if start == -1:
        raise ValueError("RTF has no font table")
    font_table = rtf[start : _group_end(rtf, start)]
    fonts: dict[int, str] = {}
    for entry in _child_groups(font_table):
        match = re.search(r"\\f(\d+)", entry)
        if match is None:
            continue
        name = _group_text(entry)
        if name:
            fonts[int(match.group(1))] = name
    return fonts


def _control_values(rtf: str, name: str) -> list[int]:
    return [
        int(value)
        for value in re.findall(rf"\\{re.escape(name)}(-?\d+)", rtf)
    ]


def _control_value(rtf: str, name: str, default: int | None = None) -> int:
    values = _control_values(rtf, name)
    if values:
        return values[-1]
    if default is not None:
        return default
    raise ValueError(f"RTF row definition has no \\{name}")


def _row_definition(body: str) -> str:
    start = body.find(r"\trowd")
    if start == -1:
        raise ValueError("table has no row definition")
    cell_boundaries = list(re.finditer(r"\\cellx(-?\d+)", body[start:]))
    if len(cell_boundaries) < 2:
        raise ValueError("table row definition has fewer than two cells")
    return body[start : start + cell_boundaries[1].end()]


def _padding_twips(
    row: str,
    cell: str,
    side: str,
) -> int:
    cell_padding = _control_values(cell, f"clpad{side}")
    if cell_padding:
        unit = _control_value(cell, f"clpadf{side}", 3)
        if unit != 3:
            raise ValueError("cell padding is not expressed in twips")
        return cell_padding[-1]

    row_padding = _control_values(row, f"trpadd{side}")
    if row_padding:
        unit = _control_value(row, f"trpaddf{side}", 3)
        if unit != 3:
            raise ValueError("row padding is not expressed in twips")
        return row_padding[-1]

    return _control_value(row, "trgaph", 0)


def _cell_geometry(
    row: str,
    cell: str,
    derived_width: int,
) -> CellGeometry:
    widths = _control_values(cell, "clwWidth")
    width = widths[-1] if widths else derived_width
    geometry = CellGeometry(
        width_twips=width,
        padding_left_twips=_padding_twips(row, cell, "l"),
        padding_right_twips=_padding_twips(row, cell, "r"),
    )
    if geometry.content_width_twips <= 0:
        raise ValueError("table cell has no usable content width")
    return geometry


def _table_cells(row: str) -> tuple[CellGeometry, CellGeometry]:
    boundaries = list(re.finditer(r"\\cellx(-?\d+)", row))
    if len(boundaries) != 2:
        raise ValueError("expected a two-cell table row definition")

    row_left = _control_value(row, "trleft", 0)
    left_edge = int(boundaries[0].group(1))
    right_edge = int(boundaries[1].group(1))
    left_cell = row[: boundaries[0].end()]
    right_cell = row[boundaries[0].end() : boundaries[1].end()]
    return (
        _cell_geometry(row, left_cell, left_edge - row_left),
        _cell_geometry(row, right_cell, right_edge - left_edge),
    )


def _table_font(
    body: str,
    fonts: dict[int, str],
) -> FontGeometry:
    font_numbers = set(_control_values(body, "f"))
    sizes = set(_control_values(body, "fs"))
    if len(font_numbers) != 1:
        raise ValueError("table does not use exactly one font")
    if len(sizes) != 1:
        raise ValueError("table does not use exactly one font size")

    number = font_numbers.pop()
    if number not in fonts:
        raise ValueError(f"font \\f{number} is missing from the font table")
    return FontGeometry(
        number=number,
        name=fonts[number],
        size_half_points=sizes.pop(),
    )


def extract_geometry(rtf: str) -> RtfGeometry:
    """Extract geometry for all five judicial-listing tables."""
    fonts = _font_names(rtf)
    tables: list[TableGeometry] = []
    for section in JudicialSection:
        start, end = find_table_body(
            rtf,
            TableOccurrence(section.rtf_occurrence),
        )
        body = rtf[start:end]
        left, right = _table_cells(_row_definition(body))
        tables.append(
            TableGeometry(
                section=section,
                font=_table_font(body, fonts),
                left=left,
                right=right,
            )
        )
    return RtfGeometry(tables=tuple(tables))


def extract_rtf_geometry(source: Path) -> RtfGeometry:
    return extract_geometry(source.read_text(encoding="latin-1"))
