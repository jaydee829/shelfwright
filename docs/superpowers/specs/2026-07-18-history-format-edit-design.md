# History Format Edit — Design

**Date:** 2026-07-18
**Status:** Approved (design walkthrough 2026-07-18)

## Problem

Users can edit a history entry's date, rating, and notes (`PATCH /history/{id}`, shipped in
the 2026-06-16 history-edit-delete design) but not its **format**. Format is the one field
that is not a simple column write: `format` lives on `Edition` (unique per `(work_id,
format)`, `NULLS NOT DISTINCT`), editions are shared catalog objects, and audiobook is
enrichment-significant — the `AudiobookScout`/`DirectKnowledgeScout` (narrators, audio
minutes) and narrator-style scouts only run when the format string contains "audiobook".
A work first enriched as ebook has no narrator data, and every format's edition carries its
own `isbn_13`/`page_count`/`audio_minutes`, so a format flip leaves the target edition
metadata-less unless an enrichment pass runs.

## Decisions made during design

- **Enrichment UX:** async, like imports — PATCH saves and returns immediately; a Cloud
  Tasks pass fills edition data in later.
- **Collision:** refuse with **409** (keep both rows); never silently merge/delete.
- **Trigger scope:** a *targeted format-completion pass*, not the full deep pass. ISBN
  comes from the fast API scouts (Hardcover format-matched edition selection), so **any**
  format change gets a cheap 2-API-call completion; audiobook targets additionally get the
  audiobook LLM scouts + narrator styles. `LLMTropeScout` and author/work-style scouting
  never run — tropes/styles belong to the unchanged Work. (Full-deep-pass reuse was
  rejected: it re-buys the paid trope/style pass per flip and entangles `deep_enriched_at`
  / poison-task retry gating tuned for first-time enrichment.)

## Section 1 — API & data flow (`PATCH /history/{id}`)

`HistoryUpdate` gains optional `format`, validated against the UI vocab
(`ebook | audiobook | paperback | hardcover`, lowercased). Free-text formats would mint
junk sibling editions under `uq_editions_work_format`.

When `format` is present and differs from the current `edition.format`:

1. Apply date/rating/notes changes **first**, so the collision check runs against the
   entry's final `date_completed`.
2. `get_or_create(Edition, work_id=<current work>, format=<new>)` — the same
   race-backstopped pattern as `add_read_event` (GH #95).
3. Collision pre-check: another `ReadingHistory` row for `(user, target_edition, final
   date_completed)` → 409 with a human message ("You already logged an audiobook read of
   this book on that date"). `uq_reading_history_user_edition_date` remains the backstop
   for the millisecond race; `IntegrityError` on flush maps to the same 409.
4. Repoint `edition_id`. The **old edition is left in place** — shared catalog object;
   other users and work metadata may reference it.
5. After the write commits: enqueue the format-completion pass if the target edition is
   missing `isbn_13`, or is an audiobook with no narrators. Enqueue is best-effort in the
   `POST /books` style — a Cloud Tasks failure logs but never fails the PATCH.

Response stays `_history_item(row)` (format now reflects the new edition) plus
`enrichment_enqueued: bool`, mirroring the add-book response shape.

## Section 2 — Format-completion pass (backend enrichment)

New function in `two_phase.py` — `complete_edition(work_id, fmt)` — following the module's
established shape (short read session → scouts with **no session held**, the #94 rule →
fresh write session):

- **Read session:** load title + primary author (scalars captured before close, detached-
  instance rule) and the target edition's state. Work or edition gone → `"missing"`.
- **Scouts, no session held:** always run the fast API manager (Hardcover + GoogleBooks)
  with `format=fmt` → ISBN, page count, audio minutes, publication date. If `fmt` is
  audiobook, additionally `AudiobookScout` + `DirectKnowledgeScout`, then
  `scout_narrator_style` per discovered narrator. Explicitly **no** `LLMTropeScout`, no
  author/work-style scouting.
- **Persist:** extract `persist_enriched_work`'s "Edition & Narrators" section
  (`etl/persist.py` ~L264–353: format-matched edition merge, case-insensitive narrator
  get-or-create, narrator-style standardization, fill-not-clobber `isbn or existing`
  semantics) into a shared helper used by both callers; the completion pass calls just
  that helper. Do NOT push a sparse row through the full `persist_enriched_work` — its
  genre/mood/trope handling would have to be carefully neutralized (fallback-trope
  pollution risk, GH #65/#70 class).
- **Embedding warm-up** (`get_cached_embedding`) before the write session for narrator-
  style strings (GH #123 pool-sizing premise).

**Delivery:** new internal endpoint `POST /internal/complete-edition/{work_id}` with the
format in the task payload, on the existing enrich queue (4-concurrent/5s), gated by
`_require_queue_caller`. Idempotent by construction (all merges) → Cloud Tasks redelivery
safe. Scouts returning nothing = final state → 200 (no 503 retry loop — no poison-pass
economics here; the pass is cheap and the entry is already saved). Transient scout
exception propagates → 500 → normal Cloud Tasks retry.

## Section 3 — Frontend (`HistoryEditView`)

- **Format select** between the context header and Rating, reusing `AddBookView`'s four
  options, initialized from `row.format` (null format → "—" placeholder; field omitted
  from the PATCH if untouched).
- `client.ts` `updateHistory` payload type gains `format?: string`; the view sends it
  **only when changed** (endpoint is `exclude_unset`).
- **409 handling:** distinguish 409 from other failures and surface the server's message
  (today's catch-all "try again" is wrong for a collision the user must resolve by
  deleting one entry).
- **Enrichment feedback:** none for now — the history list doesn't display narrators/ISBN,
  so no toast/spinner infra. Save navigates back to `/history` as today. Revisit when
  narrators surface in the UI.

## Section 4 — Error handling & testing

- **Unit (API), parametrized atomic cases:** repoint to existing sibling edition; create
  missing edition; same-format no-op creates nothing; invalid format → 422; collision →
  409; combined date+format edit checks collision against the NEW date; enqueue failure
  still returns 200. Assertion completeness (CLAUDE.md #1): repoint tests also assert the
  old edition still exists, rating/notes survived, and another user's row on the same
  edition is untouched.
- **Unit (two_phase):** house session-counting fixture asserts `open_sessions == 0` during
  scouts; composition test asserts the completion pass never invokes `LLMTropeScout` /
  author-work style scouting; persist-helper tests assert narrator merge + `isbn_13`
  fill-not-clobber; idempotent double-run.
- **db_integration (CI-first merge gate):** real-Postgres PATCH repoint under the
  `NULLS NOT DISTINCT` constraint; real 409 from the unique-index race path; internal
  endpoint end-to-end with stubbed scouts.
- **Frontend:** `HistoryEditView.test.tsx` — select renders with current format; PATCH
  body includes `format` only when changed; 409 renders the server message (`...Once`
  mock variants per the vitest rejection-path pitfall).

## Out of scope

- Editing title/author (work identity) — separate feature, touches dedup.
- Displaying narrators/audio minutes in history UI (future; adds the "filling in"
  affordance moment).
- Sweep-based backfill of metadata-less editions created before this feature.
