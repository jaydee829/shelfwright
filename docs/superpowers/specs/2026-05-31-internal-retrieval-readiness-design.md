# Spec 3: Internal Retrieval Readiness — Design

**Status:** Approved (2026-05-31)
**Part of:** ADR-035 phased mesh delivery, spec 3 of 4 (order 1 → (2, 3) → 4)
**Predecessors:** Spec 1 (mesh runtime, merged), Spec 2 (Explorer web discovery, merged)

## Goal

Prove the mesh's **internal** (Postgres DB) retrieval returns *real, correctly-ranked*
results against a real-embedding-seeded database, exercise the real Flow 1 ETL
end-to-end once, and retire the orphaned internal/external naming. This makes internal
retrieval trustworthy so Spec 4's end-to-end recommendation can lean on it.

"Internal = retrieval from our Postgres DB" per ADR-035 (vs External = web discovery,
delivered in Spec 2).

## Context: what already exists vs. the gap

**Exists:**
- `db_integration` test infrastructure (ADR-034): a dedicated test database, pgvector
  extension, ORM schema, and truncate-per-test isolation (`test/conftest.py`).
- Integration tests for `search_internal_database`, `check_reading_history`,
  `update_reading_status`, `log_suggestion`, `get_unacted_suggestions`
  (`test/integration/test_mcp_tools.py`).
- The Flow 1 Dagster ETL: `raw_history → enriched_metadata → vectorized_tropes`
  (`orchestration/assets.py`), the `ScoutManager`, `TropeManager`, `StyleManager`, and
  embedding helper (`scouts/utils.py`, `gemini-embedding-001` at 1536-d).

**The gap (what this spec closes):**
1. Existing retrieval tests seed **stub embeddings** (`[0.1]*1536`, identical vectors), so
   `Trope.embedding.cosine_distance(...)` ordering is never exercised — every distance is
   0. Real *semantic ranking* is unproven.
2. `get_user_trope_preferences` has only a mock unit test — no integration coverage.
3. The real Flow 1 ETL has never been run end-to-end against a database (the scouts had
   wiring gaps surfaced only when first run live — ENV-015). We do not know the full
   pipeline populates a queryable DB.
4. `agents/search_strategies.py` uses "Internal/External" in the *old, confusing* sense
   (Mode A in-process grounding vs Mode B simulated A2A — both are web search). It is an
   orphaned MLflow experiment, uses the quota-dead `gemini-2.0-flash`, has no production
   importer, and is superseded by the Spec 2 Explorer.

## Architecture — three independent threads

### Thread 1 — Real-embedding ranking proof (CI-deterministic)

Approach A (chosen): a committed fixture of **real** `gemini-embedding-001` vectors gives
`db_integration` tests real geometry without an API call at test time.

- **`scripts/gen_trope_embedding_fixture.py`** — one-time, manually-run generator
  (`api_dependent`). Embeds a curated set of trope/style strings spanning 2–3 semantic
  clusters via `gemini-embedding-001` at 1536-d (matching `scouts/utils.EMBEDDING_DIMENSIONS`).
  Writes `test/data/trope_embeddings.json` as `{ "<string>": [float, ...1536], ... }`.
  Clusters (illustrative): *romance* = {"enemies to lovers", "slow burn romance"};
  *grimdark* = {"grimdark war", "brutal military strategy"}. Near-cluster pairs must be
  closer to each other than to the other cluster — the generator prints the pairwise
  cosine matrix so the author can sanity-check before committing.
- **`test/data/trope_embeddings.json`** — the committed cached vectors. Source of truth for
  the deterministic ranking tests. Regenerated only by re-running the script.
- **`test/integration/test_internal_retrieval.py`** (`db_integration`):
  - A helper loads the fixture once.
  - Seed two Works: one tagged with romance-cluster Tropes, one with grimdark-cluster
    Tropes, each Trope carrying its cached real vector.
  - Patch `TropeManager._get_embedding` (and `StyleManager._get_embedding` if styles are
    included) to resolve a known query string to the same cached vector.
  - **Ranking assertion:** `search_internal_database(target_tropes=["enemies to lovers"])`
    returns the romance Work ranked **above** the grimdark Work — an ordering assertion,
    not merely `len(results) > 0`.
  - **`get_user_trope_preferences` coverage:** seed reading history whose works carry known
    tropes at differing frequencies; assert the tool returns them in descending-frequency
    order.

Note on `search_internal_database`: it currently collects candidate work ids into a
`set()` then `.filter(Work.id.in_(...))`, which does not itself preserve cosine ordering in
the *final* list. If the ranking assertion requires the final result order to reflect
similarity, the test will surface that — the minimal fix (order the final query by the
candidate distance, or rank candidates before the final fetch) is in scope for this thread.

### Thread 2 — Live Flow 1 ETL smoke (`api_dependent`, excluded from CI)

- **`test/data/etl_smoke/<partition_key>.csv`** — the committed smoke fixture, starting
  with **1 physical book** (Google Books / Hardcover scout path + embeddings). Once that is
  green, **add 1 audiobook** row to exercise the Audible/Gemini scout path. If the audiobook
  path proves too non-deterministic to assert on reliably, **log it as a bug** (bugs.md) and
  ship the smoke with the single physical book.
- **`test/integration/test_flow1_etl_live.py`** (`api_dependent`; also needs a DB):
  - The `raw_history` asset reads a hardcoded `data/raw/{partition_key}.csv`. The test
    copies the committed `test/data/etl_smoke/<key>.csv` into `data/raw/<key>.csv` before
    materializing and removes it afterward (fixture with teardown), so the repo's real
    `data/raw/` is never polluted.
  - Register the dynamic CSV partition key (`csv_partitions`,
    `DynamicPartitionsDefinition`) for `<key>` before materializing.
  - Materialize `raw_history → enriched_metadata → vectorized_tropes` for that partition
    using Dagster's `materialize([...], partition_key=<key>, resources={"db_manager":
    DatabaseManager(test_db_url), "scout_manager": ScoutManager(...real...)})`.
  - **Assert** the test DB then contains: the Work, ≥1 Trope with a **non-null embedding**,
    the Author, an Edition, and a ReadingHistory row.
- **Scope discipline (per approval):** fix whatever blocks a minimal happy-path run; if the
  live run surfaces deeper/structural ETL bugs, log them (bugs.md + an `issues.md` REC) and
  keep Spec 3 bounded rather than hardening the whole pipeline here.

### Thread 3 — Naming cleanup / remove dead experiment

- **Delete** `src/agentic_librarian/agents/search_strategies.py` and
  `test/unit/test_search_strategies.py` (no production importer; orphaned; dead model;
  superseded by the Spec 2 Explorer).
- **`docs/project_notes/decisions.md`** — add **ADR-039**: the orphaned A/B search
  experiment is removed; functional naming (internal = DB retrieval, external = web
  discovery) is canonical. `agents/services.py` already conforms (`search_internal_database`
  tool; Explorer documented as external web discovery) — no rename needed there.

## Data flow

Production flow is unchanged. Thread 1 drives the **read** path
(`search_internal_database`, `get_user_trope_preferences`) against committed real vectors,
with the query embedding sourced from the same fixture (via patch) so cosine distances are
deterministic. Thread 2 drives the **write** path (the ETL) against the isolated test
database (ADR-034).

## Error handling

- Thread 1 adds no production code beyond the possible minimal ranking-order fix in
  `search_internal_database`; it is otherwise pure assertion. The non-UUID guard
  (`get_work_details`, Spec 2) already covers the bad-id path.
- Thread 2 observes the ETL; "fix blockers, log deep bugs" governs how far fixes go.

## Testing & CI

Today's CI (`.github/workflows/lint.yml`) runs `pytest -m "not api_dependent and not slow"`
with **no Postgres service**, so `db_integration` tests auto-skip in CI (conftest's
`is_db_reachable()` is False) — they only run in the devcontainer. This spec **adds a
Postgres service to CI** so the new ranking proof and the existing `db_integration` tests
actually gate there.

- **Thread 1b — Postgres in CI.** Add a `pgvector/pgvector:pg16` service container to the
  `ci` job with a `pg_isready` health check, and set the connection env vars the conftest
  reads (`POSTGRES_HOST=localhost`, `POSTGRES_PORT=5432`, `POSTGRES_USER=librarian`,
  `POSTGRES_PASSWORD=librarian_secret_password`, `POSTGRES_DB=agentic_librarian`). The
  conftest then creates the dedicated `*_test` database, the `vector` extension, and the
  schema. The pgvector image is required because the conftest runs
  `CREATE EXTENSION vector`. Cost: one image pull + a handful of small DB tests
  (~20–40s) — no meaningful slowdown.
- **CI (deterministic):** with Postgres present, `test_internal_retrieval.py` (and the
  existing `db_integration` suite) run in CI. The fixture's cached vectors mean no API call.
- **`api_dependent` (never in CI):** `test_flow1_etl_live.py`; the fixture generator is a
  manually-run script, not a test.
- The full offline suite stays green.

## Files

- **Create:** `scripts/gen_trope_embedding_fixture.py`, `test/data/trope_embeddings.json`,
  `test/integration/test_internal_retrieval.py`, `test/data/etl_smoke/<key>.csv`,
  `test/integration/test_flow1_etl_live.py`
- **Modify:** `.github/workflows/lint.yml` (add the Postgres/pgvector service + env),
  `docs/project_notes/decisions.md` (ADR-039), and on completion
  `docs/project_notes/issues.md` (record Spec 3 outcome / any new REC).
- **Delete:** `src/agentic_librarian/agents/search_strategies.py`,
  `test/unit/test_search_strategies.py`

## Out of scope (→ Spec 4)

Full Librarian→Analyst→Explorer→Critic end-to-end; Trope-RAG justification; web-candidate
de-dup and scout-enrichment of discoveries (REC-016); SEC-001/002 hardening (security.md).

## Success criteria

1. A `db_integration` test proves `search_internal_database` ranks a semantically-near work
   above a far one using real cached embeddings (ordering assertion), and it **runs in CI**
   (Postgres/pgvector service added to the workflow).
2. `get_user_trope_preferences` has integration coverage against a seeded DB.
3. An `api_dependent` smoke materializes the real Flow 1 ETL on ≥1 physical book and asserts
   the DB is populated (Work + embedded Trope + Author + Edition + ReadingHistory); the
   audiobook path is added if stable, else logged as a bug.
4. `search_strategies.py` and its test are removed; ADR-039 records the canonical naming.
5. Offline suite green; no production behavior change beyond a possible minimal
   ranking-order fix in `search_internal_database`.
