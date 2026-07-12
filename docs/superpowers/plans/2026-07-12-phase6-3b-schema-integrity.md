# Phase 6.3 PR-D Schema Integrity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land the schema hardening (#95 #97 #108 #109) and the gated dedup tooling on branch `feat/phase6-3b-schema-integrity` — one migration, constraint-paired code, the #97 status/sweep, and the `--dedup-for-constraints` planner whose dry-run report the user must approve before prod apply.

**Architecture:** One hand-written alembic migration (house style: explicit names, symmetric downgrade) adds the five unique constraints, ~10 FK/composite indexes, converts all 13 naive DateTime columns to timestamptz, and adds `works.deep_enriched_at`. A `get_or_create` helper (SAVEPOINT + IntegrityError re-query) backs every get-or-create site including `log_suggestion` (which gains dedup — #88's root cause) and `ingest.py`'s unguarded edition insert; `enrich_fast`'s write session takes a normalized-title+author advisory lock. `enrich_deep` stamps `deep_enriched_at`; the internal endpoint 503s (retryable) when a fingerprint-less work's deep pass yields nothing; `clean_catalog.py` gains `--requeue-unenriched` and `--dedup-for-constraints` following its existing plan → refuse-gate → apply pattern.

**Tech Stack:** alembic (5-migration chain, CI rebuilds from scratch every run), SQLAlchemy 2.0 (`begin_nested`, pg advisory locks), Postgres 16 (`NULLS NOT DISTINCT`).

## Global Constraints

- Spec: `docs/superpowers/specs/2026-07-12-phase6-3-data-integrity-design.md` (PR-D sections + "PR-D inputs from PR-C's final review" + "Dedup backfill (THE USER GATE)").
- **The prod sequence is operator-gated and OUT of plan scope**: dedup dry-run → user approval → apply → user runs `alembic upgrade head` → merge. Nothing in this plan touches prod.
- Exact schema objects (names binding):
  - Uniques: `uq_authors_name_lower` on `(lower(name))`; `uq_narrators_name_lower`; `uq_editions_work_format` on `(work_id, format)` **NULLS NOT DISTINCT**; `uq_reading_history_user_edition_date` on `(user_id, edition_id, date_completed)`; `uq_suggestions_active` partial on `(user_id, work_id) WHERE status = 'Suggested'`.
  - Indexes (`ix_<table>_<col>`): `editions.work_id`, `reading_history.edition_id`, `work_tropes.trope_id`, `work_contributors.author_id`, `suggestions.work_id`, `author_styles.style_id`, `work_styles.style_id`, `usage.conversation_id`, `narrator_styles.style_id`, `edition_narrators.narrator_id`.
  - timestamptz: all 13 DateTime columns from the spec inventory (suggestions.suggested_at; conversations.created_at/updated_at; messages.created_at; users.created_at; usage.created_at; user_credentials.created_at/updated_at; user_libraries.created_at; availability_cache.fetched_at; import_jobs.created_at; import_rows.created_at/updated_at) via `ALTER ... TYPE timestamptz USING <col> AT TIME ZONE 'UTC'`; models gain `DateTime(timezone=True)`; **`availability_cache.fetched_at` keeps NO default** (deliberate); `Suggestions.suggested_at` must not be missed.
  - `works.deep_enriched_at` TIMESTAMPTZ NULL.
- Migration: single new head (ADR-058 guard), hand-written, symmetric downgrade, no application-code imports; CI's conftest full-chain upgrade is the proof it runs.
- `get_or_create(session, model, defaults=None, **filters)` semantics: query → miss → `begin_nested()` + insert + release → on IntegrityError rollback nested + re-query (must find the row) — the caller's outer transaction survives.
- Advisory lock: `SELECT pg_advisory_xact_lock(hashtext(:key))` with `key = norm_title + '|' + norm_author`, executed in `enrich_fast`'s WRITE session before the dedup re-check; skipped when the bind dialect isn't postgresql (sqlite guard-style check, mirroring `db/session.py`'s pool-kwargs guard).
- Dedup planner uses STRUCTURAL distinguishers only (the #69 lesson): case/normalized-value groupings and relationship repoints; never a sometimes-populated column. Works duplicates are REPORT-ONLY. Orphan authors = no work_contributors AND no author_styles.
- Tests: `.venv/Scripts/python -m pytest ...`; new unit tests DB-free/sqlite where possible; constraint/dedup behavior tests are `db_integration` (CI-gated — collect-check locally, say so). Lint/format every touched file (`uvx ruff check` / `uvx ruff format`).
- No `[skip ci]`. Do not modify `frontend/**`.

---

### Task 1: The migration + model updates — #108 #109 #95-schema #97-column

**Files:**
- Create: `alembic/versions/<newrev>_phase6_3_schema_hardening.py` (revises `c4f81a2d9b6e`)
- Modify: `src/agentic_librarian/db/models.py` (13× `DateTime(timezone=True)`; `Work.deep_enriched_at`; `index=True` on the 10 index columns where expressible — composite/functional uniques stay migration-only per the no-`__table_args__` house style, with a models.py comment pointing at the migration)
- Modify: `src/agentic_librarian/availability/service.py` (remove the `.replace(tzinfo=UTC)` band-aids — grep for them; timestamptz reads back aware)
- Test: `test/integration/test_schema_hardening.py` (new, db_integration)

**Interfaces:**
- Produces: `Work.deep_enriched_at: Mapped[datetime | None]` (Task 3 stamps it); the five unique constraints (Task 2's helper relies on them; Task 4 dedups ahead of them).

- [ ] **Step 1: Write the failing test** — `test/integration/test_schema_hardening.py` (db_integration; CI executes):

```python
"""PR-D migration: constraints, indexes, timestamptz, deep_enriched_at (#95 #97 #108 #109)."""

import pytest
from sqlalchemy import inspect, text

pytestmark = pytest.mark.db_integration


def test_unique_indexes_exist(db_url):
    from agentic_librarian.db.session import DatabaseManager

    insp = inspect(DatabaseManager(db_url).engine)
    author_uniques = {i["name"] for i in insp.get_indexes("authors") if i.get("unique")}
    assert "uq_authors_name_lower" in author_uniques
    edition_uniques = {i["name"] for i in insp.get_indexes("editions") if i.get("unique")}
    assert "uq_editions_work_format" in edition_uniques
    rh = {i["name"] for i in insp.get_indexes("reading_history") if i.get("unique")}
    assert "uq_reading_history_user_edition_date" in rh
    sugg = {i["name"] for i in insp.get_indexes("suggestions") if i.get("unique")}
    assert "uq_suggestions_active" in sugg


def test_fk_indexes_exist(db_url):
    from agentic_librarian.db.session import DatabaseManager

    insp = inspect(DatabaseManager(db_url).engine)
    for table, col in [
        ("editions", "work_id"), ("reading_history", "edition_id"), ("work_tropes", "trope_id"),
        ("work_contributors", "author_id"), ("suggestions", "work_id"), ("author_styles", "style_id"),
        ("work_styles", "style_id"), ("usage", "conversation_id"), ("narrator_styles", "style_id"),
        ("edition_narrators", "narrator_id"),
    ]:
        names = {i["name"] for i in insp.get_indexes(table)}
        assert f"ix_{table}_{col}" in names, f"missing ix_{table}_{col}"


def test_timestamps_are_timestamptz(db_url):
    from agentic_librarian.db.session import DatabaseManager

    m = DatabaseManager(db_url)
    with m.get_session() as s:
        rows = s.execute(text(
            "SELECT table_name, column_name FROM information_schema.columns "
            "WHERE data_type = 'timestamp without time zone' AND table_schema = 'public'"
        )).all()
    assert rows == [], f"still naive: {rows}"


def test_works_deep_enriched_at(db_url):
    from agentic_librarian.db.session import DatabaseManager

    insp = inspect(DatabaseManager(db_url).engine)
    cols = {c["name"] for c in insp.get_columns("works")}
    assert "deep_enriched_at" in cols
```

- [ ] **Step 2: Verify collection** (locally these skip; CI's conftest runs the full chain then these assert).

- [ ] **Step 3: Write the migration** — follow `c4f81a2d9b6e`'s structure (docstring header, typed module constants, hand-written upgrade/downgrade). Upgrade order: (1) timestamptz ALTERs via `op.execute` loops (13 exact `ALTER TABLE x ALTER COLUMN y TYPE timestamptz USING y AT TIME ZONE 'UTC'` statements); (2) `works.deep_enriched_at` via `op.add_column(sa.Column("deep_enriched_at", sa.DateTime(timezone=True), nullable=True))`; (3) the 10 `op.create_index` calls; (4) the 5 uniques — functional/partial/NULLS-NOT-DISTINCT ones via `op.execute` raw DDL (alembic's create_index doesn't express NULLS NOT DISTINCT):

```python
op.execute("CREATE UNIQUE INDEX uq_authors_name_lower ON authors (lower(name))")
op.execute("CREATE UNIQUE INDEX uq_narrators_name_lower ON narrators (lower(name))")
op.execute("CREATE UNIQUE INDEX uq_editions_work_format ON editions (work_id, format) NULLS NOT DISTINCT")
op.execute(
    "CREATE UNIQUE INDEX uq_reading_history_user_edition_date "
    "ON reading_history (user_id, edition_id, date_completed)"
)
op.execute("CREATE UNIQUE INDEX uq_suggestions_active ON suggestions (user_id, work_id) WHERE status = 'Suggested'")
```

Downgrade: drop the 5 uniques, the 10 indexes, `deep_enriched_at`, and revert the 13 columns to `timestamp without time zone` (`USING <col> AT TIME ZONE 'UTC'`), in reverse order.

- [ ] **Step 4: models.py** — the 13 columns get `DateTime(timezone=True)` (defaults/onupdate lambdas unchanged; fetched_at keeps no default); `Work` gains `deep_enriched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)`; simple single-column FK indexes get `index=True` ONLY where the migration name matches alembic's default (it won't — migration uses explicit `ix_<table>_<col>`, which IS SQLAlchemy's default naming for `index=True`; verify one; if they align, add `index=True` for documentation parity; if not, leave models untouched for indexes and add a comment block referencing the migration). Functional/partial/composite uniques: models.py comment only.

- [ ] **Step 5: Remove the fetched_at band-aids** — grep `replace(tzinfo=UTC)` in `availability/service.py` (freshness checks in `availability_for` + `batch_availability`); replace `row.fetched_at.replace(tzinfo=UTC)` with `row.fetched_at` (aware post-migration). CI's availability suites pin behavior.

- [ ] **Step 6: Run** the DB-free suite (`test/unit -q`, green minus known failures); collect-check the new integration file; `.venv/Scripts/python -c "from agentic_librarian.db import models"` sanity import.

- [ ] **Step 7: Lint, format, commit**

```bash
git add alembic/versions src/agentic_librarian/db/models.py src/agentic_librarian/availability/service.py test/integration/test_schema_hardening.py
git commit -m "feat(db): phase 6.3 schema hardening — uniques, FK indexes, timestamptz, deep_enriched_at (#95 #97 #108 #109)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 2: get_or_create helper + adoption + advisory lock — #95-code

**Files:**
- Create: `src/agentic_librarian/db/get_or_create.py`
- Modify: `src/agentic_librarian/etl/persist.py` (authors ~L120-124 in the desired-loop, narrators ~L233-237, editions ~L207, reading_history guard ~L288-292), `src/agentic_librarian/enrichment/two_phase.py` (add_read_event edition site; enrich_fast advisory lock), `src/agentic_librarian/etl/ingest.py:88-92` (the UNGUARDED edition insert), `src/agentic_librarian/imports/worker.py` (`_upsert_suggestion`), `src/agentic_librarian/mcp/server.py` (`log_suggestion` gains dedup — note #88 in the comment)
- Test: `test/unit/test_get_or_create.py` (new, sqlite), `test/integration/test_constraint_backstops.py` (new, db_integration)

**Interfaces:**
- Produces: `get_or_create(session, model, defaults: dict | None = None, **filters) -> tuple[obj, bool]` (obj, created).

- [ ] **Step 1: Failing unit tests** — `test/unit/test_get_or_create.py` (sqlite, file-based; define a tiny standalone Table/model with a unique column inside the test — do NOT use app models on sqlite):

```python
def test_returns_existing(...):        # pre-inserted row -> (row, False), no insert
def test_creates_when_missing(...):    # empty -> (row, True)
def test_integrity_race_recovers(...): # monkeypatch flush to raise IntegrityError once after a
                                       # concurrent insert is simulated -> helper re-queries -> (existing, False),
                                       # outer transaction still usable (a subsequent query works)
```

(Write real bodies; the third test simulates the race by inserting the conflicting row via a second connection/session before calling the helper with a stubbed first-query-miss — e.g. patch the helper's initial query path or insert between an explicit pre-check. Keep it honest: the SAVEPOINT recovery and outer-transaction survival are the binding assertions.)

- [ ] **Step 2: Implement the helper**

```python
"""Constraint-backed get-or-create (GH #95). The SELECT-then-INSERT races that used to
create duplicates are now backstopped by unique constraints; this helper turns the
IntegrityError loser into a clean re-query instead of a 500. SAVEPOINT (begin_nested)
so the caller's outer transaction survives the rolled-back insert."""

from sqlalchemy.exc import IntegrityError


def get_or_create(session, model, defaults=None, **filters):
    instance = session.query(model).filter_by(**filters).first()
    if instance is not None:
        return instance, False
    params = dict(filters)
    params.update(defaults or {})
    try:
        with session.begin_nested():
            instance = model(**params)
            session.add(instance)
            session.flush()
        return instance, True
    except IntegrityError:
        instance = session.query(model).filter_by(**filters).first()
        if instance is None:  # constraint fired but filters don't match it (e.g. case-variant name)
            raise
        return instance, False
```

NOTE the case-variant nuance: `uq_authors_name_lower` fires on `lower(name)` but `filter_by(name=name)` is exact — for authors/narrators the ADOPTION SITES must keep their existing `func.lower(...)` first-query AND pass a lower-aware re-query. Design: sites with non-trivial predicates call the helper with a `query=` escape? NO — keep the helper simple; authors/narrators keep their existing case-insensitive query-first code and only wrap the INSERT in the same begin_nested/IntegrityError-requery pattern via a second tiny helper `insert_or_requery(session, instance, requery: Callable)`. Implement BOTH helpers in the module; simple sites (editions, reading_history, suggestions) use `get_or_create`; authors/narrators use `insert_or_requery` with their `func.lower` requery lambda.

- [ ] **Step 3: Adopt at every site** (read each first; preserve surrounding behavior/comments):
  - persist.py authors + narrators → `insert_or_requery` with the existing lower() lookup as requery.
  - persist.py editions (~L207 region) + two_phase.add_read_event edition + **ingest.py:88-92** (currently NO guard — becomes `get_or_create(session, Edition, defaults={...page_count...}, work_id=..., format=...)`).
  - persist.py reading_history guard + add_read_event's insert → keep their date-guards, wrap inserts.
  - worker.`_upsert_suggestion` → `get_or_create(..., user_id=..., work_id=..., status="Suggested", defaults={"context": context})` preserving the bool return.
  - mcp `log_suggestion` → dedup: `get_or_create` on (user_id, work_id, status="Suggested") with context/justification/conversation_id in defaults; when not created, return "Already an active suggestion for work {id} — not duplicated." (comment: closes #88's root cause; the partial unique backs it).
  - `enrich_fast` write session, before `_find_existing` re-check:

```python
        if session.get_bind().dialect.name == "postgresql":
            # GH #95: works can't carry a cross-table unique (title+author spans tables) —
            # serialize concurrent same-book creators instead. xact-scoped: released on commit.
            session.execute(
                text("SELECT pg_advisory_xact_lock(hashtext(:k))"),
                {"k": f"{_normalize(title)}|{_normalize(author)}"},
            )
```

- [ ] **Step 4: Failing integration tests** — `test/integration/test_constraint_backstops.py` (db_integration): sequential double-insert of same-cased and case-variant authors resolves to one row; duplicate active suggestion via `log_suggestion` twice → one row + "not duplicated" message; duplicate edition via ingest path → one row. (CI executes against the Task-1 constraints.)

- [ ] **Step 5: Run** unit suite; collect-checks; lint/format; commit:

```bash
git commit -m "feat(db): constraint-backed get-or-create everywhere; advisory lock on work creation; log_suggestion dedup (#95, #88 root cause)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 3: Enrichment status + retry + sweep — #97

**Files:**
- Modify: `src/agentic_librarian/enrichment/two_phase.py` (`enrich_deep` stamps `deep_enriched_at`), `src/agentic_librarian/api/internal.py` (503-on-empty), `scripts/clean_catalog.py` + `src/agentic_librarian/etl/enrichment_sweep.py` (new: the queryable planner)
- Test: `test/unit/test_enrichment_sweep.py` (new), `test/integration/test_internal_enrich_api.py` (extend)

**Interfaces:**
- Produces: `plan_requeue(session) -> list[RequeueCandidate]` (work_id, title, reason: "no_real_trope" | "never_deep_enriched") in `etl/enrichment_sweep.py`, consuming `is_fallback_trope_name` (#111).

- [ ] **Step 1:** `enrich_deep`: in the write session (and on the row-is-None path, which needs a SHORT session now) set `work.deep_enriched_at = datetime.now(UTC)` — the timestamp means "the deep pass COMPLETED", including confirmed-empty; the predicate distinguishes fingerprint-less works. On the scouts-found-nothing path open a brief session solely to stamp (read the current shape first — post-#94 it returns True with no session; add `with db_manager.get_session() as s: s.get(Work, work_id).deep_enriched_at = ...`).
- [ ] **Step 2:** `api/internal.py` enrich endpoint: after a True return, evaluate: work has NO real trope (shared predicate over its links) AND `_run_scouts` yielded nothing this pass → respond 503 `{"work_id":..., "status": "empty_deep_pass"}` so Cloud Tasks retries with backoff. Mechanically: `enrich_deep` must surface "yielded nothing" — change its return to `bool | str`? NO — keep `-> bool` for compat and add a second function OR return an Enum-like str... **Decision: `enrich_deep` returns `"done" | "empty" | "missing"`** (str literals; internal.py maps missing→404, empty→503 when the work still has no real trope, done→200). Update the ONE caller (internal.py) — grep for others (tests). Document in both docstrings.
- [ ] **Step 3:** `etl/enrichment_sweep.py`: `plan_requeue(session)` — works where `deep_enriched_at IS NULL`, plus works whose every linked trope is fallback/junk (reuse the predicate; join pattern from trope_backfill). `clean_catalog.py` gains `--requeue-unenriched` (plan prints table; `--apply --yes` calls `enqueue_enrichment(str(work_id))` per candidate, reusing the existing refuse-gate; requires Cloud Tasks env — document in the mode's help text).
- [ ] **Step 4:** Tests: unit — `plan_requeue` against a mocked/sqlite-free session? (needs query shapes → make it db_integration instead, seeded: one work with real trope + stamped (excluded), one unstamped (included, "never_deep_enriched"), one stamped-but-fallback-only (included, "no_real_trope")). Extend `test_internal_enrich_api.py`: empty-deep-pass on a trope-less work → 503; empty on a work WITH real tropes → 200.
- [ ] **Step 5:** Run/lint/commit:

```bash
git commit -m "feat(enrichment): deep_enriched_at stamping, retryable empty-pass 503, requeue sweep (#97)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 4: Dedup planner — the USER-GATE tooling

**Files:**
- Create: `src/agentic_librarian/etl/dedup_backfill.py`
- Modify: `scripts/clean_catalog.py` (`--dedup-for-constraints` mode)
- Test: `test/integration/test_dedup_backfill.py` (new, db_integration, seeded duplicates)

**Interfaces:**
- Produces: `plan_dedup(session) -> DedupPlan` (dataclass: per-class lists with row details + counts) and `apply_dedup(session, plan) -> dict` (per-class applied counts). Classes: `duplicate_authors` (group by lower(name); keep oldest id; repoint work_contributors + author_styles — mind PK collisions: a repoint that would duplicate an existing (work_id, author_id, role) link DELETES the loser link instead), `duplicate_narrators` (same; narrator_styles + edition_narrators), `duplicate_editions` (group by (work_id, COALESCE(format,'')); keep oldest; repoint reading_history + edition_narrators with the same collision rule), `duplicate_reading_history` (exact (user_id, edition_id, date_completed) groups; keep oldest), `duplicate_suggestions` (per (user_id, work_id) with status='Suggested'; keep oldest), `orphan_authors` (no work_contributors AND no author_styles; delete), `duplicate_works_REPORT_ONLY` (normalized title+author groups; details only, never applied).

- [ ] **Step 1: Failing integration tests** — seed each duplicate class synthetically; assert the PLAN identifies exactly the seeded groups (and nothing else) and `apply_dedup` converges: post-apply, re-plan is empty (minus report-only works), FK repoints verified (a reading_history row moved to the surviving edition), collision rule verified (duplicate link deleted, not duplicated).
- [ ] **Step 2: Implement** planner + applier (pure structural SQL/ORM; every deletion in the plan lists the exact ids; `apply_dedup` takes the PLAN as input — apply-what-was-shown, the #69 discipline).
- [ ] **Step 3: clean_catalog mode** — `--dedup-for-constraints` prints the plan grouped by class with counts + samples (and the full id lists to a timestamped report file under `data/reports/dedup-<ts>.txt` for the user's review); `--apply --yes` + refuse-gate applies THE SAME computed plan. Mode help documents the sequence (PR-C deployed → dry-run → approval → apply → alembic → merge).
- [ ] **Step 4:** Run/collect-check/lint/commit:

```bash
git commit -m "feat(catalog): dedup-for-constraints planner + applier — the gated pre-constraint backfill (#95)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 5: Rollout runbook + docs

**Files:**
- Create: `docs/runbooks/phase6-3-schema-rollout.md` (operator What/Why/How/Done-when, the house runbook style — see `docs/runbooks/shelfwright-launch.md`): sequence = verify PR-C deployed → pg_dump snapshot (command) → `--dedup-for-constraints` dry-run ([Claude] runs, [You] review the report file) → `--apply --yes` → `alembic upgrade head` from the branch ([You], per lift1 runbook §3 mechanics) → merge PR-D → deploy guard passes → post-checks (constraint sanity queries; `--requeue-unenriched` dry-run as the first #97 report).
- Modify: `docs/project_notes/decisions.md` (ADR-060: constraint-backed get-or-create + advisory-lock works dedup + gated backfill sequence — Context/Decision/Consequences, citing #95/#88/#69), `docs/project_notes/key_facts.md` (Database bullet: constraints + timestamptz + deep_enriched_at noted).
- [ ] Write all three; lint markdown by eye; commit:

```bash
git commit -m "docs: phase 6.3 schema rollout runbook + ADR-060 + key facts (#95 #97 #108 #109)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## Out of scope (operator-gated, post-plan)

The prod sequence itself (dry-run → approval → apply → alembic → merge) and closing #95 #96 #97 #98 #108 #109 #110 #111 #112 (+#88 comment) after acceptance.
