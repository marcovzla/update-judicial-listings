"""Canonical judicial roster models and name normalization."""

from __future__ import annotations

import html
import re
from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict


class JudicialSection(StrEnum):
    rtf_occurrence: int
    display_title: str

    SUPREME_COURT = (1, "supreme_court", "Supreme Court")
    COURT_OF_APPEAL = (2, "court_of_appeal", "Court of Appeal")
    CHANCERY = (3, "chancery", "Chancery Division")
    FAMILY = (4, "family", "Family Division")
    KINGS_BENCH = (5, "kings_bench", "King's Bench Division")

    def __new__(
        cls,
        rtf_occurrence: int,
        serialized_key: str,
        display_title: str,
    ) -> JudicialSection:
        member = str.__new__(cls, serialized_key)
        member._value_ = serialized_key
        member.rtf_occurrence = rtf_occurrence
        member.display_title = display_title
        return member

    @property
    def serialized_key(self) -> str:
        return self.value


def clean_space(value: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(value).replace("\xa0", " ")).strip()


def normalize_name(value: str) -> str:
    value = clean_space(value)
    value = re.sub(r"^(?:The\s+Right\s+Hon(?:ourable)?|The\s+Rt\s+Hon)\s+", "", value)
    value = re.sub(r"^The\s+(Lord|Lady)\b", r"\1", value)
    value = re.sub(r"\s*\([^)]*\)\s*$", "", value)
    return clean_space(value)


def person_key(name: str) -> str:
    """Return a stable key for matching names with different honorifics."""
    value = normalize_name(name).casefold().replace("\u2019", "'")
    value = re.sub(r"\b(the|right|hon|honourable|rt)\b", " ", value)
    value = re.sub(
        r"\b(lord|lady|sir|dame|mr|mrs|ms|justice|baroness|dbe|obe|cbe|kc|kbe)\b",
        " ",
        value,
    )
    return re.sub(r"[^a-z0-9]+", " ", value).strip()


class ImmutableModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)


class Judge(ImmutableModel):
    name: str
    position: str = ""
    url: str = ""
    source: str = ""
    appointment: str = ""
    extra_dates: tuple[str, ...] = ()
    lines: tuple[str, ...] = ()


class SectionConfig(ImmutableModel):
    name: str
    url: str


class RosterSection(ImmutableModel):
    section: JudicialSection
    url: str
    judges: tuple[Judge, ...]


class RosterSource(ImmutableModel):
    key: str
    url: str


class Roster(ImmutableModel):
    retrieved_at: datetime | None = None
    sources: tuple[RosterSource, ...] = ()
    sections: tuple[RosterSection, ...]
    warnings: tuple[str, ...] = ()
