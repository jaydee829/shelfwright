# Phase 6.2 — Concurrency & Capacity (Design)

**Date:** 2026-07-12 · **Issues:** #93, #94, #101, #102, #103 · **Roadmap:** plan.md Phase 6.2

## Goal

Make one Cloud Run instance safely serve dozens of concurrent users: stop parking blocking
work on the uvicorn event loop (#93), stop holding DB sessions across external LLM/scout/
Thunder calls (#94), consolidate and tune the connection pools (#102), make the embedding
cache actually hit (#101), and bound every outbound call (#103).

**Delivery shape: two PRs from this one spec** (decision under the "unless large enough to
stand alone" ground rule — #93+#94 together are an architectural restructure):
- **PR-A `perf/phase6-2a-quick-wins`**: #101 + #102 + #103. Small, independent, low-risk.
- **PR-B `refactor/phase6-2b-async-sessions`**: #93 + #94, built on PR-A's consolidated pool.

## Verified findings (2026-07-12 exploration; deltas from the issue bodies)

- All 11 mesh tools registered via `FunctionTool` in `agents/services.py` are sync `def`s
  running inline on the ADK event loop; ADK builds tool schemas from the function
  **signature**, so any async wrapper must preserve `__signature__`/`__name__`/`__doc__`.
- `add_book_to_history` (mcp/server.py:484-552) calls `enrich_and_persist_work` inline —
  the full `create_scout_manager()` (fast + deep tiers) synchronously on the chat path;
  its error path is `print()`, not logger.
- `get_current_user` (api/auth.py:65-125): JWT verify is **offline** (CPU, not network —
  milder than the issue claimed) but the DB query/insert block is sync on the loop; the
  ContextVar set at line 124 is the only part that must stay in the coroutine.
- Both `imports/tasks.py` and `enrichment/tasks.py` build a **fresh `CloudTasksClient`
  (new gRPC channel) per enqueue**; `commit()` loops that up to 2000× inside `async def`.
  `retry()` has the same loop shape.
- `imports/worker.py` is an **unlisted 7th engine-sprawl instance** (module-level
  `DatabaseManager()`, no `set_db_manager` seam, not in the lifespan fan-out) — and it
  already works around #94 by calling `enrich_fast` *outside* its own session, with a
  comment saying why. That convention becomes the rule.
- The `_record_event_usage` pattern (agents/runtime.py:51-68) is the sanctioned template:
  `await asyncio.to_thread(...)` copies context, so `get_required_user_id()` ContextVars
  survive.
- `get_cached_embedding(client, model, text)` keys its LRU on the client instance;
  `TropeManager`/`StyleManager` build a fresh `genai.Client` per instantiation and are
  instantiated per MCP tool call / per persisted row → near-zero hit rate. The correct
  singleton pattern already exists at `api/analysis_style.py:119-135` (double-checked lock).
- `genai_http_options()` (llm_retry.py) threads retries into **all four** `genai.Client`
  sites but sets no timeout. `HttpOptions.timeout` exists in installed google-genai 2.8.0
  and is in **milliseconds** (the requests-based scout convention is seconds — unit trap).
- `AudiobookScout.fetch_page_content` (metadata_scout.py:356) is a bare `requests.get`;
  the class extends `LLMScout`, which has **no** `timeout`/`_session` attribute — it cannot
  reuse `APIScout`'s machinery without restructuring.
- Nothing in the current test suite exercises the cache hit-rate, timeout presence, or
  loop-blocking — all three bug classes are structurally invisible today.

## PR-A design

### #101 — shared genai client + working embedding cache

1. `scouts/utils.py` gains `get_shared_genai_client()` — module-level singleton with
   double-checked locking, `api_key` from `GOOGLE_SEARCH_API_KEY`, `http_options=
   genai_http_options()` (mirrors `analysis_style.py`, which migrates to this helper).
2. `get_cached_embedding(model_name, text)` — **client param removed**; the LRU keys on
   `(model_name, text)`; on miss it embeds via the shared client. Throttle behavior
   (`_throttle_embedding`) unchanged. `functools.lru_cache` is thread-safe (matters once
   PR-B moves tool bodies to threads).
3. `TropeManager`/`StyleManager` stop constructing their own clients; `_get_embedding`
   calls the new signature. `analysis_style.default_embedder()` delegates to the shared
   helper (its local singleton and lock are removed).
4. Managers keep their `session` param — only the client moves.

### #102 — pool flags + engine consolidation

1. `db/session.py:68`: `create_engine(db_url, connect_args=connect_args,
   pool_pre_ping=True, pool_recycle=1800, pool_size=5, max_overflow=2)`.
   Math: 2 max instances × (5+2) = 14 connections, safely under db-f1-micro's ~25.
   `pool_pre_ping` kills the post-maintenance "server closed the connection" class.
2. Lifespan fan-out (api/main.py) additionally injects the shared manager into
   `mcp/server` (chat tools run in-process), `enrichment/two_phase`, and
   `imports/worker` (which gains the standard `set_db_manager` seam). Their module-level
   fallback managers remain for non-API processes (Dagster/CLI construct their own).
3. Stale comments corrected: two_phase's "deferred to Stage 4" note; main.py's lifespan
   docstring lists the new modules.

### #103 — outbound timeouts

1. `llm_retry.py`: `types.HttpOptions(retry_options=RETRY_OPTIONS, timeout=120_000)` —
   **milliseconds**; 120s accommodates grounded deep-scout calls that legitimately run
   ~a minute. Propagates to all four client sites via the existing factory.
2. `metadata_scout.py`: module-level `_page_session = requests.Session()` mounting the
   existing `_API_RETRY` adapter; `fetch_page_content` uses
   `_page_session.get(url, headers=headers, timeout=15)` (seconds). AudiobookScout keeps
   its `LLMScout` base — no hierarchy change.

## PR-B design

### #93 — off-loop execution

1. **Mesh tools**: `agents/services.py` gains `make_async_tool(fn)` → an `async def`
   wrapper doing `return await asyncio.to_thread(fn, *args, **kwargs)`, with
   `functools.wraps` + explicit `__signature__ = inspect.signature(fn)` so ADK's schema
   generation is unchanged. All 11 `FunctionTool(...)` registrations become
   `FunctionTool(make_async_tool(...))`. ContextVars survive (`to_thread` copies context —
   the `_record_event_usage` precedent). The implementation plan must verify against
   installed google-adk 2.2.0 that `FunctionTool` awaits coroutine functions.
2. **Auth**: `get_current_user` stays `async def` (documented ContextVar constraint); the
   JWT verify + the entire DB resolve/provision block move into one sync helper called via
   `await asyncio.to_thread(...)`; `current_user_id.set(result.id)` remains in the
   coroutine.
3. **Cloud Tasks**: both `tasks.py` modules cache the client at module level
   (`_client()` stays as the test seam, now returning the cached instance; reset seam for
   tests). `commit()` and `retry()` wrap their enqueue loops in
   `await asyncio.to_thread(_enqueue_all, ids)`.

### #94 — sessions never span external calls

The rule (promoted from imports/worker's existing convention): **read-session → external
work with no session held → fresh write-session that re-checks dedup before persisting.**

1. `enrichment/two_phase.py` `enrich_fast` / `enrich_deep`: split each into
   (a) short dedup/read session; (b) `manager.enrich(...)` with no session open;
   (c) fresh session: re-run the dedup query (the #95 TOCTOU window shrinks back to
   milliseconds), construct `TropeManager`/`StyleManager` on that session,
   `persist_enriched_work`, flush. On deep: a transient LLM failure no longer rolls back
   (and re-pays) completed scout work *held in a transaction* — scouts finished before the
   write transaction opens.
2. `api/availability.py` + `availability/service.py`: three-phase batch —
   (a) session: read `UserLibrary`, works, and all fresh cache rows;
   (b) no session: Thunder fetches for the misses only;
   (c) session: write-through cache updates. `availability_for` splits into a pure
   cache-read helper and a fetch/write pair so `mcp/server.check_availability` uses the
   same shape. The "ALWAYS 200 / badge is best-effort" contract is unchanged.
3. `mcp/server.enrich_and_persist_work` — **re-routed through two-phase** (see chat
   contract below): short-session dedup → `enrich_fast(...)` (which is now session-split
   itself) → `enqueue_enrichment(work_id)` for the deep pass. The full-scout inline path
   is removed from the chat surface; error path switches from `print()` to
   `logger.exception`.

### Chat contract for async deep enrichment (user decision, 2026-07-12)

The user requires: new books still get the **full** enrichment before the mesh treats them
as recommendation-grade (the Critic must verify matches against the deep trope/style
fingerprint — shallow-only would be "no better than Goodreads"), async is acceptable, and
**the Librarian must tell the user it is investigating in the background** and surface the
result on a later turn.

Mechanics (no frontend changes):
1. `enrich_and_persist_work` / `add_book_to_history` return payloads gain
   `"enrichment": "deep_pending"` (or `"complete"` when the work was already cataloged)
   plus a human-oriented note: deep analysis is running in the background (~1–2 min) and
   tropes/styles will be available on the next turn.
2. The Librarian instruction (services.py) is extended: when a tool reports
   `deep_pending`, (a) tell the user the Librarian is still investigating the book in the
   background; (b) do NOT present trope/style-based conclusions or recommendations anchored
   on that book this turn — offer to follow up next turn; (c) on a later turn, tools
   naturally see the enriched data (`get_work_details`, `search_internal_database`).
3. A freshly fast-passed work cannot silently pollute recommendations: candidate scoring
   runs on trope/style vectors, which the work lacks until the deep pass lands; the
   instruction change makes the Librarian say so instead of guessing.
4. **Deferred (frontend follow-up, not this phase):** a visual "enriching…" indicator
   (SSE status event + UI affordance). Noted for the 6.5 frontend-resilience grouping.

## Testing

- **PR-A units (local, DB-free):** cache hits across two manager instances (count embed
  calls via monkeypatched client); shared-client singleton identity; `genai_http_options()
  .timeout == 120_000`; `fetch_page_content` passes `timeout=15` (mocked session);
  engine kwargs asserted via a `DatabaseManager` engine inspection test (sqlite ignores
  pool args — assert against `create_engine` call or use a Postgres-URL lazy engine's pool
  class attributes without connecting).
- **PR-B units:** `make_async_tool` returns a coroutine function whose `__signature__`/
  `__name__`/`__doc__` match the wrapped tool; wrapper executes off the event loop (assert
  thread identity differs); auth dependency still sets the ContextVar (existing
  `test_api_requires_auth` + CI `test_api_auth`); Cloud Tasks client cached (two enqueues,
  one construction) with seam reset.
- **CI (db_integration):** existing two_phase fast/deep, availability, mcp-tool, and auth
  integration suites must stay green — they pin the behavior the restructure must preserve.
  Extend two_phase tests: persist still happens when dedup re-check finds no row; work is
  NOT duplicated when a concurrent insert lands between read and write sessions (simulated
  by pre-inserting between phases via the test seam).
- **Post-deploy smoke (operator/Claude):** live chat turn with an add-book flow; verify the
  "investigating in the background" message and next-turn trope availability.

## Risks & rollout

- PR-B touches the chat hot path. Mitigations: behavior pinned by the CI integration
  suites; PR-A lands first and deploys independently; post-merge smoke on prod chat.
- to_thread moves tool bodies onto the default executor (cap ~32 threads) — fine at
  dozens-of-users scale; the enrich/import queues (4/5 concurrent) remain the heavy-work
  throttles.
- Pool sizing: 7 connections per instance shared by more concurrent threads than before —
  `pool_timeout` defaults (30s) apply; the #94 session-shortening is what makes the
  smaller pool viable (sessions no longer idle for minutes).

## Decisions delegated to Claude (for user review)

1. Two PRs from one spec (quick wins vs restructure), PR-A first.
2. Shared genai client helper lives in `scouts/utils.py`; `analysis_style.py` migrates to
   it (one singleton per process, not two).
3. `get_cached_embedding` signature change (client param dropped) — all three callers
   updated in the same PR; no back-compat shim.
4. Pool numbers: `pool_size=5, max_overflow=2, pool_recycle=1800, pool_pre_ping=True`.
5. Gemini HTTP timeout 120s (120_000 ms); page-fetch timeout 15s via a retrying session.
6. `make_async_tool` wrapper approach (signature-preserving) over hand-written per-tool
   async wrappers or an ADK version bump.
7. Chat add-book re-routed through two-phase with the chat contract above (user-approved
   2026-07-12: async OK if communicated + no shallow-data recommendations); visual
   indicator deferred to the frontend grouping.
8. `imports/worker.py` added to the consolidation set (exploration finding, not in the
   issue text).
