"""Render a canonical layout plan into an updated RTF."""

from __future__ import annotations

import re
from pathlib import Path

from ..rtf.text import TableOccurrence, escape_text, find_table_body
from .model import LayoutPlan, expand_plan_to_tables

CELL_PARA_OPEN = (
    "\\pard\\plain \\ltrpar"
    "\\s66\\ql \\li0\\ri0\\sb60\\sa60\\widctlpar\\intbl\\tx567\\wrapdefault"
    "\\aspalpha\\aspnum\\faauto\\adjustright\\rin0\\lin0\\pararsid2712918 "
    "\\rtlch\\fcs1 \\af0\\afs20\\alang1025 \\ltrch\\fcs0 "
    "\\f1\\fs20\\lang2057\\langfe2057\\cgrid\\langnp2057\\langfenp2057 "
)

TERMINATOR_PARA_OPEN = (
    "\\pard\\plain \\ltrpar\\ql \\li0\\ri0\\sa200\\sl276\\slmult1\\widctlpar"
    "\\intbl\\wrapdefault\\aspalpha\\aspnum\\faauto\\adjustright\\rin0\\lin0 "
    "\\rtlch\\fcs1 \\af0\\afs20\\alang1025 \\ltrch\\fcs0 "
    "\\f1\\fs20\\lang2057\\langfe2057\\cgrid\\langnp2057\\langfenp2057 "
    "{\\rtlch\\fcs1 \\af0 \\ltrch\\fcs0 \\insrsid2712918\\charrsid619144 "
)

RUN_PREFIX = (
    "\\rtlch\\fcs1 \\af0 \\ltrch\\fcs0 "
    "\\insrsid2712918\\charrsid4870674"
)


def _capture_row_definition(body: str) -> str:
    start = body.find("\\trowd ")
    if start == -1:
        raise ValueError("table body has no row definition")

    cell_boundaries = list(re.finditer(r"\\cellx\d+", body[start:]))
    if len(cell_boundaries) < 2:
        raise ValueError("table row definition has fewer than two cells")

    return body[start : start + cell_boundaries[1].end()]


def _row_definition(row_definition: str, index: int, *, last: bool) -> str:
    row_numbers = f"\\irow{index}\\irowband{index}"
    if last:
        row_numbers += "\\lastrow"
    return re.sub(
        r"\\irow\d+\\irowband\d+",
        lambda _match: row_numbers,
        row_definition,
        count=1,
    )


def _make_row(
    row_definition: str,
    index: int,
    left: str,
    right: str,
    *,
    first: bool,
    last: bool,
) -> str:
    row = _row_definition(row_definition, index, last=last)
    cells = (
        CELL_PARA_OPEN
        + "{"
        + RUN_PREFIX
        + " "
        + escape_text(left)
        + "\\cell }"
        + "{"
        + RUN_PREFIX
        + " "
        + escape_text(right)
        + "\\cell }"
    )
    close = "\\row }" if last else "\\row \\ltrrow}"
    terminator = TERMINATOR_PARA_OPEN + row + close
    return row + cells + terminator if first else cells + terminator


def _make_table_body(
    left: tuple[str, ...],
    right: tuple[str, ...],
    row_definition: str,
) -> str:
    row_count = max(len(left), len(right))
    return "".join(
        _make_row(
            row_definition,
            index,
            left[index] if index < len(left) else "",
            right[index] if index < len(right) else "",
            first=index == 0,
            last=index == row_count - 1,
        )
        for index in range(row_count)
    )


def _outside_table_bodies(
    rtf: str,
    occurrences: list[TableOccurrence],
) -> str:
    spans = sorted(find_table_body(rtf, occurrence) for occurrence in occurrences)
    segments: list[str] = []
    previous_end = 0
    for start, end in spans:
        segments.append(rtf[previous_end:start])
        previous_end = end
    segments.append(rtf[previous_end:])
    return "".join(segments)


def render_rtf(source: Path, plan: LayoutPlan, output: Path) -> None:
    """Write an RTF whose judge-table bodies follow ``plan``."""
    tables = expand_plan_to_tables(plan)
    occurrences = [
        TableOccurrence(table.occurrence)
        for table in tables
    ]

    original = source.read_text(encoding="latin-1")
    rendered = original

    for table in sorted(
        tables,
        key=lambda value: value.occurrence,
        reverse=True,
    ):
        occurrence = TableOccurrence(table.occurrence)
        start, end = find_table_body(rendered, occurrence)
        row_definition = _capture_row_definition(rendered[start:end])
        body = _make_table_body(
            table.left,
            table.right,
            row_definition,
        )
        rendered = rendered[:start] + body + rendered[end:]

    if _outside_table_bodies(
        original,
        occurrences,
    ) != _outside_table_bodies(rendered, occurrences):
        raise RuntimeError("render changed content outside the judge-table bodies")

    output.write_text(rendered, encoding="latin-1")
