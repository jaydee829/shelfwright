# Lift 2 Stage 4 — Cleanups + Rollout Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land the Lift 2 cleanups (4→1 DB-pool consolidation, INF-030 off-loop writes, `/history` pagination) and the rollout-enabling code (multi-stage Docker serving the SPA same-origin, the IAM-gate flip, prod secrets/env, provisioning scripts, security.md, runbook), then hand off to an operator rollout.

**Architecture:** Two code PRs + a separate operator rollout. **PR-A** (Tasks 1–2) is the behavior-preserving seam refactor. **PR-B** (Tasks 3–10) is reviewed then **held** until the operator has provisioned Cloud Tasks/secrets and applied the prod migration — because merging PR-B opens the IAM gate atomically with the new image (spec D2). The operator rollout (Task 10's runbook) is executed out-of-band by the human.

**Tech Stack:** FastAPI · SQLAlchemy · `asyncio.to_thread` · Vite/React 19/TypeScript · Vitest + RTL · multi-stage Docker (Node 22 build + Python 3.11 runtime) · Cloud Run · Cloud Tasks · Secret Manager · `gcloud`.

**Spec:** `docs/superpowers/specs/2026-06-10-lift2-stage4-cleanups-rollout-design.md`

---

## Conventions for this plan

- **Branch:** create `feat/lift2-stage4-cleanups-rollout` off `main` before Task 1. PR-A and PR-B can share the branch with PR-A's commits landing first, or PR-B can branch off PR-A — coordinator's choice; the tasks are ordered so PR-A's commits precede PR-B's.
- **Backend tests** run in the app image (the Windows host has no local Python env). Use this wrapper from `C:\dev\agentic_librarian`:
  ```powershell
  docker run --rm -v C:\dev\agentic_librarian:/app -w /app --network agentic_librarian_default `
    -e POSTGRES_HOST=db agentic_librarian-app:latest `
    bash -c "pip install -q claude-agent-sdk; pytest -q <ARGS>"
  ```
  Unit tests need no DB; `db_integration` tests need the `db` service on the compose network (already wired by `-e POSTGRES_HOST=db --network agentic_librarian_default`).
- **Frontend tests/build/lint** run on the Windows host in `C:\dev\agentic_librarian\frontend`: `npm run test` / `npm run build` / `npm run lint`.
- **Commits:** end messages with `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`. Do not stage stray untracked files (`.claude/`, `uv.lock`).

---

## File Structure

**PR-A (refactor):**
- `src/agentic_librarian/api/main.py` — add `set_db_manager` + a `lifespan` handler that builds one `DatabaseManager` and propagates it to all four modules; store on `app.state.db_manager`.
- `src/agentic_librarian/chat/stream.py` — move the two `on_persist` writes off-loop via `asyncio.to_thread`.
- `src/agentic_librarian/agents/runtime.py` — make `_record_event_usage` async and route `record_llm_call` through `asyncio.to_thread`.
- `test/unit/test_db_pool_consolidation.py` *(new)* — startup wires one shared manager into all four modules.
- `test/unit/test_offloop_writes.py` *(new)* — the off-loaded writes still resolve the user context.

**PR-B (rollout-enabling):**
- `src/agentic_librarian/api/main.py` — `/history` `limit`/`offset`; SPA static serving + fallback (registered last).
- `frontend/src/api/client.ts` + `frontend/src/views/HistoryView.tsx` — paginated history + "Load more".
- `frontend/src/views/HistoryView.test.tsx` *(new)*.
- `test/unit/test_api_history.py` — update mock chains + add pagination tests.
- `test/integration/test_api_history_db.py` — add a real-DB paging test.
- `test/unit/test_spa_serving.py` *(new)* — root/fallback/asset/api-precedence/traversal.
- `Dockerfile.api` — Node build stage + copy `dist` → `/app/static`.
- `.github/workflows/deploy.yml` — paths, gate flip, secrets, env, max-instances, `GET /` smoke.
- `infra/00-config.sh` — add queue + invoker-SA + secret-name vars.
- `infra/08-cloud-tasks.sh` *(new)* · `infra/09-prod-secrets.sh` *(new)*.
- `docs/project_notes/security.md` — open-gate boundary update.
- `docs/runbooks/lift2-stage4-rollout.md` *(new)* — the operator runbook.

---

# PART 1 — PR-A: the seam refactor (behavior-preserving)

### Task 1: Consolidate the four DatabaseManager pools into one lifespan-injected manager

**Files:**
- Modify: `src/agentic_librarian/api/main.py` (add `set_db_manager` + `lifespan`; pass `lifespan=` to `FastAPI(...)`)
- Test: `test/unit/test_db_pool_consolidation.py` (create)

Today four modules each construct a lazy `DatabaseManager`: `api/main.py:25`, `api/auth.py:27`, `chat/transcript.py:16`, `core/usage.py:22`. `auth`, `transcript`, `usage` already expose `set_db_manager`; `main` does not. We add one to `main`, then a FastAPI `lifespan` that builds a single manager and pushes it into all four (and onto `app.state`). Endpoints read the module-global `db_manager` at call time, so reassignment takes effect. Existing tests that do `TestClient(app)` **without** a `with` block never trigger `lifespan`, so their monkeypatches are undisturbed; tests that want the wired state use `with TestClient(app)`.

- [ ] **Step 1: Write the failing test**

Create `test/unit/test_db_pool_consolidation.py`:

```python
"""Lift 2 Stage 4: startup consolidates the four lazy DatabaseManager pools into one
shared, lifespan-injected manager (INF-030 companion / Stage 1 review note)."""

import pytest
from fastapi.testclient import TestClient

from agentic_librarian.api import auth as auth_mod
from agentic_librarian.api import main as main_mod
from agentic_librarian.chat import transcript as transcript_mod
from agentic_librarian.core import usage as usage_mod


@pytest.fixture()
def _restore_db_managers():
    """lifespan mutates module globals; snapshot + restore so test order can't leak."""
    saved = (main_mod.db_manager, auth_mod.db_manager, transcript_mod.db_manager, usage_mod.db_manager)
    yield
    main_mod.db_manager, auth_mod.db_manager, transcript_mod.db_manager, usage_mod.db_manager = saved


def test_startup_consolidates_all_four_pools(_restore_db_managers):
    # Entering the TestClient context runs the lifespan startup. DatabaseManager() is
    # lazy (no connection until first get_session), so this is offline-safe.
    with TestClient(main_mod.app):
        shared = main_mod.app.state.db_manager
        assert main_mod.db_manager is shared
        assert auth_mod.db_manager is shared
        assert transcript_mod.db_manager is shared
        assert usage_mod.db_manager is shared
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest test/unit/test_db_pool_consolidation.py -v`
Expected: FAIL — `AttributeError: ... 'State' object has no attribute 'db_manager'` (no lifespan yet).

- [ ] **Step 3: Write minimal implementation**

In `src/agentic_librarian/api/main.py`, change the imports/app construction. Replace:

```python
app = FastAPI(title="Agentic Librarian API")
db_manager = DatabaseManager()
# NOTE: several modules each own a lazy DatabaseManager (this module, api/auth.py,
# chat/transcript.py, core/usage.py) — ~4 pools. Acceptable at friends-scale; consolidating
# into one shared manager is deferred to Lift 2 Stage 4 (cleanups), per the Stage 1 final review.
```

with:

```python
db_manager = DatabaseManager()


def set_db_manager(new_manager: DatabaseManager) -> None:
    """Override this module's db_manager (tests / the shared-pool lifespan) — mcp/server.py pattern."""
    global db_manager
    db_manager = new_manager


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    """Build ONE DatabaseManager at startup and inject it into every module that used to
    own a lazy pool (Lift 2 Stage 4 — closes the Stage 1 ~4-pools note). Lazy construction
    means no DB connection happens here. Tests that use TestClient WITHOUT a `with` block
    skip lifespan and keep their own monkeypatched managers."""
    shared = DatabaseManager()
    app.state.db_manager = shared
    set_db_manager(shared)
    auth.set_db_manager(shared)
    transcript.set_db_manager(shared)
    usage.set_db_manager(shared)
    yield


app = FastAPI(title="Agentic Librarian API", lifespan=lifespan)
```

Add the required imports near the top of `main.py` (alongside the existing imports):

```python
import contextlib

from agentic_librarian.api import auth
from agentic_librarian.core import usage
```

Note `from agentic_librarian.chat import stream, transcript` already exists (line 12) — reuse `transcript`. The existing `from agentic_librarian.api.auth import AuthenticatedUser, get_current_user` stays; add the module import `from agentic_librarian.api import auth` so `auth.set_db_manager` resolves.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest test/unit/test_db_pool_consolidation.py -v`
Expected: PASS.

- [ ] **Step 5: Run the broader API suite to prove no regression**

Run: `pytest test/unit/test_api_history.py test/unit/test_api_works.py test/unit/test_api_requires_auth.py -v`
Expected: PASS (these use `TestClient` without `with`, so lifespan does not disturb them).

- [ ] **Step 6: Commit**

```bash
git add src/agentic_librarian/api/main.py test/unit/test_db_pool_consolidation.py
git commit -m "refactor(api): consolidate 4 DatabaseManager pools into one lifespan-injected manager

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: INF-030 — move the two per-turn DB writes off the event loop

**Files:**
- Modify: `src/agentic_librarian/chat/stream.py:55-58` (the two `on_persist` calls)
- Modify: `src/agentic_librarian/agents/runtime.py:51-65,95` (`_record_event_usage` → async + `to_thread`)
- Test: `test/unit/test_offloop_writes.py` (create)

Both writes are synchronous INSERTs on the asyncio event loop: `transcript.append_message` (via `on_persist` in `stream.py`) and `usage.record_llm_call` (in `runtime._record_event_usage`). `asyncio.to_thread` runs them on a worker thread; it **copies the current context**, so `current_user_id`/`as_user` still resolve inside the worker — which we prove, since both writes are user-scoped.

- [ ] **Step 1: Write the failing test**

Create `test/unit/test_offloop_writes.py`:

```python
"""INF-030: the per-turn DB writes run off the event loop via asyncio.to_thread, and the
worker thread still sees the user context (to_thread copies contextvars). These tests pin
the invariant that the user identity survives the thread hop for BOTH writes."""

import asyncio
import inspect
from uuid import UUID

import pytest

from agentic_librarian.agents import runtime as runtime_mod
from agentic_librarian.core import usage as usage_mod
from agentic_librarian.core.user_context import as_user

UID = UUID("00000000-0000-4000-8000-000000000001")


def test_record_event_usage_is_async():
    """The runtime call site must be a coroutine so the metering write can be awaited
    off-loop (it's driven inside an `async for` over ADK events)."""
    assert inspect.iscoroutinefunction(runtime_mod._record_event_usage)


class _CapturingSession:
    def __init__(self, sink):
        self._sink = sink

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def add(self, obj):
        self._sink.append(obj)

    def flush(self):
        pass


class _CapturingManager:
    def __init__(self, sink):
        self._sink = sink

    def get_session(self):
        return _CapturingSession(self._sink)


@pytest.mark.asyncio
async def test_record_llm_call_offloaded_keeps_user_context(monkeypatch):
    sink = []
    monkeypatch.setattr(usage_mod, "db_manager", _CapturingManager(sink))
    with as_user(UID):
        # Exactly how runtime now performs the write: off-loop via to_thread.
        await asyncio.to_thread(
            usage_mod.record_llm_call,
            vendor="gemini",
            model="gemini-3.1-flash-lite",
            input_tokens=10,
            output_tokens=5,
            conversation_id=None,
        )
    assert len(sink) == 1
    assert sink[0].user_id == UID  # the worker thread saw the context user
```

(`record_llm_call` is best-effort and swallows exceptions, so a wrong/missing context would yield `len(sink) == 0` — the assertion fails meaningfully.) Ensure `pytest-asyncio` is available; the repo already uses `@pytest.mark.asyncio` elsewhere (e.g. the stream tests).

- [ ] **Step 2: Run tests to verify the red**

Run: `pytest test/unit/test_offloop_writes.py -v`
Expected: `test_record_event_usage_is_async` **FAILS** (`_record_event_usage` is currently sync) — that's the genuine red for the runtime change. `test_record_llm_call_offloaded_keeps_user_context` PASSES already: it's a characterization test of the `to_thread`+contextvar mechanism the call sites will use (the existing stream/runtime suites are the regression guard for the wiring itself).

- [ ] **Step 3: Move the transcript writes off-loop in `stream.py`**

In `src/agentic_librarian/chat/stream.py`, inside `drive()` replace:

```python
            with as_user(user_id):
                reply = await conv.asend(message)
                on_persist("user", message)
                on_persist("assistant", reply)
```

with:

```python
            with as_user(user_id):
                reply = await conv.asend(message)
                # INF-030: persist off the event loop (sync INSERT). to_thread copies the
                # context, so append_message's get_required_user_id still resolves this user.
                await asyncio.to_thread(on_persist, "user", message)
                await asyncio.to_thread(on_persist, "assistant", reply)
```

(`import asyncio` is already present at `stream.py:10`.)

- [ ] **Step 4: Move the usage write off-loop in `runtime.py`**

In `src/agentic_librarian/agents/runtime.py`, make `_record_event_usage` async and await the off-loaded write. Replace the `def _record_event_usage(...)` block (lines ~51–65):

```python
async def _record_event_usage(event, conversation_id: uuid.UUID | None) -> None:
    """Meter one ADK event if it carries usage (duck-typed: unit-test fakes and
    non-LLM events simply lack usage_metadata)."""
    um = getattr(event, "usage_metadata", None)
    if um is None:
        return
    # INF-030: the metering INSERT runs off the event loop. to_thread copies the context,
    # so record_llm_call's get_required_user_id still resolves the turn's user.
    await asyncio.to_thread(
        record_llm_call,
        vendor="gemini",
        model=getattr(event, "model_version", None) or os.environ.get("GEMINI_MODEL", "gemini-3.1-flash-lite"),
        input_tokens=getattr(um, "prompt_token_count", 0) or 0,
        output_tokens=getattr(um, "candidates_token_count", 0) or 0,
        conversation_id=conversation_id,
    )
```

And at the call site (line ~95) change:

```python
            _record_event_usage(event, self.conversation_id)
```

to:

```python
            await _record_event_usage(event, self.conversation_id)
```

Confirm `import asyncio` exists at the top of `runtime.py`; if not, add it.

- [ ] **Step 5: Run the affected suites**

Run: `pytest test/unit/test_offloop_writes.py -v` and the existing chat/runtime tests:
`pytest -q -k "stream or runtime or usage or conversation"`
Expected: PASS. (The existing `sse_turn` tests await the generator, so the `to_thread` calls complete within the turn; the runtime tests drive `asend` on an event loop, so the new `await` is valid.)

- [ ] **Step 6: Commit**

```bash
git add src/agentic_librarian/chat/stream.py src/agentic_librarian/agents/runtime.py test/unit/test_offloop_writes.py
git commit -m "perf(chat): move transcript + usage writes off the event loop (INF-030)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

**PR-A is now complete.** Open it, address review, merge (a normal prod deploy — safe, gate stays closed).

---

# PART 2 — PR-B: rollout-enabling code (reviewed, then HELD until the operator rollout)

### Task 3: `/history` pagination — backend (INF-029)

**Files:**
- Modify: `src/agentic_librarian/api/main.py:52-85` (`get_history`)
- Modify: `test/unit/test_api_history.py` (update mock chains + add pagination tests)
- Modify: `test/integration/test_api_history_db.py` (add a real-DB paging test)

Mirror `/works` exactly: `limit: int = Query(50, ge=1, le=200)`, `offset: int = Query(0, ge=0)`, applied as `.offset(offset).limit(limit)` after `order_by`.

- [ ] **Step 1: Write the failing tests**

Append to `test/unit/test_api_history.py`:

```python
def _history_chain(mock_session, results):
    """Wire query().join().join().filter().options().order_by().offset().limit().all()."""
    mock_query = mock_session.query.return_value
    mock_query.join.return_value = mock_query
    mock_query.filter.return_value = mock_query
    mock_query.options.return_value = mock_query
    mock_query.order_by.return_value = mock_query
    mock_query.offset.return_value = mock_query
    mock_query.limit.return_value = mock_query
    mock_query.all.return_value = results
    return mock_query


def test_get_history_pagination_params_forwarded():
    with patch("agentic_librarian.api.main.db_manager") as mock_db:
        mock_session = MagicMock()
        mock_db.get_session.return_value.__enter__.return_value = mock_session
        mock_query = _history_chain(mock_session, [])

        response = client.get("/history?limit=10&offset=20")
        assert response.status_code == 200
        mock_query.offset.assert_called_once_with(20)
        mock_query.limit.assert_called_once_with(10)


def test_get_history_limit_cap_enforced():
    assert client.get("/history?limit=500").status_code == 422
    assert client.get("/history?limit=0").status_code == 422
    assert client.get("/history?offset=-1").status_code == 422
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest test/unit/test_api_history.py -v`
Expected: FAIL — `test_get_history_pagination_params_forwarded` errors (handler has no `offset`/`limit`), and the cap test returns 200 not 422.

- [ ] **Step 3: Implement the handler change**

In `src/agentic_librarian/api/main.py`, change `get_history`'s signature and query. Replace:

```python
@app.get("/history")
def get_history(user: AuthenticatedUser = Depends(get_current_user)):  # noqa: B008
    with db_manager.get_session() as session:
        # Query reading history with eager loading for efficiency
        history_entries = (
            session.query(ReadingHistory)
            .join(Edition)
            .join(Work)
            .filter(ReadingHistory.user_id == user.id)  # my history, not the commons (ADR-048)
            .options(
                joinedload(ReadingHistory.edition)
                .joinedload(Edition.work)
                .joinedload(Work.contributors)
                .joinedload(WorkContributor.author)
            )
            .order_by(ReadingHistory.date_completed.desc())
            .all()
        )
```

with:

```python
@app.get("/history")
def get_history(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    user: AuthenticatedUser = Depends(get_current_user),  # noqa: B008
):
    """Paginated reading log, newest first (INF-029 — mirrors /works)."""
    with db_manager.get_session() as session:
        # Query reading history with eager loading for efficiency
        history_entries = (
            session.query(ReadingHistory)
            .join(Edition)
            .join(Work)
            .filter(ReadingHistory.user_id == user.id)  # my history, not the commons (ADR-048)
            .options(
                joinedload(ReadingHistory.edition)
                .joinedload(Edition.work)
                .joinedload(Work.contributors)
                .joinedload(WorkContributor.author)
            )
            .order_by(ReadingHistory.date_completed.desc(), ReadingHistory.id)
            .offset(offset)
            .limit(limit)
            .all()
        )
```

(`Query` is already imported at `main.py:1`. The `ReadingHistory.id` tiebreak makes pages stable when dates collide — same reasoning as `/works`' `Work.id` tiebreak.)

- [ ] **Step 4: Fix the three pre-existing history unit tests (their mock chains lack offset/limit)**

In `test/unit/test_api_history.py`, the existing `test_get_history_empty`, `test_get_history_with_data`, `test_get_history_no_date` each build the chain inline without `offset`/`limit`. Add the two missing lines to each one's chain block:

```python
        mock_query.offset.return_value = mock_query
        mock_query.limit.return_value = mock_query
```

(insert right after the existing `mock_query.order_by.return_value = mock_query` line in each of the three tests).

- [ ] **Step 5: Add the real-DB paging test**

Append to `test/integration/test_api_history_db.py`:

```python
def test_history_paginates_newest_first(two_user_client, db_url):
    from datetime import date as _date

    from agentic_librarian.db.models import Edition as _Edition
    from agentic_librarian.db.models import ReadingHistory as _RH

    # Add three more reads for DEFAULT_USER on the shared edition, distinct dates.
    manager = DatabaseManager(db_url)
    with manager.get_session() as session:
        edition_id = session.query(_Edition).first().id
        for d in (_date(2023, 3, 3), _date(2024, 4, 4), _date(2025, 5, 5)):
            session.add(_RH(edition_id=edition_id, user_id=DEFAULT_USER_ID, date_completed=d))
        session.flush()

    c = two_user_client(DEFAULT_USER_ID, "jaydee829@gmail.com")
    page1 = c.get("/history?limit=2&offset=0").json()
    page2 = c.get("/history?limit=2&offset=2").json()
    assert [h["date_completed"] for h in page1] == ["2025-05-05", "2024-04-04"]  # newest first
    assert [h["date_completed"] for h in page2] == ["2023-03-03", "2021-01-01"]  # next page, no overlap
```

- [ ] **Step 6: Run both suites**

Run (unit): `pytest test/unit/test_api_history.py -v` → PASS
Run (integration, needs DB): `pytest test/integration/test_api_history_db.py -v` → PASS

- [ ] **Step 7: Commit**

```bash
git add src/agentic_librarian/api/main.py test/unit/test_api_history.py test/integration/test_api_history_db.py
git commit -m "feat(api): paginate /history with limit/offset (INF-029)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: `/history` pagination — frontend ("Load more")

**Files:**
- Modify: `frontend/src/api/client.ts:76-78` (`getHistory`)
- Modify: `frontend/src/views/HistoryView.tsx`
- Test: `frontend/src/views/HistoryView.test.tsx` (create)

A page size of 50; `HistoryView` appends pages and hides "Load more" once a page returns fewer than a full page.

- [ ] **Step 1: Write the failing test**

Create `frontend/src/views/HistoryView.test.tsx`:

```tsx
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import HistoryView from './HistoryView'
import * as client from '../api/client'

vi.mock('../api/client')

function item(id: string, title: string): client.HistoryItem {
  return { id, title, authors: ['A. Uthor'], date_completed: '2024-01-01', rating: 4, format: 'ebook' }
}

afterEach(() => vi.clearAllMocks())

describe('HistoryView pagination', () => {
  it('loads the first page and appends the next on "Load more"', async () => {
    const full = Array.from({ length: 50 }, (_, i) => item(`a${i}`, `Book ${i}`))
    vi.mocked(client.getHistory).mockResolvedValueOnce(full).mockResolvedValueOnce([item('b0', 'Page 2 Book')])

    render(<HistoryView />)
    expect(await screen.findByText('Book 0')).toBeInTheDocument()
    expect(client.getHistory).toHaveBeenCalledWith(50, 0)

    await userEvent.click(screen.getByRole('button', { name: /load more/i }))
    expect(await screen.findByText('Page 2 Book')).toBeInTheDocument()
    expect(client.getHistory).toHaveBeenCalledWith(50, 50)
  })

  it('hides "Load more" when the first page is short (no more rows)', async () => {
    vi.mocked(client.getHistory).mockResolvedValueOnce([item('a0', 'Only Book')])
    render(<HistoryView />)
    expect(await screen.findByText('Only Book')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /load more/i })).not.toBeInTheDocument()
  })
})
```

- [ ] **Step 2: Run to verify failure**

Run (in `frontend/`): `npm run test -- HistoryView`
Expected: FAIL — `getHistory` isn't called with `(50, 0)` (current signature takes no args) and there's no "Load more" button.

- [ ] **Step 3: Update the client**

In `frontend/src/api/client.ts` replace:

```ts
export function getHistory(): Promise<HistoryItem[]> {
  return getJson<HistoryItem[]>('/history')
}
```

with:

```ts
export function getHistory(limit = 50, offset = 0): Promise<HistoryItem[]> {
  return getJson<HistoryItem[]>(`/history?limit=${limit}&offset=${offset}`)
}
```

- [ ] **Step 4: Update the view**

Replace `frontend/src/views/HistoryView.tsx` with:

```tsx
import { useEffect, useState } from 'react'
import { getHistory, type HistoryItem } from '../api/client'
import './HistoryView.css'

const PAGE_SIZE = 50

export default function HistoryView() {
  const [items, setItems] = useState<HistoryItem[] | null>(null)
  const [hasMore, setHasMore] = useState(false)
  const [loadingMore, setLoadingMore] = useState(false)

  useEffect(() => {
    void getHistory(PAGE_SIZE, 0).then((page) => {
      setItems(page)
      setHasMore(page.length === PAGE_SIZE)
    })
  }, [])

  async function loadMore() {
    if (items === null) return
    setLoadingMore(true)
    try {
      const page = await getHistory(PAGE_SIZE, items.length)
      setItems([...items, ...page])
      setHasMore(page.length === PAGE_SIZE)
    } finally {
      setLoadingMore(false)
    }
  }

  if (items === null) return <p>Loading…</p>
  if (items.length === 0) return <p>Nothing here yet — finish a book and it'll show up.</p>

  return (
    <div>
      <h2>Reading history</h2>
      <ul className="history-list">
        {items.map((h) => (
          <li key={h.id} className="history-row">
            <div className="history-main">
              <span className="history-title">{h.title}</span>
              <span className="history-authors">{h.authors.join(', ')}</span>
            </div>
            <div className="history-meta">
              {h.rating != null && <span className="history-rating">{'★'.repeat(h.rating)}</span>}
              {h.format && <span className="history-format">{h.format}</span>}
              {h.date_completed && <span className="history-date">{h.date_completed}</span>}
            </div>
          </li>
        ))}
      </ul>
      {hasMore && (
        <button className="history-load-more" onClick={() => void loadMore()} disabled={loadingMore}>
          {loadingMore ? 'Loading…' : 'Load more'}
        </button>
      )}
    </div>
  )
}
```

- [ ] **Step 5: Run test, lint, build**

Run (in `frontend/`): `npm run test -- HistoryView` → PASS
Run: `npm run lint` → clean
Run: `npm run build` → succeeds

- [ ] **Step 6: Commit**

```bash
git add frontend/src/api/client.ts frontend/src/views/HistoryView.tsx frontend/src/views/HistoryView.test.tsx
git commit -m "feat(web): paginate history with Load more (INF-029)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 5: Serve the SPA same-origin from FastAPI

**Files:**
- Modify: `src/agentic_librarian/api/main.py` (add a root route + catch-all fallback, registered LAST)
- Test: `test/unit/test_spa_serving.py` (create)

Serve the built SPA from a directory given by `SPA_DIST_DIR` (the Docker stage copies `dist` there). Real files are served as-is; unknown non-API paths fall back to `index.html` so React-Router deep links work. API routes are declared above the catch-all, so they win. A path-traversal guard keeps the catch-all from escaping the dist dir.

- [ ] **Step 1: Write the failing test**

Create `test/unit/test_spa_serving.py`:

```python
"""Lift 2 Stage 4: FastAPI serves the built SPA same-origin, with an index.html fallback
for client-side routes, real built files served as-is, API routes taking precedence, and a
path-traversal guard on the catch-all."""

from fastapi.testclient import TestClient

from agentic_librarian.api.main import app


def _build_dist(root):
    (root / "assets").mkdir()
    (root / "index.html").write_text('<!doctype html><div id="root"></div>')
    (root / "assets" / "app.js").write_text('console.log("spa")')
    return root


def test_root_serves_index(tmp_path, monkeypatch):
    dist = _build_dist(tmp_path)
    monkeypatch.setenv("SPA_DIST_DIR", str(dist))
    r = TestClient(app).get("/")
    assert r.status_code == 200
    assert 'id="root"' in r.text


def test_unknown_path_falls_back_to_index(tmp_path, monkeypatch):
    dist = _build_dist(tmp_path)
    monkeypatch.setenv("SPA_DIST_DIR", str(dist))
    r = TestClient(app).get("/add")  # a client route, not a file
    assert r.status_code == 200
    assert 'id="root"' in r.text


def test_real_asset_is_served(tmp_path, monkeypatch):
    dist = _build_dist(tmp_path)
    monkeypatch.setenv("SPA_DIST_DIR", str(dist))
    r = TestClient(app).get("/assets/app.js")
    assert r.status_code == 200
    assert 'console.log("spa")' in r.text


def test_api_route_wins_over_spa_catch_all(tmp_path, monkeypatch):
    dist = _build_dist(tmp_path)
    monkeypatch.setenv("SPA_DIST_DIR", str(dist))
    r = TestClient(app).get("/health")  # unauthenticated API route
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_path_traversal_is_blocked(tmp_path, monkeypatch):
    # Secret lives OUTSIDE the dist dir. Call the handler directly — the HTTP client would
    # normalize the `..` away before routing, so a direct call is what exercises the guard.
    dist = tmp_path / "dist"
    dist.mkdir()
    _build_dist(dist)
    (tmp_path / "secret.txt").write_text("TOP-SECRET")
    monkeypatch.setenv("SPA_DIST_DIR", str(dist))

    from agentic_librarian.api.main import spa_catch_all

    resp = spa_catch_all("../secret.txt")
    # The escaped path is refused → falls back to the SPA shell (index.html), not the secret.
    assert resp.path.endswith("index.html")
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest test/unit/test_spa_serving.py -v`
Expected: FAIL — `GET /` returns 404 (no SPA routes yet).

- [ ] **Step 3: Implement static serving (append to the BOTTOM of `main.py`, after every other route + include_router)**

Add the imports near the top of `main.py`:

```python
import os

from fastapi.responses import FileResponse
```

Then append at the very end of `src/agentic_librarian/api/main.py` (must come after all `@app.get/post` and `include_router` calls so API routes match first):

```python
# ---------------------------------------------------------------------------
# SPA static serving (Lift 2 Stage 4) — served same-origin from this container.
# Registered LAST so every API route above takes precedence over the catch-all.
# ---------------------------------------------------------------------------


def _spa_dir() -> str:
    return os.environ.get("SPA_DIST_DIR", "/app/static")


def _spa_index() -> FileResponse:
    return FileResponse(os.path.join(_spa_dir(), "index.html"))


@app.get("/")
def spa_root():
    return _spa_index()


@app.get("/{full_path:path}")
def spa_catch_all(full_path: str):
    """Serve a real built file when one exists; otherwise return the SPA shell so
    client-side routes (e.g. /add, /history) resolve. The realpath check is a
    path-traversal guard — a candidate that escapes the dist dir falls back to the shell."""
    root = os.path.realpath(_spa_dir())
    candidate = os.path.realpath(os.path.join(root, full_path))
    if (candidate == root or candidate.startswith(root + os.sep)) and os.path.isfile(candidate):
        return FileResponse(candidate)
    return _spa_index()
```

- [ ] **Step 4: Run to verify pass**

Run: `pytest test/unit/test_spa_serving.py -v`
Expected: PASS (all five).

- [ ] **Step 5: Prove no API regression**

Run: `pytest test/unit/test_api_history.py test/unit/test_api_works.py test/unit/test_api_requires_auth.py -v`
Expected: PASS — the catch-all does not shadow `/history`, `/works`, `/health`, etc.

- [ ] **Step 6: Commit**

```bash
git add src/agentic_librarian/api/main.py test/unit/test_spa_serving.py
git commit -m "feat(api): serve the SPA same-origin with an index.html fallback

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 6: Multi-stage Dockerfile — build the SPA, copy it into the runtime

**Files:**
- Modify: `Dockerfile.api`

Add a Node 22 build stage (Vite 8 needs Node ≥22.12; `node:22-slim` tracks the latest 22.x) that compiles `frontend/` into `/spa/dist`, then copy that into the Python runtime at `/app/static` and set `SPA_DIST_DIR`. The runtime still has no Node.

- [ ] **Step 1: Rewrite `Dockerfile.api`**

Replace the file with:

```dockerfile
# Production API image (Lift 0 → Lift 2 Stage 4: now multi-stage, also serving the SPA).
# Stage 1 builds the Vite SPA with Node; the slim Python runtime copies the static output
# and has no Node/build tools, no editable install, uvicorn entrypoint.

# --- Stage 1: build the SPA ---
FROM node:22-slim AS spa-build
WORKDIR /spa
# Install deps from the lockfile first (cached unless deps change), then build.
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build   # → /spa/dist

# --- Stage 2: Python runtime ---
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    SPA_DIST_DIR=/app/static

RUN useradd --create-home appuser
WORKDIR /app

# Non-editable install of the package + prod deps only (no [dev] or [claude] extras).
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir .

# The built SPA, served same-origin by FastAPI (see api/main.py spa_catch_all).
COPY --from=spa-build /spa/dist /app/static

USER appuser
EXPOSE 8080

# Cloud Run injects PORT (default 8080). Shell form for env expansion; exec for signals.
CMD exec uvicorn agentic_librarian.api.main:app --host 0.0.0.0 --port ${PORT:-8080} --timeout-graceful-shutdown 25
```

- [ ] **Step 2: Build the image locally to verify both stages succeed**

Run (from `C:\dev\agentic_librarian`): `docker build -f Dockerfile.api -t librarian-api:stage4 .`
Expected: the Node stage runs `npm ci` + `npm run build` (emitting `dist/`), the Python stage installs and copies `/app/static`. Build succeeds.

- [ ] **Step 3: Smoke the SPA out of the built image (no DB, no Firebase needed)**

Run:
```bash
docker run --rm -d -p 8080:8080 -e DATABASE_URL=postgresql://x:x@nohost:5432/x --name spa-smoke librarian-api:stage4
curl -fsS http://localhost:8080/health
curl -fsS http://localhost:8080/ | grep -q 'id="root"' && echo "SPA OK"
docker rm -f spa-smoke
```
Expected: `/health` returns `{"status":"ok"}`; `/` contains `id="root"` → prints `SPA OK`.

- [ ] **Step 4: Commit**

```bash
git add Dockerfile.api
git commit -m "build: multi-stage image — compile the Vite SPA, serve it from the runtime

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 7: `deploy.yml` — paths, gate flip, secrets/env, max-instances, `GET /` smoke

**Files:**
- Modify: `.github/workflows/deploy.yml`

This is the change that opens the gate on PR-B's deploy (spec D2). It references secrets that must already exist (Task 8 / operator rollout) — which is why PR-B is **held** until the operator has provisioned.

- [ ] **Step 1: Add `frontend/**` to the path filter**

In the `on.push.paths` list, add `"frontend/**"`:

```yaml
    paths:
      - "src/**"
      - "frontend/**"
      - "pyproject.toml"
      - "Dockerfile.api"
      - ".github/workflows/deploy.yml"
      - ".dockerignore"
```

- [ ] **Step 2: Flip the gate + wire secrets/env/instances in the deploy step**

Replace the `flags:` block of the `Deploy to Cloud Run` step:

```yaml
          flags: >-
            --no-allow-unauthenticated
            --service-account=librarian-api-runtime@${{ vars.GCP_PROJECT_ID }}.iam.gserviceaccount.com
            --add-cloudsql-instances=${{ vars.GCP_CLOUDSQL_CONNECTION }}
            --set-secrets=DATABASE_URL=librarian-db-url:latest
            --set-env-vars=SIGNUP_MODE=invite,GOOGLE_CLOUD_PROJECT=${{ vars.GCP_PROJECT_ID }}
            --max-instances=1
            --memory=512Mi
```

with:

```yaml
          flags: >-
            --allow-unauthenticated
            --service-account=librarian-api-runtime@${{ vars.GCP_PROJECT_ID }}.iam.gserviceaccount.com
            --add-cloudsql-instances=${{ vars.GCP_CLOUDSQL_CONNECTION }}
            --set-secrets=DATABASE_URL=librarian-db-url:latest,GOOGLE_SEARCH_API_KEY=librarian-google-search-key:latest,GOOGLE_BOOKS_API_KEY=librarian-google-books-key:latest,HARDCOVER_API_KEY=librarian-hardcover-key:latest
            --set-env-vars=SIGNUP_MODE=invite,GOOGLE_CLOUD_PROJECT=${{ vars.GCP_PROJECT_ID }},AGENT_BACKEND=adk,GEMINI_MODEL=gemini-3.1-flash-lite,CLOUD_TASKS_QUEUE=${{ vars.GCP_CLOUD_TASKS_QUEUE }},ENRICH_TARGET_BASE_URL=${{ vars.GCP_RUN_BASE_URL }},ENRICH_INVOKER_SA=${{ vars.GCP_ENRICH_INVOKER_SA }},ENRICH_OIDC_AUDIENCE=${{ vars.GCP_RUN_BASE_URL }},SEARCH_ENGINE_ID=${{ vars.GCP_SEARCH_ENGINE_ID }}
            --max-instances=2
            --memory=512Mi
```

New repo **Variables** (the operator sets these in GitHub repo settings during the rollout; they are non-secret config): `GCP_CLOUD_TASKS_QUEUE` (the full queue path), `GCP_RUN_BASE_URL` (the Cloud Run service base URL — used for BOTH `ENRICH_TARGET_BASE_URL` and `ENRICH_OIDC_AUDIENCE` so the enqueue and verify audiences match — spec gotcha), `GCP_ENRICH_INVOKER_SA` (the invoker SA email), `GCP_SEARCH_ENGINE_ID`. The three keys are **secrets** in Secret Manager (Task 8 / `infra/09`).

- [ ] **Step 3: Extend the in-runner image smoke with a `GET /` SPA assertion**

In the `Smoke-test image in runner` step, after the `/health` loop and before `docker rm -f api-smoke`, add:

```bash
          if ! curl -fsS http://localhost:8080/ | grep -q 'id="root"'; then
            echo "::error::/ did not serve the SPA shell"; docker logs api-smoke; docker rm -f api-smoke; exit 1
          fi
```

This runs against the freshly built image with no DB and no Firebase — a broken SPA never reaches the registry. (The existing post-deploy live smoke keeps the `/health` + `401`-without-Firebase checks.)

- [ ] **Step 4: Validate the workflow YAML parses**

Run (from repo root, in the app image or any python): `python -c "import yaml; yaml.safe_load(open('.github/workflows/deploy.yml')); print('yaml ok')"`
Expected: `yaml ok`.

- [ ] **Step 5: Commit**

```bash
git add .github/workflows/deploy.yml
git commit -m "ci: open the IAM gate + wire prod secrets/env + GET / smoke (Stage 4)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 8: Provisioning scripts — Cloud Tasks queue/SA + prod key secrets

**Files:**
- Modify: `infra/00-config.sh` (add queue / invoker-SA / secret-name vars)
- Create: `infra/08-cloud-tasks.sh`
- Create: `infra/09-prod-secrets.sh`

Operator-run during the rollout. Match the existing one-shot, `source 00-config.sh` style (`set -euo pipefail`, fail-loud on `ALREADY_EXISTS`).

- [ ] **Step 1: Add config vars**

Append to `infra/00-config.sh` (before the final `gcloud config set project` line):

```bash
# --- Lift 2 Stage 4: async enrichment (Cloud Tasks) + deep-scout key secrets ---
export TASKS_QUEUE_NAME="librarian-enrich"
export TASKS_QUEUE_PATH="projects/${PROJECT_ID}/locations/${REGION}/queues/${TASKS_QUEUE_NAME}"
export ENRICH_INVOKER_SA_NAME="librarian-enrich-invoker"
export ENRICH_INVOKER_SA="${ENRICH_INVOKER_SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
export SECRET_GOOGLE_SEARCH="librarian-google-search-key"
export SECRET_GOOGLE_BOOKS="librarian-google-books-key"
export SECRET_HARDCOVER="librarian-hardcover-key"
```

- [ ] **Step 2: Create `infra/08-cloud-tasks.sh`**

```bash
#!/usr/bin/env bash
# Lift 2 Stage 4: provision the Cloud Tasks queue + the OIDC invoker SA, and grant the
# runtime SA the rights to enqueue tasks that call the internal enrich route as the invoker.
# One-shot (fails loud on ALREADY_EXISTS). Source-relative so it runs from anywhere.
set -euo pipefail
source "$(dirname "$0")/00-config.sh"

# 1) The queue the fast /books pass enqueues onto.
gcloud tasks queues create "${TASKS_QUEUE_NAME}" --location="${REGION}"

# 2) The service account whose OIDC token authorizes calls to the internal enrich route.
gcloud iam service-accounts create "${ENRICH_INVOKER_SA_NAME}" \
  --display-name="Cloud Tasks → internal enrich invoker"

# 3) That invoker SA may invoke the Cloud Run service (the now-open IAM gate still gates
#    the internal route via this OIDC identity, verified in-app).
gcloud run services add-iam-policy-binding "${SERVICE}" \
  --region="${REGION}" \
  --member="serviceAccount:${ENRICH_INVOKER_SA}" \
  --role="roles/run.invoker"

# 4) The runtime SA (which runs /books) may enqueue tasks…
gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
  --member="serviceAccount:${RUNTIME_SA}" \
  --role="roles/cloudtasks.enqueuer"

# 5) …and may mint OIDC tokens AS the invoker SA when creating those tasks.
gcloud iam service-accounts add-iam-policy-binding "${ENRICH_INVOKER_SA}" \
  --member="serviceAccount:${RUNTIME_SA}" \
  --role="roles/iam.serviceAccountUser"

echo "Cloud Tasks provisioned."
echo "  Set these GitHub repo Variables for deploy.yml:"
echo "    GCP_CLOUD_TASKS_QUEUE = ${TASKS_QUEUE_PATH}"
echo "    GCP_ENRICH_INVOKER_SA = ${ENRICH_INVOKER_SA}"
echo "    GCP_RUN_BASE_URL      = (the Cloud Run service URL; used for ENRICH_TARGET_BASE_URL + ENRICH_OIDC_AUDIENCE)"
echo "    GCP_SEARCH_ENGINE_ID  = (the Programmable Search Engine id)"
```

- [ ] **Step 3: Create `infra/09-prod-secrets.sh`**

```bash
#!/usr/bin/env bash
# Lift 2 Stage 4: create the three deep-scout key secrets from the operator's environment
# (never hardcoded), add the first version, and grant the runtime SA read access.
# Export the three source vars before running, e.g.:
#   export GOOGLE_SEARCH_API_KEY=... GOOGLE_BOOKS_API_KEY=... HARDCOVER_API_KEY=...
# One-shot (fails loud on ALREADY_EXISTS).
set -euo pipefail
source "$(dirname "$0")/00-config.sh"

: "${GOOGLE_SEARCH_API_KEY:?export GOOGLE_SEARCH_API_KEY before running}"
: "${GOOGLE_BOOKS_API_KEY:?export GOOGLE_BOOKS_API_KEY before running}"
: "${HARDCOVER_API_KEY:?export HARDCOVER_API_KEY before running}"

create_secret () {
  local name="$1" value="$2"
  gcloud secrets create "${name}" --replication-policy="automatic"
  printf '%s' "${value}" | gcloud secrets versions add "${name}" --data-file=-
  gcloud secrets add-iam-policy-binding "${name}" \
    --member="serviceAccount:${RUNTIME_SA}" \
    --role="roles/secretmanager.secretAccessor"
}

create_secret "${SECRET_GOOGLE_SEARCH}" "${GOOGLE_SEARCH_API_KEY}"
create_secret "${SECRET_GOOGLE_BOOKS}"  "${GOOGLE_BOOKS_API_KEY}"
create_secret "${SECRET_HARDCOVER}"     "${HARDCOVER_API_KEY}"

echo "Prod key secrets created and granted to ${RUNTIME_SA}."
```

- [ ] **Step 4: Lint the scripts (syntax only — do NOT execute; they mutate prod)**

Run (in the app image or any bash): `bash -n infra/08-cloud-tasks.sh && bash -n infra/09-prod-secrets.sh && echo "scripts parse ok"`
Expected: `scripts parse ok`. (Execution happens in the operator rollout, Task 10.)

- [ ] **Step 5: Commit**

```bash
git add infra/00-config.sh infra/08-cloud-tasks.sh infra/09-prod-secrets.sh
git commit -m "infra: provisioning scripts for Cloud Tasks queue/SA + prod key secrets

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 9: Update `security.md` for the open IAM gate

**Files:**
- Modify: `docs/project_notes/security.md`

- [ ] **Step 1: Add the open-gate boundary subsection**

Append to `docs/project_notes/security.md` (after the "Multi-user trust boundary" section):

```markdown
## Cloud Run IAM gate OPEN (Lift 2 Stage 4, 2026-06-10)

The Cloud Run service is now deployed `--allow-unauthenticated`: the platform IAM gate no
longer fronts the app. The boundary is therefore enforced entirely **in-app**:

- **Every user-facing route is Firebase-gated** — the Lift 1 auth dependency verifies a
  Firebase ID token (401 missing/invalid, 403 verified-but-not-invited, 503 cert-fetch
  outage). `SIGNUP_MODE=invite` keeps the door closed to the uninvited.
- **`/health` is intentionally open** (unauthenticated liveness); `GET /` and the SPA static
  assets are public by design (the app shell carries no data; all data calls are Firebase-gated).
- **The internal enrich route (`POST /internal/enrich/{work_id}`) is queue-OIDC-gated** — it
  verifies the Cloud Tasks invoker SA's Google-signed OIDC token (email == `ENRICH_INVOKER_SA`,
  `email_verified`, audience == `ENRICH_OIDC_AUDIENCE`) and **fails closed** if either var is
  unset. This gate is independent of (and survives) the now-open IAM gate.

**Expiring assumptions:** transport-level "single user" reasoning (the SEC-001/SEC-002
residual-risk arguments, the absence of rate limiting) no longer holds now that the gate is
open to invited friends. A missing/incomplete Cloud Tasks setup degrades to enrichment
**no-ops**, not exposure. Full security re-review — rate limiting, abuse controls, non-Google
OIDC `email_verified` semantics — is Lift 3 (open signup).
```

- [ ] **Step 2: Commit**

```bash
git add docs/project_notes/security.md
git commit -m "docs(security): record the open IAM gate boundary (Stage 4)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 10: Write the operator rollout runbook

**Files:**
- Create: `docs/runbooks/lift2-stage4-rollout.md`

This is the script the human executes (the actual provisioning/migration/merge/verify). It encodes the D2-forced ordering: **provision + migrate BEFORE merging PR-B**, because merging PR-B opens the gate atomically with the new image.

- [ ] **Step 1: Create the runbook**

Create `docs/runbooks/lift2-stage4-rollout.md`:

```markdown
# Lift 2 Stage 4 — Rollout Runbook

Takes the friends-and-family beta live: opens the Cloud Run IAM gate, provisions async
enrichment, applies the first prod write-path migration, and verifies the live stack.

**Ordering is load-bearing (spec D2):** merging PR-B's deploy opens the gate *and* ships the
SPA/chat-reachable image at once. So **provision + migrate while the gate is still closed**,
then merge PR-B as the deliberate gate-opening act.

**Prereqs:** PR-A merged; PR-B reviewed/approved and **held** (not merged); `gcloud` authed to
`agentic-librarian-prod`; cloud-sql-proxy available.

## 1. Provision (gate still CLOSED)
1. `bash infra/08-cloud-tasks.sh` — creates the queue, the invoker SA, and the IAM grants.
   Note the printed `GCP_CLOUD_TASKS_QUEUE` / `GCP_ENRICH_INVOKER_SA` values.
2. Export the three keys, then `bash infra/09-prod-secrets.sh` — creates the key secrets +
   grants the runtime SA `secretAccessor`.
3. Set the GitHub repo **Variables** PR-B's `deploy.yml` reads:
   - `GCP_CLOUD_TASKS_QUEUE` = the full queue path
   - `GCP_RUN_BASE_URL` = the Cloud Run service base URL (used for BOTH `ENRICH_TARGET_BASE_URL`
     and `ENRICH_OIDC_AUDIENCE` — they MUST match or every enrichment 403s)
   - `GCP_ENRICH_INVOKER_SA` = the invoker SA email
   - `GCP_SEARCH_ENGINE_ID` = the Programmable Search Engine id

## 2. Back up prod (first prod write boundary)
- `gcloud sql export sql librarian-sql gs://agentic-librarian-prod-backups/pre-stage4-$(date +%Y%m%d).sql.gz --database=agentic_librarian`
- This rollout is the first prod write (chat). From here: **back up before every migration.**

## 3. Apply the migration (gate still CLOSED)
- Start cloud-sql-proxy to the prod instance; run `alembic upgrade head` via the docker wrapper
  (Lift 1 runbook pattern). This moves prod from the Lift 1 head `c804d02d6fbb` to the Stage 1
  head `30f1e46533e9` (`conversations`, `messages`, `usage.conversation_id` FK).
- Verify: `alembic current` shows `30f1e46533e9`; the `conversations`/`messages` tables exist.

## 4. Sanity (gate still CLOSED)
- The current (old) image is still healthy: minted-IAM-token `GET /health` returns ok.

## 5. Merge PR-B → CD opens the gate
- Merge PR-B. CD builds the multi-stage image, deploys with the new env/secrets, and flips to
  `--allow-unauthenticated`. The CD in-runner smoke asserts `/health` + `GET /` SPA; the live
  smoke asserts `/health` + `401`-without-Firebase.

## 6. Manual live verification (gate now OPEN)
Run through the browser as an invited user:
- [ ] Google sign-in succeeds; the SPA shell loads at `/`.
- [ ] A chat turn streams live activity then a reply.
- [ ] Add-a-book logs a read (fast pass returns in seconds).
- [ ] ~2 minutes later, deep enrichment has completed (tropes appear on the work) — confirms the
      Cloud Task fired and the queue-OIDC internal route accepted it.
- [ ] A metered `usage` row was written (check via a `GET /works` enriched payload or DB peek).
- [ ] `/history` paginates ("Load more" fetches the next page).

## 7. Cost watch
- Confirm the budget alert is live; eyeball the first real `usage` rows; confirm `max-instances=2`.

## Rollback (ONLY if step 5/6 fails or the deploy is broken)
- Revert the PR-B merge commit on `main`. The next CD deploy re-applies
  `--no-allow-unauthenticated` (re-closing the gate) and reverts the image in one move.
- The applied migration is **additive and safe to leave** — do not run a down-migration; the
  unused `conversations`/`messages` tables are harmless.
```

- [ ] **Step 2: Commit**

```bash
git add docs/runbooks/lift2-stage4-rollout.md
git commit -m "docs(runbook): Lift 2 Stage 4 rollout (provision→migrate→merge→verify)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

**PR-B is now complete.** Open it, address review — but **do not merge** until the operator has completed runbook steps 1–4. Merging PR-B is runbook step 5.

---

# PART 3 — Operator rollout (out-of-band)

The human executes `docs/runbooks/lift2-stage4-rollout.md` steps 1–7. The agent's role ends at "PR-B reviewed and held"; surface the runbook and confirm the provisioning/migration are done before the PR-B merge.

---

## Final review

After all tasks, dispatch a final holistic code review over the whole branch (both PRs' diffs) per subagent-driven-development, then use `superpowers:finishing-a-development-branch`. Note the merge of PR-B is gated on the operator rollout (it is the gate-opening act), so "finishing" PR-B = open-PR-and-hold, not merge.
