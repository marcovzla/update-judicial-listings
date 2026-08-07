"""Turn roster differences into decision-sized review items."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import Field

from .roster_comparison import (
    DifferenceKind,
    RosterComparison,
    RosterDifference,
    RosterEntry,
)
from .roster_types import (
    ImmutableModel,
    JudicialSection,
    Roster,
    clean_space,
    listing_name,
    normalize_name,
    person_key,
)


class ReviewStatus(StrEnum):
    NO_CHANGES = "no_changes"
    REVIEW_REQUIRED = "review_required"


class ReviewAction(StrEnum):
    USE_OFFICIAL = "use_official"
    KEEP_CURRENT = "keep_current"
    MANUAL = "manual"


class ReviewOption(ImmutableModel):
    reply: str
    action: ReviewAction
    label: str


class ReviewDecisionOption(ImmutableModel):
    action: ReviewAction


class ReviewReplyOption(ImmutableModel):
    reply: str
    label: str
    decision: ReviewDecisionOption


class PersonReviewItem(ImmutableModel):
    kind: Literal["person"] = "person"
    id: str
    key: str
    current: tuple[RosterEntry, ...]
    official: tuple[RosterEntry, ...]
    differences: tuple[RosterDifference, ...]
    options: tuple[ReviewOption, ...]
    question_markdown: str = ""
    reply_options: tuple[ReviewReplyOption, ...] = ()


class OrderReviewItem(ImmutableModel):
    kind: Literal["order"] = "order"
    id: str
    section: JudicialSection
    current: tuple[RosterEntry, ...]
    official: tuple[RosterEntry, ...]
    differences: tuple[RosterDifference, ...]
    options: tuple[ReviewOption, ...]
    question_markdown: str = ""
    reply_options: tuple[ReviewReplyOption, ...] = ()


ReviewItem = Annotated[
    PersonReviewItem | OrderReviewItem,
    Field(discriminator="kind"),
]


class ReviewInputs(ImmutableModel):
    source_rtf: str
    source_sha256: str
    rtf_roster: str
    rtf_geometry: str
    official_roster: str
    comparison: str


class ReviewSummary(ImmutableModel):
    total: int
    people: int
    sections: int


class RosterReview(ImmutableModel):
    created_at: datetime
    inputs: ReviewInputs
    status: ReviewStatus
    summary: ReviewSummary
    items: tuple[ReviewItem, ...]
    warnings: tuple[str, ...] = ()


def _entry_key(entry: RosterEntry) -> str:
    return person_key(entry.judge.name) or normalize_name(entry.judge.name).casefold()


def _entry_sort_key(entry: RosterEntry) -> tuple[int, int, str]:
    return (
        entry.section.rtf_occurrence,
        entry.order,
        entry.judge.name.casefold(),
    )


def _roster_entries(
    roster: Roster,
) -> tuple[
    dict[str, list[RosterEntry]],
    dict[JudicialSection, list[RosterEntry]],
]:
    by_key: dict[str, list[RosterEntry]] = {}
    by_section: dict[JudicialSection, list[RosterEntry]] = {
        section: [] for section in JudicialSection
    }

    for roster_section in roster.sections:
        for order, judge in enumerate(roster_section.judges, start=1):
            entry = RosterEntry(
                section=roster_section.section,
                order=order,
                judge=judge,
            )
            by_key.setdefault(_entry_key(entry), []).append(entry)
            by_section[roster_section.section].append(entry)

    return by_key, by_section


def _person_options() -> tuple[ReviewOption, ...]:
    return (
        ReviewOption(
            reply="A",
            action=ReviewAction.USE_OFFICIAL,
            label="Use the official roster entries.",
        ),
        ReviewOption(
            reply="B",
            action=ReviewAction.KEEP_CURRENT,
            label="Keep the current RTF entries.",
        ),
        ReviewOption(
            reply="C",
            action=ReviewAction.MANUAL,
            label="Provide a manual replacement entry.",
        ),
    )


def _order_options() -> tuple[ReviewOption, ...]:
    return (
        ReviewOption(
            reply="A",
            action=ReviewAction.USE_OFFICIAL,
            label="Use the official order.",
        ),
        ReviewOption(
            reply="B",
            action=ReviewAction.KEEP_CURRENT,
            label="Keep the current RTF order.",
        ),
    )


def _person_sort_key(
    key: str,
    current: dict[str, list[RosterEntry]],
    official: dict[str, list[RosterEntry]],
) -> tuple[int, int, str]:
    entries = [*current.get(key, []), *official.get(key, [])]
    return min((_entry_sort_key(entry) for entry in entries), default=(99, 99, key))


def build_review(
    rtf: Roster,
    official: Roster,
    comparison: RosterComparison,
    inputs: ReviewInputs,
    created_at: datetime,
) -> RosterReview:
    """Group raw comparison facts into choices a user can decide once."""
    current_by_key, current_by_section = _roster_entries(rtf)
    official_by_key, official_by_section = _roster_entries(official)
    person_differences: dict[str, list[RosterDifference]] = {}
    order_differences: dict[JudicialSection, list[RosterDifference]] = {}

    for difference in comparison.differences:
        if difference.kind is DifferenceKind.ORDER_CHANGED:
            entry = difference.official or difference.rtf
            if entry is None:
                raise ValueError("an order difference must contain a roster entry")
            order_differences.setdefault(entry.section, []).append(difference)
        else:
            person_differences.setdefault(difference.key, []).append(difference)

    person_items = [
        PersonReviewItem(
            id=f"person:{key}",
            key=key,
            current=tuple(
                sorted(current_by_key.get(key, []), key=_entry_sort_key)
            ),
            official=tuple(
                sorted(official_by_key.get(key, []), key=_entry_sort_key)
            ),
            differences=tuple(person_differences[key]),
            options=_person_options(),
        )
        for key in sorted(
            person_differences,
            key=lambda value: _person_sort_key(
                value,
                current_by_key,
                official_by_key,
            ),
        )
    ]
    order_items = [
        OrderReviewItem(
            id=f"order:{section.serialized_key}",
            section=section,
            current=tuple(
                sorted(current_by_section.get(section, []), key=_entry_sort_key)
            ),
            official=tuple(
                sorted(official_by_section.get(section, []), key=_entry_sort_key)
            ),
            differences=tuple(order_differences[section]),
            options=_order_options(),
        )
        for section in JudicialSection
        if section in order_differences
    ]
    raw_items: tuple[ReviewItem, ...] = (*person_items, *order_items)
    items = tuple(
        _attach_review_question(item, index, len(raw_items))
        for index, item in enumerate(raw_items, start=1)
    )

    return RosterReview(
        created_at=created_at,
        inputs=inputs,
        status=(
            ReviewStatus.REVIEW_REQUIRED if items else ReviewStatus.NO_CHANGES
        ),
        summary=ReviewSummary(
            total=len(items),
            people=len(person_items),
            sections=len(order_items),
        ),
        items=items,
        warnings=comparison.warnings,
    )


def _display_entry(entry: RosterEntry) -> str:
    display = f"{entry.section.display_title}: {listing_name(entry.judge)}"
    if entry.judge.position:
        return f"{display} ({entry.judge.position})"
    return display


def _md_cell(value: str) -> str:
    return clean_space(value).replace("|", "\\|") or "Not listed"


def _entry_cell(entry: RosterEntry) -> str:
    name = listing_name(entry.judge)
    if entry.judge.position:
        return _md_cell(f"{name} ({entry.judge.position})")
    return _md_cell(name)


def _entries_by_section(
    entries: tuple[RosterEntry, ...],
) -> dict[JudicialSection, list[RosterEntry]]:
    grouped: dict[JudicialSection, list[RosterEntry]] = {}
    for entry in entries:
        grouped.setdefault(entry.section, []).append(entry)
    return grouped


def _difference_text(difference: RosterDifference) -> str:
    rtf = difference.rtf
    official = difference.official

    if difference.kind is DifferenceKind.ADDED and official is not None:
        judge = listing_name(official.judge)
        if official.judge.position:
            judge = f"{judge} ({official.judge.position})"
        return f"Add {judge} to the end of the {official.section.display_title} list."
    if difference.kind is DifferenceKind.REMOVED and rtf is not None:
        return f"Remove {_display_entry(rtf)}."
    if (
        difference.kind is DifferenceKind.MOVED
        and rtf is not None
        and official is not None
    ):
        return (
            f"Move {listing_name(official.judge)} from "
            f"{rtf.section.display_title} to the end of "
            f"{official.section.display_title}."
        )
    if (
        difference.kind is DifferenceKind.NAME_CHANGED
        and rtf is not None
        and official is not None
    ):
        return f"Change {rtf.judge.name} to {official.judge.name}."
    if (
        difference.kind is DifferenceKind.POSITION_CHANGED
        and rtf is not None
        and official is not None
    ):
        current = rtf.judge.position or "(none)"
        source = official.judge.position or "(none)"
        return f"Change {official.judge.name}'s position from {current} to {source}."
    if (
        difference.kind is DifferenceKind.FIRST_LISTING_APPOINTMENT_CHANGED
        and rtf is not None
        and official is not None
    ):
        current = rtf.judge.first_listing_appointment
        source = official.judge.first_listing_appointment
        if current is not None and source is None:
            return f"Remove {official.judge.name}'s appointment annotation."
        return (
            f"Change {official.judge.name}'s appointment annotation from "
            f"{listing_name(rtf.judge)} to {listing_name(official.judge)}."
        )
    if (
        difference.kind is DifferenceKind.ORDER_CHANGED
        and rtf is not None
        and official is not None
    ):
        return (
            f"Move {official.judge.name} from position {rtf.order} "
            f"to {official.order}."
        )
    raise ValueError(f"incomplete {difference.kind} difference")


def _review_title(item: PersonReviewItem) -> str:
    entries = item.official or item.current
    return entries[0].judge.name


def _person_review_type(item: PersonReviewItem) -> str:
    kinds = {difference.kind for difference in item.differences}
    if kinds <= {
        DifferenceKind.ADDED,
        DifferenceKind.REMOVED,
        DifferenceKind.MOVED,
    }:
        return "Membership change"
    if kinds == {DifferenceKind.NAME_CHANGED}:
        return "Name wording"
    if kinds == {DifferenceKind.POSITION_CHANGED}:
        return "Position wording"
    if kinds == {DifferenceKind.FIRST_LISTING_APPOINTMENT_CHANGED}:
        return "Appointment annotation"
    return "Person change"


def _human_join(values: list[str]) -> str:
    if not values:
        return ""
    if len(values) == 1:
        return values[0]
    if len(values) == 2:
        return f"{values[0]} or {values[1]}"
    return f"{', '.join(values[:-1])}, or {values[-1]}"


def _reply_line(options: tuple[ReviewOption, ...]) -> str:
    choices = [
        f"`{option.reply}` {option.label.rstrip('.')}" for option in options
    ]
    return f"**Reply with:** {_human_join(choices)}."


def _person_question_markdown(
    item: PersonReviewItem,
    index: int,
    total: int,
) -> str:
    current = _entries_by_section(item.current)
    official = _entries_by_section(item.official)
    sections = sorted(
        set(current) | set(official),
        key=lambda section: section.rtf_occurrence,
    )
    lines = [
        f"**Review {index}/{total}: {_review_title(item)}**",
        "",
        f"**Type:** {_person_review_type(item)}",
        "",
        "| Table | Current RTF | Official source |",
        "|---|---|---|",
    ]
    for section in sections:
        current_cell = "<br>".join(
            _entry_cell(entry) for entry in current.get(section, [])
        )
        official_cell = "<br>".join(
            _entry_cell(entry) for entry in official.get(section, [])
        )
        lines.append(
            f"| {_md_cell(section.display_title)} | "
            f"{current_cell or 'Not listed'} | "
            f"{official_cell or 'Not listed'} |"
        )

    lines.extend(
        [
            "",
            "**Differences**",
            "",
            *(
                f"- {_difference_text(difference)}"
                for difference in item.differences
            ),
            "",
            _reply_line(item.options),
        ]
    )
    return "\n".join(lines)


def _order_question_markdown(
    item: OrderReviewItem,
    index: int,
    total: int,
) -> str:
    lines = [
        f"**Review {index}/{total}: {item.section.display_title} order**",
        "",
        "**Type:** Order change  ",
        f"**Table:** {item.section.display_title}",
        "",
        "| Judge | Current order | Official order |",
        "|---|---:|---:|",
    ]
    for difference in item.differences:
        current = difference.rtf
        official = difference.official
        entry = official or current
        if entry is None:
            raise ValueError("an order difference must contain a roster entry")
        lines.append(
            f"| {_md_cell(entry.judge.name)} | "
            f"{current.order if current is not None else 'Not listed'} | "
            f"{official.order if official is not None else 'Not listed'} |"
        )

    lines.extend(["", _reply_line(item.options)])
    return "\n".join(lines)


def _question_markdown(
    item: PersonReviewItem | OrderReviewItem,
    index: int,
    total: int,
) -> str:
    if isinstance(item, PersonReviewItem):
        return _person_question_markdown(item, index, total)
    return _order_question_markdown(item, index, total)


def _attach_review_question(
    item: PersonReviewItem | OrderReviewItem,
    index: int,
    total: int,
) -> PersonReviewItem | OrderReviewItem:
    reply_options = tuple(
        ReviewReplyOption(
            reply=option.reply,
            label=option.label,
            decision=ReviewDecisionOption(action=option.action),
        )
        for option in item.options
    )
    return item.model_copy(
        update={
            "question_markdown": _question_markdown(item, index, total),
            "reply_options": reply_options,
        }
    )


def render_review_questions(review: RosterReview) -> str:
    """Render the review plan as concise questions for the user."""
    lines = ["# Judicial roster review", ""]
    if not review.items:
        lines.extend(["No roster differences found.", ""])
        return "\n".join(lines)

    lines.extend(
        [
            (
                f"Found {review.summary.total} review items: "
                f"{review.summary.people} "
                f"{'person' if review.summary.people == 1 else 'people'} and "
                f"{review.summary.sections} section-order "
                f"{'change' if review.summary.sections == 1 else 'changes'}."
            ),
            "",
        ]
    )

    for index, item in enumerate(review.items):
        if index:
            lines.extend(["---", ""])
        lines.extend([item.question_markdown, ""])

    return "\n".join(lines)
