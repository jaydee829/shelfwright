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
    - **[2026-06-01]** Item 3 (one-shot commitment) addressed: the SequentialAgent already enforces step
      order, and CRITIC_INSTRUCTION now tells the Critic to always commit to a best-effort recommendation
      (never ask a clarifying question / never return empty). Item 4 (multi-agent final-text extraction)
      remains open — re-evaluate after a post-enrichment-hardening e2e.

### 2026-05-31 - REC-018: Spec 4 Follow-ups (Critic ranking + live e2e verification)
- **Status**: Item 2 **Resolved (2026-06-02)** — the full live chain ran end-to-end: the ADK backend (`test_recommendation_e2e`) and the Claude backend (`test_claude_e2e`) each produced a justified recommendation and logged a Suggestion (verified in the DB). Item 1 (explicit candidate ranking) remains **Open** as a recommendation-quality refinement.
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
- **Status**: Resolved (2026-06-01) — StyleScout output normalized to {attr: str} (_flatten_style_map hoists one nested level, drops non-strings) and persist_enriched_work guards every style loop via _iter_style_items (skips+warns on non-string values). Unit + db_integration regression tests.
- **Description**: During the successful PR #22 live e2e, `enrich_and_persist_work` for one discovered book failed with `psycopg2.ProgrammingError: can't adapt type 'dict'`. A Style was looked up/created with `styles.name` = a nested analysis dict (`{'prose_density': '…', …}`) instead of a string label. The StyleScout's structured style analysis is being passed through as the Style *name*. Caught by the `except Exception` in `enrich_and_persist_work` (degrades gracefully — that work's styles aren't persisted; the run continued and still produced a recommendation).
- **URL**: N/A
- **Notes**: Fix in `persist_enriched_work` / the StyleScout→Style mapping (mcp/server.py + scouts): a Style needs a scalar string name; the prose-density-style analysis object should map to `Style.description` or be decomposed into named style rows, not used as the name. Add a regression test that persists a discovered work whose StyleScout output is a dict-shaped style.

### 2026-06-01 - REC-022: Hardcover enrichment silently returns nothing — over-strict `_eq` filters (live e2e finding)
- **Status**: Resolved (2026-06-01) — HardcoverScout rewritten as a 2-step fuzzy search->books-by-id lookup with author-matched, read-count-ranked hit selection (companion titles excluded); format/country preference applied in Python. ADR-043. Live-verified for a known title.
- **Description**: Verifying the PR #22 e2e, HardcoverScout (priority 1, the *primary* physical/ebook + audiobook-length/mood source) contributed nothing for every web-discovered book. The token is valid (HTTP 200), but the GraphQL query filters editions with three exact-match `_eq` clauses — `book.title _eq $title` AND `edition_format _eq $format` ("ebook") AND `country.name _eq "United States of America"` — which almost never all match real data: e.g. "The Spanish Love Deception" *exists* (8 editions) but their `edition_format` values are `""`/`null`/`"Paperback"` (never lowercase "ebook") and most lack the US country row; "The Serpent & The Wings of Night" returns 0 on exact title (the `&`/article differs from Hardcover's stored title). `_make_request` swallows non-200s, so failures are invisible. Note: Hardcover blocks `_ilike` ("not permitted on this server"), so fuzzy title matching needs another approach (normalized exact title, or `book` lookup then editions).
- **URL**: N/A
- **Notes**: Loosen the query in `HardcoverScout.search` (metadata_scout.py:195): drop/relax the `edition_format` and `country` `_eq` filters (select editions, then prefer a format/country match in Python — the scout already loops editions for format selection); normalize the title or query `books` by title then fetch editions. Add a test asserting a known title returns a non-empty result. Until fixed, ebook/physical discoveries rely on Google Books (rate-limited, REC-016) + LLM scouts only.

### 2026-06-01 - REC-023: A single scout's exception aborts the whole multi-scout enrichment (e2e verification finding)
- **Status**: Resolved (2026-06-01) — `ScoutManager.enrich` now wraps each `scout.search(...)` in try/except (warn + continue), so one scout raising no longer discards metadata already gathered by earlier scouts. Unit test `test_enrich_isolates_a_failing_scout`. **Verified end-to-end on the Claude backend:** with the Gemini grounding scouts (StyleScout/LLMTropeScout) hitting the daily 429 cap, four web-discovered works (Divine Rivals, Things We Left Behind, Cover Story, Kiln Me Softly) persisted with **Hardcover-sourced descriptions + page counts** (which were empty before this branch).
- **Description**: Verifying REC-021/REC-022 via a Claude-backend recommendation e2e, every `enrich_and_persist_work` aborted with `enrich_and_persist_work error: 429 RESOURCE_EXHAUSTED (gemini-2.5-flash)`. The REST scouts swallow errors (`_make_request` → `{}`), but the LLM scouts let `generate_content` exceptions propagate; `enrich()` had no per-scout isolation, so a grounding-scout 429 propagated out and the caller discarded the ENTIRE book — including the priority-1 Hardcover data gathered first.
- **URL**: N/A
- **Notes**: This is the defensive fix. The deeper issue (the Claude backend still depending on Gemini `generate_content` for enrichment) is REC-024.

### 2026-06-01 - REC-024: Claude backend's enrichment scouts still use Gemini (next spec)
- **Status**: Resolved (2026-06-02) — GroundedLLM seam (scouts/grounded_llm.py): GeminiGroundedLLM + ClaudeGroundedLLM chosen by AGENT_BACKEND, injected into LLMScout. All four LLM scouts route through it; AGENT_BACKEND=claude runs the scouts (recommendation + Flow-1 ETL) on the Max subscription. Embeddings stay Gemini. ADR-044.
- **Description**: PR #23 / ADR-041 abstracted only the recommendation MESH (Analyst/Explorer/Critic) to Claude + shared MCP DB tools. The per-discovery `enrich_and_persist_work` still runs the shared Flow-1 `ScoutManager`, whose LLM scouts (StyleScout/LLMTropeScout/DirectKnowledgeScout) do **Gemini-native grounding** (`gemini-2.5-flash` via `GROUNDING_MODEL`) and whose trope/style dedup uses Gemini embeddings. So a Claude-backend run is NOT free of the Gemini `generate_content` free-tier daily cap — under that cap the grounding scouts 429 and contribute nothing (REC-023 keeps Hardcover/REST data; styles/tropes are still lost).
- **URL**: N/A
- **Notes**: Next spec: give StyleScout/LLMTropeScout/DirectKnowledgeScout backend-selectable **Claude variants** (Claude + WebSearch for grounding) so a Claude run's enrichment is off Gemini `generate_content`. Embeddings (`gemini-embedding-001`, separate low-volume quota) may stay on Gemini initially. Mirror the `RecommendationBackend` strategy seam (ADR-041) at the scout layer.

### 2026-06-05 - PRS-025: Review + merge the 4 production-build fix PRs
- **Status**: Completed
- **Description**: Addressed Gemini Code Assist review findings on the open PRs from the production DB build, then squash-merged all four to main (in order #29 → #30 → #31 → #32). All fixes TDD'd (red→green) and verified with the full fast suite (198 passed) on merged main.
- **URL**: https://github.com/jaydee829/agentic_librarian/pulls?q=is%3Apr+29..32
- **Notes**:
    1. **#29** (embed throttle): also fixed lock-held-during-`time.sleep()` — slot now reserved atomically inside `_embed_lock`, sleep outside (Gemini suggestion taken verbatim). `test/unit/test_embed_throttle.py`.
    2. **#30** (null contributor name): also strip/validate `role` — whitespace-only or non-string roles fall back to "Author".
    3. **#31** (gitignore db dumps): merged as-is, no findings.
    4. **#32** (cleaning non-unique index): also fixed the latent `split_authors` misalignment — Author_X frame now built with `index=df.index` (see bugs.md 2026-06-05).
    - Gemini Code Assist consumer code review sunsets 2026-07-17 (new installs blocked 2026-06-18) — PR review automation will need a replacement (e.g. /code-review).

### 2026-06-05 - CLI-026: librarian CLI chat harness (ADR-045, PR #33)
- **Status**: Merged (d82f0cb); live verification pending
- **Description**: `librarian` console-script REPL with multi-turn conversations on BOTH backends — ADK wraps LibrarianConversation (+ event callback); Claude gains a true conversational mode (persistent ClaudeSDKClient session delegating to the analyst/explorer/critic specialist mesh via SDK subagents/Task tool). MLflow conversation capture (`librarian_conversations` experiment) with never-block degradation. Built spec→plan→subagent-driven TDD; 206 offline tests pass.
- **URL**: https://github.com/jaydee829/agentic_librarian/pull/33
- **Notes**:
    - **Live verification (updated 2026-06-05, same day)**: item (1) **resolved positive** — subagents DO see the in-process librarian MCP server; the first live test instead surfaced a permission-layer bug (`AgentDefinition.tools` scopes but doesn't grant — see bugs.md 2026-06-05) fixed in the follow-up PR, with a live re-probe returning the user's real 20-trope profile via `agent: analyst` delegation. Still pending: (2) explorer's `WebSearch` live exercise; (3) the full 2-turn smoke per backend (`test/integration/test_cli_chat_live.py`, api_dependent); (4) MLflow connect speed at CLI startup (escape hatch `--no-mlflow`).
    - Invocation in the app container: `python -m agentic_librarian.cli` always works; the `librarian` script is installed at `/home/appuser/.local/bin/librarian` (not on default exec PATH — add to PATH in Dockerfile/compose as a follow-up, or rebuild the image so the build-time editable install picks up [project.scripts]).
    - Known minor follow-ups from final review: no banner on `--once`; consider MLFLOW_HTTP_REQUEST_TIMEOUT in compose.

### 2026-06-05 - INF-029: Unify /history and /works pagination contract (Lift 2)
- **Status**: Open
- **Description**: `/history` endpoint is unpaginated while `/works` paginates (limit 1-200); unify the API contract during Lift 2 front-end work. Tracked per Lift 0 / ADR-047.
- **URL**: N/A
- **Notes**: Consciously deferred from Lift 0; address alongside the front-end API-contract work in Lift 2.

### 2026-06-05 - IMP-028: Single-title import (spec/single-title-import)
- **Status**: Merged pending live verification
- **Description**: `add_book_to_history` MCP tool (new-row-per-read model: re-reads
  insert read events; same-date duplicate guard; read count derived from row count) +
  conversational IMPORT flow on both Librarians + `librarian add` CLI subcommand.
  date_completed defaults to today. Spec: docs/superpowers/specs/2026-06-05-single-title-import-design.md.
- **URL**: N/A (PR pending)
- **Notes**:
    - **Phase-4 front end (logged per user decision)**: the web UI's add form must
      AUTO-FILL the completion-date field with today (visible + editable) rather than
      hiding the default — the default stays explicit to the user.
    - CSV drift accepted: DB is the history source of truth as of 2026-06-05 (see
      key_facts.md); bulk imports remain CSV/Dagster.

### 2026-06-05 - TUNE-027: Conversation tuning round (spec/conversation-tuning)
- **Status**: In Progress (live verification pending)
- **Description**: Explorer search budget (prompt) + maxTurns=25 guard; verification-by-enrichment (enrich_and_persist_work exposed to BOTH conversational Librarians; null = drop candidate, continue); internal-first routing with novelty triggers; series rule in critic+librarians; analyst on haiku; elapsed-seconds event trace. Spec: docs/superpowers/specs/2026-06-05-conversation-tuning-design.md.
- **URL**: N/A (PR pending)
- **Notes**:
    - **Tracked follow-up (schema, ask-first)**: add `series_name`/`series_position` to `works`, populated from Hardcover `featured_series` + a backfill pass over the existing catalog — makes the series rule deterministic instead of model-knowledge-based.

### 2026-06-09 - INF-030: Move per-turn chat DB writes off the event loop (Lift 2 Stage 4)
- **Status**: Open (deferred from Lift 2 Stage 1, PR #43)
- **Description**: The SSE chat turn (`chat/stream.py` `sse_turn`) issues synchronous INSERTs on the asyncio event loop — both `transcript.append_message` calls (`on_persist`) AND the `usage.record_llm_call` write in `runtime._record_event_usage`. Under concurrent `/chat` load this blocks the loop. Gemini flagged the transcript write HIGH on PR #43; the fix must cover BOTH writes coherently via `asyncio.to_thread(...)`.
- **URL**: PR #43 (review reply discussion_r3383988870); see also the `core/usage.py` latency note.
- **Notes**: Do alongside the Stage 4 DatabaseManager pool consolidation. `asyncio.to_thread` copies the current context, so verify `as_user`/`current_user_id` still resolves inside the worker thread. No live impact until the service deploys against Cloud SQL (Stage 4 opens the IAM gate).
