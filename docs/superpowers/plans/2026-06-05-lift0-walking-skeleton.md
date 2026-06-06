# Lift 0 — GCP Walking Skeleton Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A real, IAM-gated Cloud Run URL serving the enriched catalog from Cloud SQL Postgres+pgvector, deployed automatically on every merge to `main`.

**Architecture:** Port the FastAPI scaffold from the legacy branch, add `GET /works`, build a slim production image (`Dockerfile.api`), and wire a GitHub Actions pipeline (Workload Identity Federation → Artifact Registry → Cloud Run). GCP infrastructure is provisioned by numbered `gcloud` scripts in `infra/` documented by a runbook; the catalog is restored from the FINAL pg_dump and verified by a checked-in script.

**Tech Stack:** FastAPI + uvicorn, SQLAlchemy 2, Cloud Run, Cloud SQL (Postgres 16 + pgvector), Secret Manager, Artifact Registry, GitHub Actions (`google-github-actions/auth@v2`, `deploy-cloudrun@v2`).

**Spec:** `docs/superpowers/specs/2026-06-05-lift0-walking-skeleton-design.md` (read it first).

---

## Project conventions the engineer must know

- **All tests run inside a throwaway Docker container** (Windows host; PowerShell syntax):
  ```powershell
  # Fast suite (no DB):
  docker run --rm -v "C:\dev\agentic_librarian:/app" -w /app agentic_librarian-app:latest python -m pytest <path> -q -m "not api_dependent and not slow"
  # DB-backed tests (isolated agentic_librarian_test DB, ADR-034):
  docker run --rm -v "C:\dev\agentic_librarian:/app" -w /app --network agentic_librarian_default -e POSTGRES_HOST=db agentic_librarian-app:latest python -m pytest <path> -q
  ```
- **Bare `ruff check` inside the container gives FALSE I001 errors** on `agentic_librarian` imports (editable install makes them first-party). The authoritative lint gate is in-container pre-commit (Task 11). Don't "fix" import ordering that pre-commit doesn't flag.
- After in-container runs, `git status` may show phantom LF/CRLF modifications. `git diff --stat` shows real changes; discard pure line-ending noise on files you didn't touch.
- Commit after every task. Branch: `feat/lift0-walking-skeleton` (already exists, has the spec).
- **Naming constants used consistently across all tasks:**
  | Thing | Value |
  |---|---|
  | GCP project | `agentic-librarian-prod` (overridable via `PROJECT_ID` env) |
  | Region | `us-central1` |
  | Cloud SQL instance | `librarian-sql` (Postgres 16, `db-f1-micro`, 10GB SSD) |
  | DB / DB user | `agentic_librarian` / `librarian` |
  | Secret | `librarian-db-url` (full `DATABASE_URL`, password embedded) |
  | Artifact Registry repo | `librarian` |
  | Image | `us-central1-docker.pkg.dev/$PROJECT_ID/librarian/librarian-api` |
  | GCS bucket | `gs://$PROJECT_ID-backups` |
  | Runtime SA | `librarian-api-runtime@$PROJECT_ID.iam.gserviceaccount.com` |
  | Deployer SA | `github-deployer@$PROJECT_ID.iam.gserviceaccount.com` |
  | WIF pool / provider | `github` / `github-provider` |
  | Cloud Run service | `librarian-api` |
  | GitHub repo Variables (Settings → Secrets and variables → Actions → Variables) | `GCP_PROJECT_ID`, `GCP_WIF_PROVIDER`, `GCP_DEPLOYER_SA`, `GCP_CLOUDSQL_CONNECTION` |

---

### Task 1: Dependencies + port the API scaffold (tests first)

**Files:**
- Modify: `pyproject.toml` (dependencies list, lines 6–37)
- Create: `src/agentic_librarian/api/__init__.py` (empty)
- Create: `src/agentic_librarian/api/main.py`
- Test: `test/unit/test_backend_scaffold.py`, `test/unit/test_api_history.py`

- [ ] **Step 1: Add fastapi + uvicorn to main dependencies**

In `pyproject.toml`, append to the `dependencies` list (after `"psycopg2-binary>=2.9.11"` — add a trailing comma to it):

```toml
    # API surface (Lift 0 walking skeleton — served by uvicorn in the prod image)
    "fastapi>=0.115",
    "uvicorn>=0.34",
    "httpx>=0.27"
```

(`httpx` is required by FastAPI's `TestClient`.)

- [ ] **Step 2: Write the failing tests (ported verbatim from the legacy branch)**

Extract both test files from the kept branch — they are the spec for the port:

```powershell
git -C C:\dev\agentic_librarian show origin/13-phase-4-web-interface-and-analysis:test/unit/test_backend_scaffold.py > test/unit/test_backend_scaffold.py
git -C C:\dev\agentic_librarian show origin/13-phase-4-web-interface-and-analysis:test/unit/test_api_history.py > test/unit/test_api_history.py
```

CAUTION (PowerShell): `>` writes UTF-16. Instead run:

```powershell
git -C C:\dev\agentic_librarian show origin/13-phase-4-web-interface-and-analysis:test/unit/test_backend_scaffold.py | Out-File -Encoding utf8 test/unit/test_backend_scaffold.py
git -C C:\dev\agentic_librarian show origin/13-phase-4-web-interface-and-analysis:test/unit/test_api_history.py | Out-File -Encoding utf8 test/unit/test_api_history.py
```

Do not modify their content — they test `/health`, `/health/db` (mocked session), and `/history` (mocked query chain: empty, with data, null date).

- [ ] **Step 3: Run tests to verify they fail (module missing)**

```powershell
docker run --rm -v "C:\dev\agentic_librarian:/app" -w /app agentic_librarian-app:latest bash -c "pip install -q 'fastapi>=0.115' 'uvicorn>=0.34' 'httpx>=0.27' && python -m pytest test/unit/test_backend_scaffold.py test/unit/test_api_history.py -q"
```

Expected: FAIL/ERROR with `ModuleNotFoundError: No module named 'agentic_librarian.api'`.
(The `pip install` inside the container is needed because the image predates the new deps; it's ephemeral.)

- [ ] **Step 4: Port the implementation**

Create empty `src/agentic_librarian/api/__init__.py`, then `src/agentic_librarian/api/main.py` exactly:

```python
from agentic_librarian.db.models import Edition, ReadingHistory, Work, WorkContributor
from agentic_librarian.db.session import DatabaseManager
from fastapi import FastAPI
from sqlalchemy import text
from sqlalchemy.orm import joinedload

app = FastAPI(title="Agentic Librarian API")
db_manager = DatabaseManager()


@app.get("/health")
def health_check():
    return {"status": "ok"}


@app.get("/health/db")
def db_health_check():
    try:
        with db_manager.get_session() as session:
            session.execute(text("SELECT 1"))
        return {"status": "connected"}
    except Exception as e:
        return {"status": "error", "detail": str(e)}


@app.get("/history")
def get_history():
    with db_manager.get_session() as session:
        # Query reading history with eager loading for efficiency
        history_entries = (
            session.query(ReadingHistory)
            .join(Edition)
            .join(Work)
            .options(
                joinedload(ReadingHistory.edition)
                .joinedload(Edition.work)
                .joinedload(Work.contributors)
                .joinedload(WorkContributor.author)
            )
            .order_by(ReadingHistory.date_completed.desc())
            .all()
        )

        return [
            {
                "id": str(h.id),
                "title": h.edition.work.title,
                "authors": [c.author.name for c in h.edition.work.contributors if c.role == "Author"],
                "date_completed": h.date_completed.isoformat() if h.date_completed else None,
                "rating": h.user_rating,
                "format": h.edition.format,
            }
            for h in history_entries
        ]
```

(Verified: both ETL paths write `role="Author"`, so the contributor filter matches real data. Model/session imports are unchanged on current `main`.)

- [ ] **Step 5: Run tests to verify they pass**

Same command as Step 3. Expected: `5 passed` (or 6 — count all collected in the two files), zero failures.

- [ ] **Step 6: Commit**

```powershell
git add pyproject.toml src/agentic_librarian/api test/unit/test_backend_scaffold.py test/unit/test_api_history.py
git commit -m "feat(api): port FastAPI scaffold from legacy branch (health, history)"
```

---

### Task 2: `DatabaseManager` honors `DATABASE_URL` first (TDD)

**Files:**
- Modify: `src/agentic_librarian/db/session.py:22-55` (`_initialize`)
- Test: `test/unit/test_db_session_url.py` (new)

**Why:** Cloud Run injects one env var, `DATABASE_URL` (the whole connection string from Secret Manager). Today `_initialize` raises on missing `POSTGRES_USER`/`POSTGRES_PASSWORD` *before* it reads `DATABASE_URL`, so the override is unreachable on its own.

- [ ] **Step 1: Write the failing test**

Create `test/unit/test_db_session_url.py`:

```python
"""DATABASE_URL must take priority over component vars (Lift 0: Cloud Run injects only DATABASE_URL)."""

import pytest

from agentic_librarian.db.session import DatabaseManager


def test_database_url_alone_is_sufficient(monkeypatch):
    """With only DATABASE_URL set (no POSTGRES_USER/PASSWORD), the engine builds from it."""
    monkeypatch.delenv("POSTGRES_USER", raising=False)
    monkeypatch.delenv("POSTGRES_PASSWORD", raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql://cloud_user:cloud_pw@/agentic_librarian?host=/cloudsql/proj:region:inst")

    manager = DatabaseManager()
    url = manager.engine.url
    assert url.username == "cloud_user"
    assert url.database == "agentic_librarian"
    assert url.query["host"] == "/cloudsql/proj:region:inst"


def test_database_url_beats_component_vars(monkeypatch):
    """DATABASE_URL wins even when component vars are also present."""
    monkeypatch.setenv("POSTGRES_USER", "componentuser")
    monkeypatch.setenv("POSTGRES_PASSWORD", "componentpw")
    monkeypatch.setenv("POSTGRES_HOST", "componenthost")
    monkeypatch.setenv("DATABASE_URL", "postgresql://urluser:urlpw@urlhost:5432/urldb")

    manager = DatabaseManager()
    assert manager.engine.url.host == "urlhost"
    assert manager.engine.url.username == "urluser"


def test_component_vars_still_work_without_database_url(monkeypatch):
    """Backwards compatibility: the component path is unchanged when DATABASE_URL is absent."""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("POSTGRES_USER", "componentuser")
    monkeypatch.setenv("POSTGRES_PASSWORD", "componentpw")
    monkeypatch.setenv("POSTGRES_HOST", "componenthost")
    monkeypatch.setenv("POSTGRES_PORT", "5433")
    monkeypatch.setenv("POSTGRES_DB", "componentdb")

    manager = DatabaseManager()
    url = manager.engine.url
    assert url.host == "componenthost"
    assert url.port == 5433
    assert url.database == "componentdb"


def test_explicit_db_url_argument_still_wins(monkeypatch):
    """A constructor-passed URL beats everything (existing contract, pinned)."""
    monkeypatch.setenv("DATABASE_URL", "postgresql://env:env@envhost:5432/envdb")
    manager = DatabaseManager(db_url="postgresql://arg:arg@arghost:5432/argdb")
    assert manager.engine.url.host == "arghost"
```

- [ ] **Step 2: Run to verify failure**

```powershell
docker run --rm -v "C:\dev\agentic_librarian:/app" -w /app agentic_librarian-app:latest python -m pytest test/unit/test_db_session_url.py -q
```

Expected: `test_database_url_alone_is_sufficient` FAILS with `ValueError: Database credentials not found...` (raised before `DATABASE_URL` is consulted). The other three should pass (they pin existing behavior).

- [ ] **Step 3: Implement the reorder**

In `src/agentic_librarian/db/session.py`, replace the body of `_initialize` between `db_url = self._db_url` and the `connect_args` block with:

```python
        db_url = self._db_url

        # A full DATABASE_URL (e.g. Cloud Run injecting the Secret Manager connection
        # string) takes priority over component vars — checked BEFORE demanding
        # POSTGRES_USER/PASSWORD, which are only required for component-wise construction.
        if db_url is None:
            db_url = os.getenv("DATABASE_URL")

        if db_url is None:
            # Check for individual environment variables
            user = os.getenv("POSTGRES_USER")
            password = os.getenv("POSTGRES_PASSWORD")

            # Prompt for missing credentials if in an interactive terminal
            if (not user or not password) and sys.stdin.isatty():
                print("\nMissing database credentials.")
                if not user:
                    user = input("Enter Postgres username: ")
                if not password:
                    password = getpass("Enter Postgres password: ")

            # Error if still missing and not interactive
            if not user or not password:
                raise ValueError(
                    "Database credentials not found. Please set DATABASE_URL, or "
                    "POSTGRES_USER and POSTGRES_PASSWORD, in your environment or .env file."
                )

            host = os.getenv("POSTGRES_HOST", "localhost")
            port = os.getenv("POSTGRES_PORT", "5432")
            db_name = os.getenv("POSTGRES_DB", "agentic_librarian")
            db_url = f"postgresql://{user}:{password}@{host}:{port}/{db_name}"
```

(The old inner `db_url = os.getenv("DATABASE_URL")` re-check disappears — it's now the first check.)

- [ ] **Step 4: Run the new tests AND the existing db session integration test**

```powershell
docker run --rm -v "C:\dev\agentic_librarian:/app" -w /app agentic_librarian-app:latest python -m pytest test/unit/test_db_session_url.py -q
docker run --rm -v "C:\dev\agentic_librarian:/app" -w /app --network agentic_librarian_default -e POSTGRES_HOST=db agentic_librarian-app:latest python -m pytest test/integration/test_db_session.py -q
```

Expected: all pass. CAUTION: if the dev environment's `.env`/compose sets a `DATABASE_URL` env var anywhere, the new priority could redirect dev DB access — check with `git grep DATABASE_URL -- .env.example docker-compose.yml` (it should appear nowhere; if it does, flag to the controller instead of proceeding).

- [ ] **Step 5: Commit**

```powershell
git add src/agentic_librarian/db/session.py test/unit/test_db_session_url.py
git commit -m "fix(db): DATABASE_URL takes priority over component vars (Cloud Run secret injection)"
```

---

### Task 3: `GET /works` — unit tests + implementation (TDD)

**Files:**
- Modify: `src/agentic_librarian/api/main.py`
- Test: `test/unit/test_api_works.py` (new)

- [ ] **Step 1: Write the failing tests**

Create `test/unit/test_api_works.py`:

```python
from unittest.mock import MagicMock, patch
from uuid import uuid4

from fastapi.testclient import TestClient

from agentic_librarian.api.main import app

client = TestClient(app)


def _mock_chain(mock_session, results):
    """Wire the query().options().order_by().offset().limit().all() chain."""
    mock_query = mock_session.query.return_value
    mock_query.options.return_value = mock_query
    mock_query.order_by.return_value = mock_query
    mock_query.offset.return_value = mock_query
    mock_query.limit.return_value = mock_query
    mock_query.all.return_value = results
    return mock_query


def _mock_work():
    work = MagicMock()
    work.id = uuid4()
    work.title = "Dune"
    work.original_publication_year = 1965
    work.genres = ["Science Fiction"]
    work.moods = ["epic"]
    contributor = MagicMock()
    contributor.role = "Author"
    contributor.author.name = "Frank Herbert"
    work.contributors = [contributor]
    work_trope = MagicMock()
    work_trope.trope.name = "Chosen One"
    work.tropes = [work_trope]
    work_style = MagicMock()
    work_style.attribute_type = "perspective"
    work_style.style.name = "Third Person Limited"
    work.styles = [work_style]
    return work


def test_get_works_empty():
    with patch("agentic_librarian.api.main.db_manager") as mock_db:
        mock_session = MagicMock()
        mock_db.get_session.return_value.__enter__.return_value = mock_session
        _mock_chain(mock_session, [])

        response = client.get("/works")
        assert response.status_code == 200
        assert response.json() == []


def test_get_works_shape():
    with patch("agentic_librarian.api.main.db_manager") as mock_db:
        mock_session = MagicMock()
        mock_db.get_session.return_value.__enter__.return_value = mock_session
        work = _mock_work()
        _mock_chain(mock_session, [work])

        response = client.get("/works")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        entry = data[0]
        assert entry["id"] == str(work.id)
        assert entry["title"] == "Dune"
        assert entry["authors"] == ["Frank Herbert"]
        assert entry["publication_year"] == 1965
        assert entry["genres"] == ["Science Fiction"]
        assert entry["moods"] == ["epic"]
        assert entry["tropes"] == ["Chosen One"]
        assert entry["styles"] == [{"attribute": "perspective", "name": "Third Person Limited"}]


def test_get_works_null_arrays_become_empty_lists():
    with patch("agentic_librarian.api.main.db_manager") as mock_db:
        mock_session = MagicMock()
        mock_db.get_session.return_value.__enter__.return_value = mock_session
        work = _mock_work()
        work.genres = None
        work.moods = None
        _mock_chain(mock_session, [work])

        response = client.get("/works")
        entry = response.json()[0]
        assert entry["genres"] == []
        assert entry["moods"] == []


def test_get_works_pagination_params_forwarded():
    with patch("agentic_librarian.api.main.db_manager") as mock_db:
        mock_session = MagicMock()
        mock_db.get_session.return_value.__enter__.return_value = mock_session
        mock_query = _mock_chain(mock_session, [])

        response = client.get("/works?limit=10&offset=20")
        assert response.status_code == 200
        mock_query.offset.assert_called_once_with(20)
        mock_query.limit.assert_called_once_with(10)


def test_get_works_limit_cap_enforced():
    # limit above 200 and below 1, and negative offset, are rejected by validation
    assert client.get("/works?limit=500").status_code == 422
    assert client.get("/works?limit=0").status_code == 422
    assert client.get("/works?offset=-1").status_code == 422
```

- [ ] **Step 2: Run to verify failure**

```powershell
docker run --rm -v "C:\dev\agentic_librarian:/app" -w /app agentic_librarian-app:latest python -m pytest test/unit/test_api_works.py -q
```

Expected: FAIL — `/works` returns 404 (route doesn't exist).

- [ ] **Step 3: Implement `GET /works`**

In `src/agentic_librarian/api/main.py`:

Update imports:

```python
from agentic_librarian.db.models import (
    Edition,
    ReadingHistory,
    Work,
    WorkContributor,
    WorkStyle,
    WorkTrope,
)
from agentic_librarian.db.session import DatabaseManager
from fastapi import FastAPI, Query
from sqlalchemy import text
from sqlalchemy.orm import joinedload, selectinload
```

Append the endpoint:

```python
@app.get("/works")
def get_works(limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)):
    """Enriched catalog listing — the walking skeleton's payload (Lift 0)."""
    with db_manager.get_session() as session:
        # selectinload for the collections: joinedload + LIMIT mis-paginates
        # (the limit would apply to joined rows, not works).
        works = (
            session.query(Work)
            .options(
                selectinload(Work.contributors).joinedload(WorkContributor.author),
                selectinload(Work.tropes).joinedload(WorkTrope.trope),
                selectinload(Work.styles).joinedload(WorkStyle.style),
            )
            .order_by(Work.title)
            .offset(offset)
            .limit(limit)
            .all()
        )

        return [
            {
                "id": str(w.id),
                "title": w.title,
                "authors": [c.author.name for c in w.contributors if c.role == "Author"],
                "publication_year": w.original_publication_year,
                "genres": w.genres or [],
                "moods": w.moods or [],
                "tropes": [wt.trope.name for wt in w.tropes],
                "styles": [{"attribute": ws.attribute_type, "name": ws.style.name} for ws in w.styles],
            }
            for w in works
        ]
```

- [ ] **Step 4: Run all API unit tests**

```powershell
docker run --rm -v "C:\dev\agentic_librarian:/app" -w /app agentic_librarian-app:latest python -m pytest test/unit/test_api_works.py test/unit/test_api_history.py test/unit/test_backend_scaffold.py -q
```

Expected: all pass.

- [ ] **Step 5: Commit**

```powershell
git add src/agentic_librarian/api/main.py test/unit/test_api_works.py
git commit -m "feat(api): GET /works — enriched catalog listing with pagination"
```

---

### Task 4: `GET /works` integration test against the real schema

**Files:**
- Test: `test/integration/test_api_works_db.py` (new)

**Why:** the unit tests mock the query chain; this proves the eager-loading actually works against real tables (the isolated `agentic_librarian_test` DB from `test/conftest.py`'s session fixtures — see `test/integration/test_db_session.py` for the `db_url` fixture pattern).

- [ ] **Step 1: Write the failing-by-construction test** (it passes only if the endpoint queries correctly)

Create `test/integration/test_api_works_db.py`:

```python
"""GET /works against the real schema in the isolated test DB (ADR-034)."""

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from agentic_librarian.api.main import app
from agentic_librarian.db.models import Author, Style, Trope, Work, WorkContributor, WorkStyle, WorkTrope
from agentic_librarian.db.session import DatabaseManager


@pytest.mark.db_integration
def test_get_works_end_to_end(db_url):
    manager = DatabaseManager(db_url)
    created = {}
    with manager.get_session() as session:
        author = Author(name="Lift Zero Author")
        trope = Trope(name="Lift Zero Trope")
        style = Style(name="Lift Zero Style", category="Work")
        work_a = Work(title="AAA Lift Zero First", original_publication_year=2001, genres=["Test"], moods=None)
        work_b = Work(title="ZZZ Lift Zero Last")
        work_a.contributors.append(WorkContributor(author=author, role="Author"))
        work_a.tropes.append(WorkTrope(trope=trope, relevance_score=1.0))
        work_a.styles.append(WorkStyle(style=style, attribute_type="perspective"))
        session.add_all([work_a, work_b])
        session.flush()
        created["a"], created["b"] = str(work_a.id), str(work_b.id)

    try:
        with patch("agentic_librarian.api.main.db_manager", manager):
            client = TestClient(app)
            data = client.get("/works", params={"limit": 200}).json()
            by_id = {entry["id"]: entry for entry in data}

            assert created["a"] in by_id and created["b"] in by_id
            entry = by_id[created["a"]]
            assert entry["title"] == "AAA Lift Zero First"
            assert entry["authors"] == ["Lift Zero Author"]
            assert entry["publication_year"] == 2001
            assert entry["genres"] == ["Test"]
            assert entry["moods"] == []
            assert entry["tropes"] == ["Lift Zero Trope"]
            assert entry["styles"] == [{"attribute": "perspective", "name": "Lift Zero Style"}]

            # Ordering: AAA... before ZZZ... in the returned page
            ids_in_order = [e["id"] for e in data]
            assert ids_in_order.index(created["a"]) < ids_in_order.index(created["b"])

            # Pagination: limit=1 returns exactly one row
            assert len(client.get("/works", params={"limit": 1}).json()) == 1
    finally:
        with manager.get_session() as session:
            for model in (WorkStyle, WorkTrope, WorkContributor):
                session.query(model).filter(model.work_id.in_(list(created.values()))).delete(synchronize_session=False)
            session.query(Work).filter(Work.id.in_(list(created.values()))).delete(synchronize_session=False)
            session.query(Author).filter(Author.name == "Lift Zero Author").delete(synchronize_session=False)
            session.query(Trope).filter(Trope.name == "Lift Zero Trope").delete(synchronize_session=False)
            session.query(Style).filter(Style.name == "Lift Zero Style").delete(synchronize_session=False)
```

- [ ] **Step 2: Run it (DB-backed)**

```powershell
docker run --rm -v "C:\dev\agentic_librarian:/app" -w /app --network agentic_librarian_default -e POSTGRES_HOST=db agentic_librarian-app:latest python -m pytest test/integration/test_api_works_db.py -q
```

Expected: PASS. If it fails, the failure is real information about the eager-loading — investigate, don't weaken the test. (Note: `Author`/`Trope`/`Style` import names and `WorkTrope.relevance_score` verified against `src/agentic_librarian/db/models.py`. The `db_url` fixture and schema creation come from `test/conftest.py` automatically.)

- [ ] **Step 3: Commit**

```powershell
git add test/integration/test_api_works_db.py
git commit -m "test(api): GET /works integration test against real schema"
```

---

### Task 5: Production image — `Dockerfile.api`

**Files:**
- Create: `Dockerfile.api`
- Modify: `.dockerignore` (verify/extend)

- [ ] **Step 1: Check `.dockerignore` covers heavy/dev paths**

Read `.dockerignore`. Ensure these entries exist (append any missing):

```
.git
data/
mlruns/
.chat_logs/
docs/
test/
conductor/
*.egg-info
__pycache__/
```

(`data/backups/` alone is 25MB of dumps; none of it belongs in an image build context.)

- [ ] **Step 2: Create `Dockerfile.api`**

```dockerfile
# Production API image (Lift 0). The dev image is ./Dockerfile — this one is slim:
# no build tools, no Node/Claude CLI, no sudo, non-editable install, uvicorn entrypoint.
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN useradd --create-home appuser
WORKDIR /app

# Non-editable install of the package + prod deps only (no [dev] or [claude] extras).
# NOTE: copying src before install means dependency layers rebuild on code changes;
# accepted for the skeleton (CI builds, ~minutes) — revisit if it starts to hurt.
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir .

USER appuser
EXPOSE 8080

# Cloud Run injects PORT (default 8080). Shell form for the env expansion; exec for signals.
CMD exec uvicorn agentic_librarian.api.main:app --host 0.0.0.0 --port ${PORT:-8080}
```

- [ ] **Step 3: Build and smoke-test locally**

```powershell
docker build -f Dockerfile.api -t librarian-api:local C:\dev\agentic_librarian
docker run --rm -d -p 8080:8080 -e DATABASE_URL=postgresql://x:x@nohost:5432/x --name api-smoke librarian-api:local
Start-Sleep -Seconds 5
curl.exe -fsS http://localhost:8080/health
curl.exe -sS http://localhost:8080/health/db
docker rm -f api-smoke
```

Expected: `/health` → `{"status":"ok"}`; `/health/db` → `{"status":"error","detail":...}` (graceful — DB is fake, the endpoint must not 500/crash). The build itself proves prod deps suffice to import the app.

- [ ] **Step 4: Commit**

```powershell
git add Dockerfile.api .dockerignore
git commit -m "feat(deploy): production API image (slim, non-root, uvicorn)"
```

---

### Task 6: CD workflow — `.github/workflows/deploy.yml`

**Files:**
- Create: `.github/workflows/deploy.yml`

**Context:** mirrors `lint.yml`'s test setup (Postgres service container, same pytest markers). Uses the four GitHub repo **Variables** (`vars.*` — set in Task 12; the workflow merges before they exist, which is fine: it only runs on pushes to `main` touching the path filter, and `workflow_dispatch` waits until we're ready).

- [ ] **Step 1: Create the workflow**

```yaml
name: Deploy to Cloud Run

on:
  push:
    branches: [ "main" ]
    paths:
      - "src/**"
      - "pyproject.toml"
      - "Dockerfile.api"
      - ".github/workflows/deploy.yml"
  workflow_dispatch:

concurrency:
  group: deploy-prod
  cancel-in-progress: false

jobs:
  deploy:
    runs-on: ubuntu-latest
    permissions:
      contents: read
      id-token: write   # WIF: exchange the GitHub OIDC token for GCP credentials
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
    env:
      IMAGE: us-central1-docker.pkg.dev/${{ vars.GCP_PROJECT_ID }}/librarian/librarian-api:${{ github.sha }}
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
          pip install ".[dev,claude]"

      - name: Run tests (same gate PRs face)
        env:
          POSTGRES_HOST: localhost
          POSTGRES_PORT: 5432
          POSTGRES_USER: librarian
          POSTGRES_PASSWORD: librarian_secret_password
          POSTGRES_DB: agentic_librarian
          GOOGLE_SEARCH_API_KEY: dummy-key-for-construction
        run: |
          pytest -m "not api_dependent and not slow"

      - name: Build production image
        run: docker build -f Dockerfile.api -t "$IMAGE" .

      - name: Smoke-test image in runner (broken images never reach the registry)
        run: |
          docker run --rm -d -p 8080:8080 -e DATABASE_URL=postgresql://x:x@nohost:5432/x --name api-smoke "$IMAGE"
          sleep 5
          curl -fsS http://localhost:8080/health
          docker rm -f api-smoke

      - id: auth
        name: Authenticate to GCP (Workload Identity Federation)
        uses: google-github-actions/auth@v2
        with:
          workload_identity_provider: ${{ vars.GCP_WIF_PROVIDER }}
          service_account: ${{ vars.GCP_DEPLOYER_SA }}

      - name: Set up gcloud
        uses: google-github-actions/setup-gcloud@v2

      - name: Push image
        run: |
          gcloud auth configure-docker us-central1-docker.pkg.dev --quiet
          docker push "$IMAGE"

      - id: deploy
        name: Deploy to Cloud Run
        uses: google-github-actions/deploy-cloudrun@v2
        with:
          service: librarian-api
          region: us-central1
          image: us-central1-docker.pkg.dev/${{ vars.GCP_PROJECT_ID }}/librarian/librarian-api:${{ github.sha }}
          flags: >-
            --no-allow-unauthenticated
            --service-account=librarian-api-runtime@${{ vars.GCP_PROJECT_ID }}.iam.gserviceaccount.com
            --add-cloudsql-instances=${{ vars.GCP_CLOUDSQL_CONNECTION }}
            --set-secrets=DATABASE_URL=librarian-db-url:latest
            --max-instances=1
            --memory=512Mi

      - id: smoke-token
        name: Mint identity token for the service
        uses: google-github-actions/auth@v2
        with:
          workload_identity_provider: ${{ vars.GCP_WIF_PROVIDER }}
          service_account: ${{ vars.GCP_DEPLOYER_SA }}
          token_format: id_token
          id_token_audience: ${{ steps.deploy.outputs.url }}
          id_token_include_email: true

      - name: Live smoke test (/health and /health/db)
        run: |
          URL="${{ steps.deploy.outputs.url }}"
          TOKEN="${{ steps.smoke-token.outputs.id_token }}"
          curl -fsS -H "Authorization: Bearer ${TOKEN}" "${URL}/health"
          BODY="$(curl -fsS -H "Authorization: Bearer ${TOKEN}" "${URL}/health/db")"
          echo "${BODY}"
          echo "${BODY}" | grep -q '"status":"connected"' || { echo "DB health check failed"; exit 1; }
```

- [ ] **Step 2: Validate YAML locally**

```powershell
docker run --rm -v "C:\dev\agentic_librarian:/app" -w /app agentic_librarian-app:latest python -c "import yaml; yaml.safe_load(open('.github/workflows/deploy.yml')); print('YAML OK')"
```

Expected: `YAML OK`.

- [ ] **Step 3: Commit**

```powershell
git add .github/workflows/deploy.yml
git commit -m "feat(deploy): CD workflow — test, build, push, deploy, smoke (WIF keyless)"
```

---

### Task 7: Provisioning scripts — `infra/`

**Files:**
- Create: `infra/00-config.sh`, `infra/01-project.sh`, `infra/02-cloudsql.sh`, `infra/03-db-user-secret.sh`, `infra/04-registry-bucket.sh`, `infra/05-iam-wif.sh`, `infra/06-restore.sh`, `infra/07-budget.sh`

These are bash scripts run from WSL (where `gcloud` will be installed — runbook Task 9 covers setup). They are reviewed-in-PR documentation as much as automation; each is safe to re-run (`|| true` on already-exists errors where noted). No TDD — they're verified live in Task 12.

- [ ] **Step 1: Create `infra/00-config.sh`** (sourced by all others)

```bash
#!/usr/bin/env bash
# Shared configuration for all Lift 0 provisioning scripts. Source me: `source 00-config.sh`
set -euo pipefail

export PROJECT_ID="${PROJECT_ID:-agentic-librarian-prod}"
export REGION="${REGION:-us-central1}"
export SQL_INSTANCE="librarian-sql"
export DB_NAME="agentic_librarian"
export DB_USER="librarian"
export SECRET_NAME="librarian-db-url"
export AR_REPO="librarian"
export BUCKET="gs://${PROJECT_ID}-backups"
export RUNTIME_SA_NAME="librarian-api-runtime"
export DEPLOYER_SA_NAME="github-deployer"
export RUNTIME_SA="${RUNTIME_SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
export DEPLOYER_SA="${DEPLOYER_SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
export SERVICE="librarian-api"
export GITHUB_REPO="jaydee829/agentic_librarian"
export DUMP_FILE="agentic_librarian_FINAL_20260605_014912.sql.gz"
```

- [ ] **Step 2: Create `infra/01-project.sh`**

```bash
#!/usr/bin/env bash
# Create the project, link billing, enable the needed APIs.
# Requires: BILLING_ACCOUNT_ID env var (find yours: `gcloud billing accounts list`).
set -euo pipefail
source "$(dirname "$0")/00-config.sh"
: "${BILLING_ACCOUNT_ID:?Set BILLING_ACCOUNT_ID (see: gcloud billing accounts list)}"

# Project IDs are globally unique; if taken, override with PROJECT_ID=... before running.
gcloud projects create "${PROJECT_ID}" || echo "Project may already exist — continuing."
gcloud billing projects link "${PROJECT_ID}" --billing-account="${BILLING_ACCOUNT_ID}"
gcloud config set project "${PROJECT_ID}"

gcloud services enable \
  run.googleapis.com \
  sqladmin.googleapis.com \
  secretmanager.googleapis.com \
  artifactregistry.googleapis.com \
  iamcredentials.googleapis.com \
  billingbudgets.googleapis.com \
  storage.googleapis.com

echo "Project ${PROJECT_ID} ready."
```

- [ ] **Step 3: Create `infra/02-cloudsql.sh`**

```bash
#!/usr/bin/env bash
# Cloud SQL: Postgres 16, smallest shared-core tier (~$12/mo), 10GB SSD.
# Takes ~10 minutes — go make coffee.
set -euo pipefail
source "$(dirname "$0")/00-config.sh"

gcloud sql instances create "${SQL_INSTANCE}" \
  --database-version=POSTGRES_16 \
  --edition=enterprise \
  --tier=db-f1-micro \
  --region="${REGION}" \
  --storage-size=10GB \
  --storage-type=SSD

gcloud sql databases create "${DB_NAME}" --instance="${SQL_INSTANCE}"

echo "Connection name (needed for the GCP_CLOUDSQL_CONNECTION GitHub variable):"
gcloud sql instances describe "${SQL_INSTANCE}" --format='value(connectionName)'
```

- [ ] **Step 4: Create `infra/03-db-user-secret.sh`**

```bash
#!/usr/bin/env bash
# Create the app DB user with a generated password, and store the FULL connection
# string in Secret Manager (Cloud Run --set-secrets injects it verbatim as DATABASE_URL).
# The password never touches disk or the shell history beyond this process.
set -euo pipefail
source "$(dirname "$0")/00-config.sh"

DB_PASSWORD="$(openssl rand -base64 24 | tr -d '/+=')"
gcloud sql users create "${DB_USER}" --instance="${SQL_INSTANCE}" --password="${DB_PASSWORD}"

CONNECTION_NAME="$(gcloud sql instances describe "${SQL_INSTANCE}" --format='value(connectionName)')"
DATABASE_URL="postgresql://${DB_USER}:${DB_PASSWORD}@/${DB_NAME}?host=/cloudsql/${CONNECTION_NAME}"

printf '%s' "${DATABASE_URL}" | gcloud secrets create "${SECRET_NAME}" --data-file=-

echo "Secret ${SECRET_NAME} created. The password exists ONLY inside it."
```

- [ ] **Step 5: Create `infra/04-registry-bucket.sh`**

```bash
#!/usr/bin/env bash
# Artifact Registry (images) and the backups bucket (pg_dump staging for import).
set -euo pipefail
source "$(dirname "$0")/00-config.sh"

gcloud artifacts repositories create "${AR_REPO}" \
  --repository-format=docker \
  --location="${REGION}" \
  --description="librarian-api images (tags = git SHAs)"

gcloud storage buckets create "${BUCKET}" \
  --location="${REGION}" \
  --uniform-bucket-level-access
```

- [ ] **Step 6: Create `infra/05-iam-wif.sh`**

```bash
#!/usr/bin/env bash
# Service accounts (least privilege) + Workload Identity Federation for GitHub Actions.
#   runtime SA:  what the Cloud Run service runs as — secret accessor + SQL client ONLY.
#   deployer SA: what CI impersonates — push images + deploy + invoke (smoke test).
#                It can NOT read secrets or the DB.
set -euo pipefail
source "$(dirname "$0")/00-config.sh"
PROJECT_NUMBER="$(gcloud projects describe "${PROJECT_ID}" --format='value(projectNumber)')"

gcloud iam service-accounts create "${RUNTIME_SA_NAME}" --display-name="librarian-api runtime"
gcloud iam service-accounts create "${DEPLOYER_SA_NAME}" --display-name="GitHub Actions deployer"

# Runtime: read the one secret + connect to Cloud SQL.
gcloud secrets add-iam-policy-binding "${SECRET_NAME}" \
  --member="serviceAccount:${RUNTIME_SA}" --role="roles/secretmanager.secretAccessor"
gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
  --member="serviceAccount:${RUNTIME_SA}" --role="roles/cloudsql.client"

# Deployer: push images, deploy the service, act-as the runtime SA, invoke for smoke tests.
gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
  --member="serviceAccount:${DEPLOYER_SA}" --role="roles/artifactregistry.writer"
gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
  --member="serviceAccount:${DEPLOYER_SA}" --role="roles/run.admin"
gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
  --member="serviceAccount:${DEPLOYER_SA}" --role="roles/run.invoker"
gcloud iam service-accounts add-iam-policy-binding "${RUNTIME_SA}" \
  --member="serviceAccount:${DEPLOYER_SA}" --role="roles/iam.serviceAccountUser"

# WIF: trust GitHub's OIDC issuer, pinned to exactly our repo.
gcloud iam workload-identity-pools create github --location=global --display-name="GitHub Actions"
gcloud iam workload-identity-pools providers create-oidc github-provider \
  --location=global \
  --workload-identity-pool=github \
  --display-name="GitHub OIDC" \
  --issuer-uri="https://token.actions.githubusercontent.com" \
  --attribute-mapping="google.subject=assertion.sub,attribute.repository=assertion.repository" \
  --attribute-condition="assertion.repository=='${GITHUB_REPO}'"

gcloud iam service-accounts add-iam-policy-binding "${DEPLOYER_SA}" \
  --role="roles/iam.workloadIdentityUser" \
  --member="principalSet://iam.googleapis.com/projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/github/attribute.repository/${GITHUB_REPO}"

CONNECTION_NAME="$(gcloud sql instances describe "${SQL_INSTANCE}" --format='value(connectionName)')"
echo ""
echo "=== Set these four GitHub repo VARIABLES (Settings > Secrets and variables > Actions > Variables) ==="
echo "GCP_PROJECT_ID=${PROJECT_ID}"
echo "GCP_WIF_PROVIDER=projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/github/providers/github-provider"
echo "GCP_DEPLOYER_SA=${DEPLOYER_SA}"
echo "GCP_CLOUDSQL_CONNECTION=${CONNECTION_NAME}"
```

- [ ] **Step 7: Create `infra/06-restore.sh`**

```bash
#!/usr/bin/env bash
# Upload the FINAL pg_dump and import it into Cloud SQL.
# PRE-FLIGHT (runbook): inspect the dump and ensure the vector extension exists —
#   zcat data/backups/${DUMP_FILE} | head -100
#   If 'CREATE EXTENSION ... vector' is NOT in the dump, create it first via:
#   gcloud sql connect librarian-sql --user=postgres --database=agentic_librarian
#   then: CREATE EXTENSION IF NOT EXISTS vector;
# The 'librarian' role must already exist (03-db-user-secret.sh) — the dump's
# ALTER ... OWNER TO librarian statements fail without it.
set -euo pipefail
source "$(dirname "$0")/00-config.sh"

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
gcloud storage cp "${REPO_ROOT}/data/backups/${DUMP_FILE}" "${BUCKET}/"

# Cloud SQL imports run as the instance's own service agent — it needs to read the bucket.
SQL_SA="$(gcloud sql instances describe "${SQL_INSTANCE}" --format='value(serviceAccountEmailAddress)')"
gcloud storage buckets add-iam-policy-binding "${BUCKET}" \
  --member="serviceAccount:${SQL_SA}" --role="roles/storage.objectViewer"

gcloud sql import sql "${SQL_INSTANCE}" "${BUCKET}/${DUMP_FILE}" --database="${DB_NAME}" --quiet

echo "Import complete. Now run the verification: see infra/verify_restore.py and the runbook."
```

- [ ] **Step 8: Create `infra/07-budget.sh`**

```bash
#!/usr/bin/env bash
# $25/month budget with email alerts at 50% / 90% / 100% (warns billing admins; never blocks).
# Requires: BILLING_ACCOUNT_ID env var.
set -euo pipefail
source "$(dirname "$0")/00-config.sh"
: "${BILLING_ACCOUNT_ID:?Set BILLING_ACCOUNT_ID (see: gcloud billing accounts list)}"
PROJECT_NUMBER="$(gcloud projects describe "${PROJECT_ID}" --format='value(projectNumber)')"

gcloud billing budgets create \
  --billing-account="${BILLING_ACCOUNT_ID}" \
  --display-name="${PROJECT_ID}-monthly" \
  --budget-amount=25USD \
  --filter-projects="projects/${PROJECT_NUMBER}" \
  --threshold-rule=percent=0.5 \
  --threshold-rule=percent=0.9 \
  --threshold-rule=percent=1.0
```

- [ ] **Step 9: Commit**

```powershell
git add infra/
git commit -m "feat(infra): numbered gcloud provisioning scripts (project, SQL, secrets, WIF, restore, budget)"
```

---

### Task 8: Restore verification — `infra/verify_restore.py`

**Files:**
- Create: `infra/verify_restore.py`

Runs anywhere a `DATABASE_URL` env var can reach the DB (in practice: through the Cloud SQL Auth Proxy — runbook covers it). Plain script with explicit assertions and a non-zero exit on any failure; no pytest (it's an ops check, not a test-suite member).

- [ ] **Step 1: Create the script**

```python
"""Verify the Cloud SQL restore matches the known 2026-06-05 production build.

Usage (runbook §7):  DATABASE_URL=postgresql://... python infra/verify_restore.py
Exits non-zero on the first failed check.
"""

import os
import sys

from sqlalchemy import create_engine, text

EXPECTED_ROW_COUNTS = {
    "works": 326,
    "editions": 335,
    "reading_history": 331,
    "authors": 230,
}

failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    status = "OK " if ok else "FAIL"
    print(f"[{status}] {label}" + (f" — {detail}" if detail else ""))
    if not ok:
        failures.append(label)


def main() -> int:
    url = os.environ.get("DATABASE_URL")
    if not url:
        print("Set DATABASE_URL (see runbook §7 for the Cloud SQL Auth Proxy invocation).")
        return 2

    engine = create_engine(url)
    with engine.connect() as conn:
        # 1. Known row counts from the completed production build (2026-06-05).
        for table, expected in EXPECTED_ROW_COUNTS.items():
            actual = conn.execute(text(f"SELECT count(*) FROM {table}")).scalar()  # noqa: S608 - fixed table names
            check(f"{table} row count", actual == expected, f"expected {expected}, got {actual}")

        # 2. pgvector extension present.
        ext = conn.execute(text("SELECT count(*) FROM pg_extension WHERE extname = 'vector'")).scalar()
        check("pgvector extension installed", ext == 1)

        # 3. Embeddings fully populated (the build's quality gate embedded every trope/style).
        for table in ("tropes", "styles"):
            total = conn.execute(text(f"SELECT count(*) FROM {table}")).scalar()  # noqa: S608
            nulls = conn.execute(text(f"SELECT count(*) FROM {table} WHERE embedding IS NULL")).scalar()  # noqa: S608
            check(f"{table} embeddings populated", total > 0 and nulls == 0, f"{total} rows, {nulls} NULL embeddings")

        # 4. Similarity search actually works (operator + data, not just bytes).
        rows = conn.execute(
            text(
                "SELECT t2.name, t1.embedding <=> t2.embedding AS dist "
                "FROM tropes t1, tropes t2 WHERE t1.id != t2.id "
                "AND t1.id = (SELECT id FROM tropes WHERE embedding IS NOT NULL LIMIT 1) "
                "ORDER BY dist ASC LIMIT 3"
            )
        ).fetchall()
        dists = [r.dist for r in rows]
        check(
            "similarity query returns ordered results",
            len(dists) == 3 and dists == sorted(dists) and all(0 <= d <= 2 for d in dists),
            f"top-3 distances: {dists}",
        )

    if failures:
        print(f"\n{len(failures)} check(s) FAILED: {failures}")
        return 1
    print("\nAll restore checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Sanity-run against the LOCAL dev DB** (same data the dump came from — all checks should pass)

```powershell
docker run --rm -v "C:\dev\agentic_librarian:/app" -w /app --network agentic_librarian_default agentic_librarian-app:latest bash -c "DATABASE_URL=postgresql://\$POSTGRES_USER:\$POSTGRES_PASSWORD@db:5432/agentic_librarian python infra/verify_restore.py"
```

NOTE: the container needs `POSTGRES_USER`/`POSTGRES_PASSWORD` — add `--env-file C:\dev\agentic_librarian\.env` to the `docker run`. Expected: `All restore checks passed.` against the dev DB. If a count differs (e.g., a book was added via `librarian add` since the dump), report the discrepancy to the controller — the EXPECTED_ROW_COUNTS pin the DUMP's contents, so verify against what the dump should contain, not necessarily the live dev DB. A clean pass here validates the script's logic.

- [ ] **Step 3: Commit**

```powershell
git add infra/verify_restore.py
git commit -m "feat(infra): restore verification script (row counts, pgvector, similarity)"
```

---

### Task 9: Runbook — `docs/runbooks/gcp-walking-skeleton.md`

**Files:**
- Create: `docs/runbooks/gcp-walking-skeleton.md`

- [ ] **Step 1: Write the runbook**

Write the full document with these sections (each section explains WHAT the step creates, WHY it exists, and WHAT IT COSTS — the reader is six-months-future-us, not a GCP expert). Include the exact commands shown:

```markdown
# Runbook: GCP Walking Skeleton (Lift 0)

One-time provisioning + restore for the production environment. Scripts live in
`infra/`; run them from WSL, in order. Total cost when done: ~$12-16/month
(Cloud SQL is the floor; everything else is cents).

## 0. Prerequisites (one-time tooling)

- Install gcloud in WSL: https://cloud.google.com/sdk/docs/install#deb
  (apt repo method), then `gcloud init` / `gcloud auth login` (browser flow).
- Find your billing account: `gcloud billing accounts list` →
  `export BILLING_ACCOUNT_ID=XXXXXX-XXXXXX-XXXXXX`

## 1. Project + APIs — `infra/01-project.sh`
Creates the `agentic-librarian-prod` project (override with `PROJECT_ID=...` if the
global ID is taken), links billing, enables the seven APIs. $0.

## 2. Cloud SQL — `infra/02-cloudsql.sh`
Postgres 16, db-f1-micro (1 shared vCPU), 10GB SSD, us-central1. ~$12/mo, the bill's
floor. Takes ~10 minutes. Prints the connection name — note it for §5.

## 3. DB user + secret — `infra/03-db-user-secret.sh`
Generates the `librarian` DB password and stores the FULL DATABASE_URL in Secret
Manager (`librarian-db-url`). The password exists only inside the secret. Cents.

## 4. Registry + bucket — `infra/04-registry-bucket.sh`
Artifact Registry repo (images, tags = git SHAs) + `gs://<project>-backups`. Cents.

## 5. IAM + WIF — `infra/05-iam-wif.sh`
Two service accounts (runtime: secret+SQL only; deployer: push+deploy+invoke only)
and the Workload Identity Federation pool pinned to jaydee829/agentic_librarian.
Prints four values → set them as GitHub repo VARIABLES (repo Settings → Secrets and
variables → Actions → Variables tab): GCP_PROJECT_ID, GCP_WIF_PROVIDER,
GCP_DEPLOYER_SA, GCP_CLOUDSQL_CONNECTION. $0.

## 6. Restore — `infra/06-restore.sh`
Pre-flight: `zcat data/backups/agentic_librarian_FINAL_20260605_014912.sql.gz | head -100`
— confirm `CREATE EXTENSION` for vector appears; if not, connect
(`gcloud sql connect librarian-sql --user=postgres --database=agentic_librarian`,
set the postgres password first if prompted: `gcloud sql users set-password postgres
--instance=librarian-sql --prompt-for-password`) and run
`CREATE EXTENSION IF NOT EXISTS vector;`. Then the script uploads the dump, grants
the SQL service agent read on the bucket, and imports. If the import fails partway:
`gcloud sql databases delete agentic_librarian --instance=librarian-sql`, recreate it
(`gcloud sql databases create ...`), fix, re-run — nothing depends on this DB until
verification passes.

## 7. Verify — `infra/verify_restore.py`
Run through the Cloud SQL Auth Proxy (the local stand-in for the Cloud Run socket):
download per https://cloud.google.com/sql/docs/postgres/connect-auth-proxy, then:
  ./cloud-sql-proxy <CONNECTION_NAME> --port 5433 &
  DATABASE_URL="postgresql://librarian:<password-from-secret>@localhost:5433/agentic_librarian" \
    python infra/verify_restore.py
(Read the password once: `gcloud secrets versions access latest --secret=librarian-db-url`.)
All checks must pass before first deploy.

## 8. First deploy
GitHub → Actions → "Deploy to Cloud Run" → Run workflow (workflow_dispatch). The
workflow tests, builds, pushes, deploys, and smoke-tests. The service URL appears in
the deploy step output.

## 9. Calling the service (it's IAM-gated — a bare browser gets 403)
  TOKEN=$(gcloud auth print-identity-token)
  curl -H "Authorization: Bearer ${TOKEN}" <SERVICE_URL>/works | head -50
Or browser-style: `gcloud run services proxy librarian-api --region us-central1`
then open http://localhost:8080/works.

## 10. Budget — `infra/07-budget.sh`
$25/mo budget, email alerts to billing admins at 50/90/100%. The 50% alert (~$12.50)
fires around normal spend — treat it as a monthly heartbeat; 90%+ means investigate.

## Teardown (if ever needed)
`gcloud projects delete agentic-librarian-prod` removes everything (billing stops).
The catalog's source of truth remains the local dev DB + `data/backups/` dumps.
```

- [ ] **Step 2: Commit**

```powershell
git add docs/runbooks/gcp-walking-skeleton.md
git commit -m "docs: GCP walking skeleton runbook (provision, restore, verify, deploy)"
```

---

### Task 10: ADR-047 — record the Lift 0 infrastructure decisions

**Files:**
- Modify: `docs/project_notes/decisions.md` (append)

- [ ] **Step 1: Read the ADR template** at the top of `docs/project_notes/decisions.md` and the most recent entry (ADR-046) for format. ADRs in this repo REQUIRE an "Alternatives Considered" section (PR #38 review precedent).

- [ ] **Step 2: Append ADR-047** following the template exactly, with this content (adapt headings to the template's):

- **Title:** ADR-047: Lift 0 walking-skeleton infrastructure (Cloud Run + Cloud SQL + WIF CD)
- **Status:** Accepted (2026-06-05)
- **Context:** The approved roadmap (ADR-046) deferred five Lift-0 decisions: DB cost posture, access gate, CD shape, region/budget, provisioning style. Spec: `docs/superpowers/specs/2026-06-05-lift0-walking-skeleton-design.md`.
- **Decision:** Cloud SQL Postgres 16 `db-f1-micro` (~$12/mo floor, accepted); Cloud Run IAM gate (`--no-allow-unauthenticated`) until Firebase Auth (Lift 1); GitHub Actions auto-deploy on merge to `main` via Workload Identity Federation (keyless, repo-pinned); us-central1; $25/mo budget with 50/90/100% alerts; scripted-gcloud provisioning (`infra/` + runbook). Secret Manager holds the full `DATABASE_URL` (Cloud Run injects secrets verbatim — no composition). The prod image (`Dockerfile.api`) is separate from the dev image.
- **Alternatives Considered:** (1) Neon/Supabase free-tier Postgres — $0/mo but second vendor + migration back to Cloud SQL for multi-user anyway; rejected. (2) IAP or app-level bearer gate — both discarded by Lift 1's Firebase Auth; IAM gate is zero throwaway code; rejected. (3) Cloud Build triggers — second CI system, deploy visibility outside GitHub; rejected. (4) Terraform from day one — two new systems at once for one environment; deferred to ~Lift 3. (5) Stop-Cloud-SQL-when-idle — saves ~$10/mo now, breaks the moment friends have accounts; rejected.
- **Consequences:** ~$12–16/mo run cost; `main` is always what's deployed; the service is invisible to unauthenticated traffic until Lift 1; infra changes go through PR-reviewed scripts; drift from console click-ops is possible (accepted until Terraform).

- [ ] **Step 3: Commit**

```powershell
git add docs/project_notes/decisions.md
git commit -m "docs: ADR-047 — Lift 0 infrastructure decisions"
```

---

### Task 11: Final gate — full fast suite + in-container pre-commit

- [ ] **Step 1: Full fast suite (with DB)**

```powershell
docker run --rm -v "C:\dev\agentic_librarian:/app" -w /app --network agentic_librarian_default -e POSTGRES_HOST=db -e GOOGLE_SEARCH_API_KEY=dummy-key-for-construction --env-file C:\dev\agentic_librarian\.env agentic_librarian-app:latest bash -c "pip install -q 'fastapi>=0.115' 'uvicorn>=0.34' 'httpx>=0.27' && python -m pytest -q -m 'not api_dependent and not slow'"
```

Expected: all pass (260+ tests). The ephemeral `pip install` covers the new deps until the image is rebuilt.

- [ ] **Step 2: In-container pre-commit (the authoritative lint gate — pinned ruff v0.4.4)**

```powershell
docker run --rm -v "C:\dev\agentic_librarian:/app" -w /app agentic_librarian-app:latest bash -c "git config --global --add safe.directory /app; pip install -q --user pre-commit 2>/dev/null; export PATH=\$PATH:~/.local/bin; SKIP=pytest pre-commit run --all-files"
```

Expected: all hooks pass. If ruff/format modifies files, review with `git diff`, keep real fixes, then re-run until clean.

- [ ] **Step 3: Discard LF/CRLF noise and commit any real fixes**

```powershell
git -C C:\dev\agentic_librarian diff --stat
# discard pure line-ending noise on untouched files; commit real lint fixes:
git add -A
git commit -m "chore: lint fixes from pre-commit gate"   # only if there are real changes
```

- [ ] **Step 4: Rebuild the dev image so the new deps are baked in** (post-merge follow-up is fine; note it)

```powershell
docker compose -f C:\dev\agentic_librarian\docker-compose.yml build app
```

---

### Task 12: Live provisioning + restore + first deploy — **USER-GATED**

**Do not start without the user present: this creates billable resources on their GCP account.**
This happens AFTER the PR merges (the workflow deploys from `main`).

- [ ] Step 0 (user): install gcloud in WSL + `gcloud auth login` + `export BILLING_ACCOUNT_ID=...` (runbook §0)
- [ ] Run `infra/01-project.sh` → project + APIs
- [ ] Run `infra/02-cloudsql.sh` → ~10 min; note the connection name
- [ ] Run `infra/03-db-user-secret.sh` → user + secret
- [ ] Run `infra/04-registry-bucket.sh` → registry + bucket
- [ ] Run `infra/05-iam-wif.sh` → SAs + WIF; user sets the four GitHub Variables it prints (web UI)
- [ ] Pre-flight dump inspection + pgvector extension (runbook §6), then `infra/06-restore.sh`
- [ ] Verify via Cloud SQL Auth Proxy + `infra/verify_restore.py` (runbook §7) — ALL checks green
- [ ] First deploy via `workflow_dispatch` (runbook §8) — workflow green end-to-end including live smoke test
- [ ] Manual confirmation: `curl` `/works` and `/history` with an identity token (runbook §9) — 326 and 331 rows
- [ ] Run `infra/07-budget.sh` → budget alarms
- [ ] Update `docs/project_notes/key_facts.md`: production URL, project ID, deploy mechanism (commit via PR or user pushes)
- [ ] Delete the legacy branch `13-phase-4-web-interface-and-analysis` (its cargo is now on `main`)

---

## Self-review notes (writing-plans checklist)

- **Spec coverage:** all spec sections map to tasks — scaffold port (T1), DATABASE_URL fix (T2), /works (T3–4), Dockerfile.api (T5), deploy.yml incl. concurrency/path-filter/smoke (T6), infra scripts + SA/WIF (T7), restore + verification (T8, T12), runbook (T9), error handling is embedded in workflow/runbook semantics, testing layers (T1–4 offline, T6 CI, T12 live). ADR (T10) added per project convention.
- **Type consistency:** endpoint names, SA names, secret name, image path, and GitHub Variable names are identical across T6/T7/T9/T12 (see the constants table).
- **Placeholders:** none; every code/script step contains its full content.
