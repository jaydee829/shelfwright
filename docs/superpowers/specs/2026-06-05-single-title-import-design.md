# Single-Title Import — Design

**Date:** 2026-06-05
**Status:** Approved (brainstormed with user)
**Branch:** `spec/single-title-import`

## Problem

There is no way to add one book to the reading history without a CSV batch. The pieces
almost exist — `enrich_and_persist_work` creates an enriched Work (no history row);
`update_reading_status` writes a history row but only for catalog works, with no rating
parameter and `date_completed` hardcoded to today. A real single-title import needs
rating, an actual completion date, format choice, and a deliberate flow.

## User decisions (brainstorm)

1. **Entry points**: conversational + CLI for singles; **bulk imports stay on the
   CSV/Dagster path** (per-book enrichment cost is identical ~1-3 min; Dagster's
   ceremony amortizes only in bulk, where it adds chunking/resume/DVC).
2. **CSV drift accepted**: single adds live only in the DB. The DB is the source of
   truth going forward; `pg_dump` snapshots are the backup; the DVC-tracked CSVs are a
   historical artifact plus the bulk-import format.
3. **Approach A**: one new tool, `add_book_to_history` — `update_reading_status` keeps
   its narrow feedback semantics, untouched.

## Design

### 1. The tool — `add_book_to_history` (`mcp/server.py`, registered MCP tool)

```python
add_book_to_history(
    title: str, author: str, date_completed: str | None = None,
    rating: int | None = None, format: str = "ebook", notes: str | None = None,
) -> str
```

**Validation upfront (SEC-002 conventions, error strings, never raises):**
- `title`/`author` via `_valid_name` (non-empty, ≤500).
- `date_completed` optional, **defaults to today** when not provided (user decision —
  the column is `NOT NULL`, so a value is needed; today is the sensible default for
  "I just finished X"). When provided: ISO `YYYY-MM-DD` via `date.fromisoformat`,
  rejected if in the future or unparseable.
  **Front-end note (logged for Phase 4):** the web UI's add form will AUTO-FILL the
  date field with today rather than hiding the default — the user sees and can edit
  what will be written. Record this in issues.md alongside the Phase-4 notes.
- `rating` optional: integer 1–5 (the column is `Integer`); reject anything else
  (including floats — no silent rounding).
- `format` capped at 50 chars (defaults "ebook"); `notes` capped at 2000.

**Flow (new-row-per-read model — user decision):**
Reading history is a log of READ EVENTS: each completed read is its own row with its
own date (and optionally its own rating). Re-read count is the row count per work —
derived for free, no schema column (the "gee-whiz" stat without machinery). Logging a
re-read refreshes recency, so a just-re-read book stops qualifying for >2-year re-read
suggestions naturally.

1. Get-or-create the enriched Work by calling `enrich_and_persist_work(title, author,
   format)` — reuses dedup (normalized title+author) and the full scout enrichment.
   `None` → return `"Error: could not resolve '<title>' by <author> — check the
   spelling, or the scouts found nothing."`
2. Get-or-create the Edition for the requested format (same pattern persist uses).
3. **Duplicate guard (same-date only)**: if a ReadingHistory row for this work (any
   edition) already has this exact `date_completed`, return `"'<title>' is already
   logged as completed <date>. No new entry written."` — catches accidental
   double-sends. A row with a DIFFERENT date is a re-read: insert a new row (the
   conversational CONFIRM step verifies with the user first; the CLI is explicit by
   nature). The success message names the read count: `"Added '<title>' … (read #2)."`
4. Insert the `ReadingHistory` row (edition link, `date_completed`, `user_rating`,
   `user_notes`). Return `"Added '<title>' to your reading history (work <id>, read #N)."`

**Required audit (same change-set):** `check_reading_history`'s >2-year re-read
eligibility must evaluate the LATEST `date_completed` for a work — add
`order_by(ReadingHistory.date_completed.desc())` (or `max()`) if it currently grabs an
arbitrary row. Regression test: a work with an old read + a recent re-read is NOT
re-read eligible.

### 2. Conversational entry point

- Tool exposed on the **Librarian only**: `claude_tools._TOOL_SCHEMAS` entry
  (`required=["title", "author"]`) + ADK `FunctionTool(add_book_to_history)`.
- Both Librarian instructions gain an IMPORT flow: when the user says they read
  something not in the catalog — gather rating/completion date if offered (the date
  defaults to today; mention that when confirming), warn that enrichment takes a
  minute or two, **confirm the details**, then call `add_book_to_history`.
- The CONFIRM HISTORY WRITES clause is extended to name `add_book_to_history` alongside
  `update_reading_status`.

### 3. CLI entry point

```
librarian add "Project Hail Mary" --author "Andy Weir"
              [--date 2026-06-01] [--rating 5] [--format hardcover] [--notes "..."]
```

- argparse **subparsers**: bare `librarian` still drops into the REPL; `--once`
  unchanged; `add` is the first subcommand. `--author` required, title positional,
  `--date` optional (defaults to today, matching the tool).
- Calls the tool function directly (no LLM, no recorder/MLflow). Prints the returned
  string; exit 0 on success, 1 when the string starts with "Error".

### 4. Guardrails + docs

- `WRITE_TOOLS` in `test/unit/test_write_authorization.py` grows to five — the
  mutation-proven invariant pins the new tool to the Librarian.
- `security.md` "Write-tool validation" posture bullet updated to mention the new tool.
- `key_facts.md` Data Ingestion section gains: DB is the history source of truth as of
  2026-06-05; single adds via `add_book_to_history` (conversation or `librarian add`);
  bulk via CSV/Dagster; CSVs no longer reflect post-build additions.

## Error handling

- All validation failures: descriptive error strings naming expected shapes
  (model-self-correctable, CLI-printable).
- Enrichment failure (scouts find nothing): honest error, nothing written.
- Duplicate: informative no-op message, nothing written.
- DB exceptions: caught, returned as error strings (existing degrade pattern).

## Testing

1. db_integration (isolated test DB): happy path (row with rating/date/notes); dedup
   to an EXISTING work (no second Work created, history added); same-date duplicate
   guard (second identical add → message, row count unchanged); **re-read path**
   (different date → second row inserted, message says "read #2", original row
   untouched); omitted date → row carries today; `check_reading_history` re-read
   eligibility uses the LATEST read (old read + recent re-read → not eligible);
   rejections — bad date (format / future), bad rating (0, 6, 3.5, "five"), blank
   title — each asserting **no row written**. Enrichment is stubbed (monkeypatch
   `enrich_and_persist_work`) so tests are offline-deterministic.
2. CLI: subcommand parse + dispatch with the tool monkeypatched (success exit 0,
   error exit 1, REPL default unaffected, `--once` unaffected).
3. Prompt assertions: IMPORT flow + extended CONFIRM clause in both Librarian variants.
4. Invariant test update (five write tools).
5. Full fast suite + in-container pre-commit gate.
6. Live verification (user-gated): one conversational add and one CLI add of a real
   book; confirm the history row, rating, and date in the DB.

## Out of scope

- Editing/deleting history rows; export-history command.
- Bulk import changes (CSV/Dagster path untouched).
- `date_started` (nullable column exists; nobody asked for it — YAGNI).
- Multi-user semantics (single-user system).
