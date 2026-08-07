"""Canonical models for judicial-listing table layouts."""

from __future__ import annotations

import re
import unicodedata
from enum import StrEnum
from pathlib import Path

from ..roster_types import ImmutableModel, JudicialSection, clean_space


class SplitStatus(StrEnum):
    INLINE = "inline"
    NAME_POSITION = "name_position"
    PENDING = "pending"
    ACCEPTED = "accepted"


class LayoutBlock(ImmutableModel):
    id: str
    name: str
    position: str
    lines: tuple[str, ...]
    split_status: SplitStatus


class LayoutTable(ImmutableModel):
    section: JudicialSection
    blocks: tuple[LayoutBlock, ...]
    column_break_after: str | None

    @property
    def id(self) -> str:
        return self.section.serialized_key

    @property
    def title(self) -> str:
        return self.section.display_title


class LayoutPlan(ImmutableModel):
    tables: tuple[LayoutTable, ...]


class ExpandedLine(ImmutableModel):
    block_id: str
    text: str
    line_index: int

    @property
    def is_first(self) -> bool:
        return self.line_index == 0


class ExpandedTable(ImmutableModel):
    section: JudicialSection
    left: tuple[ExpandedLine, ...]
    right: tuple[ExpandedLine, ...]

    @property
    def occurrence(self) -> int:
        return self.section.rtf_occurrence


class LayoutDecisions(ImmutableModel):
    splits: dict[str, tuple[str, ...]]
    column_breaks: dict[JudicialSection, str | None]


def block_slug(name: str) -> str:
    normalized = unicodedata.normalize("NFKD", clean_space(name))
    ascii_name = normalized.encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_name.casefold()).strip("-")
    return slug or "block"


def block_id_for(
    section: JudicialSection,
    source_order: int,
    name: str,
) -> str:
    if source_order < 1:
        raise ValueError("source_order must be one-based")
    return f"{section.serialized_key}:{source_order:03d}-{block_slug(name)}"


def person_display_line(name: str, position: str) -> str:
    return f"{name} ({position})" if position else name


def position_line(position: str) -> str:
    return f"({position})"


def normalize_position_text(value: str) -> str:
    value = clean_space(value)
    if value.startswith("("):
        value = value[1:].strip()
    if value.endswith(")"):
        value = value[:-1].strip()
    return clean_space(value)


def normalize_position_lines(lines: tuple[str, ...] | list[str]) -> str:
    return normalize_position_text(" ".join(clean_space(line) for line in lines))


def load_plan(path: Path) -> LayoutPlan:
    plan = LayoutPlan.model_validate_json(path.read_text(encoding="utf-8"))
    validate_plan(plan)
    return plan


def write_plan(path: Path, plan: LayoutPlan) -> None:
    validate_plan(plan)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(plan.model_dump_json(indent=2) + "\n", encoding="utf-8")


def validate_plan(plan: LayoutPlan, *, require_no_pending: bool = False) -> None:
    seen_sections: set[JudicialSection] = set()
    seen_block_ids: set[str] = set()
    for table in plan.tables:
        if table.section in seen_sections:
            raise ValueError(f"duplicate layout section: {table.section}")
        seen_sections.add(table.section)
        validate_table(table, require_no_pending=require_no_pending)
        for block in table.blocks:
            if block.id in seen_block_ids:
                raise ValueError(f"duplicate block id: {block.id}")
            seen_block_ids.add(block.id)


def validate_table(table: LayoutTable, *, require_no_pending: bool = False) -> None:
    block_ids: set[str] = set()
    for source_order, block in enumerate(table.blocks, start=1):
        expected_id = block_id_for(table.section, source_order, block.name)
        if block.id != expected_id:
            raise ValueError(
                f"{block.id}: expected block id {expected_id} for source order "
                f"{source_order}"
            )
        if block.id in block_ids:
            raise ValueError(f"duplicate block id in table {table.id}: {block.id}")
        block_ids.add(block.id)
        validate_block(block, require_no_pending=require_no_pending)

    if (
        table.column_break_after is not None
        and table.column_break_after not in block_ids
    ):
        raise ValueError(
            f"table {table.id}: column_break_after does not name a block: "
            f"{table.column_break_after}"
        )


def validate_block(block: LayoutBlock, *, require_no_pending: bool = False) -> None:
    if not block.name:
        raise ValueError(f"{block.id}: name is empty")
    if require_no_pending and block.split_status == SplitStatus.PENDING:
        raise ValueError(f"{block.id}: pending split decisions must be applied first")
    if not block.lines:
        raise ValueError(f"{block.id}: block must have at least one line")
    for index, line in enumerate(block.lines, start=1):
        if "\n" in line or "\r" in line:
            raise ValueError(f"{block.id}: line {index} contains an embedded newline")
        if not clean_space(line):
            raise ValueError(f"{block.id}: line {index} is empty")

    if block.split_status == SplitStatus.INLINE:
        expected = (person_display_line(block.name, block.position),)
        if block.lines != expected:
            raise ValueError(f"{block.id}: inline lines do not match name/position")
        return

    if not block.position:
        raise ValueError(f"{block.id}: split block has no position")
    if block.lines[0] != block.name:
        raise ValueError(f"{block.id}: first split line must be the name")

    if block.split_status == SplitStatus.NAME_POSITION:
        expected = (block.name, position_line(block.position))
        if block.lines != expected:
            raise ValueError(f"{block.id}: name_position lines are invalid")
        return

    if block.split_status not in (SplitStatus.PENDING, SplitStatus.ACCEPTED):
        raise ValueError(f"{block.id}: unsupported split status {block.split_status}")
    if len(block.lines) < 2:
        raise ValueError(f"{block.id}: split block has no position lines")
    validate_position_split(block.id, block.position, block.lines[1:])


def validate_position_split(
    block_id: str,
    position: str,
    split_lines: tuple[str, ...] | list[str],
) -> tuple[str, ...]:
    cleaned = tuple(clean_space(line) for line in split_lines)
    if not cleaned:
        raise ValueError(f"{block_id}: split must contain at least one position line")
    for index, line in enumerate(cleaned, start=1):
        if "\n" in line or "\r" in line:
            raise ValueError(f"{block_id}: split line {index} contains a newline")
        if not line:
            raise ValueError(f"{block_id}: split line {index} is empty")
    if normalize_position_lines(cleaned) != normalize_position_text(position):
        raise ValueError(
            f"{block_id}: split lines do not rejoin to the original position"
        )
    return cleaned


def has_pending_splits(plan: LayoutPlan) -> bool:
    return any(
        block.split_status == SplitStatus.PENDING
        for table in plan.tables
        for block in table.blocks
    )


def decisions_from_plan(plan: LayoutPlan) -> LayoutDecisions:
    validate_plan(plan)
    splits: dict[str, tuple[str, ...]] = {}
    column_breaks: dict[JudicialSection, str | None] = {}

    for table in plan.tables:
        column_breaks[table.section] = table.column_break_after
        for block in table.blocks:
            if block.split_status == SplitStatus.PENDING:
                splits[block.id] = block.lines[1:]

    return LayoutDecisions(
        splits=splits,
        column_breaks=column_breaks,
    )


def write_decisions(path: Path, decisions: LayoutDecisions) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(decisions.model_dump_json(indent=2) + "\n", encoding="utf-8")


def apply_decisions(
    plan: LayoutPlan,
    decisions: LayoutDecisions,
) -> LayoutPlan:
    known_block_ids = {block.id for table in plan.tables for block in table.blocks}
    for block_id in decisions.splits:
        if block_id not in known_block_ids:
            raise ValueError(f"layout decisions.splits has unknown block: {block_id}")

    tables: list[LayoutTable] = []
    for table in plan.tables:
        blocks = tuple(
            apply_split_decision(block, decisions.splits[block.id])
            if block.id in decisions.splits
            else block
            for block in table.blocks
        )
        column_break_after = decisions.column_breaks.get(
            table.section,
            table.column_break_after,
        )
        tables.append(
            LayoutTable(
                section=table.section,
                blocks=blocks,
                column_break_after=column_break_after,
            )
        )

    new_plan = LayoutPlan(tables=tuple(tables))
    validate_plan(new_plan, require_no_pending=True)
    return new_plan


def apply_split_decision(
    block: LayoutBlock,
    split_lines: tuple[str, ...],
) -> LayoutBlock:
    cleaned_lines = validate_position_split(block.id, block.position, split_lines)
    return block.model_copy(
        update={
            "lines": (block.name, *cleaned_lines),
            "split_status": SplitStatus.ACCEPTED,
        }
    )


def expand_plan_to_tables(
    plan: LayoutPlan,
    *,
    require_no_pending: bool = True,
) -> list[ExpandedTable]:
    validate_plan(plan, require_no_pending=require_no_pending)
    return [expand_table(table) for table in plan.tables]


def expand_table(table: LayoutTable) -> ExpandedTable:
    block_ids = [block.id for block in table.blocks]
    break_index = (
        -1
        if table.column_break_after is None
        else block_ids.index(table.column_break_after)
    )

    left: list[ExpandedLine] = []
    right: list[ExpandedLine] = []

    for index, block in enumerate(table.blocks):
        column_lines = left if index <= break_index else right
        column_lines.extend(
            ExpandedLine(
                block_id=block.id,
                text=line,
                line_index=line_index,
            )
            for line_index, line in enumerate(block.lines)
        )

    return ExpandedTable(
        section=table.section,
        left=tuple(left),
        right=tuple(right),
    )
