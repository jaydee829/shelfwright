# Phase 6.3 — Data Integrity (Design)

**Date:** 2026-07-12 · **Issues:** #95 #96 #97 #98 #108 #109 #110 #111 #112 + #123 (folded in)
**Roadmap:** plan.md Phase 6.3 · **Ground rule:** prod backfills/migrations require a dry-run
report → user approval before applying (user directive 2026-07-12).

## Goal

Make the communal catalog trustworthy under concurrent multi-user writes: fix the verified
data-loss bug (#96), stop garbage entering the catalog (#98), make enrichment failure loud and
recoverable (#97), back every get-or-create with a real constraint (#95), and pay the cheap
schema debts now (#108 timestamptz, #109 indexes, #110 upsert, #111 shared predicate,
#112 tool correctness, #123 embeds out of sessions).

**Delivery shape: two PRs.** The user's dry-run gate cleanly splits risk:
- **PR-C `fix/phase6-3a-integrity-code`** — pure code, no schema change, deployable
  immediately. Fixes the pollution sources BEFORE prod data is measured and cleaned.
- **PR-D `feat/phase6-3b-schema-integrity`** — one migration + constraint-paired code + the
  gated prod sequence (dedup dry-run → approval → apply → operator alembic → merge).

## Verified findings shaping the design (2026-07-12 exploration)

- persist.py's **narrator branch already implements the #96 fix pattern** (existing-edition
  merge at persist.py:279); the work-contributor branch simply lacks the parallel merge. The
  empirical repro test exists (`test_two_phase_deep.py::test_enrich_deep_updates_same_work_idempotently`,
  the SAWarning source) but asserts only trope counts. #96 also **writes orphan Author rows**
  (flushed at persist.py:122-124, never linked).
- The corrected #69 predicate lives inline in `trope_backfill.plan_fallback_prune`
  (clean-name ⊆ genres∪moods, case-folded) — not yet a reusable helper; persist's
  `has_real_trope` (persist.py:338-343) still uses `justification IS NOT NULL` (#111).
- `ScoutManager.enrich` seeds `merged_data` from raw caller input before any scout runs and
  never returns falsy; `source_priority` (appended per contributing scout) is the natural
  "did anyone confirm this book" signal (#98). The LLMTropeScout prompt has no unknown-book
  escape.
- `enrich_deep` returns True with **zero record-keeping** when scouts find nothing; `Work`
  has no enrichment status column at all — "enriched, empty" and "never attempted" are
  indistinguishable (#97). ImportRow's status/outcome/error_detail shape is the house
  pattern. **No cron/scheduler infra exists anywhere** — clean_catalog.py's
  plan → refuse-gate → apply CLI is the only bulk-op precedent.
- Editions have **three** get-or-create sites, one (`etl/ingest.py:88-92`) entirely
  unguarded; `log_suggestion` (mcp) has **zero dedup** while the import worker's
  `_upsert_suggestion` is idempotent — this asymmetry is #88's likely root cause (#95).
- No `__table_args__` exists anywhere in models.py (composite constraints will be a first);
  migrations are hand-written with explicit names (`ix_<table>_<col>`), symmetric downgrades,
  nullable-first column adds; **5** migrations exist; CI rebuilds the schema from the full
  chain every run; the ADR-058 guard enforces migrate-before-merge.
- All 13 DateTime columns are naive; `availability_cache.fetched_at` deliberately has **no
  default** (do not add one in #108). The composite-PK-not-leftmost index gap is systemic:
  #109's list plus `narrator_styles.style_id` and `edition_narrators.narrator_id`.
- `availability/service.py:168` already carries a `# GH #110 covers the durable upsert`
  comment; no pg-dialect insert exists in the repo yet.
- `update_reading_status` is the file's only exact-match (un-normalized) work lookup, writes
  `date.today()` unconditionally (poisoning the 2-year re-read rule for "read years ago"),
  and has no dup guard (#112).
- #123 pre-embed: every tag string the write session will standardize is available in the
  scout row dict before any session opens (`enriched_tropes[].trope_name`, `author_style`/
  `work_style`/`narrator_styles` values, plus genres∪moods for the fallback path).

## PR-C design (code only)

### #96 — contributor merge on existing works
Restructure `persist_enriched_work`: resolve/create Authors and build the desired contributor
set AFTER the work lookup; new-work branch unchanged; existing-work branch diffs desired
(author_id, role) pairs against `work.contributors` and appends only missing links (mirroring
the narrator merge at persist.py:279); Author rows are created only when they will be linked
(kills the orphan side effect). Regression: extend the existing deep-pass idempotency test to
assert a newly discovered co-author IS linked on re-persist and no orphan Author rows exist;
add `filterwarnings = ["error::sqlalchemy.exc.SAWarning"]` to pyproject's pytest config
(bugs.md's standing recommendation) — any new silent-non-flush bug fails loudly.

### #111 — one real-trope predicate
New `etl/trope_predicate.py` (or a function in tag_cleaning.py): `is_fallback_trope_name(name,
genres, moods) -> bool` implementing the #69 semantics (clean_trope_name, case-folded subset of
genres∪moods; empty-clean = neither). `plan_fallback_prune` and persist's `has_real_trope`
guard both call it: the guard becomes "work has ≥1 WorkTrope whose name is NOT a fallback for
this work's genres/moods". #97's sweep (PR-D) reuses it.

### #98 — garbage-title gate
1. `ScoutManager.enrich` returns `{}` when `source_priority` is empty after the loop (no scout
   contributed anything) — `two_phase._run_scouts`' existing `if not enriched: return None`
   and the /books 404 + import `not_found` outcomes revive with no caller changes.
2. LLMTropeScout prompt gains: "If you cannot verify that this book actually exists, return
   {\"tropes\": []}." Empty tropes from the deep tier contribute nothing, so a deep-only
   response no longer marks the book as confirmed (deep scouts still append to
   source_priority only when returning truthy data — verify and, if the trope scout returns
   `{"tropes": []}`, treat it as non-contributing).
3. Confidence gate falls out structurally: fast-pass (API scouts only) creation requires a
   real metadata source to have contributed; no separate gate needed (YAGNI).

### #112 — update_reading_status correctness
1. Lookup via `_normalized_col` (match every other path).
2. Optional `date_completed: str | None` and `year: int | None` params, validated like
   add_book_to_history (ISO date / plausible year 1900..current; year → Jan 1 convention,
   documented in the docstring). Precedence: date_completed > year > today-fallback.
3. Write via `two_phase.add_read_event` (gains the work+date dup guard for free; deletes the
   inline placeholder-edition/insert block).
4. When the today-fallback fires, the tool reply says the date was assumed today so the agent
   knows; the Librarian FEEDBACK HANDLING instruction gains: on "read that years ago"-style
   feedback, ask roughly when (a year is enough) before calling the tool.

### #110 — availability cache upsert + eviction
`availability/service.py`: shared `_upsert_cache_row(session, slug, title, author, formats,
now)` using `sqlalchemy.dialects.postgresql.insert(...).on_conflict_do_update` on the
composite PK; used by `availability_for` and `batch_availability` phase 3 (per-row: one
conflict can no longer abort a whole batch write-back; the outer best-effort guard stays as
defense-in-depth). Eviction: after a successful phase-3 write, `DELETE FROM availability_cache
WHERE fetched_at < now() - interval '30 days'` (piggybacked, no cron; also run it in
`availability_for`'s write path cheaply — one indexed-scan delete per write is fine at this
scale... fetched_at is unindexed; at current row counts a seq scan is fine — note in code).

### #123 — embeds out of sessions, pool to 5+2
1. `etl/persist.py` gains `collect_embedding_texts(row) -> list[str]` (trope names from
   `enriched_tropes`, style strings via the `_iter_style_items` validation, fallback tags
   genres∪moods when the row would write fallbacks).
2. `two_phase._persist_row` (and the ETL asset path can adopt later) warms the cache before
   its write session: `for t in collect_embedding_texts(row): get_cached_embedding(EMBED_MODEL, t)`
   — LRU-1024 makes the in-session `standardize_*` calls cache hits; ZERO signature changes.
   Note: `standardize_*` only embeds on exact-name miss, so warming embeds some texts that
   the session wouldn't have — acceptable (cache-priced) and bounded per row.
3. `mcp/server.py` search tools: compute `tm._get_embedding`/`sm._get_embedding` target lists
   BEFORE opening their sessions (reorder; managers constructed with session=None? — managers
   need a session only for find_similar/create; `_get_embedding` doesn't touch it. Construct
   the managers inside the session as today but hoist the embedding loops above the `with` by
   calling `get_cached_embedding(model, text)` directly).
4. `db/session.py`: max_overflow 5 → 2, comment updated (the #123 condition is met); ADR-059
   consequence line updated; key_facts updated. Closes #123.

## PR-D design (schema + gated ops)

### One migration (`phase 6.3 schema hardening`)
1. **#95 uniques** (created AFTER the gated dedup cleans prod):
   - `CREATE UNIQUE INDEX uq_authors_name_lower ON authors (lower(name))`
   - `uq_narrators_name_lower` likewise
   - `uq_editions_work_format ON editions (work_id, format) NULLS NOT DISTINCT` (PG16)
   - `uq_reading_history_user_edition_date ON reading_history (user_id, edition_id, date_completed)`
   - `uq_suggestions_active ON suggestions (user_id, work_id) WHERE status = 'Suggested'`
2. **#109 indexes**: `editions.work_id`, `reading_history.edition_id`, `work_tropes.trope_id`,
   `work_contributors.author_id`, `suggestions.work_id`, `author_styles.style_id`,
   `work_styles.style_id`, `usage.conversation_id`, plus the systemic pair the issue missed:
   `narrator_styles.style_id`, `edition_narrators.narrator_id`. Named `ix_<table>_<col>`.
3. **#108 timestamptz**: all 13 DateTime columns `ALTER ... TYPE timestamptz USING <col> AT
   TIME ZONE 'UTC'`; models gain `DateTime(timezone=True)`; the
   `fetched_at.replace(tzinfo=UTC)` band-aid in availability/service.py is removed;
   `availability_cache.fetched_at` keeps NO default (deliberate). `Suggestions.suggested_at`
   is on the list (easy to miss).
4. **#97 column**: `works.deep_enriched_at TIMESTAMPTZ NULL`.
Symmetric hand-written downgrade, house naming, single head (ADR-058 guard).

### #95 code (paired with the constraints)
- `db/get_or_create.py` helper: query → insert → on IntegrityError rollback-to-savepoint and
  re-query (`session.begin_nested()` so the retry doesn't kill the caller's transaction).
  Adopted at: authors/narrators (persist.py), all three edition sites (persist.py,
  two_phase.add_read_event, **ingest.py's unguarded insert**), reading_history
  (add_read_event keeps its date-guard + gains the constraint backstop), suggestions
  (worker._upsert_suggestion AND `log_suggestion`, which gains dedup — fixes #88's root
  cause; note on #88 when closing).
- `enrich_fast` write session: `SELECT pg_advisory_xact_lock(hashtext(:norm_title || '|' ||
  :norm_author))` before the dedup re-check (Postgres-only; skip on sqlite test URLs).
- Works duplicate sweep backstop lands in the dedup tooling below.

### #97 code
- `_persist_row`/deep path sets `work.deep_enriched_at = now()` when the deep pass persists
  (including a confirmed-empty result — the timestamp means "deep pass completed", the trope
  predicate distinguishes fingerprintless works).
- `api/internal.py` enrich endpoint: when `enrich_deep` completed but the work still has no
  real trope (shared #111 predicate) AND the scouts yielded nothing → return 503 (Cloud Tasks
  retries with backoff; retry exhaustion is the poison-task end state, swept below).
- `clean_catalog.py --requeue-unenriched`: lists works with no real trope (predicate) or
  NULL deep_enriched_at, prints the plan, `--apply --yes` re-enqueues deep enrichment via
  `enqueue_enrichment` (operator-run with prod env; Cloud Scheduler automation deferred to
  6.6/#114). Also absorbs the 6.2b deferral (deep-enqueue failure recovery).

### Dedup backfill (THE USER GATE)
`clean_catalog.py --dedup-for-constraints`:
- **Plan (dry-run) report**: case-insensitive duplicate authors + narrators (merge target =
  oldest row; repoint work_contributors/author_styles, narrator links; then delete);
  duplicate editions per (work_id, COALESCE(format,'')) (repoint reading_history +
  edition_narrators; delete); exact-duplicate reading_history rows (keep oldest); duplicate
  Suggested suggestions (keep oldest); duplicate works by normalized title+author (REPORT
  ONLY with row details — work merges are complex; if any exist the user decides case by
  case before the constraint lands... note: no works unique constraint exists in the
  migration, so work dupes don't block it; the advisory lock prevents new ones);
  plus the #96 orphan-author list (authors with no work_contributors AND no author_styles —
  delete after approval).
- Structural distinguishers only, per the #69 lesson (verify-backfill-distinguisher): every
  class keys on real relationships/normalized values, never on sometimes-populated columns.
- **Sequence**: PR-C deployed (pollution stopped) → dry-run on prod → **user reviews report
  and approves** → `--apply --yes` → user runs `alembic upgrade head` on prod (constraints
  now succeed) → merge PR-D → deploy (ADR-058 guard passes; new code relies on constraints).
- pg_dump snapshot before apply (house rule).

## Testing

- PR-C: SAWarning-as-error suite-wide; #96 regression (existing repro test extended);
  predicate unit tests (fallback vs real vs junk-clean names); #98 unit tests (empty
  source_priority → {}, prompt text pinned); #112 unit tests (year/date parsing, dup guard
  via add_read_event, normalized lookup — CI integration for the write path); #110 unit
  (upsert called; eviction SQL emitted) + CI availability suites; #123 unit (cache warmed
  before session — reuse the `open_sessions_during_scout`-style probe for embeds).
- PR-D: migration runs in CI's full-chain rebuild; get_or_create helper unit tests (sqlite
  IntegrityError path) + CI concurrent-ish tests (sequential double-insert hits constraint →
  helper recovers); advisory-lock skipped-on-sqlite guard; 503-on-empty-deep CI test;
  dedup planner unit tests on synthetic duplicates (plan correctness) — apply path exercised
  in CI against seeded duplicates.
- Post-deploy: dedup dry-run doubles as the prod data-quality report for the user.

## Decisions delegated to Claude (for user review)

1. Two PRs (code first, schema+gated-ops second); PR-C deploys before prod data is measured.
2. #97 = `deep_enriched_at` timestamp + 503-retry + operator sweep mode; Cloud Scheduler
   deferred to 6.6/#114 (no cron infra exists; clean_catalog is the house pattern).
3. #98 gate = empty `source_priority` → `{}` (structural), not an LLM-judged confidence score.
4. #112 keeps a today-fallback (flagged in the reply) rather than refusing dateless writes;
   Librarian instructed to ask for a year.
5. #95 works-dedup = advisory lock + report-only sweep (no cross-table unique on works).
6. `NULLS NOT DISTINCT` on the editions unique (PG16 feature; prod is PG16).
7. #123 via LRU warming (zero signature changes) rather than threading embeddings through
   persist signatures.
8. Suggestions partial unique keys on status='Suggested' only (historical rows may repeat).
9. SAWarning promoted to error suite-wide (may surface latent warnings in CI — fixed as found).
