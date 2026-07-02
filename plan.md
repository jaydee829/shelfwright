# Execution Plan: Agentic Librarian

## Phase 1: Infrastructure & Data Layer
**Goal**: Establish the storage foundation and data models. [COMPLETED]

## Phase 2: Flow 1 - Intake & Enrichment (ETL)
**Goal**: Transform raw CSV reading history into a deep, vectorized knowledge base. [COMPLETED]

## Phase 2.5: Abstract Scout Refactor
**Goal**: Migrate metadata scouts to a hierarchical Strategy Pattern. [COMPLETED]

## Phase 2.6: ETL Hardening & Style Enrichment
**Goal**: Resolve identified gaps in Flow 1 metadata and ingestion logic.
1.  **Contributor Role Support**: Refactor `HistoryIngestor` and scouts to map roles (Editor, Translator, etc.) into the `work_contributors` table.
2.  **Deep Style Scouting**: Implement a specialized `LLMScout` to populate `style_attributes` (JSONB) for Authors and Narrators (pacing, tone, voice differentiation).

## Phase 3: Flow 2 - Recommendation Engine (A2A Mesh)
**Goal**: Build a cognitive mesh of specialized agents using the **Google AI Agent SDK**. [COMPLETED]

### The 4-Agent Mesh
1.  **The Librarian**: Orchestrator. Manages the conversation and delegates to specialists.
2.  **The Analyst**: Strategist. Decomposes "vibes" into tropes and manages the long-term User Style Profile.
3.  **The Explorer**: Scout. Web-based discovery using Gemini Search Grounding.
4.  **The Critic**: Matchmaker. Nuanced ranking including negative signal feedback (e.g., social signals).

### Order of Work
1.  **MCP Server Implementation**:
    -   Expose Postgres/pgvector as an MCP server.
    -   Implement tools for `search_internal_database`, `get_unacted_suggestions`, and `update_reading_status`.
    -   **Enhancement**: Update `check_reading_history` to include temporal re-read logic (>2 years since `date_completed`).
2.  **Agent SDK Foundation**:
    -   Initialize the **Google AI Agent SDK** framework.
    -   Define the 4 Agent Services and their communication contracts.
3.  **Memory & Logic**:
    -   Implement "Re-read Decay" logic in the Librarian.
    -   Implement "Persistence Logic" (re-prioritizing unread suggestions) in the Librarian.
4.  **Style-Based Ranking**:
    -   Enhance `Critic` and `search_internal_database` to query and weight results by Author/Narrator `style_attributes` (satisfying Level 5 Use Cases).
5.  **Feedback Loop**:
    -   Implement "Conversational Correction" (UC6.1) and "Social Signals" (UC6.2) handling.
6.  **Trope-RAG Justification**:
    -   Build prompt templates that anchor final recommendations in retrieved trope **names and descriptions** for evidence-based grounding.

## Phase 4: Web Interface & Analysis
**Goal**: Visualize the reading history and interact with the Librarian.
*   **Order of Work**:
    1.  Initialize Vite/React frontend.
    2.  Build "Trope Cloud" and "Author Style" radar charts using Recharts.
    3.  Implement the Chat UI for recommendation requests and feedback handling.
*   **Packages**: `vite`, `react`, `recharts`, `fastapi`.

## Phase 5: MLOps & Verification
**Goal**: Monitor performance and data health.
*   **Order of Work**:
    1.  Log agent mesh performance (latency, delegation success) to MLFlow.
    2.  Implement a drift detection script for the `Tropes` table to monitor semantic consistency.
    3.  **E2E Testing**: Initialize `test/e2e/` with Playwright to verify full system flows (e.g., UC3.1, UC6.1).
    4.  Final System E2E test: From raw CSV update to verified recommendation.

## Phase 6: Scaling Hardening (beta → dozens of users)
**Goal**: Close the reliability, cost, and data-integrity gaps found by the 2026-07-02 full-codebase
review before growing past friends-and-family. All items are GitHub issues filed 2026-07-02
(work-log entry in `docs/project_notes/issues.md`; fits between ADR-046's beta and Lift 3 productization).

### 6.1 Prod-risk hotfixes (do immediately, tiny diffs)
1.  **#89** deploy.yml pins `--memory=512Mi` — codify the 2Gi OOM fix + enrich-queue rates in infra/08; verify live memory.
2.  **#90** Durable `[skip ci]` squash fix: repo setting → squash message = "PR title" (CD anomaly was diagnosed 2026-06-22; discipline-only today).
3.  **#91** Enable Cloud SQL automated backups (one `gcloud sql instances patch`).
4.  **#99** Import rows stranded `pending` after failed enqueue (one-line retry-filter fix).

### 6.2 Concurrency & capacity (the "can one instance serve dozens" cluster)
5.  **#93** Stop blocking the event loop: async-wrap sync MCP tools (`asyncio.to_thread`), auth dependency, import enqueue loop.
6.  **#94** Don't hold DB sessions across scout/LLM/Thunder calls (fast/deep enrich, chat enrich tool, availability).
7.  **#102** Pool hygiene: `pool_pre_ping`/`pool_recycle`, deliberate sizing vs db-f1-micro (~25 conns), consolidate the ~6 per-module engines.
8.  **#101** Fix the defeated embedding LRU cache (cheapest chat-latency + quota win).
9.  **#103** Outbound timeouts: Gemini `HttpOptions`, Audible fetch.

### 6.3 Data integrity under concurrent users
10. **#95** Unique constraints + advisory lock behind every get-or-create (works/authors/editions/history/suggestions; feeds #88).
11. **#96** Contributor-drop on existing works (verified bug).
12. **#98** Garbage/hallucinated titles must not enter the communal catalog (dead 404 path; trope-scout unknown-book escape).
13. **#97** Deep-enrichment failure visibility: retryable 5xx on empty yield, status column, reconciliation sweep (extends DEBT-035).
14. **#108** timestamptz migration · **#109** FK indexes · **#110** availability_cache upsert+eviction · **#111** shared real-vs-fallback trope predicate · **#112** `update_reading_status` date/dedup/normalization.

### 6.4 Cost & abuse guards (prerequisite for onboarding dozens)
15. **#100** Meter enrichment LLM calls; per-user chat/import budgets + message length cap; paid-tier decision; deep-queue fairness (bulk vs interactive).
16. **#113** Cap chat history reseeded per turn.

### 6.5 Frontend resilience (cold-start-proofing the UX)
17. **#104** Shared load/error/retry hook; auth-gate retry; Settings save-gating (data loss).
18. **#105** Chat + library-search races · **#106** import-view resume/poll-cap/file-input · **#107** history search.

### 6.6 Ops maturity
19. **#114** Uptime check + 5xx/memory/queue-depth alerts; rollback runbook; min-instances decision.
20. **#115** Low-severity sweep checklist (batch opportunistically).
