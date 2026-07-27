"""Apply review decisions to the official roster."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, cast

from pydantic import BaseModel, Field, ValidationError, model_validator

from .prepare import REVIEW_ITEMS_NAME
from .roster_comparison import DifferenceKind, RosterDifference, RosterEntry
from .roster_review import (
    OrderReviewItem,
    PersonReviewItem,
    ReviewAction,
    RosterReview,
)
from .roster_types import (
    ImmutableModel,
    JudicialSection,
    Roster,
    clean_space,
    person_key,
)

RESOLVED_ROSTER_NAME = "resolved_roster.json"
APPROVED_CHANGES_NAME = "approved_changes.json"


class ApplyReviewError(RuntimeError):
    pass


class ManualReviewEntry(ImmutableModel):
    section: JudicialSection
    order: int = Field(ge=1)
    name: str = Field(min_length=1)
    position: str = ""


class ReviewDecision(ImmutableModel):
    action: ReviewAction
    note: str = ""
    entry: ManualReviewEntry | None = None

    @model_validator(mode="after")
    def validate_entry(self) -> ReviewDecision:
        if self.action is ReviewAction.MANUAL and self.entry is None:
            raise ValueError("manual decisions require an entry")
        if self.action is not ReviewAction.MANUAL and self.entry is not None:
            raise ValueError("entry is only valid for manual decisions")
        return self


class ReviewDecisions(ImmutableModel):
    decisions: dict[str, ReviewDecision]


class ApprovedChange(ImmutableModel):
    id: str
    kind: Literal["person", "order"]
    action: ReviewAction
    note: str
    entry: ManualReviewEntry | None
    effects: tuple[RosterDifference, ...]


class ApprovedChanges(ImmutableModel):
    created_at: datetime
    review_items: str
    decisions: str
    changes: tuple[ApprovedChange, ...]


class AppliedReview(ImmutableModel):
    resolved_roster: Path
    approved_changes: Path


def _as_mapping(value: object, label: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    return cast(dict[str, object], value)


def _as_list(value: object, label: str) -> list[object]:
    if not isinstance(value, list):
        raise ValueError(f"{label} must be a list")
    return cast(list[object], value)


class ResolvedRosterEditor:
    """Mutable editor for the serialized canonical roster."""

    def __init__(self, roster: Roster) -> None:
        self.roster = _as_mapping(
            json.loads(roster.model_dump_json()),
            "official roster",
        )

    def sections(self) -> list[object]:
        return _as_list(self.roster.get("sections"), "resolved.sections")

    def judges(self, section: JudicialSection) -> list[object]:
        for value in self.sections():
            section_data = _as_mapping(value, "resolved.sections[]")
            if section_data.get("section") == section.value:
                return _as_list(
                    section_data.get("judges"),
                    f"resolved.{section.value}.judges",
                )
        raise ValueError(f"resolved roster has no {section.display_title} section")

    def remove_key(
        self,
        section: JudicialSection,
        key: str,
    ) -> dict[str, object] | None:
        judges = self.judges(section)
        for index, value in enumerate(judges):
            judge = _as_mapping(value, f"resolved.{section.value}.judges[{index}]")
            if person_key(str(judge.get("name", ""))) == key:
                return _as_mapping(judges.pop(index), "removed judge")
        return None

    def remove_key_everywhere(self, key: str) -> None:
        for section in JudicialSection:
            while self.remove_key(section, key) is not None:
                pass

    def insert_current_entry(self, entry: RosterEntry) -> None:
        judges = self.judges(entry.section)
        key = person_key(entry.judge.name)
        if any(
            person_key(str(_as_mapping(value, "judge").get("name", ""))) == key
            for value in judges
        ):
            return

        insert_at = max(0, min(entry.order - 1, len(judges)))
        judges.insert(insert_at, json.loads(entry.judge.model_dump_json()))

    def insert_manual_entry(self, entry: ManualReviewEntry) -> None:
        name = clean_space(entry.name)
        if not name:
            raise ValueError("manual entry name must not be blank")

        judges = self.judges(entry.section)
        insert_at = max(0, min(entry.order - 1, len(judges)))
        judges.insert(
            insert_at,
            {
                "name": name,
                "position": clean_space(entry.position),
                "source": "manual review decision",
            },
        )

    def set_fields(
        self,
        section: JudicialSection,
        key: str,
        *,
        name: str | None = None,
        position: str | None = None,
    ) -> None:
        for value in self.judges(section):
            judge = _as_mapping(value, f"resolved.{section.value}.judges[]")
            if person_key(str(judge.get("name", ""))) != key:
                continue
            if name is not None:
                judge["name"] = name
            if position is not None:
                judge["position"] = position
            return

    def move_to_order(
        self,
        section: JudicialSection,
        key: str,
        order: int,
    ) -> None:
        judges = self.judges(section)
        for index, value in enumerate(judges):
            judge = _as_mapping(value, f"resolved.{section.value}.judges[{index}]")
            if person_key(str(judge.get("name", ""))) != key:
                continue
            moved = judges.pop(index)
            judges.insert(max(0, min(order - 1, len(judges))), moved)
            return

    def result(self) -> Roster:
        return Roster.model_validate_json(json.dumps(self.roster))


def _apply_keep_current_difference(
    editor: ResolvedRosterEditor,
    difference: RosterDifference,
) -> None:
    rtf = difference.rtf
    official = difference.official

    if difference.kind is DifferenceKind.ADDED and official is not None:
        editor.remove_key(official.section, difference.key)
    elif difference.kind is DifferenceKind.REMOVED and rtf is not None:
        editor.insert_current_entry(rtf)
    elif (
        difference.kind is DifferenceKind.MOVED
        and rtf is not None
        and official is not None
    ):
        editor.remove_key(official.section, difference.key)
        editor.insert_current_entry(rtf)
    elif (
        difference.kind is DifferenceKind.NAME_CHANGED
        and rtf is not None
        and official is not None
    ):
        editor.set_fields(
            official.section,
            difference.key,
            name=rtf.judge.name,
        )
    elif (
        difference.kind is DifferenceKind.POSITION_CHANGED
        and rtf is not None
        and official is not None
    ):
        editor.set_fields(
            official.section,
            difference.key,
            position=rtf.judge.position,
        )
    elif difference.kind is DifferenceKind.ORDER_CHANGED and rtf is not None:
        editor.move_to_order(rtf.section, difference.key, rtf.order)
    else:
        raise ValueError(f"cannot keep current for {difference.kind} difference")


def _apply_decision(
    editor: ResolvedRosterEditor,
    item: PersonReviewItem | OrderReviewItem,
    decision: ReviewDecision,
) -> None:
    if decision.action is ReviewAction.USE_OFFICIAL:
        return
    if decision.action is ReviewAction.KEEP_CURRENT:
        for difference in item.differences:
            _apply_keep_current_difference(editor, difference)
        return
    if isinstance(item, OrderReviewItem):
        raise ValueError("manual decisions are only valid for person review items")
    if decision.entry is None:
        raise ValueError("manual decisions require an entry")
    editor.remove_key_everywhere(item.key)
    editor.insert_manual_entry(decision.entry)


def _input_path(review_items: Path, value: str) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return review_items.parent / path


def _write_json(path: Path, value: BaseModel) -> None:
    path.write_text(value.model_dump_json(indent=2) + "\n", encoding="utf-8")


def _apply_review(run: Path, decisions_path: Path) -> AppliedReview:
    review_items_path = run / REVIEW_ITEMS_NAME
    review = RosterReview.model_validate_json(
        review_items_path.read_text(encoding="utf-8")
    )
    official_path = _input_path(review_items_path, review.inputs.official_roster)
    official = Roster.model_validate_json(official_path.read_text(encoding="utf-8"))
    decisions = ReviewDecisions.model_validate_json(
        decisions_path.read_text(encoding="utf-8")
    )

    missing = [
        item.id for item in review.items if item.id not in decisions.decisions
    ]
    if missing:
        raise ValueError(f"missing review decisions for: {', '.join(missing)}")

    editor = ResolvedRosterEditor(official)
    approved: list[ApprovedChange] = []
    for item in review.items:
        decision = decisions.decisions[item.id]
        _apply_decision(editor, item, decision)
        approved.append(
            ApprovedChange(
                id=item.id,
                kind=item.kind,
                action=decision.action,
                note=decision.note,
                entry=decision.entry,
                effects=item.differences,
            )
        )

    resolved_path = run / RESOLVED_ROSTER_NAME
    changes_path = run / APPROVED_CHANGES_NAME
    _write_json(resolved_path, editor.result())
    _write_json(
        changes_path,
        ApprovedChanges(
            created_at=datetime.now(UTC),
            review_items=str(review_items_path),
            decisions=str(decisions_path),
            changes=tuple(approved),
        ),
    )
    return AppliedReview(
        resolved_roster=resolved_path,
        approved_changes=changes_path,
    )


def apply_review(run: Path, decisions: Path) -> AppliedReview:
    """Apply every review decision without modifying the source RTF."""
    try:
        return _apply_review(run, decisions)
    except (OSError, ValidationError, ValueError) as exc:
        raise ApplyReviewError(f"failed to apply review: {exc}") from exc
