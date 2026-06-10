# Lift 2 Stage 3 — Async Enrichment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a book in seconds (fast API-scout pass persists the Work + logs the read immediately) while the slow LLM enrichment runs later in the background via a Cloud Tasks → internal endpoint, and light up the Add-book view + the Recommendations "I read this" flow that consume it.

**Architecture:** A new self-contained `enrichment/` service layer splits the existing all-scouts enrichment into a **fast tier** (Hardcover + Google Books, priorities 1–2) and a **deep tier** (Audiobook, DirectKnowledge, Style, Trope, priorities 3–6). `POST /books` (Firebase-gated) runs the fast tier, persists the Work + read-event using the already-shared `persist_enriched_work`, and enqueues a Cloud Task. `POST /internal/enrich/{work_id}` (queue-OIDC-gated, **not** Firebase) is the Cloud Tasks target: it loads the Work, runs the deep tier, and `persist_enriched_work` *updates* the same Work — idempotently. No schema migration: Stage 3 reuses the existing Work/Edition/ReadingHistory/Trope/Style tables. Routers follow the Stage 2 pattern (`recommendations.py` / `analysis.py`): a self-contained module with its own lazy `db_manager` + `set_db_manager`, included in `api/main.py`.

**Tech Stack:** FastAPI + SQLAlchemy 2.0 (backend), `google-cloud-tasks` (enqueue) + `google-auth` OIDC verification (already transitive), pytest (`db_integration`), Vite 8 / React 19 / react-router 7 / Vitest + RTL (frontend).

**Deferred to Stage 4 (do NOT do here):** opening the Cloud Run IAM gate (`--allow-unauthenticated`), provisioning the live Cloud Tasks queue + service account, prod secrets/env wiring, `DatabaseManager` pool consolidation, `/history` pagination, the multi-stage Docker build, `security.md` boundary update, and the rollout runbook. Stage 3 builds and unit/integration-tests the code with the Cloud Tasks client and OIDC verification **mocked**; the live gate flip and queue provisioning happen in Stage 4.

---

## Context the implementer needs

**Test execution (this Windows clone).** The running app container mounts the WSL clone, so `docker exec` would test stale code. Run pytest in a throwaway container that mounts THIS clone (use the PowerShell tool, not Bash — Bash mangles Windows volume paths):

```
docker run --rm -v C:\dev\agentic_librarian:/app -w /app --network agentic_librarian_default -e POSTGRES_HOST=db agentic_librarian-app:latest pytest <args>
```

`db_integration` tests need the `db` service on that network (Postgres + pgvector). Unit tests (no DB) run the same way.

**Frontend** runs on the **Windows host** (Node 24.14.0 present), NOT in a container: `npm run test` / `npm run build` / `npm run lint` from `C:\dev\agentic_librarian\frontend`.

**Authoritative lint = CI** (`.github/workflows/lint.yml`, isolated pre-commit, ruff `v0.15.16`). Since DEBT-032, `[tool.ruff.lint.isort] known-first-party = ["agentic_librarian"]` makes bare image-ruff and CI agree — keep `agentic_librarian` imports in their own group, last. Run `ruff check` / `ruff format` in the container before committing.

**Key existing seams (read before starting):**
- `src/agentic_librarian/scouts/metadata_scout.py` — `ScoutManager.register_scout(scout, priority)` (lower = first) and `.enrich(title, author, format, **kwargs) -> dict`. Audiobook/DirectKnowledge scouts self-skip when `"audiobook" not in format`.
- `src/agentic_librarian/orchestration/definitions.py:18` — `create_scout_manager()` registers all six scouts (priorities 1–6). LLM scouts (priorities 3–6) require a Google key at construction (`LLMScout.__init__` raises `ValueError` without one); API scouts (1–2) do not.
- `src/agentic_librarian/etl/persist.py:58` — `persist_enriched_work(session, row, trope_manager, style_manager) -> Work | None`. It **upserts**: matches an existing Work by exact `row["Title"]` + `row["Author_1"]`, creates it if absent, and idempotently adds styles/tropes/narrators (each guarded by an `existing_link` check). It calls `get_required_user_id()` **only** inside `if date_completed:` — so a row with `date_completed=None` needs no user context. Row keys it reads: `Title`, `Author_1`, `format`, `skip_enrichment`, `date_completed`, `contributors`, `genres`, `moods`, `enriched_tropes`, `author_style`, `work_style`, `narrator_styles`, `narrator_names`, `isbn_13`, `page_count`, `audio_minutes`, `publication_date`, `original_publication_year`.
- `src/agentic_librarian/mcp/server.py:579` — `enrich_and_persist_work(title, author, format)` is the existing all-scouts discovery write surface; **leave it untouched** (it's SEC-002-hardened and has its own tests). Stage 3's `enrichment/two_phase.py` is a parallel, tiered surface for the user-initiated add flow.
- `src/agentic_librarian/api/auth.py` — `get_current_user` (Firebase dependency) + `AuthenticatedUser(id, email)`; `_verify_token` is the test seam.
- `src/agentic_librarian/core/user_context.py` — `as_user(uuid)` context manager, `get_required_user_id()`, `DEFAULT_USER_ID`, `DEFAULT_USER_EMAIL`.
- `src/agentic_librarian/scouts/trope_manager.py` — `TropeManager(session)` construction needs `GOOGLE_SEARCH_API_KEY` in env; `standardize_trope` calls module-level `get_cached_embedding` (network) — patch `agentic_librarian.scouts.trope_manager.get_cached_embedding` in tests.

**File structure (new files):**
- `src/agentic_librarian/enrichment/__init__.py` — package marker.
- `src/agentic_librarian/enrichment/two_phase.py` — `enrich_fast`, `enrich_deep`, `add_read_event`, `_scout_and_persist`, plus `db_manager`/`set_db_manager`.
- `src/agentic_librarian/enrichment/tasks.py` — `enqueue_enrichment(work_id) -> bool` + `_client()` seam.
- `src/agentic_librarian/api/books.py` — `POST /books` router (Firebase-gated) + `db_manager`/`set_db_manager`.
- `src/agentic_librarian/api/internal.py` — `POST /internal/enrich/{work_id}` router + `verify_queue_oidc` dependency + `_verify_oidc` seam.
- Tests mirror these under `test/unit/` and `test/integration/`.
- Frontend: `frontend/src/views/AddBookView.tsx` (+ `.css`, `.test.tsx`); edits to `client.ts`, `App.tsx`, `Nav.tsx`, `RecommendationsView.tsx`, `vite.config.ts`.

---

### Task 1: Fast & deep scout-manager factories

**Files:**
- Modify: `src/agentic_librarian/orchestration/definitions.py:18-29`
- Test: `test/unit/test_scout_factories.py`

- [ ] **Step 1: Write the failing test**

```python
# test/unit/test_scout_factories.py
from agentic_librarian.orchestration.definitions import (
    create_deep_scout_manager,
    create_fast_scout_manager,
)
from agentic_librarian.scouts.metadata_scout import (
    AudiobookScout,
    DirectKnowledgeScout,
    GoogleBooksScout,
    HardcoverScout,
    LLMTropeScout,
    StyleScout,
)


def test_fast_manager_has_only_api_scouts_in_priority_order():
    mgr = create_fast_scout_manager()
    types = [type(s) for s, _ in mgr.scouts]
    assert types == [HardcoverScout, GoogleBooksScout]


def test_deep_manager_has_only_llm_scouts_in_priority_order(monkeypatch):
    # LLM scouts require a Google key at construction.
    monkeypatch.setenv("GOOGLE_SEARCH_API_KEY", "dummy-key-for-construction")
    mgr = create_deep_scout_manager()
    types = [type(s) for s, _ in mgr.scouts]
    assert types == [AudiobookScout, DirectKnowledgeScout, StyleScout, LLMTropeScout]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker run --rm -v C:\dev\agentic_librarian:/app -w /app --network agentic_librarian_default -e POSTGRES_HOST=db agentic_librarian-app:latest pytest test/unit/test_scout_factories.py -v`
Expected: FAIL — `ImportError: cannot import name 'create_fast_scout_manager'`.

- [ ] **Step 3: Write minimal implementation**

Add to `src/agentic_librarian/orchestration/definitions.py` (after `create_scout_manager`, leaving it unchanged):

```python
def create_fast_scout_manager() -> ScoutManager:
    """Fast tier (Lift 2 Stage 3): API scouts only (Hardcover, Google Books) — no LLM,
    so the add-a-book request returns in seconds and needs no Google API key."""
    manager = ScoutManager()
    manager.register_scout(HardcoverScout(), priority=1)
    manager.register_scout(GoogleBooksScout(), priority=2)
    return manager


def create_deep_scout_manager() -> ScoutManager:
    """Deep tier (Lift 2 Stage 3): the slow LLM scouts (audiobook, style, tropes) run later
    via the Cloud Tasks internal endpoint. Audiobook/DirectKnowledge self-skip on non-audiobook
    formats; StyleScout (5) runs after them so narrator_names is populated; LLMTropeScout (6) last."""
    manager = ScoutManager()
    manager.register_scout(AudiobookScout(), priority=3)
    manager.register_scout(DirectKnowledgeScout(), priority=4)
    manager.register_scout(StyleScout(), priority=5)
    manager.register_scout(LLMTropeScout(), priority=6)
    return manager
```

- [ ] **Step 4: Run test to verify it passes**

Run: `docker run --rm -v C:\dev\agentic_librarian:/app -w /app --network agentic_librarian_default -e POSTGRES_HOST=db agentic_librarian-app:latest pytest test/unit/test_scout_factories.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add src/agentic_librarian/orchestration/definitions.py test/unit/test_scout_factories.py
git commit -m "feat: fast/deep scout-manager factories for two-phase enrichment"
```

---

### Task 2: `enrichment/two_phase.py` — fast enrich + persist

**Files:**
- Create: `src/agentic_librarian/enrichment/__init__.py`
- Create: `src/agentic_librarian/enrichment/two_phase.py`
- Test: `test/integration/test_two_phase_fast.py`

- [ ] **Step 1: Write the failing test**

```python
# test/integration/test_two_phase_fast.py
import pytest

from agentic_librarian.db.models import Edition, Work
from agentic_librarian.db.session import DatabaseManager

pytestmark = pytest.mark.db_integration


class _FakeManager:
    """Stands in for a real ScoutManager: returns a fixed fast-pass metadata dict."""

    def __init__(self, result):
        self._result = result

    def enrich(self, title, author, format="Paperback", **kwargs):
        return self._result


def test_enrich_fast_persists_new_work_and_reports_created(db_url, monkeypatch):
    from agentic_librarian.enrichment import two_phase

    manager = DatabaseManager(db_url)
    monkeypatch.setattr(two_phase, "db_manager", manager)
    fixed = {
        "title": "Project Hail Mary",
        "contributors": [{"name": "Andy Weir", "role": "Author"}],
        "genres": ["Sci-Fi"],
        "moods": [],
        "isbn_13": "9780593135204",
    }
    monkeypatch.setattr(two_phase, "create_fast_scout_manager", lambda: _FakeManager(fixed))

    work_id, created = two_phase.enrich_fast("Project Hail Mary", "Andy Weir", "ebook")

    assert created is True
    with manager.get_session() as s:
        work = s.get(Work, work_id)
        assert work is not None and work.title == "Project Hail Mary"
        edition = s.query(Edition).filter_by(work_id=work_id, format="ebook").first()
        assert edition is not None


def test_enrich_fast_dedups_existing_work_without_rescouting(db_url, monkeypatch):
    from agentic_librarian.enrichment import two_phase

    manager = DatabaseManager(db_url)
    monkeypatch.setattr(two_phase, "db_manager", manager)
    fixed = {"title": "Dune", "contributors": [{"name": "Frank Herbert", "role": "Author"}],
             "genres": [], "moods": []}
    monkeypatch.setattr(two_phase, "create_fast_scout_manager", lambda: _FakeManager(fixed))

    first_id, first_created = two_phase.enrich_fast("Dune", "Frank Herbert", "ebook")
    second_id, second_created = two_phase.enrich_fast("  dune ", "FRANK HERBERT", "ebook")

    assert first_created is True
    assert second_created is False  # normalized title+author matched the existing work
    assert first_id == second_id


def test_enrich_fast_returns_none_when_scouts_find_nothing(db_url, monkeypatch):
    from agentic_librarian.enrichment import two_phase

    manager = DatabaseManager(db_url)
    monkeypatch.setattr(two_phase, "db_manager", manager)
    monkeypatch.setattr(two_phase, "create_fast_scout_manager", lambda: _FakeManager({}))

    assert two_phase.enrich_fast("Nonexistent", "Nobody", "ebook") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker run --rm -v C:\dev\agentic_librarian:/app -w /app --network agentic_librarian_default -e POSTGRES_HOST=db agentic_librarian-app:latest pytest test/integration/test_two_phase_fast.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'agentic_librarian.enrichment'`.

- [ ] **Step 3: Write minimal implementation**

Create `src/agentic_librarian/enrichment/__init__.py`:

```python
"""Two-phase async enrichment (Lift 2 Stage 3)."""
```

Create `src/agentic_librarian/enrichment/two_phase.py`:

```python
"""Two-phase enrichment service (Lift 2 Stage 3).

Fast pass: API scouts only (seconds) — persist the Work + log the read immediately.
Deep pass: the slow LLM scouts, run later by the Cloud Tasks internal endpoint, which
re-persists (updates) the SAME Work. Reuses the shared persist_enriched_work so the
catalog is built identically to the ETL and discovery paths (DRY).

This is a parallel surface to mcp/server.py's enrich_and_persist_work (the all-scouts
discovery write tool, left untouched): same persist core, tiered scouts."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import func

from agentic_librarian.core.user_context import get_required_user_id
from agentic_librarian.db.models import Author, Edition, ReadingHistory, Work, WorkContributor
from agentic_librarian.db.session import DatabaseManager
from agentic_librarian.etl.persist import persist_enriched_work
from agentic_librarian.orchestration.definitions import (
    create_deep_scout_manager,
    create_fast_scout_manager,
)
from agentic_librarian.scouts.style_manager import StyleManager
from agentic_librarian.scouts.trope_manager import TropeManager

# Per-module lazy pool (the recommendations.py/analysis.py Stage 2 pattern). Pool
# consolidation across the API modules is deferred to Stage 4.
db_manager = DatabaseManager()


def set_db_manager(new_manager: DatabaseManager) -> None:
    """Override the module db_manager (for tests) — the mcp/server.py pattern."""
    global db_manager
    db_manager = new_manager


def _normalize(s: str) -> str:
    return " ".join((s or "").strip().lower().split())


def _normalized_col(col):
    """SQL-side equivalent of _normalize: lowercase, collapse whitespace, trim."""
    return func.trim(func.regexp_replace(func.lower(col), r"\s+", " ", "g"))


def _scout_and_persist(session, manager, *, title: str, author: str, fmt: str) -> Work | None:
    """Run a scout tier and persist via the shared function. Returns the Work, or None
    if the scouts found nothing / the row had no usable contributors. date_completed=None
    so persist writes NO reading_history (and needs no user context) — the read-event is
    logged separately by add_read_event."""
    enriched = manager.enrich(title=title, author=author, format=fmt)
    if not enriched:
        return None
    row = {
        "Title": title,
        "Author_1": author,
        "format": fmt,
        "skip_enrichment": False,
        "date_completed": None,
        **enriched,
        "genres": list(enriched.get("genres") or []),
        "moods": list(enriched.get("moods") or []),
    }
    return persist_enriched_work(session, row, TropeManager(session=session), StyleManager(session=session))


def enrich_fast(title: str, author: str, fmt: str = "ebook") -> tuple[UUID, bool] | None:
    """Fast pass: de-dup against the catalog; if new, run the API scouts and persist the
    Work + Edition. Returns (work_id, created) — created=False on a de-dup hit (already in
    the catalog, so no deep-enrichment re-enqueue needed) — or None if the scouts found
    nothing. Logs NO reading_history (see add_read_event)."""
    fmt = (fmt or "ebook")[:50]
    with db_manager.get_session() as session:
        existing = (
            session.query(Work)
            .join(WorkContributor)
            .join(Author)
            .filter(_normalized_col(Work.title) == _normalize(title))
            .filter(_normalized_col(Author.name) == _normalize(author))
            .first()
        )
        if existing:
            return existing.id, False

        work = _scout_and_persist(session, create_fast_scout_manager(), title=title, author=author, fmt=fmt)
        if work is None:
            return None
        session.flush()
        return work.id, True


def add_read_event(
    work_id: UUID, *, completed, rating: int | None, notes: str | None, fmt: str
) -> dict:
    """Log a read-event for the current user against work_id (the existing
    add_book_to_history semantics: a re-read on a new date is a new row; the same
    work+date is a no-op). Requires user context (as_user / the auth dependency)."""
    user_id = get_required_user_id()
    with db_manager.get_session() as session:
        prior_reads = (
            session.query(ReadingHistory)
            .join(Edition)
            .filter(Edition.work_id == work_id, ReadingHistory.user_id == user_id)
            .all()
        )
        if any(r.date_completed == completed for r in prior_reads):
            return {"read_number": len(prior_reads), "already_logged": True}
        edition = session.query(Edition).filter_by(work_id=work_id, format=fmt).first()
        if not edition:
            edition = Edition(work_id=work_id, format=fmt)
            session.add(edition)
            session.flush()
        session.add(
            ReadingHistory(
                edition_id=edition.id,
                user_id=user_id,
                date_completed=completed,
                user_rating=rating,
                user_notes=notes,
            )
        )
        session.flush()
        return {"read_number": len(prior_reads) + 1, "already_logged": False}


def enrich_deep(work_id: UUID) -> bool:
    """Deep pass (Cloud Tasks target): load the Work, run the slow LLM scouts, and
    re-persist — persist_enriched_work matches the same Work by title+author and updates
    it with tropes/styles/narrators (idempotent: existing links are not duplicated).
    Returns False if no Work has that id (a non-retryable 404 for the queue)."""
    with db_manager.get_session() as session:
        work = session.get(Work, work_id)
        if work is None:
            return False
        author = next((c.author.name for c in work.contributors if c.role == "Author"), None)
        if author is None:
            return False
        fmt = work.editions[0].format if work.editions else "ebook"
        _scout_and_persist(session, create_deep_scout_manager(), title=work.title, author=author, fmt=fmt)
        session.flush()
        return True
```

- [ ] **Step 4: Run test to verify it passes**

Run: `docker run --rm -v C:\dev\agentic_librarian:/app -w /app --network agentic_librarian_default -e POSTGRES_HOST=db agentic_librarian-app:latest pytest test/integration/test_two_phase_fast.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add src/agentic_librarian/enrichment/__init__.py src/agentic_librarian/enrichment/two_phase.py test/integration/test_two_phase_fast.py
git commit -m "feat: two-phase enrichment service — fast enrich + read-event"
```

---

### Task 3: `enrich_deep` + `add_read_event` integration tests

**Files:**
- Test: `test/integration/test_two_phase_deep.py`

(Implementation already written in Task 2; this task proves the deep pass and the read-event semantics, including idempotency.)

- [ ] **Step 1: Write the failing test**

```python
# test/integration/test_two_phase_deep.py
from datetime import date, timedelta

import pytest

from agentic_librarian.core.user_context import DEFAULT_USER_ID, as_user
from agentic_librarian.db.models import Edition, ReadingHistory, Work, WorkContributor, WorkTrope
from agentic_librarian.db.session import DatabaseManager

pytestmark = pytest.mark.db_integration


class _FakeManager:
    def __init__(self, result):
        self._result = result

    def enrich(self, title, author, format="Paperback", **kwargs):
        return self._result


def _seed_work(manager, *, title, author, fmt="ebook"):
    from agentic_librarian.db.models import Author

    with manager.get_session() as s:
        work = Work(title=title)
        s.add(work)
        s.flush()
        a = Author(name=author)
        s.add(a)
        s.flush()
        s.add(WorkContributor(work_id=work.id, author_id=a.id, role="Author"))
        s.add(Edition(work_id=work.id, format=fmt))
        s.flush()
        return work.id


def test_enrich_deep_updates_same_work_idempotently(db_url, monkeypatch):
    from agentic_librarian.enrichment import two_phase
    from agentic_librarian.scouts import trope_manager

    monkeypatch.setenv("GOOGLE_SEARCH_API_KEY", "dummy-key-for-construction")
    monkeypatch.setattr(trope_manager, "get_cached_embedding", lambda *a, **k: [0.1] * 1536)

    manager = DatabaseManager(db_url)
    monkeypatch.setattr(two_phase, "db_manager", manager)
    deep = {"enriched_tropes": [{"trope_name": "Found Family", "relevance_score": 0.9}], "narrator_names": []}
    monkeypatch.setattr(two_phase, "create_deep_scout_manager", lambda: _FakeManager(deep))

    work_id = _seed_work(manager, title="Dune", author="Frank Herbert")

    assert two_phase.enrich_deep(work_id) is True
    assert two_phase.enrich_deep(work_id) is True  # retry-safe (Cloud Tasks redelivery)

    with manager.get_session() as s:
        links = s.query(WorkTrope).filter_by(work_id=work_id).all()
        assert len(links) == 1  # single trope link despite two runs


def test_enrich_deep_returns_false_for_unknown_work(db_url, monkeypatch):
    from uuid import uuid4

    from agentic_librarian.enrichment import two_phase

    manager = DatabaseManager(db_url)
    monkeypatch.setattr(two_phase, "db_manager", manager)
    assert two_phase.enrich_deep(uuid4()) is False


def test_add_read_event_logs_and_dedups_rereads(db_url, monkeypatch):
    from agentic_librarian.enrichment import two_phase

    manager = DatabaseManager(db_url)
    monkeypatch.setattr(two_phase, "db_manager", manager)
    work_id = _seed_work(manager, title="Hyperion", author="Dan Simmons")
    today = date.today()
    earlier = today - timedelta(days=30)

    with as_user(DEFAULT_USER_ID):
        first = two_phase.add_read_event(work_id, completed=today, rating=5, notes=None, fmt="ebook")
        dupe = two_phase.add_read_event(work_id, completed=today, rating=5, notes=None, fmt="ebook")
        reread = two_phase.add_read_event(work_id, completed=earlier, rating=4, notes=None, fmt="ebook")

    assert first == {"read_number": 1, "already_logged": False}
    assert dupe == {"read_number": 1, "already_logged": True}
    assert reread == {"read_number": 2, "already_logged": False}
    with manager.get_session() as s:
        rows = s.query(ReadingHistory).join(Edition).filter(Edition.work_id == work_id).all()
        assert len(rows) == 2
```

- [ ] **Step 2: Run test to verify it fails (then passes)**

Run: `docker run --rm -v C:\dev\agentic_librarian:/app -w /app --network agentic_librarian_default -e POSTGRES_HOST=db agentic_librarian-app:latest pytest test/integration/test_two_phase_deep.py -v`
Expected: PASS (3 passed) — the implementation from Task 2 already satisfies these. If any fail, fix `two_phase.py`, not the test.

- [ ] **Step 3: Commit**

```bash
git add test/integration/test_two_phase_deep.py
git commit -m "test: deep-enrich idempotency + read-event re-read semantics"
```

---

### Task 4: `enrichment/tasks.py` — Cloud Tasks enqueue

**Files:**
- Create: `src/agentic_librarian/enrichment/tasks.py`
- Modify: `pyproject.toml:43` (add `google-cloud-tasks` dependency)
- Test: `test/unit/test_enqueue_enrichment.py`

- [ ] **Step 1: Write the failing test**

```python
# test/unit/test_enqueue_enrichment.py
from agentic_librarian.enrichment import tasks


class _FakeClient:
    def __init__(self):
        self.created = []

    def create_task(self, *, parent, task):
        self.created.append((parent, task))
        return object()


def _set_env(monkeypatch):
    monkeypatch.setenv("CLOUD_TASKS_QUEUE", "projects/p/locations/us-central1/queues/enrich")
    monkeypatch.setenv("ENRICH_TARGET_BASE_URL", "https://librarian.example.run.app")
    monkeypatch.setenv("ENRICH_INVOKER_SA", "queue-invoker@p.iam.gserviceaccount.com")


def test_enqueue_builds_oidc_task_targeting_the_internal_route(monkeypatch):
    _set_env(monkeypatch)
    fake = _FakeClient()
    monkeypatch.setattr(tasks, "_client", lambda: fake)

    assert tasks.enqueue_enrichment("11111111-1111-4111-8111-111111111111") is True

    parent, task = fake.created[0]
    assert parent == "projects/p/locations/us-central1/queues/enrich"
    http = task["http_request"]
    assert http["url"] == "https://librarian.example.run.app/internal/enrich/11111111-1111-4111-8111-111111111111"
    assert http["oidc_token"]["service_account_email"] == "queue-invoker@p.iam.gserviceaccount.com"


def test_enqueue_skips_when_queue_not_configured(monkeypatch):
    monkeypatch.delenv("CLOUD_TASKS_QUEUE", raising=False)
    called = {"n": 0}
    monkeypatch.setattr(tasks, "_client", lambda: called.__setitem__("n", called["n"] + 1))

    assert tasks.enqueue_enrichment("abc") is False  # local dev: no queue, fast pass still succeeds
    assert called["n"] == 0  # client never constructed
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker run --rm -v C:\dev\agentic_librarian:/app -w /app --network agentic_librarian_default -e POSTGRES_HOST=db agentic_librarian-app:latest pytest test/unit/test_enqueue_enrichment.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'agentic_librarian.enrichment.tasks'`.

- [ ] **Step 3: Write minimal implementation**

Create `src/agentic_librarian/enrichment/tasks.py`:

```python
"""Cloud Tasks enqueue for the deep-enrichment pass (Lift 2 Stage 3).

The fast /books pass enqueues a task that re-enters the service as POST
/internal/enrich/{work_id} with the queue's OIDC token. Cloud Run throttles CPU after a
response, so an in-process background thread is unreliable; a queued task runs as a fresh
request with full CPU + long timeout.

Config (wired in prod in Stage 4; absent in local dev → enqueue is a logged no-op):
  CLOUD_TASKS_QUEUE     full queue path projects/<p>/locations/<loc>/queues/<q>
  ENRICH_TARGET_BASE_URL  the Cloud Run base URL (no trailing slash)
  ENRICH_INVOKER_SA     service-account email the queue signs the OIDC token as
  ENRICH_OIDC_AUDIENCE  optional explicit audience (defaults to the target URL)"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)


def _client():
    """Seam for tests. Lazily imports google-cloud-tasks so the dependency is only
    needed where enqueue actually runs."""
    from google.cloud import tasks_v2

    return tasks_v2.CloudTasksClient()


def enqueue_enrichment(work_id: str) -> bool:
    """Enqueue the deep-enrichment task for work_id. Returns True if enqueued, False if
    Cloud Tasks is not configured (local dev) — the caller treats a False/raised result as
    non-fatal so the fast add still succeeds."""
    queue = os.environ.get("CLOUD_TASKS_QUEUE")
    base = os.environ.get("ENRICH_TARGET_BASE_URL")
    sa = os.environ.get("ENRICH_INVOKER_SA")
    if not (queue and base and sa):
        logger.info("enrichment enqueue skipped — Cloud Tasks not configured (work %s)", work_id)
        return False

    from google.cloud import tasks_v2

    url = f"{base.rstrip('/')}/internal/enrich/{work_id}"
    audience = os.environ.get("ENRICH_OIDC_AUDIENCE") or url
    task = {
        "http_request": {
            "http_method": tasks_v2.HttpMethod.POST,
            "url": url,
            "oidc_token": {"service_account_email": sa, "audience": audience},
        }
    }
    _client().create_task(parent=queue, task=task)
    logger.info("enqueued deep enrichment for work %s", work_id)
    return True
```

Add `google-cloud-tasks` to `pyproject.toml` `dependencies` (after the `firebase-admin` line, before the closing `]`):

```toml
    "firebase-admin>=6.5",
    # Cloud Tasks enqueue for deep enrichment (Lift 2 Stage 3) — queues a fresh request
    # so the slow LLM pass runs with full Cloud Run CPU + long timeout.
    "google-cloud-tasks>=2.16"
```

> NOTE: the test monkeypatches `_client`, so it passes without the package installed. If `test_enqueue_builds_oidc_task...` errors on `from google.cloud import tasks_v2`, the image lacks the dep — rebuild the test image after adding it (`docker build -f Dockerfile.api -t agentic_librarian-app:latest .`) or install into the running container for the loop. The dep ships to prod via the image build.

- [ ] **Step 4: Run test to verify it passes**

Run: `docker run --rm -v C:\dev\agentic_librarian:/app -w /app --network agentic_librarian_default -e POSTGRES_HOST=db agentic_librarian-app:latest pytest test/unit/test_enqueue_enrichment.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add src/agentic_librarian/enrichment/tasks.py pyproject.toml test/unit/test_enqueue_enrichment.py
git commit -m "feat: Cloud Tasks enqueue for deep enrichment"
```

---

### Task 5: `POST /books` — fast-pass endpoint (Firebase-gated)

**Files:**
- Create: `src/agentic_librarian/api/books.py`
- Modify: `src/agentic_librarian/api/main.py:1-28` (include the router)
- Test: `test/integration/test_books_api.py`

- [ ] **Step 1: Write the failing test**

```python
# test/integration/test_books_api.py
from datetime import date, timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from agentic_librarian.api import auth
from agentic_librarian.api import books as books_mod
from agentic_librarian.api import main as api_main
from agentic_librarian.core.user_context import DEFAULT_USER_EMAIL, DEFAULT_USER_ID
from agentic_librarian.db.models import Author, Edition, ReadingHistory, User, Work, WorkContributor
from agentic_librarian.db.session import DatabaseManager
from agentic_librarian.enrichment import two_phase

pytestmark = pytest.mark.db_integration


@pytest.fixture
def client(db_url, monkeypatch):
    manager = DatabaseManager(db_url)
    monkeypatch.setattr(api_main, "db_manager", manager)
    monkeypatch.setattr(books_mod, "db_manager", manager)
    monkeypatch.setattr(two_phase, "db_manager", manager)
    monkeypatch.setitem(
        api_main.app.dependency_overrides,
        auth.get_current_user,
        lambda: auth.AuthenticatedUser(id=DEFAULT_USER_ID, email=DEFAULT_USER_EMAIL),
    )
    yield TestClient(api_main.app)


class _FakeManager:
    def __init__(self, result):
        self._result = result

    def enrich(self, title, author, format="Paperback", **kwargs):
        return self._result


def _stub_fast(monkeypatch, result):
    monkeypatch.setattr(two_phase, "create_fast_scout_manager", lambda: _FakeManager(result))


def test_add_book_persists_logs_and_enqueues(client, monkeypatch):
    enqueued = []
    monkeypatch.setattr(books_mod, "enqueue_enrichment", lambda wid: enqueued.append(wid) or True)
    _stub_fast(monkeypatch, {"title": "Project Hail Mary",
                             "contributors": [{"name": "Andy Weir", "role": "Author"}], "genres": [], "moods": []})

    resp = client.post("/books", json={"title": "Project Hail Mary", "author": "Andy Weir",
                                        "format": "ebook", "rating": 5})

    assert resp.status_code == 200
    body = resp.json()
    assert body["title"] == "Project Hail Mary"
    assert body["read_number"] == 1
    assert body["already_logged"] is False
    assert body["enrichment_enqueued"] is True
    assert enqueued == [body["work_id"]]  # newly created → deep pass enqueued


def test_add_book_not_found_returns_404(client, monkeypatch):
    monkeypatch.setattr(books_mod, "enqueue_enrichment", lambda wid: True)
    _stub_fast(monkeypatch, {})  # scouts find nothing

    resp = client.post("/books", json={"title": "Nope", "author": "Nobody"})
    assert resp.status_code == 404


def test_add_book_rereads_do_not_reenqueue(client, db_url, monkeypatch):
    calls = []
    monkeypatch.setattr(books_mod, "enqueue_enrichment", lambda wid: calls.append(wid) or True)
    manager = DatabaseManager(db_url)
    with manager.get_session() as s:
        work = Work(title="Dune")
        s.add(work); s.flush()
        a = Author(name="Frank Herbert"); s.add(a); s.flush()
        s.add(WorkContributor(work_id=work.id, author_id=a.id, role="Author"))
        s.add(Edition(work_id=work.id, format="ebook")); s.flush()
    _stub_fast(monkeypatch, {"title": "Dune", "contributors": [{"name": "Frank Herbert", "role": "Author"}]})

    resp = client.post("/books", json={"title": "Dune", "author": "Frank Herbert",
                                       "format": "ebook", "date_completed": "2020-01-01"})
    assert resp.status_code == 200
    assert resp.json()["enrichment_enqueued"] is False  # de-dup hit → no re-enqueue
    assert calls == []


def test_add_book_rejects_future_date(client, monkeypatch):
    monkeypatch.setattr(books_mod, "enqueue_enrichment", lambda wid: True)
    _stub_fast(monkeypatch, {"title": "X", "contributors": [{"name": "Y", "role": "Author"}]})
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    resp = client.post("/books", json={"title": "X", "author": "Y", "date_completed": tomorrow})
    assert resp.status_code == 422


def test_add_book_rejects_blank_title(client):
    resp = client.post("/books", json={"title": "   ", "author": "Y"})
    assert resp.status_code == 422


def test_add_book_is_user_scoped(client, db_url, monkeypatch):
    # The read-event lands on the authenticated user, not another.
    monkeypatch.setattr(books_mod, "enqueue_enrichment", lambda wid: True)
    other = uuid4()
    manager = DatabaseManager(db_url)
    with manager.get_session() as s:
        s.add(User(id=other, email="other@example.com")); s.flush()
    _stub_fast(monkeypatch, {"title": "Hyperion", "contributors": [{"name": "Dan Simmons", "role": "Author"}]})

    body = client.post("/books", json={"title": "Hyperion", "author": "Dan Simmons"}).json()
    with manager.get_session() as s:
        rows = s.query(ReadingHistory).filter(ReadingHistory.user_id == other).all()
        assert rows == []  # nothing logged to the other user
        mine = s.query(ReadingHistory).filter(ReadingHistory.user_id == DEFAULT_USER_ID).all()
        assert len(mine) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker run --rm -v C:\dev\agentic_librarian:/app -w /app --network agentic_librarian_default -e POSTGRES_HOST=db agentic_librarian-app:latest pytest test/integration/test_books_api.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'agentic_librarian.api.books'`.

- [ ] **Step 3: Write minimal implementation**

Create `src/agentic_librarian/api/books.py`:

```python
"""Add-a-book endpoint (Lift 2 Stage 3) — the fast pass of two-phase enrichment.

POST /books runs the API-only scouts (seconds), persists the Work + logs the read-event
immediately, and enqueues a Cloud Task for the deep LLM pass. Firebase-gated; the
read-event is scoped to the authenticated user (ADR-048)."""

from __future__ import annotations

import logging
from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator

from agentic_librarian.api.auth import AuthenticatedUser, get_current_user
from agentic_librarian.core.user_context import as_user
from agentic_librarian.db.session import DatabaseManager
from agentic_librarian.enrichment import two_phase
from agentic_librarian.enrichment.tasks import enqueue_enrichment

logger = logging.getLogger(__name__)
router = APIRouter()
db_manager = DatabaseManager()  # reserved for future direct reads; two_phase owns the writes


def set_db_manager(new_manager: DatabaseManager) -> None:
    """Override the module db_manager (for tests) — the mcp/server.py pattern."""
    global db_manager
    db_manager = new_manager


class AddBookRequest(BaseModel):
    title: str = Field(..., max_length=500)
    author: str = Field(..., max_length=500)
    format: str = Field("ebook", max_length=50)
    rating: int | None = Field(None, ge=1, le=5)
    notes: str | None = Field(None, max_length=2000)
    date_completed: date | None = None

    @field_validator("title", "author")
    @classmethod
    def _non_blank(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("must be a non-empty string")
        return v.strip()

    @field_validator("date_completed")
    @classmethod
    def _not_future(cls, v: date | None) -> date | None:
        if v is not None and v > date.today():
            raise ValueError("date_completed cannot be in the future")
        return v


@router.post("/books")
def add_book(req: AddBookRequest, user: AuthenticatedUser = Depends(get_current_user)):  # noqa: B008
    fast = two_phase.enrich_fast(req.title, req.author, req.format)
    if fast is None:
        raise HTTPException(
            status_code=404,
            detail=f"Couldn't find '{req.title}' by {req.author}. Check the spelling and try again.",
        )
    work_id, created = fast
    completed = req.date_completed or date.today()
    with as_user(user.id):
        event = two_phase.add_read_event(
            work_id, completed=completed, rating=req.rating, notes=req.notes, fmt=req.format
        )

    enqueued = False
    if created:
        # A failed enqueue must not fail the add — the book is already saved.
        try:
            enqueued = enqueue_enrichment(str(work_id))
        except Exception:  # noqa: BLE001 - enqueue is best-effort; deep pass can be retried later
            logger.exception("deep-enrichment enqueue failed for work %s", work_id)

    return {
        "work_id": str(work_id),
        "title": req.title,
        "read_number": event["read_number"],
        "already_logged": event["already_logged"],
        "enrichment_enqueued": enqueued,
    }
```

Wire the router in `src/agentic_librarian/api/main.py` — add the import and `include_router` alongside the Stage 2 routers:

```python
from agentic_librarian.api.books import router as books_router
```
```python
app.include_router(recommendations_router)
app.include_router(analysis_router)
app.include_router(books_router)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `docker run --rm -v C:\dev\agentic_librarian:/app -w /app --network agentic_librarian_default -e POSTGRES_HOST=db agentic_librarian-app:latest pytest test/integration/test_books_api.py -v`
Expected: PASS (6 passed).

- [ ] **Step 5: Commit**

```bash
git add src/agentic_librarian/api/books.py src/agentic_librarian/api/main.py test/integration/test_books_api.py
git commit -m "feat: POST /books fast-pass add-a-book endpoint"
```

---

### Task 6: `POST /internal/enrich/{work_id}` — queue-OIDC-gated deep pass

**Files:**
- Create: `src/agentic_librarian/api/internal.py`
- Modify: `src/agentic_librarian/api/main.py` (include the router)
- Test: `test/integration/test_internal_enrich_api.py`

- [ ] **Step 1: Write the failing test**

```python
# test/integration/test_internal_enrich_api.py
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from agentic_librarian.api import internal as internal_mod
from agentic_librarian.api import main as api_main
from agentic_librarian.db.models import Author, Edition, Work, WorkContributor
from agentic_librarian.db.session import DatabaseManager
from agentic_librarian.enrichment import two_phase

pytestmark = pytest.mark.db_integration

VALID_AUD = "https://librarian.example.run.app/internal/enrich/x"
QUEUE_SA = "queue-invoker@p.iam.gserviceaccount.com"


@pytest.fixture
def client(db_url, monkeypatch):
    manager = DatabaseManager(db_url)
    monkeypatch.setattr(two_phase, "db_manager", manager)
    monkeypatch.setenv("ENRICH_INVOKER_SA", QUEUE_SA)
    monkeypatch.setenv("ENRICH_OIDC_AUDIENCE", VALID_AUD)
    yield TestClient(api_main.app)


def _seed_work(manager):
    with manager.get_session() as s:
        work = Work(title="Dune"); s.add(work); s.flush()
        a = Author(name="Frank Herbert"); s.add(a); s.flush()
        s.add(WorkContributor(work_id=work.id, author_id=a.id, role="Author"))
        s.add(Edition(work_id=work.id, format="ebook")); s.flush()
        return work.id


def test_valid_queue_token_runs_deep_enrich(client, db_url, monkeypatch):
    manager = DatabaseManager(db_url)
    work_id = _seed_work(manager)
    monkeypatch.setattr(internal_mod, "_verify_oidc",
                        lambda token, audience: {"email": QUEUE_SA, "email_verified": True})
    called = {}
    monkeypatch.setattr(internal_mod.two_phase, "enrich_deep",
                        lambda wid: called.setdefault("wid", wid) or True)

    resp = client.post(f"/internal/enrich/{work_id}", headers={"Authorization": "Bearer good"})
    assert resp.status_code == 200
    assert str(called["wid"]) == str(work_id)


def test_missing_token_is_rejected(client):
    resp = client.post(f"/internal/enrich/{uuid4()}")
    assert resp.status_code == 401


def test_wrong_service_account_is_forbidden(client, monkeypatch):
    monkeypatch.setattr(internal_mod, "_verify_oidc",
                        lambda token, audience: {"email": "attacker@evil.com", "email_verified": True})
    resp = client.post(f"/internal/enrich/{uuid4()}", headers={"Authorization": "Bearer x"})
    assert resp.status_code == 403


def test_bad_token_signature_is_forbidden(client, monkeypatch):
    def _boom(token, audience):
        raise ValueError("invalid signature")

    monkeypatch.setattr(internal_mod, "_verify_oidc", _boom)
    resp = client.post(f"/internal/enrich/{uuid4()}", headers={"Authorization": "Bearer x"})
    assert resp.status_code == 403


def test_unknown_work_returns_404(client, monkeypatch):
    monkeypatch.setattr(internal_mod, "_verify_oidc",
                        lambda token, audience: {"email": QUEUE_SA, "email_verified": True})
    monkeypatch.setattr(internal_mod.two_phase, "enrich_deep", lambda wid: False)
    resp = client.post(f"/internal/enrich/{uuid4()}", headers={"Authorization": "Bearer good"})
    assert resp.status_code == 404
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker run --rm -v C:\dev\agentic_librarian:/app -w /app --network agentic_librarian_default -e POSTGRES_HOST=db agentic_librarian-app:latest pytest test/integration/test_internal_enrich_api.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'agentic_librarian.api.internal'`.

- [ ] **Step 3: Write minimal implementation**

Create `src/agentic_librarian/api/internal.py`:

```python
"""Internal deep-enrichment endpoint (Lift 2 Stage 3) — the Cloud Tasks target.

POST /internal/enrich/{work_id} runs the slow LLM scouts and updates the Work. It is NOT
Firebase-gated: it sits behind the (Stage-4) open IAM gate and is protected instead by the
OIDC token the Cloud Tasks queue attaches — only the queue's service account may call it.
Idempotent: Cloud Tasks may redeliver, and two_phase.enrich_deep is retry-safe."""

from __future__ import annotations

import logging
import os
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException

from agentic_librarian.enrichment import two_phase

logger = logging.getLogger(__name__)
router = APIRouter()


def _verify_oidc(token: str, audience: str) -> dict:
    """Seam for tests: monkeypatch THIS to fake the queue's OIDC token. Verifies the
    Google-signed ID token's signature, expiry, issuer, and audience, returning its claims."""
    from google.auth.transport import requests as ga_requests
    from google.oauth2 import id_token

    return id_token.verify_oauth2_token(token, ga_requests.Request(), audience=audience)


def _require_queue_caller(authorization: str | None) -> None:
    """Fail-closed OIDC gate: 401 if no bearer token, 403 if it isn't a valid token from
    the configured queue service account."""
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token.")
    token = authorization[7:].strip()
    if not token:
        raise HTTPException(status_code=401, detail="Missing bearer token.")
    expected_sa = os.environ.get("ENRICH_INVOKER_SA")
    audience = os.environ.get("ENRICH_OIDC_AUDIENCE")
    if not expected_sa:
        # Misconfigured deployment — fail closed, never open.
        logger.error("ENRICH_INVOKER_SA unset; refusing internal enrichment call")
        raise HTTPException(status_code=403, detail="Internal endpoint not configured.")
    try:
        claims = _verify_oidc(token, audience)
    except Exception as e:  # noqa: BLE001 - any verification failure is a rejection
        logger.info("internal OIDC verification rejected: %s: %s", type(e).__name__, e)
        raise HTTPException(status_code=403, detail="Caller is not the enrichment queue.") from e
    if claims.get("email") != expected_sa or not claims.get("email_verified", False):
        logger.info("internal call from unexpected principal: %s", claims.get("email"))
        raise HTTPException(status_code=403, detail="Caller is not the enrichment queue.")


@router.post("/internal/enrich/{work_id}")
def enrich(work_id: UUID, authorization: str | None = Header(None)):  # noqa: B008
    _require_queue_caller(authorization)
    if not two_phase.enrich_deep(work_id):
        # Non-retryable: the work no longer exists. 404 stops Cloud Tasks from retrying.
        raise HTTPException(status_code=404, detail="work not found")
    return {"work_id": str(work_id), "status": "enriched"}
```

Wire the router in `src/agentic_librarian/api/main.py`:

```python
from agentic_librarian.api.internal import router as internal_router
```
```python
app.include_router(books_router)
app.include_router(internal_router)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `docker run --rm -v C:\dev\agentic_librarian:/app -w /app --network agentic_librarian_default -e POSTGRES_HOST=db agentic_librarian-app:latest pytest test/integration/test_internal_enrich_api.py -v`
Expected: PASS (5 passed).

- [ ] **Step 5: Commit**

```bash
git add src/agentic_librarian/api/internal.py src/agentic_librarian/api/main.py test/integration/test_internal_enrich_api.py
git commit -m "feat: queue-OIDC-gated POST /internal/enrich/{work_id} deep pass"
```

---

### Task 7: Allow "Read" status on recommendations + full backend run

**Files:**
- Modify: `src/agentic_librarian/api/recommendations.py:20-21`
- Test: `test/integration/test_recommendations_api.py` (add a case)

- [ ] **Step 1: Write the failing test**

Append to `test/integration/test_recommendations_api.py`:

```python
def test_mark_read_removes_from_active_list(client, db_url):
    manager = DatabaseManager(db_url)
    sid, _ = _seed_suggestion(manager, user_id=DEFAULT_USER_ID, title="Read It", author="Q")

    resp = client.post(f"/recommendations/{sid}/status", json={"status": "Read"})
    assert resp.status_code == 200
    assert resp.json() == {"id": str(sid), "status": "Read"}
    assert client.get("/recommendations").json() == []  # no longer "Suggested"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker run --rm -v C:\dev\agentic_librarian:/app -w /app --network agentic_librarian_default -e POSTGRES_HOST=db agentic_librarian-app:latest pytest test/integration/test_recommendations_api.py::test_mark_read_removes_from_active_list -v`
Expected: FAIL — 422 (only "Dismissed" allowed today).

- [ ] **Step 3: Write minimal implementation**

In `src/agentic_librarian/api/recommendations.py`, update the docstring note and the allow-set:

```python
# Stage 3 wires the '✓ I read this' flow (add-book → status Read); 'Dismissed' = 'Not for me'.
ALLOWED_STATUS_UPDATES = {"Dismissed", "Read"}
```

- [ ] **Step 4: Run the FULL backend suite**

Run: `docker run --rm -v C:\dev\agentic_librarian:/app -w /app --network agentic_librarian_default -e POSTGRES_HOST=db agentic_librarian-app:latest pytest -m "not api_dependent and not slow and not live" -q`
Expected: PASS — the prior 345 + the Stage 3 additions, no regressions.

- [ ] **Step 5: Lint, then commit**

Run: `docker run --rm -v C:\dev\agentic_librarian:/app -w /app agentic_librarian-app:latest ruff check src/agentic_librarian/enrichment src/agentic_librarian/api test`
Then `ruff format` the same paths. Expected: clean (after `git diff` confirms no CRLF-only churn).

```bash
git add src/agentic_librarian/api/recommendations.py test/integration/test_recommendations_api.py
git commit -m "feat: allow 'Read' status on recommendations (I-read-this flow)"
```

---

### Task 8: Frontend API client — `addBook` + vite proxy

**Files:**
- Modify: `frontend/src/api/client.ts`
- Modify: `frontend/vite.config.ts:7`
- Test: `frontend/src/api/client.test.ts` (add cases)

- [ ] **Step 1: Write the failing test**

Add to `frontend/src/api/client.test.ts` (follow the file's existing fetch-mock idiom; if it stubs `getIdToken`, keep that):

```typescript
import { addBook } from './client'

describe('addBook', () => {
  it('POSTs the form and returns the result', async () => {
    const body = {
      work_id: 'w1', title: 'Project Hail Mary',
      read_number: 1, already_logged: false, enrichment_enqueued: true,
    }
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } }),
    )
    vi.stubGlobal('fetch', fetchMock)

    const result = await addBook({ title: 'Project Hail Mary', author: 'Andy Weir', format: 'ebook', rating: 5 })

    expect(result).toEqual(body)
    const [path, init] = fetchMock.mock.calls[0]
    expect(path).toBe('/books')
    expect(init.method).toBe('POST')
    expect(JSON.parse(init.body)).toMatchObject({ title: 'Project Hail Mary', author: 'Andy Weir' })
  })

  it('throws on a 404 (book not found)', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('nope', { status: 404 })))
    await expect(addBook({ title: 'X', author: 'Y' })).rejects.toThrow()
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run (Windows host, in `C:\dev\agentic_librarian\frontend`): `npm run test -- client`
Expected: FAIL — `addBook` is not exported.

- [ ] **Step 3: Write minimal implementation**

Add to `frontend/src/api/client.ts`:

```typescript
export interface AddBookInput {
  title: string
  author: string
  format?: string
  rating?: number | null
  notes?: string | null
  date_completed?: string | null
}

export interface AddBookResult {
  work_id: string
  title: string
  read_number: number
  already_logged: boolean
  enrichment_enqueued: boolean
}

export async function addBook(input: AddBookInput): Promise<AddBookResult> {
  const res = await authedFetchRaw('/books', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(input),
  })
  if (!res.ok) throw new Error(`addBook → ${res.status}`)
  return res.json() as Promise<AddBookResult>
}
```

Add `/books` to the dev proxy in `frontend/vite.config.ts`:

```typescript
const API_PATHS = ['/chat', '/conversations', '/history', '/works', '/recommendations', '/analysis', '/books', '/health']
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npm run test -- client`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/api/client.ts frontend/src/api/client.test.ts frontend/vite.config.ts
git commit -m "feat(web): addBook API client + /books dev proxy"
```

---

### Task 9: Add-a-book view + route + nav item

**Files:**
- Create: `frontend/src/views/AddBookView.tsx`
- Create: `frontend/src/views/AddBookView.css`
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/components/Nav.tsx`
- Test: `frontend/src/views/AddBookView.test.tsx`

- [ ] **Step 1: Write the failing test**

```tsx
// frontend/src/views/AddBookView.test.tsx
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../api/client', () => ({ addBook: vi.fn() }))

import { addBook } from '../api/client'
import AddBookView from './AddBookView'

function renderView() {
  return render(
    <MemoryRouter>
      <AddBookView />
    </MemoryRouter>,
  )
}

describe('AddBookView', () => {
  beforeEach(() => vi.mocked(addBook).mockResolvedValue({
    work_id: 'w1', title: 'Dune', read_number: 1, already_logged: false, enrichment_enqueued: true,
  }))
  afterEach(() => vi.clearAllMocks())

  it('prefills the date-finished field with today', () => {
    renderView()
    const today = new Date().toISOString().slice(0, 10)
    expect(screen.getByLabelText(/date finished/i)).toHaveValue(today)
  })

  it('submits the form and shows a confirmation', async () => {
    renderView()
    await userEvent.type(screen.getByLabelText(/title/i), 'Dune')
    await userEvent.type(screen.getByLabelText(/author/i), 'Frank Herbert')
    await userEvent.click(screen.getByRole('button', { name: /add to history/i }))

    expect(vi.mocked(addBook)).toHaveBeenCalledWith(
      expect.objectContaining({ title: 'Dune', author: 'Frank Herbert' }),
    )
    expect(await screen.findByText(/added .*dune/i)).toBeInTheDocument()
  })

  it('shows an error when the book is not found', async () => {
    vi.mocked(addBook).mockRejectedValue(new Error('addBook → 404'))
    renderView()
    await userEvent.type(screen.getByLabelText(/title/i), 'Ghost')
    await userEvent.type(screen.getByLabelText(/author/i), 'Nobody')
    await userEvent.click(screen.getByRole('button', { name: /add to history/i }))
    expect(await screen.findByText(/couldn.t add/i)).toBeInTheDocument()
  })

  it('disables submit until title and author are filled', () => {
    renderView()
    expect(screen.getByRole('button', { name: /add to history/i })).toBeDisabled()
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm run test -- AddBookView`
Expected: FAIL — cannot resolve `./AddBookView`.

- [ ] **Step 3: Write minimal implementation**

Create `frontend/src/views/AddBookView.tsx`:

```tsx
import { useState, type FormEvent } from 'react'
import { useLocation } from 'react-router'
import { addBook, setRecommendationStatus } from '../api/client'
import './AddBookView.css'

function today(): string {
  return new Date().toISOString().slice(0, 10)
}

interface Prefill {
  title?: string
  author?: string
  suggestionId?: string
}

export default function AddBookView() {
  const prefill = (useLocation().state as Prefill | null) ?? {}
  const [title, setTitle] = useState(prefill.title ?? '')
  const [author, setAuthor] = useState(prefill.author ?? '')
  const [format, setFormat] = useState('ebook')
  const [rating, setRating] = useState('')
  const [notes, setNotes] = useState('')
  const [dateFinished, setDateFinished] = useState(today())
  const [busy, setBusy] = useState(false)
  const [done, setDone] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const canSubmit = title.trim() !== '' && author.trim() !== '' && !busy

  async function onSubmit(e: FormEvent) {
    e.preventDefault()
    setBusy(true)
    setError(null)
    setDone(null)
    try {
      const result = await addBook({
        title: title.trim(),
        author: author.trim(),
        format,
        rating: rating ? Number(rating) : null,
        notes: notes.trim() || null,
        date_completed: dateFinished || null,
      })
      // Came from a recommendation's "I read this" → close the loop.
      if (prefill.suggestionId) await setRecommendationStatus(prefill.suggestionId, 'Read')
      setDone(`Added “${result.title}” to your history.`)
    } catch {
      setError("Couldn't add that book — check the title and author and try again.")
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="addbook">
      <h2>Add a book</h2>
      <form onSubmit={onSubmit} className="addbook-form">
        <label>
          Title
          <input value={title} onChange={(e) => setTitle(e.target.value)} required />
        </label>
        <label>
          Author
          <input value={author} onChange={(e) => setAuthor(e.target.value)} required />
        </label>
        <label>
          Format
          <select value={format} onChange={(e) => setFormat(e.target.value)}>
            <option value="ebook">ebook</option>
            <option value="audiobook">audiobook</option>
            <option value="paperback">paperback</option>
            <option value="hardcover">hardcover</option>
          </select>
        </label>
        <label>
          Rating
          <select value={rating} onChange={(e) => setRating(e.target.value)}>
            <option value="">—</option>
            {[1, 2, 3, 4, 5].map((n) => (
              <option key={n} value={n}>{'★'.repeat(n)}</option>
            ))}
          </select>
        </label>
        <label>
          Date finished
          <input type="date" value={dateFinished} onChange={(e) => setDateFinished(e.target.value)} />
        </label>
        <label>
          Notes
          <textarea value={notes} onChange={(e) => setNotes(e.target.value)} rows={3} />
        </label>
        <button type="submit" disabled={!canSubmit}>Add to history</button>
      </form>
      {done && <p className="addbook-done">{done}</p>}
      {error && <p className="addbook-error">{error}</p>}
    </div>
  )
}
```

Create `frontend/src/views/AddBookView.css`:

```css
.addbook-form {
  display: grid;
  gap: 0.75rem;
  max-width: 28rem;
}
.addbook-form label {
  display: grid;
  gap: 0.25rem;
  font-size: 0.9rem;
}
.addbook-done {
  color: var(--ok, #2e7d32);
}
.addbook-error {
  color: var(--danger, #c62828);
}
```

Add the route in `frontend/src/App.tsx` (import + nested route):

```tsx
import AddBookView from './views/AddBookView'
```
```tsx
          <Route path="analysis" element={<AnalysisView />} />
          <Route path="add" element={<AddBookView />} />
```

Add the nav item in `frontend/src/components/Nav.tsx`:

```tsx
  { to: '/analysis', label: 'Analysis', icon: '📊', end: false },
  { to: '/add', label: 'Add', icon: '➕', end: false },
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npm run test -- AddBookView`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/views/AddBookView.tsx frontend/src/views/AddBookView.css frontend/src/App.tsx frontend/src/components/Nav.tsx frontend/src/views/AddBookView.test.tsx
git commit -m "feat(web): add-a-book view, route, and nav item"
```

---

### Task 10: Wire Recommendations "I read this" → prefilled add-book

**Files:**
- Modify: `frontend/src/views/RecommendationsView.tsx`
- Modify: `frontend/src/views/RecommendationsView.test.tsx`

- [ ] **Step 1: Write the failing test**

Replace the Stage-2 `shows "I read this" as disabled` case and add a navigation assertion. The view now uses `useNavigate`, so the test renders inside a router and asserts navigation state. Update `RecommendationsView.test.tsx`:

```tsx
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../api/client', () => ({
  getRecommendations: vi.fn(),
  setRecommendationStatus: vi.fn(),
}))

import { getRecommendations, setRecommendationStatus } from '../api/client'
import RecommendationsView from './RecommendationsView'

const rec = {
  id: 'r1', work_id: 'w1', title: 'Project Hail Mary', authors: ['Weir'],
  justification: 'You loved The Martian', context: null,
  suggested_at: '2026-06-01T00:00:00', status: 'Suggested',
}

function LocationProbe() {
  const loc = useLocation()
  return <div data-testid="loc">{loc.pathname}|{JSON.stringify(loc.state)}</div>
}

function renderWithRouter() {
  return render(
    <MemoryRouter initialEntries={['/recommendations']}>
      <Routes>
        <Route path="/recommendations" element={<RecommendationsView />} />
        <Route path="/add" element={<LocationProbe />} />
      </Routes>
    </MemoryRouter>,
  )
}

describe('RecommendationsView', () => {
  beforeEach(() => {
    vi.mocked(getRecommendations).mockResolvedValue([rec])
    vi.mocked(setRecommendationStatus).mockResolvedValue()
  })
  afterEach(() => vi.clearAllMocks())

  it('renders recommendation cards with the justification', async () => {
    renderWithRouter()
    expect(await screen.findByText('Project Hail Mary')).toBeInTheDocument()
    expect(screen.getByText(/You loved The Martian/)).toBeInTheDocument()
  })

  it('dismisses a recommendation and removes the card', async () => {
    renderWithRouter()
    await screen.findByText('Project Hail Mary')
    await userEvent.click(screen.getByRole('button', { name: /not for me/i }))
    expect(vi.mocked(setRecommendationStatus)).toHaveBeenCalledWith('r1', 'Dismissed')
    await waitFor(() => expect(screen.queryByText('Project Hail Mary')).not.toBeInTheDocument())
  })

  it('"I read this" navigates to /add prefilled with the title, author, and suggestion id', async () => {
    renderWithRouter()
    await screen.findByText('Project Hail Mary')
    await userEvent.click(screen.getByRole('button', { name: /i read this/i }))
    const probe = await screen.findByTestId('loc')
    expect(probe.textContent).toContain('/add')
    expect(probe.textContent).toContain('"title":"Project Hail Mary"')
    expect(probe.textContent).toContain('"author":"Weir"')
    expect(probe.textContent).toContain('"suggestionId":"r1"')
  })

  it('shows an empty state when there are no picks', async () => {
    vi.mocked(getRecommendations).mockResolvedValue([])
    renderWithRouter()
    expect(await screen.findByText(/no recommendations/i)).toBeInTheDocument()
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm run test -- RecommendationsView`
Expected: FAIL — the "I read this" button is disabled / does not navigate.

- [ ] **Step 3: Write minimal implementation**

Update `frontend/src/views/RecommendationsView.tsx` — import `useNavigate`, replace the disabled button:

```tsx
import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router'
import { getRecommendations, setRecommendationStatus, type Recommendation } from '../api/client'
import './RecommendationsView.css'

export default function RecommendationsView() {
  const navigate = useNavigate()
  const [recs, setRecs] = useState<Recommendation[] | null>(null)
  const [busy, setBusy] = useState<string | null>(null)

  useEffect(() => {
    void getRecommendations().then(setRecs)
  }, [])

  async function dismiss(id: string) {
    setBusy(id)
    try {
      await setRecommendationStatus(id, 'Dismissed')
      setRecs((current) => (current ? current.filter((r) => r.id !== id) : current))
    } finally {
      setBusy(null)
    }
  }

  function readThis(r: Recommendation) {
    // Open the add-book form prefilled; on a successful add it marks this suggestion Read.
    navigate('/add', { state: { title: r.title, author: r.authors.join(', '), suggestionId: r.id } })
  }

  if (recs === null) return <p>Loading…</p>
  if (recs.length === 0) return <p>No recommendations right now — ask the Librarian in Chat for ideas.</p>

  return (
    <div>
      <h2>Recommendations</h2>
      <div className="rec-list">
        {recs.map((r) => (
          <article key={r.id} className="rec-card">
            <div className="rec-head">
              <span className="rec-title">{r.title}</span>
              <span className="rec-authors">{r.authors.join(', ')}</span>
            </div>
            {r.justification && <p className="rec-why">{r.justification}</p>}
            <div className="rec-actions">
              <button onClick={() => readThis(r)}>✓ I read this</button>
              <button onClick={() => void dismiss(r.id)} disabled={busy === r.id}>Not for me</button>
            </div>
          </article>
        ))}
      </div>
    </div>
  )
}
```

- [ ] **Step 4: Run the FULL frontend suite + build + lint**

Run (in `frontend/`): `npm run test`, then `npm run build`, then `npm run lint`.
Expected: all green — the Stage 2 suite (23) plus the Stage 3 additions, type-check clean, no lint errors.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/views/RecommendationsView.tsx frontend/src/views/RecommendationsView.test.tsx
git commit -m "feat(web): wire 'I read this' to the prefilled add-book flow"
```

---

## Self-Review

**1. Spec coverage (§3 two-phase enrichment + §2 add-book/recommendations):**
- "fast pass — ScoutManager restricted to the API scouts" → Task 1 (`create_fast_scout_manager`) + Task 2 (`enrich_fast`). ✓
- "persists the Work + Edition with basic metadata, logs the read-event" → Task 2 (`enrich_fast` + `add_read_event`) + Task 5 (`POST /books`). ✓
- "enqueues a Cloud Task … No match → an honest 'couldn't find it'" → Task 4 (`enqueue_enrichment`) + Task 5 (404 path). ✓
- "internal endpoint runs the slow LLM scouts, persist_enriched_work updates the same Work, idempotent, queue-OIDC-gated, rejects non-queue callers" → Task 1 (`create_deep_scout_manager`) + Task 2/3 (`enrich_deep` idempotent) + Task 6 (OIDC gate, 403 on wrong SA/bad token). ✓
- "A fast-only enrichment path is added to ScoutManager" → Task 1. ✓
- D9 actionable recommendations "✓ I read this → add-book prefilled → status Read" → Task 7 (allow "Read") + Task 9 (view) + Task 10 (wire). ✓
- D10 add-book form, date pre-filled today/editable, look-up-on-submit, re-reads = new row → Task 9 (form, `today()` prefill) + Task 2 (`add_read_event` re-read semantics). ✓
- §5 testing: fast pass runs only API scouts + enqueues (Cloud Tasks mocked) — Task 5; internal runs deep scouts, idempotent, rejects non-queue — Tasks 3 & 6; user-scoping — Task 5 `test_add_book_is_user_scoped`. ✓
- **Deferred correctly (NOT in this plan):** IAM gate, queue/SA provisioning, prod secrets, pool consolidation, `/history` pagination, multi-stage Docker, `security.md`, runbook — all Stage 4. No DB migration (no new tables). ✓

**2. Placeholder scan:** No TBD/TODO; every code step shows complete code; every test step shows full test bodies; run commands have explicit expected output. ✓

**3. Type consistency:** `enrich_fast → tuple[UUID, bool] | None`; `add_read_event → {"read_number", "already_logged"}`; `enrich_deep → bool`; `enqueue_enrichment(str) → bool`; `/books` response keys (`work_id`, `title`, `read_number`, `already_logged`, `enrichment_enqueued`) match `AddBookResult` in `client.ts`; `_verify_oidc(token, audience) → claims dict` used identically in `internal.py` and its tests; nav/route path `/add` matches `navigate('/add', …)` and the `Prefill` state shape (`title`/`author`/`suggestionId`) consumed by `AddBookView`. ✓

**DRY note for reviewers:** `two_phase.py` repeats ~15 lines of normalized-dedup that also exist in `mcp/server.py:enrich_and_persist_work`. This is intentional — the SEC-002-hardened discovery tool stays untouched while the user-add surface gets tiered scouts; both share the real persistence core (`persist_enriched_work`). A future refactor may converge them once the seams settle.

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-06-09-lift2-stage3-async-enrichment.md`. Two execution options:

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks (spec compliance then code quality), fast iteration.

**2. Inline Execution** — Execute tasks in this session with checkpoints for review.

Which approach?
