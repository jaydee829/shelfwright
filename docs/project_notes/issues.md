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
    2. **[Open]** `ExplorerAgent` (`agents/services.py`) has no search tool wired; the real search strategies in `agents/search_strategies.py` are only used by the standalone `run_search_experiment` benchmark.
