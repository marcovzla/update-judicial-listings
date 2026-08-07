"""judiciary.uk roster adapter."""

from __future__ import annotations

import re
from datetime import date
from html.parser import HTMLParser
from typing import TypedDict
from urllib.parse import urljoin

import requests

from ..roster_types import (
    Judge,
    JudicialSection,
    RosterSection,
    SectionConfig,
    clean_space,
    normalize_name,
)

JUDICIARY_INDEX_URL = (
    "https://www.judiciary.uk/about-the-judiciary/who-are-the-judiciary/"
    "senior-judiciary-list/"
)

LINK_TEXTS: dict[JudicialSection, str] = {
    JudicialSection.COURT_OF_APPEAL: "Lord and Lady Justices of Appeal",
    JudicialSection.CHANCERY: "Chancery Division Judges",
    JudicialSection.FAMILY: "Family Division Judges",
    JudicialSection.KINGS_BENCH: "King's Bench Division Judges",
}

NAME_START_RE = re.compile(
    r"^(?:"
    r"(?:The\s+)?Lord|Lady|Sir|Dame|Mr|Mrs|Ms|Baroness"
    r")(?:\s+Justice)?\b"
)
DATE_RE = re.compile(r"\b\d{1,2}-\d{1,2}-(?:\d{2}|\d{4})\b")


class _ParsedCell(TypedDict):
    text: str
    url: str


class _LinkParser(HTMLParser):
    """Capture links from the senior judiciary index page."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[tuple[str, str]] = []
        self._href = ""
        self._text: list[str] = []
        self._in_link = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a":
            return
        href = dict(attrs).get("href")
        if href is None:
            return
        self._href = href
        self._text = []
        self._in_link = True

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._in_link:
            text = clean_space("".join(self._text))
            if text:
                self.links.append((text, self._href))
            self._href = ""
            self._text = []
            self._in_link = False

    def handle_data(self, data: str) -> None:
        if self._in_link:
            self._text.append(data)


class _LinkCapturingTableParser(HTMLParser):
    """Parse table rows and retain the first link in each cell."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[list[_ParsedCell]] = []
        self._in_table = False
        self._in_row = False
        self._in_cell = False
        self._cell_text: list[str] = []
        self._cell_link = ""
        self._row: list[_ParsedCell] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_dict = dict(attrs)
        if tag == "table":
            self._in_table = True
        elif self._in_table and tag == "tr":
            self._in_row = True
            self._row = []
        elif self._in_row and tag in {"td", "th"}:
            self._in_cell = True
            self._cell_text = []
            self._cell_link = ""
        elif self._in_cell and tag == "br":
            self._cell_text.append("\n")
        elif self._in_cell and tag == "a" and not self._cell_link:
            self._cell_link = attrs_dict.get("href") or ""

    def handle_endtag(self, tag: str) -> None:
        if tag in {"td", "th"} and self._in_cell:
            self._row.append({"text": "".join(self._cell_text), "url": self._cell_link})
            self._in_cell = False
        elif tag == "tr" and self._in_row:
            if self._row:
                self.rows.append(self._row)
            self._in_row = False
        elif tag == "table":
            self._in_table = False

    def handle_data(self, data: str) -> None:
        if self._in_cell:
            self._cell_text.append(data)


def _link_key(value: str) -> str:
    return clean_space(value).replace("\u2019", "'").casefold()


def _discover_sections(
    session: requests.Session,
) -> dict[JudicialSection, SectionConfig]:
    response = session.get(JUDICIARY_INDEX_URL, timeout=30)
    response.raise_for_status()
    final_url = response.url
    text = response.text
    parser = _LinkParser()
    parser.feed(text)

    by_text = {
        _link_key(text): urljoin(final_url, href) for text, href in parser.links
    }
    sections: dict[JudicialSection, SectionConfig] = {}
    missing: list[str] = []
    for section, title in LINK_TEXTS.items():
        url = by_text.get(_link_key(title))
        if url is None:
            missing.append(title)
            continue
        sections[section] = SectionConfig(name=title, url=url)

    if missing:
        raise ValueError(
            "failed to discover senior judiciary roster links from "
            f"{final_url}: {', '.join(missing)}"
        )
    return sections


def _cell_lines(raw: str) -> list[str]:
    lines = [clean_space(line) for line in raw.splitlines()]
    return [line for line in lines if line]


def _appointment_date(value: str) -> date:
    day, month, short_year = (int(part) for part in value.split("-"))
    year = short_year + 2000 if short_year < 100 else short_year
    return date(year, month, day)


def _parse_page(
    session: requests.Session,
    section: SectionConfig,
) -> tuple[list[Judge], str]:
    response = session.get(section.url, timeout=30)
    response.raise_for_status()
    final_url = response.url
    text = response.text
    parser = _LinkCapturingTableParser()
    parser.feed(text)

    judges: list[Judge] = []
    for row in parser.rows:
        if len(row) < 2:
            continue
        first_lines = _cell_lines(row[0]["text"])
        second_lines = _cell_lines(row[1]["text"])
        if not first_lines:
            continue
        candidate = first_lines[0]
        if candidate.lower() in {"name", "judge"}:
            continue
        if not NAME_START_RE.match(candidate):
            continue

        position_parts: list[str] = []
        for line in first_lines[1:]:
            if not DATE_RE.search(line):
                position_parts.append(line.strip())
        position = clean_space(" ".join(position_parts))
        if position.startswith("(") and position.endswith(")"):
            position = position[1:-1].strip()

        judges.append(
            Judge(
                name=normalize_name(candidate),
                position=position,
                url=urljoin(final_url, row[0]["url"]) if row[0]["url"] else "",
                source=final_url,
                appointment=(
                    _appointment_date(second_lines[0]) if second_lines else None
                ),
                extra_dates=tuple(second_lines[1:]),
            )
        )

    return judges, final_url


def fetch_judiciary(
    session: requests.Session,
) -> tuple[tuple[RosterSection, ...], tuple[str, ...]]:
    sections: list[RosterSection] = []
    warnings: list[str] = []
    for key, section in _discover_sections(session).items():
        judges, final_url = _parse_page(session, section)
        sections.append(
            RosterSection(
                section=key,
                url=final_url,
                judges=tuple(judges),
            )
        )
        if not judges:
            warnings.append(f"{section.name} returned 0 entries from {final_url}.")
    return tuple(sections), tuple(warnings)
