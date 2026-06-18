# Bulk Reading-History Import — Design

**Date:** 2026-06-18
**Status:** Approved (brainstormed), pending plan
**Origin:** Onboarding work — let a new user seed their reading history (and wishlist) from an
existing source (Goodreads export, or a self-kept CSV) instead of adding books one at a time.

---

## 1. Background & Problem

A new user's value depends on having a reading history for the recommender to work from. Today the
only intake is `POST /books` (`api/books.py`) — **one book per request**. Seeding a real library that
way is hopeless for onboarding.

We want a **robust bulk import** that accepts a user's reading history from whatever source they keep
it in, conforming each row to the enrichment contract: **title, author, format, date_completed**
(required), with **rating, notes** optional. Everything else (tropes, styles, narrators, genres,
descriptions) is enriched from other sources downstream.

**Code reality the design builds on:**

- **Catalog is communal; history is per-user (ADR-048).** `ReadingHistory` points at an `Edition`,
  which belongs to a `Work`. "Importing a book" = resolve-or-create `Work`+`Edition` in the shared
  catalog, then insert a per-user `ReadingHistory` row.
- **The single-add pipeline already does the per-row work.** `POST /books` → `enrich_fast(title,
  author, fmt)` (de-dup against catalog; on miss run the fast API scouts and persist Work+Edition;
  returns `(work_id, created)` or `None`) → `add_read_event(...)` (insert `ReadingHistory`; dup-safe on
  work+date) → `enqueue_enrichment(work_id)` (Cloud Task → `POST /internal/enrich/{work_id}` for the
  slow LLM pass). The importer is **orchestration around this existing, tested code.**
- **The deep pass is already queued, parallel, and auto-retried.** `enqueue_enrichment` uses Cloud
  Tasks; the queue's dispatch rate is the throttle; `internal.py` returns 404 only when the work is gone
  (non-retryable) and `enrich_deep` is idempotent, so Cloud Tasks redelivery is safe.
- **Scouts self-throttle / back off.** `APIScout` (Google Books + Hardcover) mounts a urllib3 `Retry`
  (3× exp backoff on 429/5xx, no `Retry-After`); `llm_retry.RETRY_OPTIONS` gives Gemini calls 5×
  backoff. These are tuned for single-discovery transients, **not** sustained parallel bursts — so the
  import burst is paced at the **queue** level, not by hammering the scouts.
- **`Suggestions` is the existing wishlist surface.** Rows with `status="Suggested"` are unioned into
  recommendation candidates by `get_unacted_suggestions` (`mcp/server.py`). A user's `to-read` /
  `currently-reading` shelves route here instead of being discarded.
- **Format vocabulary:** `ebook`, `audiobook`, `paperback`, `hardcover` (from `AddBookView` /
  `add_book`); `format` is a free string column but these are the canonical values.

## 2. Goals

- Bulk-import a reading history from a **Goodreads export** or a **generic user CSV**, via a guided
  wizard with **auto-detected column mapping** the user can confirm/adjust.
- Per row: **de-dup** against the communal catalog (free link) → **shallow** resolve on a miss (fast
  API scouts) → **queue deep** enrichment (Cloud Tasks).
- Route shelves to the right destination: completed reads → `ReadingHistory`; `to-read` /
  `currently-reading` → `Suggestions` (opt-in, with provenance tags).
- Robust at scale: hundreds-to-low-thousands of rows, **quota-safe by construction** (queue-throttled,
  not a synchronous scout burst), with **free per-row retry**, a **progress UI**, and full
  **idempotency** (re-import is safe).

## 3. Non-Goals (YAGNI)

- **No non-CSV sources for v1** — no StoryGraph/Audible/Libib API integrations, no JSON/XLSX. The
  column-mapping wizard already absorbs arbitrary CSVs; other formats are later adapters.
- **No in-progress reading state.** We have no schema for "currently reading" (`ReadingHistory`
  requires `date_completed`); `currently-reading` rows become wishlist suggestions tagged for later
  promotion.
- **No server-side file storage.** The import is stateless between preview and commit (client
  re-uploads the small CSV); no temp-file lifecycle / cleanup job.
- **No background cron reaper for v1.** Stalled rows are recovered by a **user-triggered** "retry
  failed/stalled rows" action.
- **No websockets.** Progress is polled.
- **No new MCP/Librarian tools.** API + UI only.

## 4. Decisions (from brainstorm)

- **D1 — Source strategy:** column-mapping wizard that **auto-detects** known sources (Goodreads)
  to pre-fill the mapping, but always lets the user confirm/adjust. One code path serves
  "Goodreads just works" and arbitrary CSV.
- **D2 — Processing model (A′ hybrid):** parse/validate/preview **synchronously**; on commit write a
  job + per-row records and **enqueue one Cloud Task per importable row**. Parallelism is a queue-rate
  knob; per-row retry is free from Cloud Tasks; progress is a real UI.
- **D3 — Enrichment depth (3 stages per row):** de-dup → shallow → queue-deep. The deep pass is queued
  in all cases (identical to today). A′ vs a single orchestrator differs only on the **cheap shallow
  layer**, which A′ parallelizes under queue throttle.
- **D4 — Shelf routing:** `read`+date → `ReadingHistory`; `to-read` → `Suggestions`
  (`context="imported:to-read"`, opt-in); `currently-reading` → `Suggestions`
  (`context="imported:currently-reading"`, opt-in, promotable to history later); `read` without a
  usable date → **skip** (recorded with a reason).
- **D5 — State between preview/commit:** **stateless re-upload** (no server-side file).
- **D6 — Report:** built **client-side** from the failed/skipped rows the progress endpoint returns.
- **D7 — Separate import queue:** import bursts use their own Cloud Tasks queue so they don't starve
  interactive deep-enrich (and vice versa).
- **D8 — Caps/thresholds:** import-size cap **2,000 rows/file**; a `processing` row older than
  **15 min** is treated as stalled and offered for retry.

## 5. Architecture & Data Flow

Three phases: a fast synchronous front (parse → map → preview), a fast commit (write + enqueue), and
an async per-row worker tail; the deep-enrichment pass is the existing prod path.

```
PHASE 1 · PREVIEW (sync, no writes)
  upload .csv → sniff source (Goodreads?) → suggest column mapping
  → parse first N rows + bucket counts → return mapping + preview + counts
  (POST /import/preview)
                         │ user confirms mapping + per-bucket opt-ins
                         ▼
PHASE 2 · COMMIT (sync, fast)
  parse ALL rows → validate → write ImportJob + ImportRow(status=pending)
  → enqueue one Cloud Task per non-skip row (import queue)
  → return import_job_id
  (POST /import/commit)
                         │ Cloud Tasks drains at max_dispatches/sec
                         ▼
PHASE 3 · PER-ROW WORKER (async, parallel, queue-throttled, OIDC-gated)
  POST /internal/import-row/{row_id}
    guard status==done (idempotent no-op)
    1. de-dup: normalized title+author in catalog? → link
    2. shallow: miss → fast Books/Hardcover scouts → persist Work+Edition
    3. route:  history → add_read_event ; suggestion → get-or-create Suggestions
    4. queue deep: if work created → enqueue_enrichment(work_id)
    5. record ImportRow outcome (idempotent)
                         │
  Frontend polls GET /import/{job_id} → derived progress
  Existing deep-enrich queue enriches new works over time
```

**Why this shape:** Phase 1 is stateless/free (re-mapping is cheap). Phase 2 never blocks on a scout,
so even a 2,000-row file commits well under the request timeout. Phase 3 holds **all** the cost and
parallelism, entirely in Cloud Tasks: free per-row retry, queue-rate throttle, and reuse of the
`enqueue_enrichment` → `/internal/enrich` path already in prod. Both `/internal/*` endpoints share the
OIDC `_require_queue_caller` gate.

## 6. Data Model

Two new tables (one Alembic revision). **Every parsed row becomes an `import_row`** — importable rows
get enqueued; skipped rows are recorded with a reason, so the skipped report and re-import are both
just queries.

**`import_jobs`** — one per uploaded file:

| column | type | notes |
|---|---|---|
| `id` | UUID PK | |
| `user_id` | FK users, indexed | ADR-048 isolation |
| `source` | str | `goodreads` \| `generic` |
| `original_filename` | str | display |
| `total_rows` | int | progress denominator |
| `created_at` | datetime | |

**`import_rows`** — one per parsed source row:

| column | type | notes |
|---|---|---|
| `id` | UUID PK | the Cloud Task targets this id |
| `import_job_id` | FK import_jobs, indexed | |
| `user_id` | FK users, indexed | worker scopes the write via `as_user` without loading the job |
| `raw_title / raw_author / raw_format / raw_date` | str/null | parsed source values (report + retry) |
| `rating / notes` | int/str null | optional |
| `destination` | str | `history` \| `suggestion` \| `skip` |
| `status` | str | `pending` → `processing` → `done` \| `failed` \| `skipped` |
| `outcome` | str null | `linked` \| `created` \| `duplicate` \| `not_found` \| `error` |
| `skip_reason` | str null | `no_completion_date` \| `bad_date` \| `currently_reading_opt_out` \| `to_read_opt_out` \| … |
| `work_id` | UUID null | set once resolved |
| `error_detail` | text null | last error (report/retry) |
| `created_at / updated_at` | datetime | `updated_at` drives stalled detection |

**Progress is derived, not counted.** No counter columns — `GET /import/{job_id}` runs
`SELECT status, outcome, count(*) … GROUP BY` over `import_rows` (indexed by `import_job_id`). This
**sidesteps the redelivery double-count problem**: the worker transitions one row's `status`; "job
complete" = zero rows in `pending`/`processing`. Cheap for hundreds of rows.

**Routing writes (Phase 3, after a `work_id` is resolved):**
- `history` → `two_phase.add_read_event(work_id, completed, rating, notes, fmt)` — dup-safe on work+date.
- `suggestion` → get-or-create a `Suggestions` row: `work_id`, `user_id`, `status="Suggested"`,
  `context="imported:to-read"` or `"imported:currently-reading"`, `suggested_at=now`. Get-or-create
  filters on `work_id + user_id + status="Suggested"` so re-import won't duplicate.

## 7. Backend Components

Five focused units; parsing/bucketing are **pure (no I/O)** — that's where format variability lives and
where most test value is.

1. **`import/parsing.py` (pure).**
   - `sniff_source(headers) -> "goodreads" | "generic"` — Goodreads has a stable header signature
     (`Book Id, Title, Author, My Rating, Exclusive Shelf, Date Read, Bookshelves, Binding, …`).
   - `suggest_mapping(headers, source) -> dict` — Goodreads = the known fixed map; generic = fuzzy
     header match (`"date read"`/`"finished"`/`"date_completed"` → `date_completed`, etc.).
   - `parse_rows(rows, mapping) -> list[ParsedRow]` — applies the mapping and normalizes:
     - **format:** Goodreads `Binding` → vocab (`Kindle Edition`→`ebook`; `Audiobook`/`Audio CD`→
       `audiobook`; `Paperback`→`paperback`; `Hardcover`→`hardcover`; unknown→`ebook`).
     - **rating:** `0` (unrated) → `null`; `1–5` passthrough.
     - **date:** tolerant parse (`YYYY/MM/DD`, `YYYY-MM-DD`); unparseable → `null`; future → invalid
       (reuse the `books.py` no-future-date rule).
     - **shelf:** `Exclusive Shelf` drives the bucket.
     - Parse with `utf-8-sig` (eat Goodreads' BOM).

2. **`import/bucketing.py` (pure).** `bucket(parsed_row, opts) -> (destination, skip_reason)` —
   encapsulates D4's routing table (incl. per-bucket opt-ins). One function = one place to reason
   about routing.

3. **`api/imports.py` (router, Firebase-gated, `include_router` in `main.py`):**
   - `POST /import/preview` — multipart upload → `sniff` + `suggest_mapping` + parse a preview sample
     (first 5 rows) + bucket counts (computed over all rows). **No DB writes.** 422 on
     empty/malformed/no-data CSV.
   - `POST /import/commit` — confirmed `mapping` + `import_to_read: bool` + `import_currently_reading:
     bool` + the file → parse all rows → write `ImportJob` + `ImportRow`s → enqueue one task per
     non-skip row → return `import_job_id`. 422 if a required mapping (`title`/`author`/
     `date_completed`) is missing, or the row count exceeds the 2,000 cap.
   - `GET /import/{job_id}` — derived-progress GROUP BY, scoped to `user_id`; returns totals +
     per-outcome counts + the failed/skipped rows (for the client-side report). Read-only.
   - `POST /import/{job_id}/retry` — re-enqueue this job's `failed` rows and `stalled` rows (still
     `processing` past the 15-min threshold); resets them to `pending` and enqueues a task each. Scoped
     to `user_id`.

4. **`api/internal.py` — add `POST /internal/import-row/{row_id}`** beside `/internal/enrich`. Reuses
   `_require_queue_caller` (OIDC). Loads the row, guards `status==done`, sets `processing`, runs the
   per-row pipeline, records outcome. The **only** place de-dup/shallow/route/queue-deep happens.

5. **`import/tasks.py` — `enqueue_import_row(row_id)`.** Near-clone of `enrichment/tasks.py` pointing at
   the separate import queue (`IMPORT_TASKS_QUEUE` env), same OIDC-token shape; logged no-op when the
   queue is unconfigured (local dev), so commit still succeeds.

**Reuse:** the per-row pipeline is `enrich_fast` (de-dup+shallow) + `add_read_event` *or* the suggestion
get-or-create + `enqueue_enrichment` — all existing, tested code.

## 8. Frontend — Import Wizard

New `ImportView` at `/import` (linked from onboarding + History), a 4-step wizard in the existing
view/`client.ts` pattern; each step is local state, no global store.

1. **Upload** → `POST /import/preview`; returns detected source, suggested mapping, preview rows,
   bucket counts. Includes a "How to export from Goodreads" link.
2. **Map columns** — dropdowns pre-filled from the suggestion (for Goodreads, a confirm). Editing a
   mapping **re-calls `/import/preview`** (stateless re-upload) to refresh the preview.
   `title`/`author`/`date_completed` required; `format` defaults to `ebook` if unmapped;
   `rating`/`notes` optional.
3. **Review & route** — buckets with **per-bucket opt-in checkboxes** (`to-read`,
   `currently-reading`, default checked). Skipped buckets shown greyed with reasons. "Start import" →
   `POST /import/commit`.
4. **Progress** — poll `GET /import/{job_id}` (~2s); live counts by outcome; stop when
   `pending+processing == 0`. "Download report" → client-side CSV of failed+skipped rows.
   "Retry failed/stalled" → re-enqueue. User can close the tab; the import continues server-side.

Polling (not websockets) matches the app's existing request/response + chat-activity-polling style.

## 9. Error Handling, Retries & Edge Cases

**Retry model by failure type:**

| Failure | Behavior |
|---|---|
| Transient scout 429/5xx | Absorbed in-process (`APIScout` 3× backoff; `llm_retry` 5× for deep). |
| Row-task throws / times out / DB hiccup | Worker returns **5xx** → Cloud Tasks redelivers (free per-row retry); idempotent on `status==done`. |
| Scouts find nothing (`enrich_fast → None`) | **Not retryable** → row `failed`, `outcome=not_found`, worker returns **200**. |
| Row no longer exists | Worker returns **404** → Cloud Tasks stops (mirrors `/internal/enrich`). |
| Cloud Tasks exhausts max attempts | Row stuck `processing`; recovered by user-triggered retry of stalled rows. |

**Stalled rows (the one real gap):** a row whose `updated_at` is older than **15 min** while still
`processing` is treated as **stalled** by `GET /import/{job_id}` and offered for "retry
failed/stalled" (re-enqueue). No cron for v1.

**Idempotency, end to end:** worker keyed by `import_row_id`, guards `status==done`; `add_read_event`
no-ops same work+date; suggestion get-or-create no-ops same work+user+status. ⟹ re-running the whole
import or any subset is safe — no duplicate history, suggestions, or catalog works.

**Edge cases handled explicitly:**
- Empty / malformed / no-data CSV → `/import/preview` 422 with a clear message.
- Required mapping missing → `/import/commit` 422 before any write.
- Encoding → parse with `utf-8-sig` (Goodreads BOM).
- Future / unparseable dates → row skipped, `skip_reason=bad_date`.
- Duplicate within the same file (same title+author+date) → second is `outcome=duplicate`.
- Re-read (same book, different dates) → two history rows (existing semantics).
- Over-cap file (> 2,000 rows) → rejected at preview with guidance.

## 10. Testing Strategy (TDD, red→green)

- **`parsing.py` (unit, pure)** — anonymized real Goodreads-export fixture: `sniff_source` detects it,
  `suggest_mapping` returns the known map, `parse_rows` normalizes Binding→format, rating 0→null, date
  formats, BOM; generic CSV with oddball headers → fuzzy map. **Highest-value surface.**
- **`bucketing.py` (unit, pure)** — exhaustive truth table: shelf × opt-in × date present/absent →
  destination + skip_reason.
- **API (unit, mocked enqueue)** — `/import/preview` shapes; `/import/commit` writes the right
  `ImportJob`/`ImportRow`s and enqueues exactly the non-skip rows (enqueue mocked, à la
  `test_enqueue_enrichment`); `/import/{job_id}` derived counts; 422 paths; over-cap rejection.
- **Worker (integration)** — seeded catalog: de-dup (no scout call, `outcome=linked`); miss (fake fast
  scout → `created` + deep enqueued); `not_found` → failed+200; redelivery of a `done` row → no-op;
  suggestion routing writes a `Suggestions` row with the right context; re-import → no duplicates.
- **Auth/isolation** — import-row endpoint rejects a non-queue caller (reuse `internal.py` OIDC tests);
  `/import/{job_id}` scoped to owner (ADR-048).
- **Frontend (vitest)** — wizard step transitions, mapping-edit re-previews, progress polling stops at
  completion, report download. (Mind the vitest rejection-path pitfall — use `…Once` mocks.)

## 11. Rollout / Ops Notes

- **New Cloud Tasks queue** for imports (created via `gcloud`, as the enrich queue was in Stage 4 —
  repo has no `.tf`). Start with a conservative `max_dispatches_per_second` (stay within the 3-retry
  Books/Hardcover budget and Gemini RPM); raise now that billing is on paid tier.
- **New env:** `IMPORT_TASKS_QUEUE` (full queue path); reuse `ENRICH_TARGET_BASE_URL`,
  `ENRICH_INVOKER_SA`, and the OIDC audience wiring from the enrich path. Unconfigured locally →
  `enqueue_import_row` is a logged no-op (commit still succeeds; rows stay `pending`).
- **Migration:** one Alembic revision adds `import_jobs` + `import_rows`.
```
