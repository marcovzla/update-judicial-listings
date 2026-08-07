"""Create the initial canonical layout plan from an approved roster."""

from __future__ import annotations

from pathlib import Path

from ..roster_types import (
    Judge,
    JudicialSection,
    Roster,
    appointment_note,
    clean_space,
)
from ..rtf.arial import text_width_points
from ..rtf.geometry import RtfGeometry
from .model import (
    LayoutBlock,
    LayoutPlan,
    LayoutTable,
    SplitStatus,
    block_id_for,
    decisions_from_plan,
    person_display_line,
    position_line,
    position_split_lines,
    validate_plan,
    write_decisions,
    write_plan,
)
from .split_requests import PositionSplitRequest, create_split_requests


def _line_with_parens(words: list[str], *, first: bool, last: bool) -> str:
    line = " ".join(words)
    if first:
        line = f"({line}"
    if last:
        line = f"{line})"
    return line


def _split_score(
    words: list[str],
    breaks: tuple[int, ...],
    available_width_points: float,
    font_size_points: float,
) -> tuple[float, float, int]:
    starts = (0, *breaks)
    ends = (*breaks, len(words))
    widths = [
        text_width_points(
            _line_with_parens(
                words[start:end],
                first=index == 0,
                last=index == len(starts) - 1,
            ),
            font_size_points,
        )
        for index, (start, end) in enumerate(zip(starts, ends, strict=True))
    ]
    overage = sum(
        max(0.0, width - available_width_points)
        for width in widths
    )
    raggedness = max(widths) - min(widths)
    semantic_penalty = 0
    for break_at in breaks:
        before = words[break_at - 1].casefold().strip(",;:")
        after = words[break_at].casefold().strip(",;:")
        if words[break_at - 1].endswith((",", ";", ":")):
            semantic_penalty -= 4
        if before in {"of", "and", "for", "in"}:
            semantic_penalty -= 2
        if after in {"and", "of"}:
            semantic_penalty += 2
    return overage, raggedness, semantic_penalty


def propose_position_split(
    position: str,
    request: PositionSplitRequest,
    font_size_points: float,
) -> tuple[str, ...]:
    words = clean_space(position).split()
    best_breaks: tuple[int, ...] | None = None
    best_score: tuple[float, float, int] | None = None

    def walk(start: int, remaining_breaks: int, breaks: tuple[int, ...]) -> None:
        nonlocal best_breaks, best_score
        if remaining_breaks == 0:
            score = _split_score(
                words,
                breaks,
                request.available_width_points,
                font_size_points,
            )
            if best_score is None or score < best_score:
                best_score = score
                best_breaks = breaks
            return
        max_break = len(words) - remaining_breaks
        for break_at in range(start + 1, max_break + 1):
            walk(break_at, remaining_breaks - 1, (*breaks, break_at))

    walk(0, request.required_lines - 1, ())
    breaks = best_breaks or ()
    starts = (0, *breaks)
    ends = (*breaks, len(words))
    return tuple(
        _line_with_parens(
            words[start:end],
            first=index == 0,
            last=index == len(starts) - 1,
        )
        for index, (start, end) in enumerate(zip(starts, ends, strict=True))
    )


def initial_block(
    section: JudicialSection,
    source_order: int,
    judge: Judge,
    available_width_points: float,
    font_size_points: float,
    split_request: PositionSplitRequest | None,
) -> LayoutBlock:
    name = clean_space(judge.name)
    position = clean_space(judge.position)
    block_id = block_id_for(section, source_order, name)
    appointment = judge.first_listing_appointment
    if appointment is not None:
        note = appointment_note(appointment)
        for line in (name, note):
            if text_width_points(line, font_size_points) > available_width_points:
                raise ValueError(
                    f"{block_id}: appointment annotation does not fit on one line"
                )

        if not position:
            combined_name = f"{name} {note}"
            name_lines = (
                (combined_name,)
                if text_width_points(combined_name, font_size_points)
                <= available_width_points
                else (name, note)
            )
            return LayoutBlock(
                id=block_id,
                name=name,
                position="",
                first_listing_appointment=appointment,
                lines=name_lines,
                split_status=SplitStatus.NAME_APPOINTMENT,
            )

        whole_position_line = position_line(position)
        if split_request is None:
            return LayoutBlock(
                id=block_id,
                name=name,
                position=position,
                first_listing_appointment=appointment,
                lines=(name, whole_position_line, note),
                split_status=SplitStatus.NAME_POSITION_APPOINTMENT,
            )

        split_lines = propose_position_split(
            position,
            split_request,
            font_size_points,
        )
        return LayoutBlock(
            id=block_id,
            name=name,
            position=position,
            first_listing_appointment=appointment,
            lines=(name, *split_lines, note),
            split_status=SplitStatus.ACCEPTED,
        )

    if not position:
        return LayoutBlock(
            id=block_id,
            name=name,
            position="",
            lines=(name,),
            split_status=SplitStatus.INLINE,
        )

    combined = person_display_line(name, position)
    if text_width_points(combined, font_size_points) <= available_width_points:
        return LayoutBlock(
            id=block_id,
            name=name,
            position=position,
            lines=(combined,),
            split_status=SplitStatus.INLINE,
        )

    whole_position_line = position_line(position)
    if split_request is None:
        return LayoutBlock(
            id=block_id,
            name=name,
            position=position,
            lines=(name, whole_position_line),
            split_status=SplitStatus.NAME_POSITION,
        )

    split_lines = propose_position_split(
        position,
        split_request,
        font_size_points,
    )
    return LayoutBlock(
        id=block_id,
        name=name,
        position=position,
        lines=(name, *split_lines),
        split_status=SplitStatus.ACCEPTED,
    )


def choose_column_break(blocks: tuple[LayoutBlock, ...]) -> str | None:
    if not blocks:
        return None
    if len(blocks) == 1:
        return blocks[0].id

    total_lines = sum(len(block.lines) for block in blocks)
    target = total_lines / 2
    best_index = 1
    best_distance = float("inf")
    best_left_lines = 0
    left_lines = 0

    for index, block in enumerate(blocks, start=1):
        left_lines += len(block.lines)
        distance = abs(left_lines - target)
        if distance < best_distance or (
            distance == best_distance and left_lines > best_left_lines
        ):
            best_index = index
            best_distance = distance
            best_left_lines = left_lines

    return blocks[best_index - 1].id


def create_layout_plan(
    resolved_roster: Path,
    geometry_path: Path,
) -> LayoutPlan:
    roster = Roster.model_validate_json(
        resolved_roster.read_text(encoding="utf-8")
    )
    roster_sections = {
        roster_section.section: roster_section for roster_section in roster.sections
    }
    geometry = RtfGeometry.model_validate_json(
        geometry_path.read_text(encoding="utf-8")
    )
    geometry_by_section = {table.section: table for table in geometry.tables}
    requests = {
        request.block: request
        for request in create_split_requests(roster, geometry).splits
    }
    tables: list[LayoutTable] = []

    for section in sorted(
        JudicialSection,
        key=lambda value: value.rtf_occurrence,
    ):
        if section not in roster_sections:
            raise ValueError(
                f"resolved roster has no {section.display_title} section"
            )
        section_geometry = geometry_by_section[section]
        available_width_points = min(
            section_geometry.left.content_width_points,
            section_geometry.right.content_width_points,
        )
        font_size_points = section_geometry.font.size_points
        judges = roster_sections[section].judges
        blocks = tuple(
            initial_block(
                section,
                source_order,
                judge,
                available_width_points,
                font_size_points,
                requests.get(block_id_for(section, source_order, judge.name)),
            )
            for source_order, judge in enumerate(judges, start=1)
        )
        tables.append(
            LayoutTable(
                section=section,
                blocks=blocks,
                column_break_after=choose_column_break(blocks),
            )
        )

    plan = LayoutPlan(tables=tuple(tables))
    validate_plan(plan)
    return plan


def diagnostics_markdown(plan: LayoutPlan) -> str:
    lines = [
        "# Layout Diagnostics",
        "",
    ]
    pending = [
        (table, block)
        for table in plan.tables
        for block in table.blocks
        if block.split_status == SplitStatus.PENDING
    ]
    if not pending:
        lines.extend(["No pending position split decisions.", ""])
        return "\n".join(lines)

    lines.extend(
        [
            "| Table | Block | Position split proposal |",
            "|---|---|---|",
        ]
    )
    for table, block in pending:
        proposal = "<br>".join(position_split_lines(block))
        lines.append(f"| {table.id} {table.title} | `{block.id}` | {proposal} |")
    lines.append("")
    return "\n".join(lines)


def write_initial_layout(
    resolved_roster: Path,
    geometry_path: Path,
    plan_path: Path,
    decisions_path: Path,
    diagnostics_path: Path,
) -> LayoutPlan:
    plan = create_layout_plan(resolved_roster, geometry_path)
    write_plan(plan_path, plan)
    write_decisions(decisions_path, decisions_from_plan(plan))
    diagnostics_path.write_text(
        diagnostics_markdown(plan),
        encoding="utf-8",
    )
    return plan
