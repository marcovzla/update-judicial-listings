"""Fetch and combine official judicial rosters."""

from __future__ import annotations

from datetime import UTC, datetime

import requests

from ..roster_types import Roster, RosterSource
from .judiciary import JUDICIARY_INDEX_URL, fetch_judiciary
from .supreme_court import SUPREME_COURT_URL, fetch_supreme_court

USER_AGENT = "Mozilla/5.0 (compatible; judicial-listing-roster-fetcher/1.0)"


class FetchError(RuntimeError):
    pass


def fetch_official_roster() -> Roster:
    try:
        with requests.Session() as session:
            session.headers["User-Agent"] = USER_AGENT
            supreme_court, supreme_court_warnings = fetch_supreme_court(session)
            judiciary_sections, judiciary_warnings = fetch_judiciary(session)
    except (requests.RequestException, ValueError) as exc:
        raise FetchError(f"failed to load official rosters: {exc}") from exc

    return Roster(
        retrieved_at=datetime.now(UTC),
        sources=(
            RosterSource(key="supreme_court", url=SUPREME_COURT_URL),
            RosterSource(key="judiciary_index", url=JUDICIARY_INDEX_URL),
        ),
        sections=(supreme_court, *judiciary_sections),
        warnings=(*supreme_court_warnings, *judiciary_warnings),
    )
