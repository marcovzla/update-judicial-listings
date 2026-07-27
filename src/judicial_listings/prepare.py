"""Prepare a judicial-listings run for user review."""

from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime
from itertools import count
from pathlib import Path

from pydantic import BaseModel

from .official_rosters import fetch_official_roster
from .roster_comparison import compare_rosters
from .roster_review import (
    ReviewInputs,
    ReviewStatus,
    build_review,
    render_review_questions,
)
from .roster_types import ImmutableModel
from .rtf.extract import extract_semantic_roster
from .rtf.geometry import extract_geometry

RTF_ROSTER_NAME = "rtf_roster.json"
RTF_GEOMETRY_NAME = "rtf_geometry.json"
OFFICIAL_ROSTER_NAME = "official_roster.json"
COMPARISON_NAME = "comparison.json"
REVIEW_ITEMS_NAME = "review_items.json"
REVIEW_QUESTIONS_NAME = "review_questions.md"


class PrepareError(RuntimeError):
    pass


class PreparedRun(ImmutableModel):
    status: ReviewStatus
    run_directory: Path
    rtf_geometry: Path
    review_items: Path
    review_questions: Path


def _write_json(path: Path, value: BaseModel) -> None:
    path.write_text(value.model_dump_json(indent=2) + "\n", encoding="utf-8")


def _source_slug(source: Path) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "-", source.stem).strip("-").lower()
    return slug or "rtf"


def _create_run_directory(
    run_root: Path,
    source: Path,
    created_at: datetime,
) -> Path:
    run_root.mkdir(parents=True, exist_ok=True)
    base_name = f"{created_at:%Y%m%d-%H%M%S}-{_source_slug(source)}"

    for suffix in count():
        name = base_name if suffix == 0 else f"{base_name}-{suffix + 1}"
        run_directory = run_root / name
        try:
            run_directory.mkdir()
        except FileExistsError:
            continue
        return run_directory

    raise AssertionError("unreachable")


def _prepare(source: Path, run_root: Path) -> PreparedRun:
    source_bytes = source.read_bytes()
    rtf = source_bytes.decode("latin-1")
    rtf_roster = extract_semantic_roster(rtf)
    rtf_geometry = extract_geometry(rtf)
    official_roster = fetch_official_roster()
    comparison = compare_rosters(rtf_roster, official_roster)
    created_at = datetime.now(UTC)

    review = build_review(
        rtf=rtf_roster,
        official=official_roster,
        comparison=comparison,
        inputs=ReviewInputs(
            source_rtf=str(source.resolve()),
            source_sha256=hashlib.sha256(source_bytes).hexdigest(),
            rtf_roster=RTF_ROSTER_NAME,
            rtf_geometry=RTF_GEOMETRY_NAME,
            official_roster=OFFICIAL_ROSTER_NAME,
            comparison=COMPARISON_NAME,
        ),
        created_at=created_at,
    )
    run_directory = _create_run_directory(run_root, source, created_at)

    _write_json(run_directory / RTF_ROSTER_NAME, rtf_roster)
    _write_json(run_directory / RTF_GEOMETRY_NAME, rtf_geometry)
    _write_json(run_directory / OFFICIAL_ROSTER_NAME, official_roster)
    _write_json(run_directory / COMPARISON_NAME, comparison)
    _write_json(run_directory / REVIEW_ITEMS_NAME, review)
    (run_directory / REVIEW_QUESTIONS_NAME).write_text(
        render_review_questions(review),
        encoding="utf-8",
    )

    return PreparedRun(
        status=review.status,
        run_directory=run_directory,
        rtf_geometry=run_directory / RTF_GEOMETRY_NAME,
        review_items=run_directory / REVIEW_ITEMS_NAME,
        review_questions=run_directory / REVIEW_QUESTIONS_NAME,
    )


def prepare(source: Path, run_root: Path) -> PreparedRun:
    """Create a complete, review-ready run without modifying the source RTF."""
    try:
        return _prepare(source, run_root)
    except (OSError, RuntimeError, ValueError) as exc:
        raise PrepareError(f"failed to prepare judicial listings: {exc}") from exc
