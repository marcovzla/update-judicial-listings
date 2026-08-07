"""Supreme Court roster adapter."""

from __future__ import annotations

import json
import re
from datetime import date
from html.parser import HTMLParser
from urllib.parse import urljoin

import requests

from ..roster_types import (
    Judge,
    JudicialSection,
    RosterSection,
    clean_space,
    normalize_name,
    parse_long_date,
    person_key,
)

SUPREME_COURT_URL = "https://www.supremecourt.uk/"
WIKIPEDIA_JUDGES_URL = (
    "https://en.wikipedia.org/wiki/"
    "List_of_judges_of_the_Supreme_Court_of_the_United_Kingdom"
)

NON_JUSTICE_SLUGS = {"judicial-conduct", "supplementary-panel"}

MONTH_NAMES = (
    "January|February|March|April|May|June|July|August|September|October|"
    "November|December"
)
FULL_DATE_RE = re.compile(rf"\b\d{{1,2}} (?:{MONTH_NAMES}) \d{{4}}\b")
PROFILE_APPOINTMENT_RE = re.compile(
    rf"(?:became|appointed|re-appointed|took up appointment|sworn in as)"
    rf"[^.]{{0,160}}?\bJustice(?: of the Supreme Court)?\s+on\s+"
    rf"(?P<date>\d{{1,2}} (?:{MONTH_NAMES}) \d{{4}})\b",
    re.IGNORECASE,
)


class _VisibleTextParser(HTMLParser):
    """Extract human-visible text while ignoring page scripts and styles."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._hidden_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        if tag in {"script", "style", "noscript"}:
            self._hidden_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript"} and self._hidden_depth:
            self._hidden_depth -= 1

    def handle_data(self, data: str) -> None:
        if not self._hidden_depth:
            self.parts.append(data)


class _TableParser(HTMLParser):
    """Capture the text cells of top-level HTML tables."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tables: list[list[list[str]]] = []
        self._table_depth = 0
        self._rows: list[list[str]] = []
        self._row: list[str] | None = None
        self._cell: list[str] | None = None
        self._cell_colspan = 1

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "table":
            self._table_depth += 1
            if self._table_depth == 1:
                self._rows = []
            return
        if self._table_depth != 1:
            return
        if tag == "tr":
            self._row = []
        elif tag in {"td", "th"} and self._row is not None:
            self._cell = []
            raw_colspan = dict(attrs).get("colspan") or "1"
            self._cell_colspan = int(raw_colspan) if raw_colspan.isdigit() else 1
        elif tag == "br" and self._cell is not None:
            self._cell.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag == "table":
            if self._table_depth == 1:
                if self._rows:
                    self.tables.append(self._rows)
                self._rows = []
            if self._table_depth:
                self._table_depth -= 1
            return
        if self._table_depth != 1:
            return
        if tag in {"td", "th"} and self._cell is not None:
            if self._row is not None:
                self._row.extend(["".join(self._cell)] * self._cell_colspan)
            self._cell = None
            self._cell_colspan = 1
        elif tag == "tr" and self._row is not None:
            if self._row:
                self._rows.append(self._row)
            self._row = None

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)


def _parse_appointment(value: str) -> date:
    return parse_long_date(value)


def extract_profile_appointment(html: str) -> date | None:
    parser = _VisibleTextParser()
    parser.feed(html)
    match = PROFILE_APPOINTMENT_RE.search(clean_space(" ".join(parser.parts)))
    if match is None:
        return None
    return _parse_appointment(match.group("date"))


def extract_wikipedia_appointments(html: str) -> dict[str, date]:
    """Return the latest Supreme Court start date for each judge in the table."""
    parser = _TableParser()
    parser.feed(html)

    for table in parser.tables:
        header_index = -1
        judge_index = -1
        served_from_index = -1
        for index, row in enumerate(table):
            normalized = [" ".join(cell.split()).casefold() for cell in row]
            for cell_index, cell in enumerate(normalized):
                if "judge of the supreme court" in cell:
                    judge_index = cell_index
                elif cell == "served from":
                    served_from_index = cell_index
            if judge_index >= 0 and served_from_index >= 0:
                header_index = index
                break

        if header_index < 0:
            continue

        appointments: dict[str, date] = {}
        for row in table[header_index + 1 :]:
            if len(row) <= max(judge_index, served_from_index):
                continue
            name = re.sub(r"\[[^]]+]", "", row[judge_index])
            key = person_key(name)
            dates = FULL_DATE_RE.findall(row[served_from_index])
            if key and dates:
                appointment = _parse_appointment(dates[-1])
                appointments[key] = appointment
                short_key = re.sub(r"\s+of\s+.+$", "", key)
                appointments.setdefault(short_key, appointment)
        return appointments

    return {}


def _add_appointment_dates(
    session: requests.Session,
    judges: list[Judge],
) -> list[Judge]:
    profile_dates: dict[str, date] = {}
    unresolved: list[Judge] = []

    for judge in judges:
        try:
            response = session.get(judge.url, timeout=30)
            response.raise_for_status()
            appointment = extract_profile_appointment(response.text)
        except (requests.RequestException, ValueError):
            appointment = None

        if appointment:
            profile_dates[person_key(judge.name)] = appointment
        else:
            unresolved.append(judge)

    wikipedia_dates: dict[str, date] = {}
    if unresolved:
        try:
            response = session.get(WIKIPEDIA_JUDGES_URL, timeout=30)
            response.raise_for_status()
            wikipedia_dates = extract_wikipedia_appointments(response.text)
        except (requests.RequestException, ValueError):
            pass

    return [
        judge.model_copy(
            update={
                "appointment": profile_dates.get(person_key(judge.name))
                or wikipedia_dates.get(person_key(judge.name))
            }
        )
        for judge in judges
    ]


def _display_name_from_description(title: str, description: str) -> tuple[str, str]:
    position = ""
    if description.startswith("President of the Supreme Court"):
        position = "President"
    elif description.startswith("Deputy President of the Supreme Court"):
        position = "Deputy President"

    name = title
    match = re.search(
        r"The Right Hon(?:ourable)?\s+(?:The\s+)?((?:Lord|Lady)\s+[^,]+)$",
        description,
    )
    if match:
        name = match.group(1)
    return normalize_name(name), position


def _decode_escaped_json_string(value: str) -> str:
    """Decode a JSON string from the escaped Next.js RSC payload."""
    if value == "null":
        return ""
    decoded = json.loads(value.replace(r"\"", '"'))
    if not isinstance(decoded, str):
        raise ValueError(f"expected JSON string, got {type(decoded).__name__}")
    return decoded


def fetch_supreme_court(
    session: requests.Session,
) -> tuple[RosterSection, tuple[str, ...]]:
    response = session.get(SUPREME_COURT_URL, timeout=30)
    response.raise_for_status()
    final_url = response.url
    text = response.text
    warnings: list[str] = []
    judges: list[Judge] = []
    seen: set[str] = set()

    block = text
    justices_start = text.find(r"\"title\":\"The Justices\"")
    if justices_start != -1:
        block_end_candidates = [
            pos
            for pos in (
                text.find(r"\"title\":\"Speeches\"", justices_start),
                text.find(r"\"title\":\"Supplementary Panel\"", justices_start),
                text.find(r"\"title\":\"Former Justices\"", justices_start),
            )
            if pos != -1
        ]
        block_end = min(block_end_candidates) if block_end_candidates else len(text)
        block = text[justices_start:block_end]

    for item in re.finditer(
        r'\\"title\\":(?P<title>\\".*?\\").{0,400}?'
        r'\\"url\\":\\"/justices/(?P<slug>[a-z-]+)\\".{0,800}?'
        r'\\"description\\":(?P<description>null|\\".*?\\")',
        block,
    ):
        slug = item.group("slug")
        if slug in NON_JUSTICE_SLUGS or slug in seen:
            continue
        title = _decode_escaped_json_string(item.group("title"))
        description = _decode_escaped_json_string(item.group("description"))
        name, position = _display_name_from_description(title, description)
        judges.append(
            Judge(
                name=name,
                position=position,
                url=urljoin(final_url, f"/justices/{slug}"),
                source=final_url,
            )
        )
        seen.add(slug)

    if not judges:
        warnings.append(
            "Could not parse Supreme Court navigation payload; falling back to "
            "homepage /justices/<slug> links and slug-derived names."
        )
        for match in re.finditer(r"/justices/([a-z-]+)", text):
            slug = match.group(1)
            if slug in NON_JUSTICE_SLUGS or slug in seen:
                continue
            seen.add(slug)
            name = " ".join(
                part.upper() if part in {"dbe", "obe"} else part.title()
                for part in slug.split("-")
            )
            judges.append(
                Judge(
                    name=normalize_name(name),
                    url=urljoin(final_url, f"/justices/{slug}"),
                    source=final_url,
                )
            )

    judges = _add_appointment_dates(session, judges)

    if len(judges) < 8:
        warnings.append(
            f"Supreme Court roster looks short ({len(judges)} entries); "
            "parser may need updating."
        )

    section = RosterSection(
        section=JudicialSection.SUPREME_COURT,
        url=final_url,
        judges=tuple(judges),
    )
    return section, tuple(warnings)
