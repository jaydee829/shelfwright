# Phase 6.2 PR-B Async & Sessions Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop parking blocking work on the event loop (#93) and stop holding DB sessions across external calls (#94), on branch `refactor/phase6-2b-async-sessions`, including the user-approved chat contract for async deep enrichment.

**Architecture:** A signature-preserving `make_async_tool` wrapper moves all 11 mesh tools onto `asyncio.to_thread`; auth's verify+DB block moves off-loop the same way; Cloud Tasks clients are cached and the import enqueue loop runs in a thread. Every enrichment/availability site restructures to *read-session → external work with no session → fresh write-session with dedup re-check*. The chat discovery tool (`enrich_and_persist_work`) re-routes through the two-phase path (fast pass + queued deep pass) while KEEPING its `str | None` contract — it has three non-ADK callers (`agents/pipeline.py:39`, `agents/backends/claude.py:94`, `claude_tools.py:93-96`); the "investigating in the background" communication flows through `add_book_to_history`'s message and the Librarian/portable instruction texts.

**Tech Stack:** asyncio.to_thread (copies ContextVars — the `runtime._record_event_usage` precedent), google-adk 2.2.0 FunctionTool, SQLAlchemy, Cloud Tasks, pytest.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-07-12-phase6-2-concurrency-capacity-design.md` (PR-B sections + "Chat contract").
- **Session rule (#94):** no `get_session()` block may contain a scout/LLM/embedding/Thunder/Cloud-Tasks network call. Read-session → external → write-session with dedup re-check.
- **Chat contract (user decision 2026-07-12):** deep enrichment is async, BUT (a) the user must be told the Librarian is investigating in the background, (b) no trope/style-based conclusions or recommendations anchored on a book whose deep pass is pending this turn. `enrich_and_persist_work` keeps returning `str | None` (work_id) — its three non-ADK callers depend on it.
- `make_async_tool(fn)`: `async def` wrapper via `asyncio.to_thread`, with `functools.wraps` AND `__signature__ = inspect.signature(fn)`; ADK schema generation must be unchanged (test asserts declaration equality).
- Auth: `get_current_user` stays `async def`; ONLY `current_user_id.set(...)` + `return` remain after the `to_thread` call; HTTPExceptions raised inside the thread propagate unchanged.
- Revert `max_overflow` 10 → **2** in `db/session.py` (the PR-A interim comment promises this) and update the comment + `test_pool_config.py` + key_facts.md.
- Detached-instance rule: capture ORM scalars (ids, names) BEFORE a session closes (CI-only DetachedInstanceError lesson).
- Tests: `.venv/Scripts/python -m pytest ...` from repo root; new unit tests DB-free/sqlite, no `db_integration` marker; `uvx ruff check` + `uvx ruff format` on every touched file (CI pre-commit enforces format). db_integration suites (two_phase, availability, mcp tools, auth) are CI-gated — state that explicitly, never claim them locally verified.
- No `[skip ci]` in commit messages. Do not modify `frontend/**`.

---

### Task 1: `make_async_tool` — all mesh tools off the event loop (#93)

**Files:**
- Modify: `src/agentic_librarian/agents/services.py` (imports; wrapper; 11 `FunctionTool(...)` registrations at lines 57, 96-99, 170-177)
- Test: `test/unit/test_make_async_tool.py` (new)

**Interfaces:**
- Produces: `make_async_tool(fn)` in `agentic_librarian.agents.services` — coroutine-function wrapper preserving `__name__`, `__doc__`, `__signature__`.

- [ ] **Step 1: Write the failing tests**

Create `test/unit/test_make_async_tool.py`:

```python
"""#93: mesh tools must run off the event loop without changing their ADK schema."""

import asyncio
import inspect
import threading

from google.adk.tools import FunctionTool

from agentic_librarian.agents.services import make_async_tool


def _sample_tool(title: str, rating: int | None = None) -> str:
    """Docstring the ADK schema uses."""
    return f"{title}:{rating}:{threading.current_thread().name}"


def test_wrapper_is_coroutine_with_preserved_metadata():
    wrapped = make_async_tool(_sample_tool)
    assert asyncio.iscoroutinefunction(wrapped)
    assert wrapped.__name__ == "_sample_tool"
    assert wrapped.__doc__ == _sample_tool.__doc__
    assert inspect.signature(wrapped) == inspect.signature(_sample_tool)


def test_adk_declaration_unchanged():
    sync_decl = FunctionTool(_sample_tool)._get_declaration()
    async_decl = FunctionTool(make_async_tool(_sample_tool))._get_declaration()
    assert async_decl.name == sync_decl.name
    assert async_decl.description == sync_decl.description
    assert str(async_decl.parameters) == str(sync_decl.parameters)


def test_wrapper_runs_off_the_event_loop():
    wrapped = make_async_tool(_sample_tool)

    async def _run():
        return await wrapped("Dune", rating=5)

    result = asyncio.run(_run())
    title, rating, thread_name = result.split(":")
    assert (title, rating) == ("Dune", "5")
    assert thread_name != threading.main_thread().name  # executed in a worker thread


def test_contextvars_survive_to_thread():
    import contextvars

    var = contextvars.ContextVar("probe")

    def _reads_var() -> str:
        return var.get()

    wrapped = make_async_tool(_reads_var)

    async def _run():
        var.set("carried")
        return await wrapped()

    assert asyncio.run(_run()) == "carried"
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/Scripts/python -m pytest test/unit/test_make_async_tool.py -v`
Expected: `ImportError: cannot import name 'make_async_tool'`.

- [ ] **Step 3: Implement in `services.py`**

Add to the imports at the top:

```python
import asyncio
import functools
import inspect
```

After the imports (before `_model_name`), add:

```python
def make_async_tool(fn):
    """Wrap a sync MCP tool as a coroutine running via asyncio.to_thread (GH #93):
    ADK's FunctionTool calls sync functions INLINE on the event loop, so one user's
    slow tool (DB + embedding + scout calls) stalls every concurrent request and SSE
    stream on the instance. to_thread copies the calling context, so the
    get_required_user_id() ContextVar still resolves (the runtime._record_event_usage
    precedent). __signature__/__name__/__doc__ are preserved because ADK builds the
    tool schema from them."""

    @functools.wraps(fn)
    async def _async_tool(*args, **kwargs):
        return await asyncio.to_thread(fn, *args, **kwargs)

    _async_tool.__signature__ = inspect.signature(fn)
    return _async_tool
```

Wrap every `FunctionTool(x)` registration (11 sites):
- Line ~57: `tools=[FunctionTool(make_async_tool(get_user_trope_preferences))],`
- Lines ~96-99 (Critic): each of `search_internal_database`, `get_work_details`, `check_reading_history`, `get_recommendation_candidates` becomes `FunctionTool(make_async_tool(...))`.
- Lines ~170-177 (Librarian): each of `get_unacted_suggestions`, `get_recommendation_candidates`, `check_reading_history`, `add_book_to_history`, `enrich_and_persist_work`, `update_reading_status`, `update_suggestion_status`, `log_suggestion` becomes `FunctionTool(make_async_tool(...))`.

(`AgentTool`/`GoogleSearchTool` registrations are untouched.)

- [ ] **Step 4: Run tests**

Run: `.venv/Scripts/python -m pytest test/unit/test_make_async_tool.py test/unit/test_agent_services.py test/unit/test_write_authorization.py -v`
Expected: all pass. If `test_agent_services.py` or `test_write_authorization.py` assert on tool function identity/names, they must still pass because `functools.wraps` preserves `__name__` — if they fail on identity (`is` checks against the raw function), update those assertions to compare `__name__` and report it.

- [ ] **Step 5: Lint, format, commit**

`uvx ruff check src/agentic_librarian/agents/services.py test/unit/test_make_async_tool.py` + `uvx ruff format` same, re-check.

```bash
git add src/agentic_librarian/agents/services.py test/unit/test_make_async_tool.py
git commit -m "perf(mesh): run all FunctionTool bodies off the event loop via make_async_tool (#93)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 2: Auth verify+DB off the loop (#93)

**Files:**
- Modify: `src/agentic_librarian/api/auth.py:65-125`
- Test: `test/unit/test_auth_offloop.py` (new)

**Interfaces:**
- Produces: module-private `_resolve_user(token: str) -> AuthenticatedUser` (sync; raises HTTPException). `get_current_user` signature unchanged.

- [ ] **Step 1: Write the failing test**

Create `test/unit/test_auth_offloop.py`:

```python
"""#93: the auth dependency's verify+DB work must run off the event loop, while the
ContextVar set stays in the coroutine (the documented constraint)."""

import asyncio
import threading
import uuid

from agentic_librarian.api import auth as auth_mod
from agentic_librarian.core.user_context import current_user_id


def test_resolve_runs_in_worker_thread_and_contextvar_set_in_coroutine(monkeypatch):
    seen = {}

    def fake_resolve(token):
        seen["thread"] = threading.current_thread().name
        return auth_mod.AuthenticatedUser(id=uuid.uuid4(), email="x@y.z")

    monkeypatch.setattr(auth_mod, "_resolve_user", fake_resolve)

    async def _run():
        result = await auth_mod.get_current_user(authorization="Bearer sometoken")
        return result, current_user_id.get()

    result, ctx_value = asyncio.run(_run())
    assert seen["thread"] != threading.main_thread().name  # resolve ran off-loop
    assert ctx_value == result.id  # ContextVar visible in the coroutine's context
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/Scripts/python -m pytest test/unit/test_auth_offloop.py -v`
Expected: FAIL — `auth_mod` has no `_resolve_user`.

- [ ] **Step 3: Restructure `get_current_user`**

Extract everything between the bearer validation and the ContextVar set into a sync helper placed above `get_current_user` (the bodies move VERBATIM — verify/claim/provision logic and all comments are unchanged):

```python
def _resolve_user(token: str) -> AuthenticatedUser:
    """Sync body of the auth dependency: verify the Firebase token and resolve (or
    provision) the user row. Runs via asyncio.to_thread (GH #93) — verify_id_token's
    JWT check and the DB query/insert otherwise block the event loop on EVERY request.
    HTTPExceptions raised here propagate through to_thread unchanged."""
    try:
        decoded = _verify_token(token)
    except (ValueError, firebase_auth.InvalidIdTokenError) as e:
        # ... [the three existing except blocks move here verbatim, lines 79-93] ...
    ...
    uid = decoded["uid"]
    email = (decoded.get("email") or "").strip().lower()
    email_verified = bool(decoded.get("email_verified"))

    with db_manager.get_session() as session:
        # ... [the existing claim/provision block moves here verbatim, lines 99-121] ...
        result = AuthenticatedUser(id=user.id, email=user.email)
    return result
```

`get_current_user` becomes:

```python
async def get_current_user(authorization: str | None = Header(None)) -> AuthenticatedUser:
    """FastAPI dependency: verify the Firebase ID token, resolve (or provision) the
    user row, set the user context, return the identity (ADR-048).

    MUST stay `async def`: a sync dependency runs in a threadpool, and a ContextVar
    set there is invisible to the endpoint. As a coroutine it shares the request
    task's context, which Starlette propagates into sync endpoints. The blocking
    verify+DB body runs via to_thread (GH #93); ONLY the ContextVar set lives here."""
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token.")
    token = authorization[7:].strip()
    if not token:
        raise HTTPException(status_code=401, detail="Missing bearer token.")
    result = await asyncio.to_thread(_resolve_user, token)
    current_user_id.set(result.id)
    return result
```

Add `import asyncio` to the module imports.

- [ ] **Step 4: Run tests**

Run: `.venv/Scripts/python -m pytest test/unit/test_auth_offloop.py test/unit/test_api_requires_auth.py -v`
Expected: all pass (`test_api_auth.py` is db_integration → CI gate; say so in the report).

- [ ] **Step 5: Lint, format, commit**

```bash
git add src/agentic_librarian/api/auth.py test/unit/test_auth_offloop.py
git commit -m "perf(auth): verify+DB resolve runs via to_thread; ContextVar set stays in the coroutine (#93)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 3: Cached Cloud Tasks clients + off-loop enqueue loop (#93)

**Files:**
- Modify: `src/agentic_librarian/imports/tasks.py:14-17`
- Modify: `src/agentic_librarian/enrichment/tasks.py:24-29`
- Modify: `src/agentic_librarian/api/imports.py:148-152` (commit's enqueue loop)
- Test: `test/unit/test_tasks_client_cache.py` (new)

**Interfaces:** none new; `_client()` stays the test seam in both modules.

- [ ] **Step 1: Write the failing tests**

Create `test/unit/test_tasks_client_cache.py`:

```python
"""#93: Cloud Tasks clients are cached at module level (one gRPC channel per process,
not one per enqueued row)."""

from types import SimpleNamespace

from agentic_librarian.enrichment import tasks as enrich_tasks
from agentic_librarian.imports import tasks as import_tasks


def _fake_tasks_v2(counter):
    class FakeClient:
        def __init__(self):
            counter.append(1)

        def create_task(self, parent, task):
            return SimpleNamespace(name="t")

    return SimpleNamespace(CloudTasksClient=FakeClient)


def test_import_client_is_cached(monkeypatch):
    counter = []
    monkeypatch.setattr(import_tasks, "_client_cached", None)
    monkeypatch.setitem(__import__("sys").modules, "google.cloud.tasks_v2", _fake_tasks_v2(counter))
    monkeypatch.setenv("IMPORT_TASKS_QUEUE", "q")
    monkeypatch.setenv("ENRICH_TARGET_BASE_URL", "https://x")
    monkeypatch.setenv("ENRICH_INVOKER_SA", "sa@x")
    import_tasks.enqueue_import_row("r1")
    import_tasks.enqueue_import_row("r2")
    assert len(counter) == 1  # one client for both enqueues


def test_enrich_client_is_cached(monkeypatch):
    counter = []
    monkeypatch.setattr(enrich_tasks, "_client_cached", None)
    monkeypatch.setitem(__import__("sys").modules, "google.cloud.tasks_v2", _fake_tasks_v2(counter))
    monkeypatch.setenv("CLOUD_TASKS_QUEUE", "q")
    monkeypatch.setenv("ENRICH_TARGET_BASE_URL", "https://x")
    monkeypatch.setenv("ENRICH_INVOKER_SA", "sa@x")
    enrich_tasks.enqueue_enrichment("w1")
    enrich_tasks.enqueue_enrichment("w2")
    assert len(counter) == 1
```

Note: `monkeypatch.setitem(sys.modules, "google.cloud.tasks_v2", ...)` intercepts the lazy `from google.cloud import tasks_v2` — if that interception proves unreliable for the `from X import Y` form in practice, monkeypatch `_client` itself to count and assert the cache via two calls to the REAL `_client()` with `tasks_v2` faked in `sys.modules`; the implementer may adjust the interception mechanics but the assertion (one construction, two enqueues) is binding.

- [ ] **Step 2: Run to verify failure**

Run: `.venv/Scripts/python -m pytest test/unit/test_tasks_client_cache.py -v`
Expected: FAIL — modules have no `_client_cached`.

- [ ] **Step 3: Cache the clients**

In BOTH `imports/tasks.py` and `enrichment/tasks.py`, replace the `_client()` function with (keep each module's existing docstring flavor):

```python
_client_cached = None
_client_lock = threading.Lock()


def _client():
    """Seam for tests. Cached (GH #93): CloudTasksClient opens a gRPC channel + auth —
    building one per enqueued row made a 2000-row commit open 2000 channels. Lazily
    imports google-cloud-tasks so the dependency is only needed where enqueue runs."""
    global _client_cached
    if _client_cached is None:
        with _client_lock:
            if _client_cached is None:
                from google.cloud import tasks_v2

                _client_cached = tasks_v2.CloudTasksClient()
    return _client_cached
```

Add `import threading` to both modules.

- [ ] **Step 4: Move commit's enqueue loop off the loop**

In `src/agentic_librarian/api/imports.py`, extract the loop into a module-level sync helper (place it above `commit`):

```python
def _enqueue_rows(row_ids: list[str]) -> None:
    """Sequential Cloud Tasks enqueues — sync gRPC calls, so callers on the event loop
    run this via asyncio.to_thread (GH #93: up to MAX_ROWS calls blocked the loop for
    minutes). A failed enqueue leaves the row 'pending'; the stale-pending retry (#99)
    recovers it."""
    for rid in row_ids:
        try:
            enqueue_import_row(rid)
        except Exception:  # noqa: BLE001 - see docstring
            logger.exception("import-row enqueue failed for row %s", rid)
```

In `commit()`, replace the loop (lines ~148-152) with:

```python
    await asyncio.to_thread(_enqueue_rows, enqueue_ids)
```

Add `import asyncio` to the module imports. (`retry()` is a sync `def` endpoint — FastAPI already runs it in a threadpool; its loop may now also call `_enqueue_rows(retry_ids)` directly for DRY, without to_thread.)

- [ ] **Step 5: Run tests**

Run: `.venv/Scripts/python -m pytest test/unit/test_tasks_client_cache.py test/unit/test_api_import_commit.py test/unit/test_api_import_routes_wired.py -v`
Expected: all pass.

- [ ] **Step 6: Lint, format, commit**

```bash
git add src/agentic_librarian/imports/tasks.py src/agentic_librarian/enrichment/tasks.py src/agentic_librarian/api/imports.py test/unit/test_tasks_client_cache.py
git commit -m "perf(tasks): cache CloudTasksClient; import enqueue loop runs off the event loop (#93)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 4: two_phase session split (#94)

**Files:**
- Modify: `src/agentic_librarian/enrichment/two_phase.py:48-102,137-152`
- Test: `test/unit/test_two_phase_sessions.py` (new); `test/integration/test_two_phase_fast.py` + `test_two_phase_deep.py` must keep passing in CI (behavior pins).

**Interfaces:**
- Produces: module-private `_run_scouts(manager, *, title, author, fmt, write_fallback_tropes=True) -> dict | None` (NO session) and `_persist_row(session, row) -> Work | None`. Public signatures of `enrich_fast`/`enrich_deep`/`add_read_event` unchanged — Task 6 calls `enrich_fast`.

- [ ] **Step 1: Write the failing tests**

Create `test/unit/test_two_phase_sessions.py`:

```python
"""#94: scouts must run with NO session held; persist re-checks dedup in a fresh session."""

from unittest.mock import MagicMock, patch

from agentic_librarian.enrichment import two_phase


def test_enrich_fast_runs_scouts_outside_any_session(monkeypatch):
    """The scout call happens between the read session and the write session."""
    session_state = {"open": 0}

    class FakeSession:
        def __enter__(self):
            session_state["open"] += 1
            m = MagicMock()
            # the dedup query chain must return None (no existing work), or enrich_fast
            # early-returns before ever reaching the scouts
            m.query.return_value.join.return_value.join.return_value.filter.return_value.filter.return_value.first.return_value = None
            return m

        def __exit__(self, *a):
            session_state["open"] -= 1
            return False

    fake_manager = MagicMock()
    fake_manager.get_session = lambda: FakeSession()
    monkeypatch.setattr(two_phase, "db_manager", fake_manager)

    scout_seen = {}

    def fake_run_scouts(manager, **kwargs):
        scout_seen["open_sessions_during_scout"] = session_state["open"]
        return None  # scouts found nothing -> enrich_fast returns None

    monkeypatch.setattr(two_phase, "_run_scouts", fake_run_scouts)
    with patch.object(two_phase, "create_fast_scout_manager", return_value=MagicMock()):
        result = two_phase.enrich_fast("New Book", "New Author")
    assert result is None
    assert scout_seen["open_sessions_during_scout"] == 0  # THE #94 assertion


def test_enrich_deep_runs_scouts_outside_any_session(monkeypatch):
    session_state = {"open": 0}

    class FakeSession:
        def __init__(self, work):
            self._work = work

        def __enter__(self):
            session_state["open"] += 1
            m = MagicMock()
            m.get.return_value = self._work
            return m

        def __exit__(self, *a):
            session_state["open"] -= 1
            return False

    work = MagicMock()
    work.title = "T"
    work.contributors = [MagicMock(role="Author", author=MagicMock(name="A"))]
    work.contributors[0].author.name = "A"
    work.editions = []
    fake_manager = MagicMock()
    fake_manager.get_session = lambda: FakeSession(work)
    monkeypatch.setattr(two_phase, "db_manager", fake_manager)

    scout_seen = {}

    def fake_run_scouts(manager, **kwargs):
        scout_seen["open_sessions_during_scout"] = session_state["open"]
        return None

    monkeypatch.setattr(two_phase, "_run_scouts", fake_run_scouts)
    with patch.object(two_phase, "create_deep_scout_manager", return_value=MagicMock()):
        assert two_phase.enrich_deep(work_id=MagicMock()) is True
    assert scout_seen["open_sessions_during_scout"] == 0
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/Scripts/python -m pytest test/unit/test_two_phase_sessions.py -v`
Expected: FAIL — `two_phase` has no `_run_scouts` (and today the scout call happens inside the session).

- [ ] **Step 3: Restructure `two_phase.py`**

Replace `_scout_and_persist` (lines 48-70) with two functions:

```python
def _run_scouts(manager, *, title: str, author: str, fmt: str, write_fallback_tropes: bool = True) -> dict | None:
    """Run a scout tier with NO session held (GH #94 — the external HTTP/LLM calls used
    to pin a pooled connection idle-in-transaction for their whole duration). Returns the
    persist-ready row dict, or None if the scouts found nothing. date_completed=None so
    persist writes NO reading_history (the read-event is logged separately)."""
    enriched = manager.enrich(title=title, author=author, format=fmt)
    if not enriched:
        return None
    return {
        "Title": title,
        "Author_1": author,
        "format": fmt,
        "skip_enrichment": False,
        "date_completed": None,
        **enriched,
        "genres": list(enriched.get("genres") or []),
        "moods": list(enriched.get("moods") or []),
        # after **enriched so a scout payload can never clobber the caller's choice
        "write_fallback_tropes": write_fallback_tropes,
    }


def _persist_row(session, row: dict) -> Work | None:
    """Persist a scouted row via the shared function (the only part needing a session)."""
    return persist_enriched_work(session, row, TropeManager(session=session), StyleManager(session=session))
```

Rewrite `enrich_fast` (same public contract):

```python
def enrich_fast(title: str, author: str, fmt: str = "ebook") -> tuple[UUID, bool] | None:
    """Fast pass: de-dup against the catalog; if new, run the API scouts (no session
    held, #94) and persist in a fresh session that RE-CHECKS the dedup (a concurrent
    import may have inserted the work while the scouts ran — the #95 TOCTOU window is
    back to milliseconds). Returns (work_id, created), or None if the scouts found
    nothing. Logs NO reading_history (see add_read_event)."""
    fmt = (fmt or "ebook")[:50]

    def _find_existing(session):
        return (
            session.query(Work)
            .join(WorkContributor)
            .join(Author)
            .filter(_normalized_col(Work.title) == _normalize(title))
            .filter(_normalized_col(Author.name) == _normalize(author))
            .first()
        )

    with db_manager.get_session() as session:
        existing = _find_existing(session)
        if existing:
            return existing.id, False  # UUID scalar — safe after close

    row = _run_scouts(create_fast_scout_manager(), title=title, author=author, fmt=fmt, write_fallback_tropes=False)
    if row is None:
        return None

    with db_manager.get_session() as session:
        existing = _find_existing(session)  # dedup re-check (#94/#95)
        if existing:
            return existing.id, False
        work = _persist_row(session, row)
        if work is None:
            return None
        session.flush()
        return work.id, True
```

Rewrite `enrich_deep` (same public contract; scalars captured before the read session closes):

```python
def enrich_deep(work_id: UUID) -> bool:
    """Deep pass (Cloud Tasks target): read the Work's identity in a short session, run
    the slow LLM scouts with NO session held (#94 — previously minutes idle-in-transaction
    at enrich-queue concurrency 4, where a late transient failure also re-paid every LLM
    call), then re-persist in a fresh session. Returns False if no Work has that id."""
    with db_manager.get_session() as session:
        work = session.get(Work, work_id)
        if work is None:
            return False
        author = next((c.author.name for c in work.contributors if c.role == "Author"), None)
        if author is None:
            return False
        title = work.title  # scalars captured before close (detached-instance rule)
        fmt = work.editions[0].format if work.editions else "ebook"

    row = _run_scouts(create_deep_scout_manager(), title=title, author=author, fmt=fmt)
    if row is None:
        return True  # scouts found nothing to add; the task is done, not retryable

    with db_manager.get_session() as session:
        _persist_row(session, row)
        session.flush()
    return True
```

NOTE the one deliberate behavior change: previously a deep pass whose scouts found nothing still "succeeded" inside one transaction; now it returns True without a write session — same external semantics (idempotent, non-retryable success).

- [ ] **Step 4: Run tests**

Run: `.venv/Scripts/python -m pytest test/unit/test_two_phase_sessions.py test/unit/test_two_phase_fallback_flag.py -v`
Expected: all pass. (`test_two_phase_fast.py`/`test_two_phase_deep.py` are db_integration — CI pins the end-to-end behavior; check they still COLLECT.)

- [ ] **Step 5: Lint, format, commit**

```bash
git add src/agentic_librarian/enrichment/two_phase.py test/unit/test_two_phase_sessions.py
git commit -m "refactor(enrichment): scouts run with no session held; persist re-checks dedup (#94)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 5: Availability three-phase batch (#94)

**Files:**
- Modify: `src/agentic_librarian/availability/service.py:73-107`
- Modify: `src/agentic_librarian/api/availability.py:33-75`
- Modify: `src/agentic_librarian/mcp/server.py:315-361` (`check_availability`)
- Test: `test/unit/test_availability_batch.py` (new); CI pins: `test_availability_api.py`, `test_availability_service_cache.py`, `test_check_availability_tool.py`.

**Interfaces:**
- Produces in `availability/service.py`:
  - `batch_availability(db_manager, libs: list[dict], items: list[tuple[str, str]]) -> dict[tuple[str, str, str], list | None]` — key `(slug, title, author)`; value = formats list, or None (Thunder failed → caller degrades to links-only). THREE phases: one read session for all fresh cache rows; Thunder fetches with NO session; one write session for the fetched results.
  - `availability_for(session, library, title, author)` KEPT (unchanged) for any straggler callers, marked in its docstring as the single-lookup path superseded by `batch_availability` on request paths.

- [ ] **Step 1: Write the failing test**

Create `test/unit/test_availability_batch.py`:

```python
"""#94: Thunder fetches happen with NO session open; cache reads/writes use short sessions."""

from unittest.mock import MagicMock

from agentic_librarian.availability import service


def test_batch_availability_fetches_outside_sessions(monkeypatch):
    session_state = {"open": 0}

    class FakeSession:
        def __enter__(self):
            session_state["open"] += 1
            m = MagicMock()
            m.get.return_value = None  # no cache rows -> everything is a miss
            return m

        def __exit__(self, *a):
            session_state["open"] -= 1
            return False

    fake_db = MagicMock()
    fake_db.get_session = lambda: FakeSession()

    fetch_seen = []

    def fake_fetch(slug, title):
        fetch_seen.append(session_state["open"])
        return []  # matched nothing (a real, cacheable result)

    monkeypatch.setattr(service.overdrive, "fetch_media", fake_fetch)

    libs = [{"slug": "lib1", "name": "Lib One"}]
    out = service.batch_availability(fake_db, libs, [("Dune", "Frank Herbert")])
    assert fetch_seen == [0]  # THE #94 assertion: no session open during Thunder call
    assert out[("lib1", "Dune", "Frank Herbert")] == []


def test_batch_availability_thunder_error_degrades_to_none(monkeypatch):
    fake_db = MagicMock()
    session = MagicMock()
    session.get.return_value = None
    fake_db.get_session.return_value.__enter__ = lambda s: session
    fake_db.get_session.return_value.__exit__ = lambda s, *a: False

    def boom(slug, title):
        raise service.ThunderError("down")

    monkeypatch.setattr(service.overdrive, "fetch_media", boom)
    out = service.batch_availability(fake_db, [{"slug": "l", "name": "L"}], [("T", "A")])
    assert out[("l", "T", "A")] is None  # ALWAYS-200 contract: badge degrades, links unaffected
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/Scripts/python -m pytest test/unit/test_availability_batch.py -v`
Expected: FAIL — `service` has no `batch_availability`.

- [ ] **Step 3: Implement `batch_availability` in `service.py`**

Add below `availability_for` (reusing `_normalize`, `_ttl`, `_shape_formats`, `_PROVIDER`, `AvailabilityCache`, `overdrive`, `ThunderError`, `datetime`/`UTC` — all already imported):

```python
def batch_availability(db_manager, libs: list[dict], items: list[tuple[str, str]]) -> dict:
    """Batch read-through cache in THREE phases (GH #94): (1) one short session reads
    every fresh cache row; (2) Thunder fetches for the misses run with NO session held
    (previously each miss pinned the request's connection idle-in-transaction);
    (3) one short session writes the fetched payloads back. Returns
    {(slug, title, author): formats-list | None} — None means Thunder failed for that
    lookup (caller degrades to links-only; the ALWAYS-200 contract is unchanged)."""
    now = datetime.now(UTC)
    results: dict = {}
    misses: list[tuple[dict, str, str]] = []

    with db_manager.get_session() as session:
        for lib in libs:
            for title, author in items:
                nt, na = _normalize(title), _normalize(author)
                row = session.get(AvailabilityCache, (_PROVIDER, lib["slug"], nt, na))
                if row is not None and (now - row.fetched_at.replace(tzinfo=UTC)) < _ttl():
                    results[(lib["slug"], title, author)] = row.payload.get("formats", [])
                else:
                    misses.append((lib, title, author))

    fetched: dict = {}
    for lib, title, author in misses:
        try:
            items_raw = overdrive.fetch_media(lib["slug"], title)  # raw title: better relevance
        except ThunderError:
            results[(lib["slug"], title, author)] = None  # degrade: no badge
            continue
        fetched[(lib["slug"], title, author)] = _shape_formats(items_raw, title, author)

    if fetched:
        with db_manager.get_session() as session:
            for (slug, title, author), formats in fetched.items():
                nt, na = _normalize(title), _normalize(author)
                row = session.get(AvailabilityCache, (_PROVIDER, slug, nt, na))
                payload = {"formats": formats}
                if row is None:
                    session.add(
                        AvailabilityCache(
                            provider=_PROVIDER,
                            library_slug=slug,
                            norm_title=nt,
                            norm_author=na,
                            payload=payload,
                            fetched_at=now,
                        )
                    )
                else:
                    row.payload = payload
                    row.fetched_at = now
                session.flush()
        results.update(fetched)
    return results
```

Update `availability_for`'s docstring first line to: `"""Single-lookup read-through cache (request paths use batch_availability, GH #94). ..."""` — body unchanged.

- [ ] **Step 4: Restructure the two callers**

`api/availability.py` `get_availability`: phase-split — session 1 loads `libs` and the works' `(id, title, author)` scalars, then close; `batch_availability(db_manager, libs, pairs)`; assemble `result` (`build_links` is pure). Replace the current single `with` block with:

```python
    with db_manager.get_session() as session:
        libs = [
            {"slug": r.library_slug, "name": r.display_name}
            for r in session.query(UserLibrary)
            .filter(UserLibrary.user_id == user.id, UserLibrary.provider == "libby")
            .order_by(UserLibrary.sort_order)
            .all()
        ]
        works = (
            session.query(Work)
            .options(joinedload(Work.contributors).joinedload(WorkContributor.author))
            .filter(Work.id.in_(parsed))
            .all()
        )
        # scalars captured before the session closes (detached-instance rule)
        work_rows = [(str(w.id), w.title, (_authors(w) or [""])[0]) for w in works]

    pairs = [(title, author) for _, title, author in work_rows]
    availability = service.batch_availability(db_manager, libs, pairs)

    for wid, title, author in work_rows:
        libby = []
        for lib in libs:
            formats = availability.get((lib["slug"], title, author))
            if formats:  # non-empty match → badge
                libby.append({"library": lib["name"], "slug": lib["slug"], "formats": formats})
        result[wid] = {"links": build_links(title, author, libraries=libs), "libby": libby}
    return result
```

`mcp/server.py` `check_availability`: same shape for one title — session 1 loads `libs` only (close), then `availability_service.batch_availability(db_manager, libs, [(title, author)])` (module import of the service already exists), then assemble `libraries`/`links`/`note` exactly as today (a `None` value where the old code caught an exception → same "Couldn't confirm live availability" note path; keep the per-lookup warning log when a value is None).

- [ ] **Step 5: Run tests**

Run: `.venv/Scripts/python -m pytest test/unit/test_availability_batch.py test/unit/test_availability_service.py test/unit/test_availability_links.py test/unit/test_mcp_tools.py -v`
Expected: all pass; db_integration availability suites collect (CI pins behavior).

- [ ] **Step 6: Lint, format, commit**

```bash
git add src/agentic_librarian/availability/service.py src/agentic_librarian/api/availability.py src/agentic_librarian/mcp/server.py test/unit/test_availability_batch.py
git commit -m "refactor(availability): three-phase batch — Thunder fetches hold no session (#94)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 6: Chat re-route — fast pass + queued deep pass + the contract (#93/#94 + user decision)

**Files:**
- Modify: `src/agentic_librarian/mcp/server.py:698-753` (`enrich_and_persist_work`), `:484-552` (`add_book_to_history`), imports.
- Modify: `src/agentic_librarian/agents/services.py` (Librarian instruction: IMPORT + ENRICH DISCOVERIES paragraphs)
- Modify: `src/agentic_librarian/agents/prompts.py:~95` (portable ENRICH DISCOVERIES paragraph — same contract)
- Test: `test/unit/test_chat_enrich_reroute.py` (new)

**Interfaces:**
- `enrich_and_persist_work(title, author, format) -> str | None` — CONTRACT UNCHANGED (three non-ADK callers: `agents/pipeline.py:39`, `agents/backends/claude.py:94`, `claude_tools.py`). Internals re-route through `two_phase.enrich_fast` + `enqueue_enrichment`.
- `add_book_to_history` return string gains the background-analysis note when the work was newly created.

- [ ] **Step 1: Write the failing tests**

Create `test/unit/test_chat_enrich_reroute.py`:

```python
"""Chat contract (user decision 2026-07-12): chat adds run the fast pass + queue the deep
pass; the user-facing message says the Librarian is still investigating."""

import uuid
from unittest.mock import patch

from agentic_librarian.mcp import server as mcp_server


def test_enrich_tool_routes_through_two_phase_and_enqueues():
    wid = uuid.uuid4()
    with (
        patch.object(mcp_server.two_phase, "enrich_fast", return_value=(wid, True)) as fast,
        patch.object(mcp_server, "enqueue_enrichment", return_value=True) as enq,
    ):
        result = mcp_server.enrich_and_persist_work(title="Dune", author="Frank Herbert")
    assert result == str(wid)
    fast.assert_called_once()
    enq.assert_called_once_with(str(wid))


def test_enrich_tool_dedup_hit_does_not_reenqueue():
    wid = uuid.uuid4()
    with (
        patch.object(mcp_server.two_phase, "enrich_fast", return_value=(wid, False)),
        patch.object(mcp_server, "enqueue_enrichment") as enq,
    ):
        result = mcp_server.enrich_and_persist_work(title="Dune", author="Frank Herbert")
    assert result == str(wid)
    enq.assert_not_called()


def test_enrich_tool_not_found_returns_none():
    with patch.object(mcp_server.two_phase, "enrich_fast", return_value=None):
        assert mcp_server.enrich_and_persist_work(title="Ghost", author="Nobody") is None


def test_add_book_message_mentions_background_analysis(monkeypatch):
    wid = uuid.uuid4()
    monkeypatch.setattr(mcp_server, "get_required_user_id", lambda: uuid.uuid4())
    with (
        patch.object(mcp_server.two_phase, "enrich_fast", return_value=(wid, True)),
        patch.object(mcp_server, "enqueue_enrichment", return_value=True),
        patch.object(
            mcp_server.two_phase, "add_read_event", return_value={"read_number": 1, "already_logged": False}
        ),
    ):
        msg = mcp_server.add_book_to_history(title="Dune", author="Frank Herbert")
    assert "background" in msg.lower()  # the Librarian relays this to the user
    assert "Dune" in msg


def test_add_book_existing_work_has_no_background_note(monkeypatch):
    wid = uuid.uuid4()
    monkeypatch.setattr(mcp_server, "get_required_user_id", lambda: uuid.uuid4())
    with (
        patch.object(mcp_server.two_phase, "enrich_fast", return_value=(wid, False)),
        patch.object(mcp_server, "enqueue_enrichment") as enq,
        patch.object(
            mcp_server.two_phase, "add_read_event", return_value={"read_number": 2, "already_logged": False}
        ),
    ):
        msg = mcp_server.add_book_to_history(title="Dune", author="Frank Herbert")
    assert "background" not in msg.lower()
    enq.assert_not_called()
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/Scripts/python -m pytest test/unit/test_chat_enrich_reroute.py -v`
Expected: FAIL — `mcp_server` has no `two_phase`/`enqueue_enrichment` attributes.

- [ ] **Step 3: Rewrite `enrich_and_persist_work`**

Add to `mcp/server.py` imports: `from agentic_librarian.enrichment import two_phase` and `from agentic_librarian.enrichment.tasks import enqueue_enrichment`.

```python
@mcp.tool()
def enrich_and_persist_work(title: str, author: str, format: str = "ebook") -> str | None:
    """De-dup a discovered book against the catalog; if new, run the FAST scouts and
    persist immediately, then queue the deep pass (tropes/styles) via Cloud Tasks —
    the same two-phase path bulk import uses (GH #93/#94: the old all-scouts inline run
    blocked the event loop for minutes). Returns the work_id, or None if the title did
    not resolve. A NEWLY persisted work has no trope/style fingerprint until the deep
    pass lands (~1-2 min): tell the user you are still investigating it, and do not
    anchor trope-based recommendations on it this turn. This is the single write
    surface for discoveries — a future authorization layer (SEC-002) wraps here."""
    # SEC-002: this is a write path fed by web-derived strings — validate shape upfront.
    if not _valid_name(title):
        logger.warning("enrich_and_persist_work rejected invalid title %r", title)
        return None
    if not _valid_name(author):
        logger.warning("enrich_and_persist_work rejected invalid author %r", author)
        return None
    try:
        resolved = two_phase.enrich_fast(title, author, format or "ebook")
        if resolved is None:
            return None
        work_id, created = resolved
        if created:
            try:
                enqueue_enrichment(str(work_id))
            except Exception:  # noqa: BLE001 - deep pass is best-effort; fast data already persisted
                logger.exception("deep-enrichment enqueue failed for work %s", work_id)
        return str(work_id)
    except Exception:  # noqa: BLE001 - degrade gracefully, never crash the agent loop
        logger.exception("enrich_and_persist_work failed for %r by %r", title, author)
        return None
```

(The `print()` calls are gone — `logger` throughout. The old inline `create_scout_manager`/`persist_enriched_work` path in this function is removed; `_normalize`/`_normalized_col` remain if other tools use them, else remove and clean imports.)

- [ ] **Step 4: Rewrite `add_book_to_history`'s enrichment + logging path**

Keep the validation block (lines 498-516) verbatim. Replace everything from line 518 down with:

```python
    resolved = None
    try:
        resolved = two_phase.enrich_fast(title, author, format)
    except Exception as e:  # noqa: BLE001 - tool surface: report, don't crash the agent loop
        return f"Error enriching '{title}': {e}"
    if resolved is None:
        return f"Error: could not resolve '{title}' by {author} — check the spelling, or the scouts found nothing."
    work_id, created = resolved
    if created:
        try:
            enqueue_enrichment(str(work_id))
        except Exception:  # noqa: BLE001 - deep pass is best-effort
            logger.exception("deep-enrichment enqueue failed for work %s", work_id)

    try:
        logged = two_phase.add_read_event(work_id, completed=completed, rating=rating, notes=notes, fmt=format)
    except Exception as e:  # noqa: BLE001
        return f"Error adding to reading history: {e}"
    if logged["already_logged"]:
        return f"'{title}' is already logged as completed {completed.isoformat()}. No new entry written."
    msg = f"Added '{title}' to your reading history (work {work_id}, read #{logged['read_number']})."
    if created:
        msg += (
            " I'm still analyzing this book in the background (~1-2 minutes) — its tropes and"
            " styles will be ready on your next turn, so tell the user that and don't draw"
            " trope-based conclusions about it yet."
        )
    return msg
```

Update the docstring's "(runs the scouts — takes a minute or two)" to "(fast metadata in seconds; the deep trope/style analysis runs in the background — the return message says when that applies)". Note: `user_id = get_required_user_id()` at line 516 stays (context must raise before any work); `two_phase.add_read_event` re-reads it internally. The now-unused duplicate read-event block (old lines 522-552) is deleted; remove imports that become unused (ruff will flag).

- [ ] **Step 5: Update the two instruction texts**

`agents/services.py` Librarian instruction — replace the step-5 ENRICH DISCOVERIES sentence "A null result means the title did not resolve (possibly hallucinated) — drop that candidate and continue." with:

```
               A null result means the title did not resolve (possibly hallucinated) — drop that
               candidate and continue. Newly enriched discoveries get their deep trope/style
               analysis in the BACKGROUND (~1-2 min): this turn they have no trope fingerprint,
               so prefer established catalog candidates for trope-based final ranking and
               present a fresh discovery as "still under analysis" rather than claiming
               trope matches for it.
```

And replace the IMPORT paragraph's "If the book is not in the catalog yet this runs enrichment and takes a minute or two; say so before calling." with:

```
            If the book is not in the catalog yet, the add returns quickly with basic metadata
            and the deep analysis continues in the background — when the tool's reply says so,
            TELL the user you are still investigating the book and that its full analysis will
            be ready shortly; do not present trope/style conclusions about it this turn.
```

`agents/prompts.py` (~line 95, the portable ENRICH DISCOVERIES text): append the same two-sentence background-analysis caveat (adapted to that prompt's voice).

- [ ] **Step 6: Run tests**

Run: `.venv/Scripts/python -m pytest test/unit/test_chat_enrich_reroute.py test/unit/test_mcp_tools.py test/unit/test_agent_services.py test/unit/test_write_authorization.py -v`
Expected: all pass (some `test_mcp_tools.py` cases may mock the old inline scout path — update their mocks to `two_phase.enrich_fast`, preserving what each test asserts).

- [ ] **Step 7: Lint, format, commit**

```bash
git add src/agentic_librarian/mcp/server.py src/agentic_librarian/agents/services.py src/agentic_librarian/agents/prompts.py test/unit/test_chat_enrich_reroute.py test/unit/test_mcp_tools.py
git commit -m "feat(chat): add-book runs fast pass + queued deep pass; Librarian announces background analysis (#93 #94)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 7: Pool overflow revert + docs (#102 close-out; ADR-059)

**Files:**
- Modify: `src/agentic_librarian/db/session.py` (max_overflow 10 → 2 + comment rewrite)
- Modify: `test/unit/test_pool_config.py` (assertion 10 → 2)
- Modify: `docs/project_notes/key_facts.md` (pool sentence: interim wording → final "5+2")
- Modify: `docs/project_notes/decisions.md` (append ADR-059)
- Test: existing `test/unit/test_pool_config.py`.

- [ ] **Step 1: Revert the interim overflow**

In `db/session.py`, change `"max_overflow": 10` to `"max_overflow": 2` and replace the INTERIM comment block with:

```python
            # GH #102: pre_ping heals stale connections after Cloud SQL restarts/idle;
            # recycle beats server-side idle kills; 5+2 per engine × max-instances=2 = 14
            # connections, safely under db-f1-micro's ~25-connection budget. Viable since
            # #94: sessions no longer idle across external LLM/scout/Thunder calls.
            # sqlite (tests) uses its own pool class that rejects QueuePool kwargs.
```

Update `test/unit/test_pool_config.py`: `assert e.pool._max_overflow == 2`.
Update `docs/project_notes/key_facts.md` pool sentence to: "Engine pools: `pool_pre_ping` + 30-min recycle, 5+2 per engine, one shared engine per API process (GH #102/#94) — 2 instances × 7 ≈ 14 of db-f1-micro's ~25 connections."

- [ ] **Step 2: Append ADR-059 to `docs/project_notes/decisions.md`** (after ADR-058):

```markdown
### ADR-059: Off-loop tool execution + sessions never span external calls (2026-07-12)
**Context:**
- ADK FunctionTool runs sync tools inline on the uvicorn event loop; every mesh tool was
  sync, the auth dependency did sync verify+DB per request, and import commit enqueued up
  to 2000 Cloud Tasks synchronously — one slow operation stalled every user on the
  instance (GH #93). Enrichment/availability held DB sessions open across scout/LLM/
  Thunder calls — minutes idle-in-transaction, pool exhaustion, a wide #95 TOCTOU window
  (GH #94). Chat's add-book ran the full 6-scout enrichment inline (~1-2 min in-chat).
**Decision:**
- `make_async_tool` (signature-preserving `asyncio.to_thread` wrapper) on all 11 mesh
  FunctionTools; auth's verify+DB body via to_thread (ContextVar set stays in the
  coroutine); Cloud Tasks clients cached at module level; commit's enqueue loop off-loop.
- The session rule: read-session → external work with NO session → fresh write-session
  that re-checks dedup. Applied to two_phase fast/deep, availability (three-phase batch),
  and the chat discovery tool.
- Chat add-book re-routed through the two-phase path (fast pass + queued deep pass) —
  user-approved contract: the Librarian announces background analysis and never anchors
  trope-based recommendations on a deep-pending work in the same turn. Tool contract
  (`str | None`) unchanged for the pipeline/Claude-backend callers.
- Pool overflow reverted to 2 (the PR-A interim 10 existed only because sessions still
  idled across external calls).
**Consequences:**
- One user's enrichment can no longer brown out the instance; chat adds return in seconds.
- The deep pass now re-scouts outside any transaction: a late transient failure re-pays
  nothing already persisted, and dedup re-checks close the widened race window.
- to_thread runs tool bodies on the default executor (~32 threads) — the enrich/import
  queues (4/5 concurrent) remain the heavy-work throttles.
```

- [ ] **Step 3: Run tests + full local suite**

Run: `.venv/Scripts/python -m pytest test/unit/test_pool_config.py -v` then `.venv/Scripts/python -m pytest test/unit -q`
Expected: pool tests pass with 2; full unit suite green (5 known pre-existing env failures excepted — name them).

- [ ] **Step 4: Lint, format, commit**

```bash
git add src/agentic_librarian/db/session.py test/unit/test_pool_config.py docs/project_notes/key_facts.md docs/project_notes/decisions.md
git commit -m "refactor(db): revert max_overflow to 2 now #94 shortens sessions; ADR-059 (#102 #94)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## Post-merge acceptance (controller/user, not plan tasks)

1. Deploy fires + goes ready (the migration guard + smoke pass).
2. Live chat smoke: add a book via chat — response in seconds, message mentions background
   analysis; next turn, `get_work_details` shows tropes (deep pass landed).
3. `/availability` returns for a multi-work batch; SSE chat streams while an import runs.
4. Close #93, #94, #102 with PR references; #101/#103 already closed on PR-A.
