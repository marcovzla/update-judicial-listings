---
name: update-judicial-listings
description: >-
  Update the judge listings in a LexisNexis All England Reports prelims RTF
  (the editorial-board pages: Supreme Court Justices, Court of Appeal, and the
  High Court divisions) and balance the two-column tables for print. Use
  whenever the user wants to refresh, update, or sync the judges/editorial board
  in an All ER (or similar legal-publishing) RTF, retrieve current official UK
  judicial rosters for that update, amend a judge's title, or rebalance the
  judge columns. Edits stay strictly inside the judge tables; everything else in
  the document is left byte-for-byte identical.
---

# Update judicial listings

## Overview

The user provides an All England Reports prelims RTF. Update the five
judicial-listing tables in it — Supreme Court, Court of Appeal, Chancery
Division, Family Division, and King's Bench Division — and return a new RTF.
Each table is marked by `#TableB` / `#TableE`; only the table bodies change.

The workflow is a single linear sequence:

1. **Determine the new table contents** — fetch the current official roster,
   compare it against the roster in the user's RTF, and confirm every content
   change with the user.
2. **Plan and render the layout** — build the canonical two-column layout and
   render it into an updated RTF.
3. **Deliver** — report the updated RTF path.

Two invariants hold across the whole workflow:

- **Source order is authoritative.** Judge order comes from the official
  sources and is never reshuffled to balance a page.
- **Only table bodies change.** Everything outside the five judge tables stays
  byte-for-byte identical.

## Source Rules

Use the source path implied by the user's request.

- Do not start from an RTF file discovered by searching the workspace unless the
  user explicitly identified that file or confirms it after discovery. If the
  user has not provided a source RTF path, ask for the path and stop. If one or
  more candidate RTF files are found, show the candidate path(s) and wait for the
  user to choose before running extraction, fetching, comparison, or rendering.
- If the user provides roster data or asks for a local-only run, use only that
  data. Do not fetch, search, or infer current office holders.
- If the user asks to retrieve, refresh, update, or sync against current
  official rosters, use `SKILL_CLI prepare`. Do not use a search engine,
  sitemap, memory, news pages, or other ad hoc sources.
- The official-source workflow is limited to the Supreme Court homepage and the
  senior judiciary list source path. The fetcher uses the judiciary index only
  to discover the four target roster pages linked from it.
- For conflict evidence only, the fetcher may follow official person/profile
  links already present in those roster sources. Do not use profile pages to add
  or remove roster membership.
- If official pages cannot be fetched or parsed, stop and report the failure.
  Do not fall back to local resource JSON.

Official source text and order are authoritative. Report every difference to the
user before changing the RTF.

## Run the CLI

`SKILL_CLI` below is shorthand for this skill's platform-specific launcher.

Before its first use:

- Use the absolute skill path shown in Codex's available-skills listing to
  determine `SKILL_DIR`.
- On Windows, replace `SKILL_CLI` with:
  `powershell.exe -NoProfile -File "<SKILL_DIR>\scripts\run.ps1"`.
- On macOS or Linux, replace `SKILL_CLI` with:
  `bash "<SKILL_DIR>/scripts/run.sh"`.

Always resolve filesystem arguments to absolute paths. Do not change into or
write runtime files inside `SKILL_DIR`. The shorthand is instructional notation;
do not assume aliases or variables persist between commands.

Example:

```text
SKILL_CLI prepare --source <SOURCE_RTF>
```

## Run Directory

Create generated artifacts under:

```text
runs/update-judicial-listings/<YYYYMMDD-HHMMSS>-<input-rtf-slug>/
```

Keep the resolved roster, layout artifacts, and final `updated.rtf` in this run
directory.

## Stage 1 — Determine the new table contents

Prepare the run:

```text
SKILL_CLI prepare --source <SOURCE_RTF>
```

If the driver prints `status: no_changes`, stop and tell the user no changes are
needed.

If it prints `status: review_required`, use the printed `review_questions` and
`review_items` paths. Reviews confirm content only — which judges, their names,
titles, and order — never layout.

Ask the generated Markdown questions from `review_questions.md` one at a time,
in order. Do not rewrite, summarize, or reformat the question; the script has
already formatted the facts and reply options. Each item in
`review_items.json` also contains the same text in `question_markdown` plus
`reply_options`, which map the displayed answer back to the decision JSON to
write.

Each review item is both the question and the approval for its covered document
effects. Do not ask later about effects already covered by an earlier review
item. After the user answers, record the matching `reply_options[].decision` in
`<RUN>/review_decisions.json`. If the user gives manual wording, write a
`manual` decision with an explicit `entry` object.

Write the answers to `<RUN>/review_decisions.json`:

```json
{
  "decisions": {
    "person:snowden": {
      "action": "use_official"
    },
    "person:bean": {
      "action": "keep_current",
      "note": "Keep the current RTF wording."
    },
    "person:example": {
      "action": "manual",
      "entry": {
        "section": "family",
        "order": 4,
        "name": "Mrs Justice Example",
        "position": "Judge"
      },
      "note": "Use the manually specified entry."
    },
    "order:court_of_appeal": {
      "action": "use_official"
    }
  }
}
```

Use each item ID exactly as it appears in `review_items.json`. Person items
support `use_official`, `keep_current`, and `manual`; a manual decision requires
an explicit `entry` object. Order items support only `use_official` and
`keep_current`.

Apply the one-pass review:

```text
SKILL_CLI apply-review --run <RUN> --decisions <RUN>/review_decisions.json
```

This writes `resolved_roster.json` and `approved_changes.json`. The resolved
roster is the source of truth for membership, names, positions, and order.

## Stage 2 — Plan and render the layout

Create the layout plan:

```text
SKILL_CLI layout plan <RUN>/resolved_roster.json --geometry <RUN>/rtf_geometry.json --output <RUN>/layout_plan.json --decisions <RUN>/layout_decisions.json --diagnostics <RUN>/layout_diagnostics.md
```

The planner preserves source order, chooses the two-column boundary, and accepts
any required position-line splits. Do not edit the generated plan or decisions.

Render the updated RTF:

```text
SKILL_CLI layout render <SOURCE_RTF> <RUN>/layout_plan.json --output <RUN>/updated.rtf
```

The renderer must leave everything outside the five judge-table bodies
byte-for-byte identical.

## Stage 3 — Deliver

Tell the user the final RTF path:

```text
<RUN>/updated.rtf
```

## Guarantees

- Only the five judge-table bodies change.
- Content outside the edited table bodies is byte-for-byte identical.
- Source order remains unchanged.
- Cell geometry is preserved from the source RTF.

## References

- `references/editing-architecture.md`
- `references/rtf-1.9.1-digest.md`
