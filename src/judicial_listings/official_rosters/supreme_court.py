"""Supreme Court roster adapter."""

from __future__ import annotations

import json
import re
from urllib.parse import urljoin

import requests

from ..roster_types import (
    Judge,
    JudicialSection,
    RosterSection,
    normalize_name,
)

SUPREME_COURT_URL = "https://www.supremecourt.uk/"

NON_JUSTICE_SLUGS = {"judicial-conduct", "supplementary-panel"}


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
