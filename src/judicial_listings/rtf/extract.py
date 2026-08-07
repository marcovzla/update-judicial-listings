"""Extract the canonical judicial roster from an All ER prelims RTF."""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

from ..roster_types import (
    Judge,
    JudicialSection,
    Roster,
    RosterSection,
    clean_space,
    normalize_name,
    parse_appointment_annotation,
)
from .text import RevisionView, TableOccurrence, decode_cells, find_table_body

NAME_RE = re.compile(
    r"^(?:Baroness|Dame|Sir|Lord Justice|Lady Justice|Mr Justice|Mrs Justice|"
    r"Ms Justice|Lord|Lady)\b"
)


def trim_trailing_empty(values: list[str]) -> list[str]:
    while values and not values[-1]:
        values.pop()
    return values


def extract_columns(
    rtf: str,
    occurrence: TableOccurrence,
    *,
    revision_view: RevisionView = RevisionView.ACCEPTED,
    preserve_line_breaks: bool = False,
) -> dict[str, list[str]]:
    start, end = find_table_body(rtf, occurrence)
    cells = decode_cells(
        rtf[start:end],
        revision_view=revision_view,
        preserve_line_breaks=preserve_line_breaks,
    )
    left = trim_trailing_empty(cells[0::2])
    right = trim_trailing_empty(cells[1::2])
    return {"left": left, "right": right}


def split_inline_name_details(line: str) -> tuple[str, str, date | None]:
    match = re.match(r"^(.*?)\s*\(([^)]*)\)\s*$", line)
    if match:
        suffix = clean_space(match.group(2))
        appointment = parse_appointment_annotation(suffix)
        return (
            clean_space(match.group(1)),
            "" if appointment is not None else suffix,
            appointment,
        )
    return clean_space(line), "", None


def make_judge(
    name: str,
    position: str,
    first_listing_appointment: date | None,
    lines: list[str],
) -> Judge:
    return Judge(
        name=name,
        position=clean_space(position).strip("()").strip(),
        first_listing_appointment=first_listing_appointment,
        lines=tuple(lines),
    )


def parse_people(columns: dict[str, list[str]]) -> list[Judge]:
    """Collapse column-list cells into judge records for comparison.

    A judge begins at a title/name line. Appointment annotations are captured
    separately; other following lines are joined as position fragments.
    """
    judges: list[Judge] = []
    for side in ("left", "right"):
        name = ""
        position = ""
        first_listing_appointment: date | None = None
        lines: list[str] = []

        for raw in columns.get(side, []):
            line = clean_space(raw)
            if not line:
                continue
            if NAME_RE.match(line):
                if name:
                    judges.append(
                        make_judge(
                            name,
                            position,
                            first_listing_appointment,
                            lines,
                        )
                    )
                parsed_name, position, first_listing_appointment = (
                    split_inline_name_details(line)
                )
                name = normalize_name(parsed_name)
                lines = [line]
            elif name:
                lines.append(line)
                appointment = parse_appointment_annotation(line)
                if appointment is not None and first_listing_appointment is None:
                    first_listing_appointment = appointment
                else:
                    position = clean_space(
                        " ".join(part for part in [position, line] if part).strip(
                            "()"
                        )
                    )
        if name:
            judges.append(
                make_judge(name, position, first_listing_appointment, lines)
            )

    return judges


def extract_semantic_roster(rtf: str) -> Roster:
    return Roster(
        sections=tuple(
            RosterSection(
                section=section,
                url="",
                judges=tuple(
                    parse_people(
                        extract_columns(
                            rtf,
                            TableOccurrence(section.rtf_occurrence),
                        )
                    )
                ),
            )
            for section in JudicialSection
        )
    )


def extract_rtf(source: Path) -> Roster:
    rtf = source.read_text(encoding="latin-1")
    return extract_semantic_roster(rtf)
