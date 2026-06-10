# Bug Log

This file tracks project bugs, their root causes, solutions, and prevention strategies.

## Templates

### YYYY-MM-DD - Brief Bug Description
- **Issue**: What went wrong
- **Root Cause**: Why it happened
- **Solution**: How it was fixed
- **Prevention**: How to avoid it in the future

## Log

### 2026-02-06 - Fragile Year Extraction in Metadata Scout
- **Issue**: `original_publication_year` extraction failed for common date formats (e.g., "January 2023", "2023/01/01"), defaulting to `None`.
- **Root Cause**: Manual string splitting `split("-")[0]` only handled "YYYY-MM-DD" format.
- **Solution**: Implemented regex-based `_extract_year` helper in `MultiSourceScout` to find the first 4-digit sequence in the input string.
- **Prevention**: Use robust parsing (regex) for external API data and maintain unit tests covering multiple date formats.

### 2026-02-17 - Ruff E722 Bare Except Clauses
- **Issue**: Bare `except:` clauses in `search_strategies.py` triggered Ruff E722 and violated the project's "No Broad Except-Pass" mandate.
- **Root Cause**: Generic exception handling used for JSON parsing failures.
- **Solution**: Replaced bare `except:` with `except json.JSONDecodeError as e:` and added warning prints for visibility into failures.
- **Prevention**: Use specific exception types when possible and always include error logging/printing in catch blocks to maintain visibility of failures.

### 2026-02-17 - Environment/Syntax Mismatch (Python 3.9 vs 3.12)
- **Issue**: `TypeError` on union type hints (`|`) and `ImportError` on `datetime.UTC`.
- **Root Cause**: Tooling was defaulting to system Python 3.9 instead of the project's Conda environment (Python 3.12).
- **Solution**: Explicitly targeted the environment binary (`.../.conda/envs/agentic_librarian/python.exe`) for all test runs and verified 3.12 compatibility.
- **Prevention**: Always use the full path to the environment's python executable or ensure `conda run -n` is correctly resolving the local binary.

### 2026-02-17 - Module-Level DB Initialization Crash in CI
- **Issue**: Github CI failed during test collection with `ValueError: Database credentials not found`.
- **Root Cause**: `DatabaseManager` was validating credentials in `__init__`, and `mcp/server.py` was instantiating a global manager at the module level. This caused crashes on import in any environment without a live DB.
- **Solution**: Implemented lazy initialization in `DatabaseManager`. Engine and SessionFactory creation are now deferred until the first session request.
- **Prevention**: Avoid heavy side effects (network, FS, cred validation) in `__init__` for global service managers.

### 2026-02-18 - In-Memory Vector Similarity Bottleneck (PR #12 Review)
- **Issue**: `StyleManager` and `TropeManager` loaded all entities into memory for similarity calculations, causing a scalability risk.
- **Root Cause**: Reliance on `numpy` for cosine similarity instead of leveraging database-native `pgvector` operators.
- **Solution**: Refactored `find_similar_style` and `find_similar_trope` to use SQLAlchemy with `pgvector`'s `cosine_distance` operator at the SQL level.
- **Prevention**: Prioritize database-level operations for large-scale vector or relational filtering.

### 2026-02-18 - N+1 Queries in Librarian Agent Tools (PR #12 Review)
- **Issue**: `get_unacted_suggestions` and `search_internal_database` were triggering multiple database round-trips for each item in the result set.
- **Root Cause**: Accessing linked relationships (work contributors, tropes, styles) inside loops without eager loading.
- **Solution**: Implemented `joinedload` and `selectinload` to fetch all required relationships in the primary query.
- **Prevention**: Use SQLAlchemy `options` for eager loading in all tool/API endpoints that return lists of objects with relationships.

### 2026-02-18 - Style Integrity Issue: Attribute Blocking
- **Issue**: One author/work could not have multiple attributes associated with the same Style record (e.g. 'pacing' and 'tone').
- **Root Cause**: `attribute_type` was not part of the primary key in Style link tables, causing unique constraint violations.
- **Solution**: Updated `AuthorStyle`, `NarratorStyle`, and `WorkStyle` models to include `attribute_type` in the composite primary key.
- **Prevention**: Ensure that all identifying metadata for a relationship is included in the primary key or unique constraints.

### 2026-02-18 - Memory Leak in Cached Embedding Methods (Ruff B019)
- **Issue**: Using `@lru_cache` on class methods in `StyleManager` and `TropeManager` created strong references to `self`, preventing garbage collection of instances.
- **Root Cause**: Instance-bound methods in a global or long-lived cache prevent the instance from being freed.
- **Solution**: Moved the cached logic to module-level helper functions (`_get_cached_embedding`) that take the client and parameters as arguments, decoupling the cache from the class instance.
- **Prevention**: Never use `@lru_cache` on methods of classes that are instantiated frequently; use module-level helpers or `cached_property` instead.

### 2026-05-30 - Live LLM Scouts: Wrong Model + Fragile Grounded-Response Parsing (ENV-015)
- **Issue**: First live run of the enrichment scouts failed: (a) `gemini-2.0-flash` returned `429 RESOURCE_EXHAUSTED` (free-tier limit 0); (b) with search grounding on, `gemini-2.5-flash-lite` returns variable shapes — clean ```json```, prose-wrapped JSON, or an empty `response.text` with the answer only in the candidate parts — so JSON extraction produced empty styles/tropes or crashed on `None.strip()`.
- **Root Cause**: `gemini-2.0-flash` is no longer free-tier (current models per Context7: `gemini-2.5-flash-lite` / `gemini-2.5-flash` / `gemini-3-flash-preview`). The scouts assumed `response.text` is always a single clean JSON string; grounded responses split content across parts and add prose. Never exercised live (no Docker on the prior machine).
- **Solution**: Made the model configurable via `GEMINI_MODEL` (default `gemini-2.5-flash-lite`). **Kept grounding ON** — it is essential so recent books outside the training data are searched rather than hallucinated (e.g. "Between Two Fires" by Christopher Buehlman triggered real `web_search_queries`). Added robust parsing: `_extract_text` falls back to concatenating candidate parts when `response.text` is empty, and `_safe_extract_json` extracts the JSON payload from code fences or surrounding prose. Live `api_dependent` smoke test passes with grounding ON.
- **Prevention**: Treat LLM output as semi-structured — extract from parts and locate the JSON block; never assume `response.text` is clean JSON. Keep an `api_dependent` test exercising grounding. Pin models to current, quota-available IDs.

### 2026-05-31 - Audiobook ETL path non-deterministic in Flow 1 smoke; physical-book path also fails when free-tier daily quota is exhausted
- **Issue**: The Flow 1 smoke test (`test_flow1_etl_live.py`) is non-deterministic under the Gemini free-tier quota constraint. Adding a second audiobook row caused immediate `429 RESOURCE_EXHAUSTED` failures. After reverting to the single physical-book row, the test passed on the first run of the day (confirmed) but failed on repeated runs once the 20 req/day quota for `gemini-2.5-flash-lite` was depleted by the session's earlier test iterations. Even the physical-book path fires 2 LLM calls (StyleScout author style, LLMTropeScout) per row.
- **Root Cause**: The free-tier Gemini quota (20 generate_content requests/day per project per model) is shared across all LLM scout calls in the entire project. Each full physical-book enrichment consumes ~2 LLM calls; each audiobook row consumes 4+. After ~10 enrichments in a day, the quota is exhausted. The `api_dependent` smoke test is designed to run with real API keys, but the free-tier quota makes it non-deterministic after the first few runs of a session.
- **Solution**: Reverted the smoke CSV to the single physical-book row. The test passes reliably on a fresh quota day. Audiobook path is excluded from the smoke per scope rule.
- **Prevention**: (a) Upgrade to a paid Gemini tier. (b) For CI, skip `api_dependent` tests (they are already excluded via marker). (c) For local runs, limit to one execution per day or mock the LLM calls in a separate fixture-driven test. See issues.md REC-017.

### 2026-05-31 - Flow 1 MLflow 403 (DNS-rebinding protection) in pytest
- **Issue**: First run of `test_flow1_etl_live.py` failed with `MlflowException: API request to endpoint /api/2.0/mlflow/experiments/get-by-name failed with error code 403 != 200. Response body: 'Invalid Host header - possible DNS rebinding attack detected'`. The `enriched_metadata` asset calls `mlflow.set_experiment()` which POSTs to `http://mlflow:5000`; the MLflow server rejected the request because the `Host: mlflow:5000` header is not on its allowed-origins list (DNS-rebinding protection is on by default).
- **Root Cause**: The Compose MLflow server is configured with `--host 0.0.0.0` but does not set `--expose-hostname` or an allowlist for the `mlflow` Docker DNS name. When pytest runs inside the `agentic_librarian_app` container, `MLFLOW_TRACKING_URI=http://mlflow:5000` is inherited from the container environment, but the MLflow server rejects that hostname.
- **Solution**: Added a `local_mlflow_tracking` autouse fixture in `test_flow1_etl_live.py` that overrides `MLFLOW_TRACKING_URI` to a local `tmp_path` file store for the duration of the test. This avoids the network call entirely and keeps the test isolated from the shared tracking server.
- **Prevention**: Integration tests should always override `MLFLOW_TRACKING_URI` to a local file store via a fixture. Do not rely on the compose-network MLflow server being reachable from test code.

### 2026-05-31 - get_work_details Crash on Non-UUID work_id (Spec 2 live run)
- **Issue**: A live Librarian→Explorer run crashed with `psycopg2.errors.InvalidTextRepresentation: invalid input syntax for type uuid: "the daughters war"`. The Critic called `get_work_details(work_id=<title>)` for a web-discovered book (no DB id); the unguarded `WHERE works.id = '...'::UUID` cast raised and propagated, killing the run.
- **Root Cause**: `get_work_details` assumed `work_id` is always a valid UUID. Web-discovered candidates have no DB row, so an agent may pass a title. The error was uncaught (unlike `log_suggestion`/`update_suggestion_status`, which wrap in try/except).
- **Solution**: Validate `work_id` as a UUID at the top of `get_work_details`; return `{}` on a non-UUID before any DB access. Added a unit test.
- **Prevention**: UUID-keyed MCP tools must validate input and degrade gracefully — an agent passing a bad id must never crash the run. Proper handling of web-discovered candidates (resolve / enrich) is Spec 4 (see issues.md REC-016).

### 2026-06-05 - split_authors silently misaligned on non-default index (PR #32 review)
- **Issue**: Beyond the InvalidIndexError fixed in PR #32, `split_authors` had a latent silent-corruption bug: on any non-default input index (e.g. a filtered frame) the Author_X columns misaligned — NaN authors on real rows plus phantom rows.
- **Root Cause**: `pd.DataFrame(author_lists.tolist())` discards the input index (fresh RangeIndex), and `pd.concat(..., axis=1)` aligns by index *label*, not position. It only worked when df happened to have a clean 0..n RangeIndex.
- **Solution**: Build the split frame on the input's index: `pd.DataFrame(author_lists.tolist(), index=df.index)`. Regression test `test_split_authors_preserves_nondefault_index`.
- **Prevention**: Any `pd.concat(axis=1)` of a derived frame must construct that frame with `index=df.index` (or use `.str.split(expand=True)`, which preserves it — `split_narrators` was already safe). Watch for `.tolist()`/`.values` between an apply and a concat: both drop the index.

### 2026-06-10 - Frontend rejection-path test fails as "unhandled error" — the mock setup leaks, not the component (Lift 2 Stage 3, PR #45)
- **Issue**: `AddBookView.test.tsx`'s "shows an error when the book is not found" test failed in vitest 4: the rejected `Error` was reported at its `new Error(...)` creation site (vitest's global unhandled-error handler) even though the component's `try/catch` handled it and the error-message assertion actually passed. Which test gets blamed is non-deterministic.
- **Root Cause**: A **persistent** `mockResolvedValue(...)` set in `beforeEach`, then **overridden** by a **persistent** `mockRejectedValue(...)` in one test, leaves a rejected promise vitest flags as unhandled (vitest-dev/vitest#1692). It is NOT a component bug — two prior attempts to "fix the `onSubmit` handler" (a `setTimeout(...).then().catch()` hack and a clean `async/await try/catch`) failed identically. A `window.reportError = () => {}` stub in `setup.ts` did NOT fix it, disproving a plausible-sounding "React 19 routes caught errors through `reportError`" theory.
- **Solution**: Use the single-use `mockResolvedValueOnce` / `mockRejectedValueOnce` variants so each mocked value is consumed exactly once and nothing lingers; drop the persistent default from `beforeEach` and set the value per-test. Isolated repro confirmed: persistent resolve→reject override fails; Once-variants pass for every rejection style.
- **Prevention**: In RTL + vitest tests, never alternate **persistent** `mockResolvedValue`/`mockRejectedValue` on the same mock across tests — use the `...Once` variants. When a test fails with the raw error reported at its creation site and no assertion mismatch, suspect a leaked promise from mock setup, not the component. Verify a proposed root cause (e.g. by applying the stub) before trusting it — a confident, wrong diagnosis cost the most time here. (Companion gotcha: `App.test.tsx` must `vi.mock` every view module, or the real view's `client.ts`→`firebase.ts` `getAuth()` throws `auth/invalid-api-key` at import and the whole suite fails to load — add a `vi.mock('./views/X', ...)` whenever you add a view to `App.tsx`.)

### 2026-06-05 - Conversational Librarian "can't access reading history" — AgentDefinition.tools scopes but does not GRANT permission
- **Issue**: First live `librarian` CLI test (PR #33): asked for a recommendation, the Librarian replied "I don't have access to your reading history" and asked clarifying questions instead. Data was fine (331 reading_history rows; `get_user_trope_preferences` returned a rich profile when called directly).
- **Root Cause**: In the Claude conversational mesh (ADR-045), specialist tools were listed only in each subagent's `AgentDefinition.tools`. In the Claude Agent SDK that list only **scopes** which tools a subagent may use — **permission** is governed by the session-level `ClaudeAgentOptions.allowed_tools`, which deliberately whitelisted just `Task` + the 4 feedback tools. Live probe confirmed: the analyst subagent attempted its tool and was permission-denied; the Librarian's direct attempt was denied too; the model misread the denial as a missing capability. (Subagent MCP-server visibility itself works — the REC-019-style open item from PR #33 is resolved positive.) Secondary find: the current SDK names the delegation block `Agent` (not `Task`), so delegations were traced as `tool: Agent` instead of `agent: <name>`.
- **Solution**: Session `allowed_tools` now permits the whole mesh (`["Task", "Agent", *LIBRARIAN_TOOL_NAMES, "WebSearch"]`); per-subagent scoping via `AgentDefinition.tools` is unchanged. `_emit_block_event` maps both `Task` and `Agent` blocks to `("agent", subagent_type)`. Live re-probe: `agent: analyst` fired, the tool ran, and the Librarian returned the user's actual 20-trope profile.
- **Prevention**: Treat `allowed_tools` as the PERMISSION layer and `AgentDefinition.tools` as the SCOPING layer — every tool any subagent needs must also be session-allowed. A "tool requires permission" denial inside an agent presents to the model as a capability gap ("I can't access X") — when an agent claims it lacks access to something that exists, check the permission layer before the data layer. Event-trace transcripts (`.chat_logs/`) made this diagnosable in minutes — keep recording.
