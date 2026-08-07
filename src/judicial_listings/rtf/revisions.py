"""RTF tracked-revision controls used by the renderer."""

from __future__ import annotations

from .text import escape_text

REVISION_TABLE = r"{\*\revtbl{Unknown;}{Codex;}}"
REVISION_SETTINGS = r"\revisions\revprop3\revbar1"


def add_revision_metadata(rtf: str) -> str:
    """Add the fixed Codex revision table and display settings."""
    if r"\revtbl" in rtf or r"\revisions" in rtf:
        raise ValueError("source RTF already contains tracked revisions")

    rsid_table = rtf.find(r"{\*\rsidtbl")
    if rsid_table == -1:
        raise ValueError("RTF has no RSID table to anchor the revision table")
    updated = rtf[:rsid_table] + REVISION_TABLE + rtf[rsid_table:]

    track_moves = updated.find(r"\trackmoves0")
    if track_moves == -1:
        raise ValueError("RTF has no \\trackmoves0 document property")
    return updated[:track_moves] + REVISION_SETTINGS + updated[track_moves:]


def inserted_text(text: str) -> str:
    return r"{\revised\revauth1 " + escape_text(text) + "}"


def deleted_text(text: str) -> str:
    return r"{\deleted\revauthdel1 " + escape_text(text) + "}"


def deleted_lines(
    lines: tuple[str, ...],
    *,
    leading_line: bool = False,
    trailing_line: bool = False,
) -> str:
    content = r"\line " if leading_line else ""
    content += r"\line ".join(escape_text(line) for line in lines)
    if trailing_line:
        content += r"\line "
    return r"{\deleted\revauthdel1 " + content + "}"
