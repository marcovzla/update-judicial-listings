"""Compare an official judicial roster with the roster extracted from an RTF."""

from __future__ import annotations

from enum import StrEnum

from .roster_types import (
    ImmutableModel,
    Judge,
    JudicialSection,
    Roster,
    normalize_name,
    person_key,
)

type Slot = tuple[str, JudicialSection]


class DifferenceKind(StrEnum):
    ADDED = "added"
    REMOVED = "removed"
    MOVED = "moved"
    NAME_CHANGED = "name_changed"
    POSITION_CHANGED = "position_changed"
    ORDER_CHANGED = "order_changed"


class RosterEntry(ImmutableModel):
    section: JudicialSection
    order: int
    judge: Judge


class RosterDifference(ImmutableModel):
    kind: DifferenceKind
    key: str
    rtf: RosterEntry | None = None
    official: RosterEntry | None = None


class RosterComparison(ImmutableModel):
    has_differences: bool
    differences: tuple[RosterDifference, ...]
    warnings: tuple[str, ...] = ()


def _entry_key(entry: RosterEntry) -> str:
    return person_key(entry.judge.name) or normalize_name(entry.judge.name).casefold()


def _entry_sort_key(entry: RosterEntry) -> tuple[int, int, str]:
    return (
        entry.section.rtf_occurrence,
        entry.order,
        entry.judge.name.casefold(),
    )


def _index_roster(
    roster: Roster,
    label: str,
) -> tuple[dict[Slot, list[RosterEntry]], list[str]]:
    entries_by_slot: dict[Slot, list[RosterEntry]] = {}
    warnings: list[str] = []

    for roster_section in roster.sections:
        for order, judge in enumerate(roster_section.judges, start=1):
            key = person_key(judge.name)
            if not key:
                key = normalize_name(judge.name).casefold()
                warnings.append(
                    f"{label}: could not create a normalized key for {judge.name!r}"
                )
            entry = RosterEntry(
                section=roster_section.section,
                order=order,
                judge=judge,
            )
            entries_by_slot.setdefault((key, roster_section.section), []).append(entry)

    return entries_by_slot, warnings


def _group_by_key(entries: list[RosterEntry]) -> dict[str, list[RosterEntry]]:
    grouped: dict[str, list[RosterEntry]] = {}
    for entry in entries:
        grouped.setdefault(_entry_key(entry), []).append(entry)
    return grouped


def _pair_entries(
    rtf: Roster,
    official: Roster,
) -> tuple[
    list[tuple[RosterEntry, RosterEntry]],
    list[tuple[RosterEntry, RosterEntry]],
    list[RosterEntry],
    list[RosterEntry],
    list[str],
]:
    rtf_slots, rtf_warnings = _index_roster(rtf, "RTF roster")
    official_slots, official_warnings = _index_roster(official, "official roster")
    same_section_pairs: list[tuple[RosterEntry, RosterEntry]] = []
    unmatched_rtf: list[RosterEntry] = []
    unmatched_official: list[RosterEntry] = []

    slots = set(rtf_slots) | set(official_slots)
    for slot in sorted(
        slots,
        key=lambda value: (value[1].rtf_occurrence, value[0]),
    ):
        rtf_entries = rtf_slots.get(slot, [])
        official_entries = official_slots.get(slot, [])
        paired = min(len(rtf_entries), len(official_entries))
        same_section_pairs.extend(
            zip(rtf_entries[:paired], official_entries[:paired], strict=True)
        )
        unmatched_rtf.extend(rtf_entries[paired:])
        unmatched_official.extend(official_entries[paired:])

    rtf_by_key = _group_by_key(unmatched_rtf)
    official_by_key = _group_by_key(unmatched_official)
    moved_pairs: list[tuple[RosterEntry, RosterEntry]] = []
    remaining_rtf: list[RosterEntry] = []
    remaining_official: list[RosterEntry] = []

    for key in sorted(set(rtf_by_key) | set(official_by_key)):
        rtf_entries = rtf_by_key.get(key, [])
        official_entries = official_by_key.get(key, [])
        if len(rtf_entries) == len(official_entries) == 1:
            moved_pairs.append((rtf_entries[0], official_entries[0]))
        else:
            remaining_rtf.extend(rtf_entries)
            remaining_official.extend(official_entries)

    return (
        same_section_pairs,
        moved_pairs,
        remaining_rtf,
        remaining_official,
        [*rtf_warnings, *official_warnings],
    )


def _membership_differences(
    moved_pairs: list[tuple[RosterEntry, RosterEntry]],
    removed: list[RosterEntry],
    added: list[RosterEntry],
) -> list[RosterDifference]:
    differences = [
        RosterDifference(
            kind=DifferenceKind.MOVED,
            key=_entry_key(official),
            rtf=rtf,
            official=official,
        )
        for rtf, official in moved_pairs
    ]
    differences.extend(
        RosterDifference(
            kind=DifferenceKind.REMOVED,
            key=_entry_key(entry),
            rtf=entry,
        )
        for entry in removed
    )
    differences.extend(
        RosterDifference(
            kind=DifferenceKind.ADDED,
            key=_entry_key(entry),
            official=entry,
        )
        for entry in added
    )

    return sorted(
        differences,
        key=lambda difference: _entry_sort_key(_entry(difference)),
    )


def _entry(difference: RosterDifference) -> RosterEntry:
    if difference.official is not None:
        return difference.official
    if difference.rtf is not None:
        return difference.rtf
    raise ValueError("a roster difference must contain an RTF or official entry")


def _content_differences(
    pairs: list[tuple[RosterEntry, RosterEntry]],
) -> list[RosterDifference]:
    differences: list[RosterDifference] = []

    for rtf, official in sorted(pairs, key=lambda pair: _entry_sort_key(pair[1])):
        key = _entry_key(official)
        if rtf.judge.name != official.judge.name:
            differences.append(
                RosterDifference(
                    kind=DifferenceKind.NAME_CHANGED,
                    key=key,
                    rtf=rtf,
                    official=official,
                )
            )
        if rtf.judge.position != official.judge.position:
            differences.append(
                RosterDifference(
                    kind=DifferenceKind.POSITION_CHANGED,
                    key=key,
                    rtf=rtf,
                    official=official,
                )
            )

    return differences


def _order_differences(
    pairs: list[tuple[RosterEntry, RosterEntry]],
) -> list[RosterDifference]:
    differences: list[RosterDifference] = []

    for section in JudicialSection:
        section_pairs = [
            pair
            for pair in pairs
            if pair[0].section == pair[1].section == section
        ]
        key_counts: dict[str, int] = {}
        for _, official in section_pairs:
            key = _entry_key(official)
            key_counts[key] = key_counts.get(key, 0) + 1

        unique_pairs = [
            pair for pair in section_pairs if key_counts[_entry_key(pair[1])] == 1
        ]
        rtf_order = sorted(unique_pairs, key=lambda pair: pair[0].order)
        official_order = sorted(unique_pairs, key=lambda pair: pair[1].order)
        rtf_positions = {
            _entry_key(pair[1]): position
            for position, pair in enumerate(rtf_order, start=1)
        }
        official_positions = {
            _entry_key(pair[1]): position
            for position, pair in enumerate(official_order, start=1)
        }

        for rtf, official in official_order:
            key = _entry_key(official)
            if rtf_positions[key] != official_positions[key]:
                differences.append(
                    RosterDifference(
                        kind=DifferenceKind.ORDER_CHANGED,
                        key=key,
                        rtf=rtf,
                        official=official,
                    )
                )

    return differences


def compare_rosters(rtf: Roster, official: Roster) -> RosterComparison:
    """Return every official-roster difference that affects the RTF."""
    same_section_pairs, moved_pairs, removed, added, warnings = _pair_entries(
        rtf,
        official,
    )
    paired = [*same_section_pairs, *moved_pairs]
    differences = [
        *_membership_differences(moved_pairs, removed, added),
        *_content_differences(paired),
        *_order_differences(same_section_pairs),
    ]

    return RosterComparison(
        has_differences=bool(differences),
        differences=tuple(differences),
        warnings=(
            *rtf.warnings,
            *official.warnings,
            *warnings,
        ),
    )
