# Internal Retrieval Readiness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prove the mesh's internal (Postgres) retrieval ranks by real semantic similarity and that the real Flow 1 ETL populates a queryable DB, then retire the orphaned internal/external naming.

**Architecture:** A committed fixture of real `gemini-embedding-001` vectors lets `db_integration` tests assert *ordering* (not just non-empty) deterministically; a minimal ranking fix in `search_internal_database` orders results by trope similarity; an `api_dependent` smoke materializes the Dagster Flow 1 ETL against the isolated test DB; CI gains a pgvector service so the deterministic DB tests gate there; the dead `search_strategies.py` experiment is removed.

**Tech Stack:** Python 3.11, SQLAlchemy + pgvector, pytest (`db_integration` / `api_dependent` markers, ADR-034), Dagster, google-genai (`gemini-embedding-001` @ 1536-d), GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-05-31-internal-retrieval-readiness-design.md`

**Branch:** `spec/internal-retrieval-readiness` (already created and checked out).

---

## Key Facts For The Implementer (read before starting)

- **Run commands in the dev container** (Postgres + deps live there). Examples use:
  `docker exec agentic_librarian_app sh -lc 'cd /app && <cmd>'`.
- **`db_integration` tests** auto-skip when Postgres is unreachable (`test/conftest.py`),
  and run against a dedicated `*_test` database that is truncated before each test (ADR-034).
- **`TropeManager(session=...)` and `StyleManager(session=...)` construct a `genai.Client`
  in `__init__`** and raise `ValueError` if `GOOGLE_SEARCH_API_KEY` is unset — even when you
  patch `_get_embedding`. So DB tests that call `search_internal_database` must set a dummy
  `GOOGLE_SEARCH_API_KEY` (construction makes no network call). `get_user_trope_preferences`
  does NOT construct a manager (pure SQL) — no key needed.
- **Embedding dimension is 1536** (`scouts/utils.EMBEDDING_DIMENSIONS`; `Vector(1536)` on
  `Trope`/`Style`). `gemini-embedding-001` defaults to 3072 and must be requested at 1536.
- **Embedding helper:** `scouts/utils.get_cached_embedding(client, model_name, text)`;
  `TropeManager._get_embedding(self, text)` wraps it.
- **CSV schema** the ETL expects: `Title,Author,Date complete,# of pages,format`. Use a date
  with an explicit 4-digit year (e.g. `1/7/2020`) so year parsing is deterministic.

---

## Task 1: Real-embedding fixture (generator + committed JSON + geometry test)

**Files:**
- Create: `scripts/gen_trope_embedding_fixture.py`
- Create: `test/data/trope_embeddings.json` (generated output, committed)
- Test: `test/unit/test_trope_embedding_fixture.py`

- [ ] **Step 1: Write the generator script**

`scripts/gen_trope_embedding_fixture.py`:

```python
"""One-time generator for the cached trope-embedding test fixture (api_dependent).

Run manually in an environment with GOOGLE_SEARCH_API_KEY set:
    python scripts/gen_trope_embedding_fixture.py

Embeds a curated set of trope strings spanning two semantic clusters with
gemini-embedding-001 at 1536-d and writes test/data/trope_embeddings.json. Prints the
pairwise cosine matrix so cluster separation can be sanity-checked before committing.
"""

import json
import os
from itertools import combinations
from pathlib import Path

import numpy as np
from agentic_librarian.scouts.utils import EMBEDDING_DIMENSIONS
from google import genai
from google.genai import types

STRINGS = [
    "enemies to lovers",
    "slow burn romance",
    "grimdark war",
    "brutal military strategy",
]
OUT = Path("test/data/trope_embeddings.json")


def _cos(a: list[float], b: list[float]) -> float:
    a, b = np.array(a), np.array(b)
    return float(a @ b / (np.linalg.norm(a) * np.linalg.norm(b)))


def main() -> None:
    api_key = os.environ.get("GOOGLE_SEARCH_API_KEY")
    if not api_key:
        raise SystemExit("GOOGLE_SEARCH_API_KEY required to generate the fixture.")
    client = genai.Client(api_key=api_key)

    vectors: dict[str, list[float]] = {}
    for s in STRINGS:
        resp = client.models.embed_content(
            model="gemini-embedding-001",
            contents=s,
            config=types.EmbedContentConfig(output_dimensionality=EMBEDDING_DIMENSIONS),
        )
        vectors[s] = list(resp.embeddings[0].values)

    print("Pairwise cosine similarity:")
    for x, y in combinations(STRINGS, 2):
        print(f"  {x!r} vs {y!r}: {_cos(vectors[x], vectors[y]):.4f}")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(vectors, indent=2))
    print(f"Wrote {OUT} ({len(vectors)} vectors, dim={EMBEDDING_DIMENSIONS}).")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run the generator to produce the committed fixture**

Run (dev container, real key in env):
```
docker exec agentic_librarian_app sh -lc 'cd /app && python scripts/gen_trope_embedding_fixture.py'
```
Expected: prints a cosine matrix where the two within-cluster pairs
(`enemies to lovers`/`slow burn romance`, `grimdark war`/`brutal military strategy`) score
higher than any romance-vs-grimdark pair, and writes `test/data/trope_embeddings.json`.
If within-cluster does NOT exceed cross-cluster, pick clearer cluster strings and rerun
(the Step 4 test enforces this).

- [ ] **Step 3: Write the geometry validation test**

`test/unit/test_trope_embedding_fixture.py`:

```python
import json
from pathlib import Path

import numpy as np

FIXTURE = Path("test/data/trope_embeddings.json")
ROMANCE = ["enemies to lovers", "slow burn romance"]
GRIMDARK = ["grimdark war", "brutal military strategy"]


def _cos(a, b):
    a, b = np.array(a), np.array(b)
    return float(a @ b / (np.linalg.norm(a) * np.linalg.norm(b)))


def test_fixture_has_all_strings_at_1536d():
    data = json.loads(FIXTURE.read_text())
    for s in ROMANCE + GRIMDARK:
        assert s in data, f"missing {s!r}"
        assert len(data[s]) == 1536


def test_fixture_clusters_are_separable():
    data = json.loads(FIXTURE.read_text())
    within = min(_cos(data[ROMANCE[0]], data[ROMANCE[1]]), _cos(data[GRIMDARK[0]], data[GRIMDARK[1]]))
    cross = max(_cos(data[r], data[g]) for r in ROMANCE for g in GRIMDARK)
    assert within > cross, f"within-cluster {within:.4f} must exceed cross-cluster {cross:.4f}"
```

- [ ] **Step 4: Run the geometry test to verify it passes**

Run:
```
docker exec agentic_librarian_app sh -lc 'cd /app && pytest test/unit/test_trope_embedding_fixture.py -v'
```
Expected: 2 passed. If `test_fixture_clusters_are_separable` fails, return to Step 2 with
clearer cluster strings.

- [ ] **Step 5: Commit**

```
git add scripts/gen_trope_embedding_fixture.py test/data/trope_embeddings.json test/unit/test_trope_embedding_fixture.py
git commit -m "test(spec3): cached real-embedding fixture + geometry guard"
```

---

## Task 2: Rank `search_internal_database` results by trope similarity

The tool currently returns the final list in arbitrary DB order (it collects candidate ids
into a `set()` then `filter(Work.id.in_(...))`). This task makes the order reflect cosine
similarity to the query, and proves it with a `db_integration` ordering test.

**Files:**
- Modify: `src/agentic_librarian/mcp/server.py` (the `search_internal_database` body)
- Test: `test/integration/test_internal_retrieval.py`

- [ ] **Step 1: Write the failing ranking test**

Create `test/integration/test_internal_retrieval.py`:

```python
import json
from pathlib import Path
from unittest.mock import patch

import pytest
from agentic_librarian.db.models import Author, Trope, Work, WorkContributor, WorkTrope
from agentic_librarian.db.session import DatabaseManager
from agentic_librarian.mcp.server import search_internal_database, set_db_manager

FIXTURE = json.loads(Path("test/data/trope_embeddings.json").read_text())
ROMANCE = ["enemies to lovers", "slow burn romance"]
GRIMDARK = ["grimdark war", "brutal military strategy"]


def _seed_work(session, title, author_name, trope_names):
    author = Author(name=author_name)
    session.add(author)
    session.flush()
    work = Work(title=title)
    session.add(work)
    session.flush()
    session.add(WorkContributor(work=work, author=author, role="Author"))
    for name in trope_names:
        trope = Trope(name=name, embedding=FIXTURE[name])
        session.add(trope)
        session.flush()
        session.add(WorkTrope(work=work, trope=trope))
    return work


@pytest.mark.db_integration
def test_search_ranks_semantically_near_work_first(db_url, monkeypatch):
    # Managers construct a genai.Client in __init__ (needs a key; no network call).
    monkeypatch.setenv("GOOGLE_SEARCH_API_KEY", "dummy-key-for-construction")
    test_db_manager = DatabaseManager(db_url)
    set_db_manager(test_db_manager)

    with test_db_manager.get_session() as session:
        _seed_work(session, "A Courtship", "Romance Author", ROMANCE)
        _seed_work(session, "The Long War", "Grimdark Author", GRIMDARK)
        session.commit()

    # The query-side embedding resolves a known string to the same cached real vector,
    # so cosine distances are deterministic.
    def fake_embedding(self, text):
        return FIXTURE[text]

    with patch("agentic_librarian.mcp.server.TropeManager._get_embedding", fake_embedding):
        results = search_internal_database(target_tropes=["enemies to lovers"])

    titles = [r["title"] for r in results]
    assert titles[:2] == ["A Courtship", "The Long War"], titles
```

- [ ] **Step 2: Run the test to verify it fails**

Run:
```
docker exec agentic_librarian_app sh -lc 'cd /app && pytest test/integration/test_internal_retrieval.py::test_search_ranks_semantically_near_work_first -v'
```
Expected: FAIL — both works are returned but order is arbitrary (the assertion on
`titles[:2]` fails intermittently/incorrectly because the final query does not order by
similarity).

- [ ] **Step 3: Add `avg_vector` at function scope**

In `src/agentic_librarian/mcp/server.py`, inside `search_internal_database`, find:

```python
        candidate_work_ids = set()

        # 1. Trope Search
        if target_tropes:
```

Replace with (initialise `avg_vector` so it is visible after the trope block):

```python
        candidate_work_ids = set()
        avg_vector = None

        # 1. Trope Search
        if target_tropes:
```

- [ ] **Step 4: Replace the final retrieval block with a ranked one**

In the same function, find the current final-retrieval block:

```python
        # 3. Final Work Retrieval
        if not candidate_work_ids:
            return []

        # Eager load contributors/authors for the final list
        works = (
            session.query(Work)
            .options(joinedload(Work.contributors).joinedload(WorkContributor.author))
            .filter(Work.id.in_(list(candidate_work_ids)))
            .limit(limit)
            .all()
        )

        return [
            {
                "id": str(w.id),
                "title": w.title,
                "authors": [c.author.name for c in w.contributors],
                "genres": w.genres,
                "description": w.description,
            }
            for w in works
        ]
```

Replace it with:

```python
        # 3. Final Work Retrieval, ordered by semantic relevance.
        if not candidate_work_ids:
            return []

        # Order candidates by their closest matching trope to the query vector (cosine
        # distance). Candidates that arrived via style-only matching (no matching trope)
        # are appended afterward in a stable order. Without this, the set + IN filter
        # returns rows in arbitrary DB order.
        ordered_ids: list = []
        if target_tropes and avg_vector is not None:
            ranked = (
                session.query(Work.id)
                .join(WorkTrope, WorkTrope.work_id == Work.id)
                .join(Trope, Trope.id == WorkTrope.trope_id)
                .filter(Work.id.in_(list(candidate_work_ids)))
                .group_by(Work.id)
                .order_by(func.min(Trope.embedding.cosine_distance(avg_vector)))
                .all()
            )
            ordered_ids = [w[0] for w in ranked]
        for wid in candidate_work_ids:
            if wid not in ordered_ids:
                ordered_ids.append(wid)
        ordered_ids = ordered_ids[:limit]

        # Eager load contributors/authors, then restore the ranked order.
        works = (
            session.query(Work)
            .options(joinedload(Work.contributors).joinedload(WorkContributor.author))
            .filter(Work.id.in_(ordered_ids))
            .all()
        )
        works_by_id = {w.id: w for w in works}
        ordered_works = [works_by_id[wid] for wid in ordered_ids if wid in works_by_id]

        return [
            {
                "id": str(w.id),
                "title": w.title,
                "authors": [c.author.name for c in w.contributors],
                "genres": w.genres,
                "description": w.description,
            }
            for w in ordered_works
        ]
```

(`func` is already imported in `server.py`: `from sqlalchemy import func, select`.)

- [ ] **Step 5: Run the ranking test to verify it passes**

Run:
```
docker exec agentic_librarian_app sh -lc 'cd /app && pytest test/integration/test_internal_retrieval.py::test_search_ranks_semantically_near_work_first -v'
```
Expected: PASS (`A Courtship` ranks above `The Long War`).

- [ ] **Step 6: Verify no regression in existing MCP DB tests**

Run:
```
docker exec agentic_librarian_app sh -lc 'cd /app && pytest test/integration/test_mcp_tools.py test/unit/test_mcp_tools.py -v'
```
Expected: all pass (the stub-vector tests still work; ordering with identical vectors is
stable since all distances are equal).

- [ ] **Step 7: Commit**

```
git add src/agentic_librarian/mcp/server.py test/integration/test_internal_retrieval.py
git commit -m "feat(mcp): rank search_internal_database by trope similarity + ordering test"
```

---

## Task 3: `get_user_trope_preferences` integration coverage

**Files:**
- Test: `test/integration/test_internal_retrieval.py` (append)

- [ ] **Step 1: Write the failing preferences test**

Append to `test/integration/test_internal_retrieval.py`:

```python
from datetime import date

from agentic_librarian.db.models import Edition, ReadingHistory
from agentic_librarian.mcp.server import get_user_trope_preferences


@pytest.mark.db_integration
def test_user_trope_preferences_ranked_by_frequency(db_url):
    test_db_manager = DatabaseManager(db_url)
    set_db_manager(test_db_manager)

    with test_db_manager.get_session() as session:
        # "Fantasy" appears in 2 read works, "Mystery" in 1 -> Fantasy ranks first.
        fantasy = Trope(name="Fantasy")
        mystery = Trope(name="Mystery")
        session.add_all([fantasy, mystery])
        session.flush()
        for i, tropes in enumerate([[fantasy, mystery], [fantasy]]):
            author = Author(name=f"Auth {i}")
            session.add(author)
            session.flush()
            work = Work(title=f"Book {i}")
            session.add(work)
            session.flush()
            session.add(WorkContributor(work=work, author=author, role="Author"))
            for t in tropes:
                session.add(WorkTrope(work=work, trope=t))
            edition = Edition(work=work, format="hardcover")
            session.add(edition)
            session.flush()
            session.add(ReadingHistory(edition=edition, date_completed=date(2020, 1, 1)))
        session.commit()

    prefs = get_user_trope_preferences()
    assert prefs[0] == "Fantasy", prefs
    assert set(prefs) == {"Fantasy", "Mystery"}, prefs
```

- [ ] **Step 2: Run it to verify it passes**

Run:
```
docker exec agentic_librarian_app sh -lc 'cd /app && pytest test/integration/test_internal_retrieval.py::test_user_trope_preferences_ranked_by_frequency -v'
```
Expected: PASS. (This exercises the existing, already-correct
`get_user_trope_preferences`; the test is the deliverable — it closes the
mock-only coverage gap. If it fails, the bug is real and in scope to fix.)

- [ ] **Step 3: Commit**

```
git add test/integration/test_internal_retrieval.py
git commit -m "test(mcp): integration coverage for get_user_trope_preferences"
```

---

## Task 4: Gate `db_integration` in CI via a pgvector service

**Files:**
- Modify: `.github/workflows/lint.yml`

- [ ] **Step 1: Add the Postgres service and test env**

Replace the entire contents of `.github/workflows/lint.yml` with:

```yaml
name: Python CI

on:
  push:
    branches: [ "**" ]
  pull_request:
    branches: [ "**" ]

jobs:
  ci:
    runs-on: ubuntu-latest
    permissions:
      contents: read
    services:
      postgres:
        image: pgvector/pgvector:pg16
        env:
          POSTGRES_USER: librarian
          POSTGRES_PASSWORD: librarian_secret_password
          POSTGRES_DB: agentic_librarian
        ports:
          - 5432:5432
        options: >-
          --health-cmd pg_isready
          --health-interval 10s
          --health-timeout 5s
          --health-retries 5
    steps:
      - uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.11'
          cache: 'pip'

      - name: Install dependencies
        run: |
          python -m pip install --upgrade pip
          pip install .[dev]
          pip install pre-commit

      - name: Run pre-commit on all files
        run: |
          SKIP=pytest pre-commit run --all-files --show-diff-on-failure

      - name: Run tests
        env:
          POSTGRES_HOST: localhost
          POSTGRES_PORT: 5432
          POSTGRES_USER: librarian
          POSTGRES_PASSWORD: librarian_secret_password
          POSTGRES_DB: agentic_librarian
          # Managers construct a genai.Client at import-time of the call path; a dummy key
          # is enough (embeddings are patched in tests, so no network call is made).
          GOOGLE_SEARCH_API_KEY: dummy-key-for-construction
        run: |
          pytest -m "not api_dependent and not slow"
```

- [ ] **Step 2: Commit and push to trigger CI**

```
git add .github/workflows/lint.yml
git commit -m "ci: add pgvector service so db_integration tests gate in CI"
git push
```

- [ ] **Step 3: Verify CI ran the db_integration tests and is green**

Check the Actions run for this push (GitHub → Actions, or `gh run list` if available).
Expected: the "Run tests" step shows the `db_integration` tests **executing** (not skipped)
and the run concludes **success**. If `db_integration` tests are still skipped, the service
env vars are not reaching the test step — re-check the `env:` block on "Run tests". If a
db test fails on a missing key, confirm `GOOGLE_SEARCH_API_KEY` is set in that `env:`.

---

## Task 5: Live Flow 1 ETL smoke (api_dependent)

This is the riskiest task: the full ETL has never run end-to-end. **Scope rule (approved):**
get a minimal happy-path run green; if the live run surfaces deeper/structural ETL bugs,
fix only what blocks the happy path, **log the rest** to `docs/project_notes/bugs.md` and a
new `docs/project_notes/issues.md` REC, and keep this task bounded.

**Files:**
- Create: `test/data/etl_smoke/20200107.csv`
- Test: `test/integration/test_flow1_etl_live.py`

- [ ] **Step 1: Create the smoke CSV (one physical book)**

`test/data/etl_smoke/20200107.csv` (note the BOM-free header matching the ETL's expected
columns; the date has an explicit 4-digit year):

```
Title,Author,Date complete,# of pages,format
The Way of Kings,Brandon Sanderson,1/7/2020,1007,hardcover
```

- [ ] **Step 2: Write the live smoke test**

`test/integration/test_flow1_etl_live.py`:

```python
import shutil
from pathlib import Path

import pytest
from agentic_librarian.db.models import Author, Edition, ReadingHistory, Trope, Work, WorkTrope
from agentic_librarian.db.session import DatabaseManager
from agentic_librarian.orchestration import assets
from agentic_librarian.orchestration.definitions import create_scout_manager
from dagster import DagsterInstance, materialize

PARTITION_KEY = "20200107"
SMOKE_CSV = Path("test/data/etl_smoke") / f"{PARTITION_KEY}.csv"


@pytest.fixture
def staged_csv():
    # raw_history reads a hardcoded data/raw/{partition_key}.csv; stage the fixture there
    # and remove it afterward so the real data/raw/ is never polluted.
    dest = Path("data/raw") / f"{PARTITION_KEY}.csv"
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(SMOKE_CSV, dest)
    yield dest
    dest.unlink(missing_ok=True)


@pytest.mark.api_dependent
@pytest.mark.db_integration
def test_flow1_etl_populates_db(db_url, staged_csv):
    instance = DagsterInstance.ephemeral()
    instance.add_dynamic_partitions(assets.csv_partitions.name, [PARTITION_KEY])
    test_db_manager = DatabaseManager(db_url)

    result = materialize(
        [assets.raw_history, assets.enriched_metadata, assets.vectorized_tropes],
        partition_key=PARTITION_KEY,
        instance=instance,
        resources={"db_manager": test_db_manager, "scout_manager": create_scout_manager()},
    )
    assert result.success

    with test_db_manager.get_session() as session:
        work = session.query(Work).filter(Work.title == "The Way of Kings").first()
        assert work is not None, "Work was not created"
        assert session.query(Author).filter(Author.name == "Brandon Sanderson").first() is not None
        assert session.query(Edition).filter(Edition.work_id == work.id).first() is not None
        wt = session.query(WorkTrope).filter(WorkTrope.work_id == work.id).first()
        assert wt is not None, "no trope linked to the work"
        trope = session.query(Trope).filter(Trope.id == wt.trope_id).first()
        assert trope.embedding is not None, "trope embedding not populated"
        assert (
            session.query(ReadingHistory).join(Edition).filter(Edition.work_id == work.id).first()
            is not None
        ), "no reading history recorded"
```

- [ ] **Step 3: Run the live smoke (real keys, dev container)**

Run:
```
docker exec agentic_librarian_app sh -lc 'cd /app && pytest test/integration/test_flow1_etl_live.py -v -m "api_dependent and db_integration"'
```
Expected: PASS. **If it fails:** diagnose the blocker. Fix only what is needed for this
one-book happy path (e.g. a resource-wiring or partition-registration detail). If the
failure is a deeper/structural ETL defect (scout data shape, manager logic), log it to
`docs/project_notes/bugs.md` and add a REC to `docs/project_notes/issues.md`, then adjust
the assertion scope (e.g. drop the trope-embedding assertion if tropes legitimately did not
enrich for this title) so the smoke still proves the core population path, and note the
reduction.

- [ ] **Step 4: Add an audiobook row, re-run, keep-or-revert**

Append a second row to `test/data/etl_smoke/20200107.csv`:

```
Project Hail Mary,Andy Weir,1/14/2020,476,audiobook
```

Re-run Step 3's command. If it passes reliably (run it twice), keep the row and extend the
assertions to also find `Project Hail Mary`. If the audiobook path is too non-deterministic
(passes inconsistently across the two runs), **revert the CSV to the single physical row**,
log the flakiness to `docs/project_notes/bugs.md` (title: "Audiobook ETL path
non-deterministic in Flow 1 smoke"), and proceed with the physical-only smoke.

- [ ] **Step 5: Verify the smoke is excluded from the offline suite**

Run:
```
docker exec agentic_librarian_app sh -lc 'cd /app && pytest -m "not api_dependent and not slow" -q 2>&1 | tail -3'
```
Expected: the suite passes and `test_flow1_etl_live.py` is deselected (it is
`api_dependent`).

- [ ] **Step 6: Commit**

```
git add test/data/etl_smoke/20200107.csv test/integration/test_flow1_etl_live.py
git add docs/project_notes/bugs.md docs/project_notes/issues.md  # only if you logged items
git commit -m "test(etl): live Flow 1 smoke materializes a populated DB (api_dependent)"
```

---

## Task 6: Remove the orphaned `search_strategies.py` experiment + ADR-039

**Files:**
- Delete: `src/agentic_librarian/agents/search_strategies.py`
- Delete: `test/unit/test_search_strategies.py`
- Modify: `docs/project_notes/decisions.md` (append ADR-039)

- [ ] **Step 1: Confirm there is no production importer**

Run:
```
docker exec agentic_librarian_app sh -lc 'cd /app && grep -rnE "search_strategies|InternalSearchAgent|ExternalA2AAgent|run_search_experiment" src/ || echo "NO SRC IMPORTERS"'
```
Expected: `NO SRC IMPORTERS` (only the test and docs reference it). If `src/` references
appear, stop and report — removal would break imports.

- [ ] **Step 2: Delete the module and its test**

```
git rm src/agentic_librarian/agents/search_strategies.py test/unit/test_search_strategies.py
```

- [ ] **Step 3: Append ADR-039 to `docs/project_notes/decisions.md`**

Add at the end of the file:

```markdown

### ADR-039: Remove the Orphaned `search_strategies.py` Experiment; Functional Naming is Canonical (2026-05-31)
**Context:**
- `agents/search_strategies.py` defined `InternalSearchAgent` (Mode A: in-process genai
  grounding) and `ExternalA2AAgent` (Mode B: simulated A2A) — both *web* search in the old,
  confusing sense flagged by ADR-035. It was a standalone MLflow experiment with no
  production importer, used the quota-dead `gemini-2.0-flash`, and was superseded by the
  Spec 2 Explorer (grounded `GoogleSearchTool`).

**Decision:**
- Remove the module and its unit test. Functional naming is canonical: **internal =
  retrieval from our Postgres DB** (`search_internal_database`, `get_user_trope_preferences`,
  `get_unacted_suggestions`, `check_reading_history`); **external = web discovery** (the
  Explorer). `agents/services.py` already conforms — no rename needed there.

**Consequences:**
- Less dead code and one fewer source of the internal/external ambiguity. The deferred
  in-process-vs-A2A (Mode A/B) comparison, if ever revisited, is an implementation detail of
  external discovery (ADR-035), not a functional split.
```

- [ ] **Step 4: Verify nothing broke**

Run:
```
docker exec agentic_librarian_app sh -lc 'cd /app && pytest -m "not api_dependent and not slow" -q 2>&1 | tail -3'
```
Expected: full offline suite passes; no import errors from the deletion.

- [ ] **Step 5: Commit**

```
git add -A
git commit -m "refactor(agents): remove orphaned search_strategies experiment (ADR-039)"
```

---

## Final verification (after all tasks)

- [ ] **Run the full offline suite in the dev container:**
```
docker exec agentic_librarian_app sh -lc 'cd /app && pytest -m "not api_dependent and not slow" -q 2>&1 | tail -5'
```
Expected: all pass; `db_integration` tests run (DB reachable in the container); the
`api_dependent` ETL smoke is deselected.

- [ ] **Push and confirm CI is green** with `db_integration` tests executing (Task 4 Step 3).

- [ ] **Finish the branch** via `superpowers:finishing-a-development-branch` (push + open a
PR for review per the established pattern; do not self-merge).
