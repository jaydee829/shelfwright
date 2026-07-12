# Phase 6.2 PR-A Quick Wins Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land the low-risk capacity fixes — a working embedding cache (#101), tuned/consolidated connection pools (#102), and outbound timeouts (#103) — on branch `perf/phase6-2a-quick-wins`.

**Architecture:** One process-wide `genai.Client` singleton in `scouts/utils.py` lets the embedding LRU key on `(model_name, text)`; `db/session.py` gains pool flags and three straggler modules join the lifespan's shared-pool fan-out; the shared `genai_http_options()` factory gains a timeout and the Audible page fetch gets a retrying session with a timeout. PR-B (#93/#94) builds on this branch after it merges.

**Tech Stack:** google-genai 2.8.0 (`HttpOptions.timeout` is **milliseconds**), SQLAlchemy QueuePool, requests + urllib3 Retry, pytest.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-07-12-phase6-2-concurrency-capacity-design.md` (PR-A sections).
- Exact values: genai timeout `120_000` (ms); page-fetch timeout `15` (seconds); pool flags exactly `pool_pre_ping=True, pool_recycle=1800, pool_size=5, max_overflow=2`.
- `get_cached_embedding(model_name: str, text: str)` — client param REMOVED, no back-compat shim; all three callers updated in the same task.
- Shared client helper: `get_shared_genai_client()` in `scouts/utils.py`, double-checked lock, key from `GOOGLE_SEARCH_API_KEY`, `http_options=genai_http_options()`.
- Managers keep their existing no-API-key `ValueError` in `__init__` (existing behavior).
- New unit tests are DB-free and run locally: `.venv/Scripts/python -m pytest ...` from repo root. Lint: `uvx ruff check <files>`; format: `uvx ruff format <files>` then re-run check (CI pre-commit enforces format — the 6.1 lesson).
- LRU caches are process-global: tests MUST call `get_cached_embedding.cache_clear()` and reset `scouts.utils._shared_client` (monkeypatch) to avoid cross-test leakage.
- No `[skip ci]` anywhere in commit messages. Do not modify `frontend/**` or any file not listed in a task.

---

### Task 1: Shared genai client + (model, text)-keyed embedding cache — #101

**Files:**
- Modify: `src/agentic_librarian/scouts/utils.py:39-56`
- Modify: `src/agentic_librarian/scouts/trope_manager.py:14-24`
- Modify: `src/agentic_librarian/scouts/style_manager.py:14-24`
- Modify: `src/agentic_librarian/api/analysis_style.py:99-135`
- Test: `test/unit/test_embedding_cache.py` (new)

**Interfaces:**
- Consumes: existing `genai_http_options()` from `agentic_librarian.llm_retry`.
- Produces: `get_shared_genai_client() -> genai.Client` and `get_cached_embedding(model_name: str, text: str) -> list[float]` in `agentic_librarian.scouts.utils` — PR-B relies on these exact names.

- [ ] **Step 1: Write the failing tests**

Create `test/unit/test_embedding_cache.py`:

```python
"""#101: the embedding cache must hit across manager instances (keyed (model, text))."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from agentic_librarian.scouts import utils


@pytest.fixture(autouse=True)
def _clean_cache(monkeypatch):
    utils.get_cached_embedding.cache_clear()
    monkeypatch.setattr(utils, "_shared_client", None)
    yield
    utils.get_cached_embedding.cache_clear()


def _fake_client(counter):
    def embed_content(model, contents, config):
        counter.append(contents)
        return SimpleNamespace(embeddings=[SimpleNamespace(values=[0.1] * utils.EMBEDDING_DIMENSIONS)])

    return SimpleNamespace(models=SimpleNamespace(embed_content=embed_content))


def test_cache_hits_across_callers(monkeypatch):
    calls = []
    monkeypatch.setattr(utils, "_shared_client", _fake_client(calls))
    v1 = utils.get_cached_embedding("gemini-embedding-001", "Found Family")
    v2 = utils.get_cached_embedding("gemini-embedding-001", "Found Family")
    assert v1 == v2
    assert len(calls) == 1  # second call was a cache hit


def test_cache_misses_on_different_text(monkeypatch):
    calls = []
    monkeypatch.setattr(utils, "_shared_client", _fake_client(calls))
    utils.get_cached_embedding("gemini-embedding-001", "Found Family")
    utils.get_cached_embedding("gemini-embedding-001", "Slow Burn")
    assert len(calls) == 2


def test_shared_client_is_singleton(monkeypatch):
    monkeypatch.setenv("GOOGLE_SEARCH_API_KEY", "test-key")
    built = []

    class FakeClient:
        def __init__(self, **kwargs):
            built.append(kwargs)

    monkeypatch.setattr(utils.genai, "Client", FakeClient)
    c1 = utils.get_shared_genai_client()
    c2 = utils.get_shared_genai_client()
    assert c1 is c2
    assert len(built) == 1
    assert built[0]["api_key"] == "test-key"


def test_shared_client_requires_key(monkeypatch):
    monkeypatch.delenv("GOOGLE_SEARCH_API_KEY", raising=False)
    with pytest.raises(ValueError, match="GOOGLE_SEARCH_API_KEY"):
        utils.get_shared_genai_client()


def test_managers_share_the_module_cache(monkeypatch):
    calls = []
    monkeypatch.setattr(utils, "_shared_client", _fake_client(calls))
    from agentic_librarian.scouts.style_manager import StyleManager
    from agentic_librarian.scouts.trope_manager import TropeManager

    tm = TropeManager(session=MagicMock(), api_key="k")
    sm = StyleManager(session=MagicMock(), api_key="k")
    tm._get_embedding("Enemies to Lovers")
    sm._get_embedding("Enemies to Lovers")  # same model+text -> cache hit
    assert len(calls) == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python -m pytest test/unit/test_embedding_cache.py -v`
Expected: failures/errors — `utils` has no `_shared_client` / `get_shared_genai_client`, and `get_cached_embedding` takes 3 args.

- [ ] **Step 3: Implement in `scouts/utils.py`**

Replace lines 39-56 (the decorated `get_cached_embedding`) with:

```python
# Process-wide genai client (GH #101). One client per process means the lru_cache below
# keys purely on (model_name, text) and actually hits across manager instances / tool
# calls — previously each TropeManager/StyleManager built its own client and the client
# identity in the cache key defeated the cache. Double-checked lock: build at most one
# client under concurrency (same pattern api/analysis_style.py pioneered).
_shared_client: genai.Client | None = None
_client_lock = threading.Lock()


def get_shared_genai_client() -> genai.Client:
    global _shared_client
    if _shared_client is None:
        with _client_lock:
            if _shared_client is None:
                from agentic_librarian.llm_retry import genai_http_options

                key = os.environ.get("GOOGLE_SEARCH_API_KEY")
                if not key:
                    raise ValueError("GOOGLE_SEARCH_API_KEY is not set — cannot build the shared genai client.")
                _shared_client = genai.Client(api_key=key, http_options=genai_http_options())
    return _shared_client


@lru_cache(maxsize=128)
def get_cached_embedding(model_name: str, text: str) -> list[float]:
    """Shared embed chokepoint for ETL ingestion, MCP tools, and the recommendation flow.
    Cached on (model_name, text) so identical tags embed over the network once per process
    (GH #101). task_type SEMANTIC_SIMILARITY keeps stored vectors and query vectors in one
    representation space; changing task_type invalidates previously-stored vectors."""
    _throttle_embedding()
    client = get_shared_genai_client()
    response = client.models.embed_content(
        model=model_name,
        contents=text,
        config=types.EmbedContentConfig(
            task_type="SEMANTIC_SIMILARITY",
            output_dimensionality=EMBEDDING_DIMENSIONS,
        ),
    )
    if not response or not response.embeddings:
        raise ValueError(f"Embedding generation returned no result for text: {text!r}")
    return response.embeddings[0].values
```

(The `from agentic_librarian.llm_retry import genai_http_options` import is deliberately inside the function — `llm_retry` imports `google.genai.types` and utils is imported by lightweight modules.)

- [ ] **Step 4: Update `trope_manager.py`**

Lines 14-24 — remove the client construction and `genai` import; keep the key validation:

```python
    def __init__(self, session: Session, api_key: str = None):
        self.session = session
        self._api_key = api_key or os.environ.get("GOOGLE_SEARCH_API_KEY")
        if not self._api_key:
            raise ValueError("Google API key not set for TropeManager.")
        self.model_name = "gemini-embedding-001"  # Current GA Gemini embedding model

    def _get_embedding(self, text: str) -> list[float]:
        """Fetch embedding from Gemini via the shared module-level client + cache (#101)."""
        return get_cached_embedding(self.model_name, text)
```

Delete the now-unused imports `from google import genai` and `from agentic_librarian.llm_retry import genai_http_options` at the top of the file (ruff will flag them if you forget).

- [ ] **Step 5: Update `style_manager.py`** — the identical change (same lines, `StyleManager` message in the ValueError stays).

- [ ] **Step 6: Update `api/analysis_style.py`**

Replace `default_embedder()` (lines 119-135) and remove the `_genai_client` global (line 99):

```python
def default_embedder() -> Callable[[str], list[float]] | None:
    """Real embedder using the same model/space as the stored Style vectors, or
    None when no API key is configured (radar then degrades to all-null)."""
    if not os.environ.get("GOOGLE_SEARCH_API_KEY"):
        return None
    return lambda text: get_cached_embedding(_EMBED_MODEL, text)
```

Keep `_lock` (line 100) — the anchor cache still uses it. Delete the `_genai_client: object | None = None` line.

- [ ] **Step 7: Run the tests + affected suites**

Run: `.venv/Scripts/python -m pytest test/unit/test_embedding_cache.py -v` — Expected: 6 passed.
Run: `.venv/Scripts/python -m pytest test/unit -q` — Expected: all pass (analysis-style unit tests exercise `default_embedder`).

- [ ] **Step 8: Lint, format, commit**

Run: `uvx ruff check src/agentic_librarian/scouts/utils.py src/agentic_librarian/scouts/trope_manager.py src/agentic_librarian/scouts/style_manager.py src/agentic_librarian/api/analysis_style.py test/unit/test_embedding_cache.py` and `uvx ruff format src/agentic_librarian/scouts/utils.py src/agentic_librarian/scouts/trope_manager.py src/agentic_librarian/scouts/style_manager.py src/agentic_librarian/api/analysis_style.py test/unit/test_embedding_cache.py`, then re-run the check.

```bash
git add -A src test
git commit -m "perf(embeddings): shared genai client; cache keyed on (model, text) (#101)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 2: Gemini HTTP timeout — #103a

**Files:**
- Modify: `src/agentic_librarian/llm_retry.py:25-27`
- Test: `test/unit/test_llm_retry.py` (extend)

**Interfaces:**
- Produces: `GENAI_TIMEOUT_MS = 120_000` module constant (documentation value; PR-B does not depend on it).

- [ ] **Step 1: Write the failing test** — append to `test/unit/test_llm_retry.py`:

```python
def test_genai_http_options_sets_timeout():
    """#103: every Gemini call must carry a client-side timeout. HttpOptions.timeout is
    MILLISECONDS (google-genai 2.8.0) — 120s accommodates grounded deep-scout calls."""
    from agentic_librarian.llm_retry import GENAI_TIMEOUT_MS, genai_http_options

    assert GENAI_TIMEOUT_MS == 120_000
    assert genai_http_options().timeout == 120_000
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/Scripts/python -m pytest test/unit/test_llm_retry.py -v`
Expected: FAIL — `ImportError: cannot import name 'GENAI_TIMEOUT_MS'`.

- [ ] **Step 3: Implement** — in `llm_retry.py`, after `RETRY_OPTIONS`:

```python
# Client-side request timeout in MILLISECONDS (HttpOptions.timeout unit — NOT seconds;
# the requests-based scouts use seconds, don't copy values between the two). 120s
# accommodates grounded deep-scout generations that legitimately run ~a minute; without
# it a hung Gemini call pins its thread + DB connection until Cloud Run kills the request.
GENAI_TIMEOUT_MS = 120_000


def genai_http_options() -> types.HttpOptions:
    """HttpOptions carrying the shared retry config + timeout, for `genai.Client(http_options=...)`."""
    return types.HttpOptions(retry_options=RETRY_OPTIONS, timeout=GENAI_TIMEOUT_MS)
```

- [ ] **Step 4: Run to verify it passes** — same command, Expected: all pass (including the two pre-existing tests).

- [ ] **Step 5: Lint, format, commit**

```bash
git add src/agentic_librarian/llm_retry.py test/unit/test_llm_retry.py
git commit -m "fix(llm): 120s client-side timeout on every Gemini call (#103)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 3: Audible page-fetch timeout via retrying session — #103b

**Files:**
- Modify: `src/agentic_librarian/scouts/metadata_scout.py:33-39` (module level, after `_API_RETRY`) and `:350-360` (`fetch_page_content`)
- Test: `test/unit/test_metadata_scout_fetch.py` (new)

**Interfaces:** none new (module-private `_page_session`, `_PAGE_TIMEOUT`).

- [ ] **Step 1: Write the failing test** — create `test/unit/test_metadata_scout_fetch.py`:

```python
"""#103: the Audible page fetch must use the retrying session WITH a timeout."""

from unittest.mock import MagicMock, patch

from agentic_librarian.scouts import metadata_scout


def test_fetch_page_content_uses_session_with_timeout():
    scout = metadata_scout.AudiobookScout.__new__(metadata_scout.AudiobookScout)  # skip __init__ (needs keys)
    fake_response = MagicMock(content=b"<html><body>Audible page</body></html>")
    with (
        patch.object(metadata_scout.AudiobookScout, "search_audible_link", return_value="https://audible.com/x"),
        patch.object(metadata_scout._page_session, "get", return_value=fake_response) as mock_get,
    ):
        text = scout.fetch_page_content("Some Title")
    assert "Audible page" in text
    kwargs = mock_get.call_args.kwargs
    assert kwargs["timeout"] == metadata_scout._PAGE_TIMEOUT == 15


def test_page_session_mounts_retry_adapter():
    adapter = metadata_scout._page_session.get_adapter("https://audible.com")
    assert adapter.max_retries.total == metadata_scout._API_RETRY.total
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/Scripts/python -m pytest test/unit/test_metadata_scout_fetch.py -v`
Expected: FAIL — `module ... has no attribute '_page_session'`.

- [ ] **Step 3: Implement** — in `metadata_scout.py`, after the `_API_RETRY` block (line 39):

```python
# Shared session for raw page scrapes (AudiobookScout extends LLMScout, which has no
# APIScout session/timeout machinery — GH #103). Same retry policy as the API scouts;
# timeout in SECONDS (requests convention — the genai HttpOptions timeout is milliseconds).
_PAGE_TIMEOUT = 15
_page_session = requests.Session()
_page_session.mount("https://", HTTPAdapter(max_retries=_API_RETRY))
_page_session.mount("http://", HTTPAdapter(max_retries=_API_RETRY))
```

And in `fetch_page_content` (line 356), replace the bare get:

```python
        response = _page_session.get(url, headers=headers, timeout=_PAGE_TIMEOUT)
```

- [ ] **Step 4: Run to verify it passes** — same command, Expected: 2 passed.

- [ ] **Step 5: Lint, format, commit**

```bash
git add src/agentic_librarian/scouts/metadata_scout.py test/unit/test_metadata_scout_fetch.py
git commit -m "fix(scouts): Audible page fetch via retrying session with 15s timeout (#103)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 4: Pool flags + engine consolidation — #102

**Files:**
- Modify: `src/agentic_librarian/db/session.py:55-69`
- Modify: `src/agentic_librarian/imports/worker.py:19-22` (add seam)
- Modify: `src/agentic_librarian/api/main.py:48-67` (lifespan fan-out + docstring)
- Modify: `src/agentic_librarian/enrichment/two_phase.py:28-29` (stale comment)
- Modify: `docs/project_notes/key_facts.md` (Database bullet)
- Test: `test/unit/test_pool_config.py` (new)

**Interfaces:**
- Produces: `imports.worker.set_db_manager(new_manager)` (standard seam signature) — PR-B relies on it.

- [ ] **Step 1: Write the failing tests** — create `test/unit/test_pool_config.py`:

```python
"""#102: pool flags on the engine; all in-process modules share the lifespan pool."""

from fastapi.testclient import TestClient

from agentic_librarian.db.session import DatabaseManager


def test_engine_pool_flags():
    # Postgres URL, lazily initialized: create_engine builds the pool WITHOUT connecting.
    m = DatabaseManager("postgresql+psycopg2://x:x@nohost:1/x")
    e = m.engine
    assert e.pool._pre_ping is True
    assert e.pool._recycle == 1800
    assert e.pool.size() == 5
    assert e.pool._max_overflow == 2


def test_lifespan_shares_one_manager_everywhere():
    from agentic_librarian.api import main as main_mod
    from agentic_librarian.enrichment import two_phase
    from agentic_librarian.imports import worker
    from agentic_librarian.mcp import server as mcp_server

    with TestClient(main_mod.app):
        shared = main_mod.app.state.db_manager
        assert main_mod.db_manager is shared
        assert mcp_server.db_manager is shared
        assert two_phase.db_manager is shared
        assert worker.db_manager is shared
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/Scripts/python -m pytest test/unit/test_pool_config.py -v`
Expected: `test_engine_pool_flags` fails on `_pre_ping`; the lifespan test fails on `worker`/`mcp_server`/`two_phase` not sharing.

- [ ] **Step 3: `db/session.py`** — replace line 68:

```python
        # GH #102: pre_ping heals stale connections after Cloud SQL restarts/idle;
        # recycle beats server-side idle kills; 5+2 per engine × max-instances=2 = 14
        # connections, safely under db-f1-micro's ~25-connection budget. Viable because
        # sessions no longer idle across external calls (#94).
        self._engine = create_engine(
            db_url,
            connect_args=connect_args,
            pool_pre_ping=True,
            pool_recycle=1800,
            pool_size=5,
            max_overflow=2,
        )
```

Note: sqlite URLs use SingletonThreadPool/NullPool and reject QueuePool kwargs — SQLAlchemy ignores pool sizing for sqlite via its dialect defaults? It does NOT — `create_engine("sqlite://", pool_size=5)` raises `TypeError`. Guard: only pass the pool kwargs for non-sqlite URLs:

```python
        pool_kwargs = {}
        if not db_url.startswith("sqlite"):
            # GH #102: pre_ping heals stale connections after Cloud SQL restarts/idle;
            # recycle beats server-side idle kills; 5+2 per engine × max-instances=2 = 14
            # connections, safely under db-f1-micro's ~25-connection budget. Viable because
            # sessions no longer idle across external calls (#94). sqlite (tests) uses its
            # own pool class that rejects QueuePool kwargs.
            pool_kwargs = {"pool_pre_ping": True, "pool_recycle": 1800, "pool_size": 5, "max_overflow": 2}
        self._engine = create_engine(db_url, connect_args=connect_args, **pool_kwargs)
```

Use the guarded version (the migration-guard unit tests build sqlite managers).

- [ ] **Step 4: `imports/worker.py`** — after line 21 (`db_manager = DatabaseManager()`):

```python
def set_db_manager(new_manager: DatabaseManager) -> None:
    """Override the module db_manager (tests / shared-pool lifespan) — mcp/server.py pattern."""
    global db_manager
    db_manager = new_manager
```

- [ ] **Step 5: `api/main.py` lifespan** — add imports near the existing api imports:

```python
from agentic_librarian.enrichment import two_phase
from agentic_librarian.imports import worker as imports_worker
from agentic_librarian.mcp import server as mcp_server
```

Extend the fan-out (after `libraries_api.set_db_manager(shared)`):

```python
        # GH #102: the in-process chat tools (mcp/server), the enrichment paths
        # (two_phase), and the import worker previously each held their own lazy pool —
        # up to ~9 engines/process. One pool per process keeps the connection math sane.
        mcp_server.set_db_manager(shared)
        two_phase.set_db_manager(shared)
        imports_worker.set_db_manager(shared)
```

Update the lifespan docstring's module list to mention them (replace "enrichment/two_phase keeps its own pool (separate path/test seam)." with "mcp/server, enrichment/two_phase, and imports/worker join the fan-out (GH #102); their module-level managers remain as fallbacks for non-API processes (Dagster, CLI).").

- [ ] **Step 6: `enrichment/two_phase.py`** — replace the stale comment at lines 28-29:

```python
# Module-level fallback pool for non-API processes; the API lifespan injects its shared
# manager via set_db_manager (GH #102 consolidation — the old "deferred to Stage 4" note was stale).
```

- [ ] **Step 7: `docs/project_notes/key_facts.md`** — in the Production **Database** bullet, append after the backups sentence:

```markdown
  Engine pools: `pool_pre_ping` + 30-min recycle, 5+2 per engine, one shared engine per
  API process (GH #102) — 2 instances × 7 ≈ 14 of db-f1-micro's ~25 connections.
```

- [ ] **Step 8: Run the tests + full local suite**

Run: `.venv/Scripts/python -m pytest test/unit/test_pool_config.py test/unit/test_migration_guard.py -v` — Expected: all pass (guard's sqlite managers unaffected by the guard clause).
Run: `.venv/Scripts/python -m pytest test/unit -q` — Expected: all pass.

- [ ] **Step 9: Lint, format, commit**

```bash
git add -A src docs/project_notes/key_facts.md test/unit/test_pool_config.py
git commit -m "perf(db): pool pre_ping/recycle/sizing; consolidate mcp+two_phase+worker onto the lifespan pool (#102)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## Out of scope (PR-B, separate plan)

#93 (to_thread wrappers, auth, enqueue loops, cached CloudTasksClient) and #94 (session
splits, availability three-phase, chat two-phase re-route + Librarian contract). PR-B
branches from main after PR-A merges.
