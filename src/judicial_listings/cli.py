"""Command-line interface for the judicial-listings workflow."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from pydantic import BaseModel, ValidationError

from .apply_review import ApplyReviewError
from .apply_review import apply_review as apply_review_run
from .layout.model import SplitStatus, load_plan
from .layout.planner import write_initial_layout
from .layout.render import render_rtf
from .official_rosters import FetchError, fetch_official_roster
from .prepare import PrepareError
from .prepare import prepare as prepare_run
from .roster_comparison import compare_rosters
from .roster_review import ReviewStatus
from .roster_types import Roster
from .rtf.extract import extract_rtf
from .rtf.geometry import extract_rtf_geometry

app = typer.Typer(
    name="judicial-listings",
    help="Update and verify All ER judicial-listing tables.",
    no_args_is_help=True,
    add_completion=False,
)
layout_app = typer.Typer(
    help="Plan and apply judicial-listing table layouts.",
    no_args_is_help=True,
)
app.add_typer(layout_app, name="layout")

SourceRtf = Annotated[
    Path,
    typer.Argument(
        help="source RTF file",
        exists=True,
        file_okay=True,
        dir_okay=False,
        readable=True,
    ),
]
PrepareSource = Annotated[
    Path,
    typer.Option(
        "--source",
        help="source RTF file",
        exists=True,
        file_okay=True,
        dir_okay=False,
        readable=True,
    ),
]
RunRoot = Annotated[
    Path,
    typer.Option(
        "--run-root",
        help="create the timestamped run directory here",
        file_okay=False,
        dir_okay=True,
        show_default=True,
    ),
]
RunDirectory = Annotated[
    Path,
    typer.Option(
        "--run",
        help="prepared run directory",
        exists=True,
        file_okay=False,
        dir_okay=True,
        readable=True,
    ),
]
DecisionsFile = Annotated[
    Path,
    typer.Option(
        "--decisions",
        help="review decisions JSON",
        exists=True,
        file_okay=True,
        dir_okay=False,
        readable=True,
    ),
]
RtfRoster = Annotated[
    Path,
    typer.Argument(
        help="RTF roster JSON produced by the extract command",
        exists=True,
        file_okay=True,
        dir_okay=False,
        readable=True,
    ),
]
OfficialRoster = Annotated[
    Path,
    typer.Argument(
        help="official roster JSON produced by the fetch command",
        exists=True,
        file_okay=True,
        dir_okay=False,
        readable=True,
    ),
]
OutputPath = Annotated[
    Path,
    typer.Option(
        "-o",
        "--output",
        help="write JSON here; use '-' for standard output",
        show_default=True,
    ),
]
ResolvedRoster = Annotated[
    Path,
    typer.Argument(
        help="resolved roster JSON produced by apply-review",
        exists=True,
        file_okay=True,
        dir_okay=False,
        readable=True,
    ),
]
LayoutPlanOutput = Annotated[
    Path,
    typer.Option(
        "-o",
        "--output",
        help="write the layout plan JSON here",
    ),
]
LayoutGeometry = Annotated[
    Path,
    typer.Option(
        "--geometry",
        help="RTF geometry JSON produced by prepare or geometry",
        exists=True,
        file_okay=True,
        dir_okay=False,
        readable=True,
    ),
]
LayoutDecisionsOutput = Annotated[
    Path,
    typer.Option(
        "--decisions",
        help="write the editable layout decisions JSON here",
    ),
]
LayoutDiagnosticsOutput = Annotated[
    Path,
    typer.Option(
        "--diagnostics",
        help="write layout diagnostics Markdown here",
    ),
]
LayoutPlanFile = Annotated[
    Path,
    typer.Argument(
        help="layout plan JSON produced by the plan command",
        exists=True,
        file_okay=True,
        dir_okay=False,
        readable=True,
    ),
]
UpdatedRtfOutput = Annotated[
    Path,
    typer.Option(
        "-o",
        "--output",
        help="write the updated RTF here",
    ),
]


def _write_json(value: BaseModel, output: Path) -> None:
    text = value.model_dump_json(indent=2) + "\n"
    if output == Path("-"):
        typer.echo(text, nl=False)
        return
    output.write_text(text, encoding="utf-8")


def _read_roster(source: Path) -> Roster:
    try:
        return Roster.model_validate_json(source.read_text(encoding="utf-8"))
    except (OSError, ValidationError) as exc:
        typer.echo(f"failed to load roster {source}: {exc}", err=True)
        raise typer.Exit(code=1) from exc


@app.command()
def prepare(
    source: PrepareSource,
    run_root: RunRoot = Path("runs/update-judicial-listings"),
) -> None:
    """Prepare the RTF and official roster for review."""
    try:
        result = prepare_run(source, run_root)
    except PrepareError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(f"status: {result.status}")
    if result.status is ReviewStatus.REVIEW_REQUIRED:
        typer.echo(f"run: {result.run_directory}")
        typer.echo(f"rtf_geometry: {result.rtf_geometry}")
        typer.echo(f"review_items: {result.review_items}")
        typer.echo(f"review_questions: {result.review_questions}")


@app.command()
def apply_review(
    run: RunDirectory,
    decisions: DecisionsFile,
) -> None:
    """Apply review decisions to produce the resolved roster."""
    try:
        result = apply_review_run(run, decisions)
    except ApplyReviewError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc

    typer.echo("status: applied")
    typer.echo(f"resolved_roster: {result.resolved_roster}")
    typer.echo(f"approved_changes: {result.approved_changes}")


@app.command()
def extract(
    source: SourceRtf,
    output: OutputPath = Path("-"),
) -> None:
    """Extract judge tables from an RTF."""
    _write_json(extract_rtf(source), output)


@app.command()
def geometry(
    source: SourceRtf,
    output: OutputPath = Path("-"),
) -> None:
    """Extract table geometry and typography from an RTF."""
    _write_json(extract_rtf_geometry(source), output)


@app.command()
def fetch(output: OutputPath = Path("-")) -> None:
    """Fetch current official judicial rosters."""
    try:
        _write_json(fetch_official_roster(), output)
    except FetchError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc


@app.command()
def compare(
    rtf: RtfRoster,
    official: OfficialRoster,
    output: OutputPath = Path("-"),
) -> None:
    """Compare the official roster with the RTF roster."""
    _write_json(
        compare_rosters(
            rtf=_read_roster(rtf),
            official=_read_roster(official),
        ),
        output,
    )


@layout_app.command("plan")
def plan_layout(
    resolved_roster: ResolvedRoster,
    geometry: LayoutGeometry,
    output: LayoutPlanOutput,
    decisions: LayoutDecisionsOutput,
    diagnostics: LayoutDiagnosticsOutput,
) -> None:
    """Create an initial layout plan from an approved roster."""
    try:
        plan = write_initial_layout(
            resolved_roster,
            geometry,
            output,
            decisions,
            diagnostics,
        )
    except (OSError, ValueError) as exc:
        typer.echo(f"failed to plan layout: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    for table in plan.tables:
        pending = sum(
            block.split_status is SplitStatus.PENDING for block in table.blocks
        )
        typer.echo(
            f"{table.id} {table.title}: {len(table.blocks)} blocks, "
            f"{pending} pending split decisions"
        )
    typer.echo(f"wrote {output}")
    typer.echo(f"wrote {decisions}")
    typer.echo(f"wrote {diagnostics}")


@layout_app.command("render")
def render_layout(
    source: SourceRtf,
    plan: LayoutPlanFile,
    output: UpdatedRtfOutput,
) -> None:
    """Render an updated RTF from a layout plan."""
    try:
        render_rtf(source, load_plan(plan), output)
    except (OSError, RuntimeError, ValueError) as exc:
        typer.echo(f"failed to render RTF: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(f"wrote {output}")


def main() -> None:
    app()
