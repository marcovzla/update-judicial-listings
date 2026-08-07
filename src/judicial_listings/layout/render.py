"""Render a canonical layout plan into an updated RTF."""

from __future__ import annotations

import re
from difflib import SequenceMatcher
from pathlib import Path

from ..roster_types import ImmutableModel
from ..rtf.extract import extract_columns
from ..rtf.revisions import (
    add_revision_metadata,
    deleted_lines,
    inserted_text,
)
from ..rtf.text import RevisionView, TableOccurrence, escape_text, find_table_body
from .model import ExpandedTable, LayoutPlan, expand_plan_to_tables

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

RUN_PREFIX = "\\rtlch\\fcs1 \\af0 \\ltrch\\fcs0 \\insrsid2712918\\charrsid4870674"
CELL_BOUNDARY_RE = re.compile(r"\\cellx\d+")


class TableLines(ImmutableModel):
    left: tuple[str, ...]
    right: tuple[str, ...]


class LineRevision(ImmutableModel):
    text: str
    revised: bool = False
    deleted_before: tuple[str, ...] = ()
    replaced: tuple[str, ...] = ()
    deleted_after: tuple[str, ...] = ()


def _capture_row_definition(body: str) -> str:
    start = body.find("\\trowd ")
    if start == -1:
        raise ValueError("table body has no row definition")

    cell_boundaries = list(CELL_BOUNDARY_RE.finditer(body[start:]))
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


def _plan_column_revisions(
    original: tuple[str, ...],
    updated: tuple[str, ...],
) -> tuple[LineRevision, ...]:
    deleted_before: list[list[str]] = [[] for _ in updated]
    replaced: list[list[str]] = [[] for _ in updated]
    deleted_after: list[list[str]] = [[] for _ in updated]
    revised = [False for _ in updated]
    unattached: list[str] = []

    matcher = SequenceMatcher[str](None, original, updated, autojunk=False)
    for (
        tag,
        original_start,
        original_end,
        updated_start,
        updated_end,
    ) in matcher.get_opcodes():
        original_lines = original[original_start:original_end]
        updated_count = updated_end - updated_start

        if tag == "equal":
            continue
        if tag == "insert":
            for index in range(updated_start, updated_end):
                revised[index] = True
            continue
        if tag == "delete":
            if updated_start > 0:
                deleted_after[updated_start - 1].extend(original_lines)
            elif updated:
                deleted_before[0].extend(original_lines)
            else:
                unattached.extend(original_lines)
            continue

        for index in range(updated_start, updated_end):
            revised[index] = True
        paired_count = min(len(original_lines), updated_count)
        for offset in range(paired_count):
            replaced[updated_start + offset].append(original_lines[offset])
        if len(original_lines) > paired_count:
            deleted_after[updated_end - 1].extend(original_lines[paired_count:])

    if unattached:
        raise ValueError("cannot attach deleted lines to an empty updated column")

    return tuple(
        LineRevision(
            text=line,
            revised=revised[index],
            deleted_before=tuple(deleted_before[index]),
            replaced=tuple(replaced[index]),
            deleted_after=tuple(deleted_after[index]),
        )
        for index, line in enumerate(updated)
    )


def _render_line(revision: LineRevision | None) -> str:
    if revision is None:
        return ""

    rendered = ""
    if revision.deleted_before:
        rendered += deleted_lines(revision.deleted_before, trailing_line=True)
    if revision.replaced:
        rendered += deleted_lines(revision.replaced)
    rendered += (
        inserted_text(revision.text)
        if revision.revised
        else escape_text(revision.text)
    )
    if revision.deleted_after:
        rendered += deleted_lines(revision.deleted_after, leading_line=True)
    return rendered


def _at(values: tuple[LineRevision, ...], index: int) -> LineRevision | None:
    return values[index] if index < len(values) else None


def _make_row(
    row_definition: str,
    index: int,
    left: LineRevision | None,
    right: LineRevision | None,
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
        + _render_line(left)
        + "\\cell }"
        + "{"
        + RUN_PREFIX
        + " "
        + _render_line(right)
        + "\\cell }"
    )
    close = "\\row }" if last else "\\row \\ltrrow}"
    terminator = TERMINATOR_PARA_OPEN + row + close
    return row + cells + terminator if first else cells + terminator


def _make_table_body(
    original: TableLines,
    updated: TableLines,
    row_definition: str,
) -> str:
    left = _plan_column_revisions(original.left, updated.left)
    right = _plan_column_revisions(original.right, updated.right)
    row_count = max(len(left), len(right))
    if row_count == 0:
        raise ValueError("cannot render an empty judge table")
    return "".join(
        _make_row(
            row_definition,
            index,
            _at(left, index),
            _at(right, index),
            first=index == 0,
            last=index == row_count - 1,
        )
        for index in range(row_count)
    )


def _split_cell_lines(values: list[str]) -> tuple[str, ...]:
    return tuple(
        line.strip()
        for cell in values
        for line in cell.split("\n")
        if line.strip()
    )


def _extract_table_lines(
    rtf: str,
    occurrence: TableOccurrence,
    *,
    revision_view: RevisionView = RevisionView.ACCEPTED,
) -> TableLines:
    columns = extract_columns(
        rtf,
        occurrence,
        revision_view=revision_view,
        preserve_line_breaks=True,
    )
    return TableLines(
        left=_split_cell_lines(columns["left"]),
        right=_split_cell_lines(columns["right"]),
    )


def _expanded_table_lines(table: ExpandedTable) -> TableLines:
    return TableLines(
        left=tuple(line.text for line in table.left),
        right=tuple(line.text for line in table.right),
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


def _verify_revision_views(
    original: str,
    rendered: str,
    tables: list[ExpandedTable],
) -> None:
    for table in tables:
        occurrence = TableOccurrence(table.occurrence)
        original_lines = _extract_table_lines(original, occurrence)
        updated_lines = _expanded_table_lines(table)
        accepted_lines = _extract_table_lines(
            rendered,
            occurrence,
            revision_view=RevisionView.ACCEPTED,
        )
        rejected_lines = _extract_table_lines(
            rendered,
            occurrence,
            revision_view=RevisionView.REJECTED,
        )
        if accepted_lines != updated_lines:
            raise RuntimeError(
                f"table {table.occurrence}: accepted revisions do not match layout plan"
            )
        if rejected_lines != original_lines:
            raise RuntimeError(
                f"table {table.occurrence}: rejected revisions do not match "
                "source lines"
            )


def render_rtf(source: Path, plan: LayoutPlan, output: Path) -> None:
    """Write line-level revisions without table-structure revisions."""
    tables = expand_plan_to_tables(plan)
    occurrences = [TableOccurrence(table.occurrence) for table in tables]
    original = source.read_text(encoding="latin-1")
    original_lines = {
        table.occurrence: _extract_table_lines(
            original,
            TableOccurrence(table.occurrence),
        )
        for table in tables
    }
    updated_lines = {
        table.occurrence: _expanded_table_lines(table) for table in tables
    }
    changed_tables = [
        table
        for table in tables
        if original_lines[table.occurrence] != updated_lines[table.occurrence]
    ]
    if not changed_tables:
        output.write_text(original, encoding="latin-1")
        return

    render_base = add_revision_metadata(original)
    rendered = render_base
    for table in sorted(
        changed_tables,
        key=lambda value: value.occurrence,
        reverse=True,
    ):
        occurrence = TableOccurrence(table.occurrence)
        start, end = find_table_body(rendered, occurrence)
        row_definition = _capture_row_definition(rendered[start:end])
        body = _make_table_body(
            original_lines[table.occurrence],
            updated_lines[table.occurrence],
            row_definition,
        )
        rendered = rendered[:start] + body + rendered[end:]

    if _outside_table_bodies(
        render_base,
        occurrences,
    ) != _outside_table_bodies(rendered, occurrences):
        raise RuntimeError("render changed content outside the judge-table bodies")

    _verify_revision_views(original, rendered, tables)
    output.write_text(rendered, encoding="latin-1")
