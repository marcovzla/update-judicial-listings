"""Apply All ER first-listing rules to an official judicial roster."""

from __future__ import annotations

from .roster_types import Judge, Roster, RosterSection, normalize_name, person_key


def _judge_key(judge: Judge) -> str:
    return person_key(judge.name) or normalize_name(judge.name).casefold()


def apply_first_listing_policy(current: Roster, official: Roster) -> Roster:
    """Append new section members and mark their first-volume appointment."""
    current_keys = {
        section.section: {_judge_key(judge) for judge in section.judges}
        for section in current.sections
    }
    adjusted_sections: list[RosterSection] = []

    for section in official.sections:
        section_keys = current_keys.get(section.section, set())
        existing: list[Judge] = []
        new: list[Judge] = []

        for judge in section.judges:
            if _judge_key(judge) in section_keys:
                existing.append(
                    judge.model_copy(update={"first_listing_appointment": None})
                )
                continue
            if judge.appointment is None:
                raise ValueError(
                    f"{section.section.display_title}: newly listed judge "
                    f"{judge.name!r} has no appointment date"
                )
            new.append(
                judge.model_copy(
                    update={"first_listing_appointment": judge.appointment}
                )
            )

        adjusted_sections.append(
            section.model_copy(update={"judges": (*existing, *new)})
        )

    return official.model_copy(update={"sections": tuple(adjusted_sections)})
