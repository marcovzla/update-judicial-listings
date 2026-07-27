"""Fetch judicial rosters from official UK court websites."""

from .fetch import FetchError, fetch_official_roster

__all__ = ["FetchError", "fetch_official_roster"]
