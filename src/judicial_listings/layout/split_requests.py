"""Identify positions that require an agent-authored line split."""

from __future__ import annotations

from enum import IntEnum
from itertools import combinations

from ..roster_types import (
    ImmutableModel,
    JudicialSection,
    Roster,
    appointment_note,
    clean_space,
)
from ..rtf.arial import FONT_NAME, text_width_points
from ..rtf.geometry import RtfGeometry
from .model import block_id_for, person_display_line, position_line


class RequiredLines(IntEnum):
    TWO = 2
    THREE = 3
    FOUR = 4


class PositionSplitRequest(ImmutableModel):
    block: str
    position: str
    required_lines: RequiredLines
    available_width_points: float
    lines: tuple[str, ...] = ()


class PositionSplitRequests(ImmutableModel):
    splits: tuple[PositionSplitRequest, ...]


def _position_lines(
    words: list[str],
    breaks: tuple[int, ...],
) -> tuple[str, ...]:
    starts = (0, *breaks)
    ends = (*breaks, len(words))
    lines = [
        " ".join(words[start:end])
        for start, end in zip(starts, ends, strict=True)
    ]
    lines[0] = f"({lines[0]}"
    lines[-1] = f"{lines[-1]})"
    return tuple(lines)


def _required_lines(
    position: str,
    available_width_points: float,
    font_size_points: float,
) -> RequiredLines:
    words = position.split()
    for line_count in RequiredLines:
        if line_count > len(words):
            continue
        for breaks in combinations(range(1, len(words)), line_count - 1):
            lines = _position_lines(words, breaks)
            if all(
                text_width_points(line, font_size_points)
                <= available_width_points
                for line in lines
            ):
                return line_count
    raise ValueError(
        f"position cannot fit within four lines: {position_line(position)}"
    )


def create_split_requests(
    roster: Roster,
    geometry: RtfGeometry,
) -> PositionSplitRequests:
    """Return explicit two-, three-, or four-line position split requests."""
    roster_sections = {
        roster_section.section: roster_section for roster_section in roster.sections
    }
    table_geometry = {table.section: table for table in geometry.tables}
    if len(table_geometry) != len(geometry.tables):
        raise ValueError("RTF geometry contains duplicate judicial sections")

    requests: list[PositionSplitRequest] = []
    for section in sorted(
        JudicialSection,
        key=lambda value: value.rtf_occurrence,
    ):
        if section not in roster_sections:
            raise ValueError(
                f"resolved roster has no {section.display_title} section"
            )
        if section not in table_geometry:
            raise ValueError(
                f"RTF geometry has no {section.display_title} section"
            )

        section_geometry = table_geometry[section]
        if section_geometry.font.name.casefold() != FONT_NAME.casefold():
            raise ValueError(
                f"{section.display_title} uses unsupported font "
                f"{section_geometry.font.name!r}"
            )
        available_width_points = min(
            section_geometry.left.content_width_points,
            section_geometry.right.content_width_points,
        )
        font_size_points = section_geometry.font.size_points

        for source_order, judge in enumerate(
            roster_sections[section].judges,
            start=1,
        ):
            name = clean_space(judge.name)
            position = clean_space(judge.position)
            block = block_id_for(section, source_order, name)
            appointment = judge.first_listing_appointment
            if appointment is None:
                if text_width_points(name, font_size_points) > available_width_points:
                    raise ValueError(f"{block}: name does not fit on one line")
            else:
                note = appointment_note(appointment)
                for line in (name, note):
                    if (
                        text_width_points(line, font_size_points)
                        > available_width_points
                    ):
                        raise ValueError(
                            f"{block}: appointment annotation does not fit "
                            "on one line"
                        )
            if not position:
                continue
            if appointment is None and (
                text_width_points(
                    person_display_line(name, position),
                    font_size_points,
                )
                <= available_width_points
            ):
                continue

            parenthesized_position = position_line(position)
            if (
                text_width_points(parenthesized_position, font_size_points)
                <= available_width_points
            ):
                continue
            requests.append(
                PositionSplitRequest(
                    block=block,
                    position=parenthesized_position,
                    required_lines=_required_lines(
                        position,
                        available_width_points,
                        font_size_points,
                    ),
                    available_width_points=available_width_points,
                )
            )

    return PositionSplitRequests(splits=tuple(requests))
