# Work Log (Issues)

This file tracks work history and ticket references.

## Templates

### YYYY-MM-DD - TICKET-ID: Brief Description
- **Status**: Completed / In Progress / Blocked
- **Description**: 1-2 line summary
- **URL**: Link to ticket or PR
- **Notes**: Any important context

## Log

### 2026-01-27 - MEM-001: Initialize Project Memory
- **Status**: Completed
- **Description**: Setting up `docs/project_notes/` and memory protocols.
- **URL**: N/A

### 2026-01-30 - ST-002: Phase 1 Step 3 - DVC & Orchestration Refactor
- **Status**: Completed
- **Description**: Fixed DVC tracking, refactored Dagster orchestration, and added unit tests for sensors.
- **URL**: [walkthrough.md](file:///C:/Users/Justin.Merrick/.gemini/antigravity/brain/49c17630-e020-4fd4-b696-bf6db697431f/walkthrough.md)

### 2026-01-30 - ETL-003: Phase 2 Step 1 - Ingest Refactoring
- **Status**: Completed
- **Description**: Refactored CSV cleaning, implemented `HistoryIngestor` class for model mapping, and updated Dagster orchestration.
- **URL**: [walkthrough.md](file:///C:/Users/Justin.Merrick/.gemini/antigravity/brain/5ce3f9cf-5c02-4fc9-b36b-dfd010ef9c9c/walkthrough.md)

### 2026-01-30 - ETL-004: Phase 2 Step 2 - MultiSourceScout Implementation
- **Status**: Completed
- **Description**: Implemented `MultiSourceScout` with dual-pathway audiobook scouting (Audible Scraping vs Direct LLM Knowledge) and MLFlow logging.
- **URL**: [walkthrough.md](file:///C:/Users/Justin.Merrick/.gemini/antigravity/brain/0e68aa32-bb55-4a26-824f-540a33780cf3/walkthrough.md)
- **Efficacy Test Plan**:
    1. Run `test_efficacy.py` in an environment with valid `GOOGLE_SEARCH_API_KEY` and `HARDCOVER_API_KEY`.
    2. Review MLFlow experiment `audiobook_scouting_comparison`.
    3. Metrics to compare: `pathway_a_latency` vs `pathway_b_latency`, `pathway_a_minutes` accuracy vs `pathway_b_minutes`.
    4. Goal: Determine if Direct LLM Knowledge (B) is reliable enough to replace Scraping (A).

### 2026-02-06 - ETL-005: Robust Metadata Year Parsing
- **Status**: Completed
- **Description**: Refactored `metadata_scout.py` to use regex for year extraction, improving robustness against various date formats.
- **URL**: [walkthrough.md](file:///C:/Users/Justin.Merrick/.gemini/antigravity/brain/d22419e7-1275-4f93-9170-efa7436e44cf/walkthrough.md)

### 2026-02-06 - ETL-006: Phase 2 Step 3 - TropeManager Implementation
- **Status**: Completed
- **Description**: Implemented `TropeManager` for semantic tag deduplication and embedding using TDD. Achieved 100% test coverage.
- **URL**: N/A

### 2026-02-06 - ETL-007: Phase 2 Step 4 - Dagster Orchestration
- **Status**: Completed
- **Description**: Orchestrated the ETL flow with Dagster using modular assets (`raw_history`, `enriched_metadata`, `vectorized_tropes`). Integrated `MultiSourceScout` and `TropeManager`.
- **URL**: N/A

### 2026-02-06 - ARC-008: Coarse-Grained MCP Strategy
- **Status**: Completed
- **Description**: Refined Phase 3 architecture to use a Hybrid Data Access model and Coarse-Grained MCP tools to handle complex transactions in Flow 2.
- **URL**: N/A

### 2026-02-06 - ARC-009: Phase 2.5 - Abstract Scout Refactor
- **Status**: Completed
- **Description**: Refactored metadata scouting into a hierarchical abstract architecture (`BaseScout` -> `APIScout`/`LLMScout`).

### 2026-02-13 - REC-010: Phase 3 Step 1 - MCP Server Core
- **Status**: Completed
- **Description**: Initialized FastMCP server with `find_recommendations` and `log_suggestion`. Implemented Dual-Verification Pattern with shared JSON fixtures.

### 2026-02-13 - REC-011: Phase 3 Step 2 - Agent Mesh Foundation
- **Status**: Completed
- **Description**: Refactored the Agent Mesh to use the Google ADK "Reasoning Dispatcher" pattern. Librarian now delegates to Analyst, Explorer, and Critic via `AgentTool`. Integrated coarse-grained MCP tools as specialist capabilities.
- **URL**: N/A

### 2026-02-17 - ARC-012: Phase 2.6 - ETL Hardening & Style Enrichment
- **Status**: Completed
- **Description**: Refactored contributor roles, implemented structured Style/Trope models with vectorization, and added "Informed Scouting" context.
- **URL**: N/A
- **Notes**: Includes `LLMTropeScout` and `StyleScout` with 9 author attributes.

### 2026-02-17 - REC-013: Phase 3 - Recommendation Engine Completion
- **Status**: Completed
- **Description**: Implemented temporal re-read logic, hybrid trope/style search, unacted suggestion persistence, and Trope-RAG justifications.
- **URL**: N/A
- **Notes**: Completed the full Specialist Mesh feedback loop (Librarian, Analyst, Explorer, Critic).

### 2026-05-30 - ENV-014: Centralize Dev to WSL2 + Compose Devcontainer
- **Status**: In Progress
- **Description**: Wired the dev container to `docker-compose.yml` (app + db + mlflow on one network), completed `.env.example`, added `.dockerignore`, and ignored `mlruns/`. Centralizing development onto a single machine under Claude Code.
- **URL**: https://github.com/jaydee829/agentic_librarian/pull/15
- **Notes**: Prior machine used a Conda env + a non-Docker agent harness; this machine uses the compose-based devcontainer (deps installed `--system` in-container, no conda). `key_facts.md` Local Development updated accordingly.

### 2026-05-30 - ENV-015: MVP Wiring Gaps (Deep-Dive Findings)
- **Status**: In Progress
- **Description**: Repository deep dive found two "implemented-but-not-wired" gaps to close for a working MVP.
- **URL**: N/A
- **Notes**:
    1. **[Resolved]** `StyleScout` and `LLMTropeScout` were implemented + unit-tested but never registered in `create_scout_manager()`, so live enrichment produced empty styles and no curated tropes (`vectorized_tropes` fell back to genres/moods). Now registered at priorities 5/6 (StyleScout after the audiobook scouts so `narrator_names` is populated first). Covered by mock unit tests + an `api_dependent` live smoke test.
    2. **[Resolved]** `ExplorerAgent` (`agents/services.py`) had no search tool wired. Spec 2 (PR #20) added `GoogleSearchTool(bypass_multi_tools_limit=True)` + its own `EXPLORER_MODEL` (gemini-2.5-flash) + an anti-hallucination instruction; grounded web discovery verified live (real 2024 titles). `search_strategies.py` left as-is.

### 2026-05-31 - REC-017: Audiobook Smoke Coverage Under Free-Tier Quota Constraint
- **Status**: Open (Spec 5 or later)
- **Description**: The Flow 1 smoke test cannot include an audiobook row without risking `429 RESOURCE_EXHAUSTED` on the free-tier Gemini API (20 requests/day). The audiobook path (AudiobookScout + DirectKnowledgeScout + StyleScout + LLMTropeScout) fires 4+ LLM calls per row, exhausting the daily budget on active dev days.
- **URL**: N/A
- **Notes**: Two options: (a) upgrade to a paid Gemini tier; (b) add a separate fixture-driven audiobook smoke that pre-seeds Audible HTML and only exercises the scraping + JSON extraction path (no quota-consuming generate_content calls). The physical-book smoke in `test_flow1_etl_live.py` covers the happy path end-to-end; audiobook is left for a future tier upgrade or fixture-based approach. See bugs.md for the rate-limit failure details.

### 2026-05-31 - REC-016: Spec 4 Requirements Surfaced by Spec 2 Live Runs
- **Status**: Resolved in Spec 4 (ADR-040)
- **Description**: Live Librarian→Explorer runs confirmed the Explorer's grounded discovery works and surfaced recommendation-flow work for Spec 4.
- **URL**: N/A
- **Notes**:
    1. **Web-candidate de-dup (Case 1)**: when the Explorer surfaces a title already in the DB (a prior suggestion or read book), resolve it to the existing `Work` (title/author match) and prefer the DB entry over the bare discovery.
    2. **Scout-enrichment of new discoveries (Case 2)**: a web-discovered book not in the DB has no tropes/styles; enrich it via the `ScoutManager` (Flow 1) so the Critic can rank/justify it — new machinery (e.g. an `enrich_work` tool, or a discover→enrich→persist→rank step).
    3. **Librarian one-shot orchestration**: `run_recommendation` is one *call*, not a forced answer; the conversational Librarian sometimes asks a clarifying question or delegates non-deterministically. Tune so a one-shot recommendation request commits to a best-effort recommendation.
    4. **Multi-agent final-response extraction**: in some delegation runs the Librarian ends on a tool/transfer event with no text, so `asend` returned "(no response)". Harden the runtime's final-text extraction for multi-agent chains.
    - Interim safety net shipped in Spec 2: `get_work_details` guards non-UUID ids (see bugs.md).
    - Items 1 (web-candidate de-dup) and 2 (scout-enrichment of discoveries) are handled by `enrich_and_persist_work`; items 3 (one-shot determinism) and 4 (final-response extraction) are handled by the fixed-order SequentialAgent pipeline returning `state['recommendation']` (ADR-040).

### 2026-05-31 - REC-018: Spec 4 Follow-ups (Critic ranking + live e2e verification)
- **Status**: Open (Spec 5 / next quota window)
- **Description**: Refinements surfaced by the Spec 4 final review.
- **URL**: N/A
- **Notes**:
    1. **Explicit candidate ranking**: the pipeline gathers `state["candidate_ids"]` (internal + enriched discoveries) but the `CriticAgent` re-derives candidates via its own `search_internal_database`. Enriched discoveries are still rankable because they are persisted with embeddings (the Critic's vector search surfaces them), but ranking is not *guaranteed* to include a specific discovery. Refinement: feed `candidate_ids`/`targets` into the pipeline Critic (without disturbing the shared conversational Critic) and have it `get_work_details` each id. Pairs with the LoggerAgent TODO (log the Critic's ranked top pick, not gather-order `candidate_ids[0]`).
    2. **Live e2e verification pending**: `test_recommendation_e2e.py` (api_dependent) could not be live-verified during Spec 4 — Gemini free-tier quota was exhausted (429). Each pipeline piece is covered by deterministic offline tests, and the critical `CriticAgent` `output_key="recommendation"` wiring is asserted offline (`test_pipeline_assembly.py`), but the full live chain (real Analyst/Explorer/Critic) must be run once when quota is available to confirm a justified recommendation is produced and logged.

### 2026-06-01 - REC-019: Verify Claude Explorer web-search tool name on first live run
- **Status**: Resolved (2026-06-01) — first live `test_claude_e2e` PASSED. `allowed_tools=["WebSearch"]` is correct: Claude's Explorer performed real web searches and surfaced live titles (e.g. *A Taste of Gold and Iron*, *The Shadows Between Us*); the Critic produced a full Trope-RAG report and `log_suggestion` persisted it. Run on the Max subscription via the in-container `claude` CLI (auth persisted in the `claude_auth` volume + `CLAUDE_CONFIG_DIR`). Gemini enrichment sub-step hit the 2.5 daily cap and degraded gracefully (didn't fail the run).
- **Description**: The Claude backend's Explorer step passes `allowed_tools=["WebSearch"]` (Claude Code's built-in web-search tool; `allowed_tools` uses PascalCase CLI tool names, cf. `["Read", "Grep"]`). This cannot be verified without an authenticated `claude` CLI.
- **URL**: N/A
- **Notes**: On the first live `test_claude_e2e` run (after `claude` auth), confirm the Explorer actually performs a web search. If it does not, the correct identifier may be the server-tool name `"web_search"` — change `agents/backends/claude.py` accordingly. The shared `EXPLORER_INSTRUCTION` was made tool-agnostic ("use your web search tool") so it reads correctly for both the ADK (`google_search`) and Claude (`WebSearch`) backends.

### 2026-06-01 - REC-020: Pipeline should tolerate transient LLM 5xx (live e2e finding)
- **Status**: Resolved (2026-06-01) — shared `HttpRetryOptions` (5 attempts, exp backoff, codes 429/500/502/503/504) applied to every Gemini call: ADK agents via `Gemini(retry_options=...)` (ADK plumbs it into the genai client's `HttpOptions`, google_llm.py) and scout/embedding `genai.Client(http_options=...)`. Paired with model routing (ADR-042): non-grounding mesh on gemini-3.1-flash-lite, grounding on gemini-2.5-flash — two separate 15-RPM free buckets instead of one. Google Books enrichment-burst backoff is the remaining piece (tracked under REC-016).
- **Description**: The first full live recommendation e2e (ADK backend) ran the whole chain (Analyst → Explorer found real 2024 romance titles → enrichment → Critic) but crashed when a Gemini call returned `503 UNAVAILABLE` ("model experiencing high demand"). The error propagated uncaught from `LlmAgent` through `run_recommendation`. Google Books also 429'd from rapid enrichment calls (degrades gracefully — discoveries skipped).
- **URL**: N/A
- **Notes**: A transient 5xx from one agent's LLM call should not crash the whole recommendation. Add retry-with-backoff on transient 5xx (ADK retry config or a wrapper) for the mesh/pipeline LLM calls; consider light rate-limiting / backoff for the per-discovery Google Books enrichment burst. Re-running when Gemini is not under load should succeed. (The array-truthiness bug surfaced in the same run is fixed + regression-tested.)

### 2026-06-01 - REC-021: Style "name" can be a dict — enrich_and_persist_work crashes on persist (live e2e finding)
- **Status**: Open (deferred to a subsequent enrichment-hardening branch)
- **Description**: During the successful PR #22 live e2e, `enrich_and_persist_work` for one discovered book failed with `psycopg2.ProgrammingError: can't adapt type 'dict'`. A Style was looked up/created with `styles.name` = a nested analysis dict (`{'prose_density': '…', …}`) instead of a string label. The StyleScout's structured style analysis is being passed through as the Style *name*. Caught by the `except Exception` in `enrich_and_persist_work` (degrades gracefully — that work's styles aren't persisted; the run continued and still produced a recommendation).
- **URL**: N/A
- **Notes**: Fix in `persist_enriched_work` / the StyleScout→Style mapping (mcp/server.py + scouts): a Style needs a scalar string name; the prose-density-style analysis object should map to `Style.description` or be decomposed into named style rows, not used as the name. Add a regression test that persists a discovered work whose StyleScout output is a dict-shaped style.

### 2026-06-01 - REC-022: Hardcover enrichment silently returns nothing — over-strict `_eq` filters (live e2e finding)
- **Status**: Open (deferred to a subsequent enrichment-hardening branch)
- **Description**: Verifying the PR #22 e2e, HardcoverScout (priority 1, the *primary* physical/ebook + audiobook-length/mood source) contributed nothing for every web-discovered book. The token is valid (HTTP 200), but the GraphQL query filters editions with three exact-match `_eq` clauses — `book.title _eq $title` AND `edition_format _eq $format` ("ebook") AND `country.name _eq "United States of America"` — which almost never all match real data: e.g. "The Spanish Love Deception" *exists* (8 editions) but their `edition_format` values are `""`/`null`/`"Paperback"` (never lowercase "ebook") and most lack the US country row; "The Serpent & The Wings of Night" returns 0 on exact title (the `&`/article differs from Hardcover's stored title). `_make_request` swallows non-200s, so failures are invisible. Note: Hardcover blocks `_ilike` ("not permitted on this server"), so fuzzy title matching needs another approach (normalized exact title, or `book` lookup then editions).
- **URL**: N/A
- **Notes**: Loosen the query in `HardcoverScout.search` (metadata_scout.py:195): drop/relax the `edition_format` and `country` `_eq` filters (select editions, then prefer a format/country match in Python — the scout already loops editions for format selection); normalize the title or query `books` by title then fetch editions. Add a test asserting a known title returns a non-empty result. Until fixed, ebook/physical discoveries rely on Google Books (rate-limited, REC-016) + LLM scouts only.
