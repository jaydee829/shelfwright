# Architectural Decision Records

This file documents key architectural decisions, their context, and trade-offs.

## Templates

### ADR-XXX: Decision Title (YYYY-MM-DD)

**Context:**
- Why the decision was needed
- What problem it solves

**Decision:**
- What was chosen

**Alternatives Considered:**
- Option 1 -> Why rejected
- Option 2 -> Why rejected

**Consequences:**
- Benefits
- Trade-offs

## Decisions

### ADR-001: Model Context Protocol (MCP) for Data Access (2026-01-27)
**Context:**
- Need a standardized way to expose database and internal utilities to specialized agents.
**Decision:**
- Adopt Model Context Protocol (MCP) as the communication layer for data tools.
**Consequences:**
- Pros: Seamless integration with MCP-compliant agents, unified tool interface.

### ADR-002: A2A Protocol for Agent Collaboration (2026-01-27)
**Context:**
- Need robust multi-agent coordination for search, filtering, and ranking.
**Decision:**
- Use the A2A Protocol (Linux Foundation standard).
**Consequences:**
- Pros: Standardized discovery, secure messaging, structured delegation.

### ADR-003: Postgres + pgvector for Storage (2026-01-27)
**Context:**
- Need to store both relational metadata and semantic embeddings (tropes, styles).
**Decision:**
- PostgreSQL with `pgvector` extension.
**Consequences:**
- Pros: Single database for relational and vector data, solid ecosystem.

### ADR-004: MLOps Stack Selection (2026-01-27)
**Context:**
- Need orchestration, versioning, and experiment tracking.
**Decision:**
- Dagster (Orchestration), DVC (Data Versioning), MLFlow (Experiment Tracking).
**Consequences:**
- Pros: Industry-standard tools for reproducibility and monitoring.

### ADR-005: Standardized Testing Strategy (2026-01-28)
**Context:**
- Need to ensure all agents and contributors follow consistent testing practices (TDD, coverage, use-case driven).
**Decision:**
- Adopt a formal testing strategy documented in `docs/testing_strategy.md`, enforced via agent protocols and a standardized `.agent/workflows/test.md` workflow.
**Consequences:**
- Pros: Higher code quality, better maintainability, consistent verification across different agents.
- Cons: Slightly higher initial overhead for new contributors.
### ADR-006: Secure Credential Management & Interactive Prompting (2026-01-28)
**Context:**
- Need to prevent hardcoded credentials and ensure users are prompted for missing information in interactive environments.
**Decision:**
- Use `python-dotenv` for local configuration.
- Implement interactive prompting for missing `POSTGRES_USER` and `POSTGRES_PASSWORD` in `DatabaseManager`.
- Raise `ValueError` if credentials remain missing in non-interactive environments.
**Consequences:**
- Pros: Enhanced security by removing fallbacks, improved user experience in local development.

### ADR-007: Local-Only Service Exposure for Development (2026-01-28)
**Context:**
- Need to minimize the attack surface of Docker services during local development.
**Decision:**
- Bind Postgres and MLFlow ports to `127.0.0.1` in `docker-compose.yml`.
**Consequences:**
- Pros: Services are not exposed to the local network by default.
- Cons: Requires manual reconfiguration for remote access or container-to-container access from outside the default bridge network.

### ADR-008: Data Versioning Scope (2026-01-30)
**Context:**
- Misconfiguration was found where orchestration code was tracked by DVC instead of Git.
**Decision:**
- DVC MUST only be used for data files (e.g., `data/raw/*.csv`).
- Orchestration code and other Python source files MUST be tracked exclusively by Git.
**Consequences:**
- Pros: standard dev ergonomics, better code reviews, avoidance of "missing file" errors during local development.

### ADR-009: Encapsulate Ingest Logic in HistoryIngestor (2026-01-30)
**Context:**
- CSV cleaning and model mapping were becoming scattered and harder to test in isolation.
**Decision:**
- Create a `HistoryIngestor` class to centralize cleaning (via `cleaning.py`) and SQLAlchemy object generation (`to_models`).
**Consequences:**
- Pros: Cleaner Dagster assets, easier unit testing of the ingest pipeline, clear path for Phase 2 enrichment.

### ADR-010: Contextual Year Inference for Ambiguous Dates (2026-01-30)
**Context:**
- The raw CSV contains ambiguous dates like "4-Jan" without a year.
- These dates appear in clusters that share a year with unambiguous dates (e.g., "1/7/2020").
**Decision:**
- Use contextual inference (`ffill` and `bfill`) on extracted years from unambiguous dates within the same CSV to fill missing years.
**Consequences:**
- Pros: Automated reconstruction of historical dates without manual data entry.
- Cons: **Dependency on Row Order**. If the CSV rows are not chronologically clustered, the inferred year may be incorrect. Downstream logic must be aware that `date_completed` may be an estimate in these cases.
### ADR-011: Dual-Pathway Audiobook Scouting & MLFlow Benchmarking (2026-01-30)
**Context:**
- Audiobook metadata (especially duration) is often inconsistent across sources.
- Comparison between "web scraping + parsing" vs "direct LLM knowledge" is needed to determine the most reliable and cost-effective method.
**Decision:**
- Implement two concurrent scouting pathways:
    - **Pathway A (Scraping)**: Google Custom Search -> BeautifulSoup Scraping -> LLM Extraction.
    - **Pathway B (Direct)**: Gemini Model with built-in Search Grounding.
- Log both results, latency, and success metrics to MLFlow for benchmarking.
**Consequences:**
- Pros: Data-driven decision making for metadata sources, fallback robustness.
- Cons: Increased API cost during the experimentation phase.

### ADR-012: Trope Deduplication via Semantic Similarity (2026-02-06)
**Context:**
- Book scouts often return inconsistent tags (e.g., "Enemies-to-Lovers" vs "Enemies to Lovers").
- We need a way to group these into standardized tropes to avoid sparse vector space and fragmented recommendations.
**Decision:**
- Use `text-embedding-004` (Gemini) for trope vectorization.
- Implement a `TropeManager` that checks for exact name matches first, then uses cosine similarity with a default threshold of `0.85` to deduplicate incoming tags.
**Consequences:**
- Pros: Automated standardization, reduces noise in the database, improves recommendation relevance.
- Cons: Small risk of false positives (merging distinct tropes) if the threshold is too low.

### ADR-013: Hybrid Data Access and Coarse-Grained MCP Tools (2026-02-06)
**Context:**
- Need to balance performance for batch ingestion (Flow 1) with agentic flexibility for recommendations (Flow 2).
- Pure MCP for batch ingestion introduces significant overhead and complex transaction management.
**Decision:**
- **Flow 1 (ETL)**: Use direct SQLAlchemy/ORM access for deterministic, high-performance batch processing.
- **Flow 2 (Agents)**: Use Model Context Protocol (MCP) for agent discovery and interaction.
- **Tool Design**: Implement "Coarse-Grained" MCP tools that encapsulate complex logic (e.g., search + filter + pgvector math) into single atomic operations.
**Consequences:**
- Pros: High performance for data pipelines, reduced latency/cost for agents, robust ACID compliance for complex recommendation transactions.
- Cons: Duplicate logic definitions (SQLAlchemy models vs MCP schemas), though minimized by sharing core internal scouts/managers.

### ADR-014: Standardized Use of Single With Statements (2026-02-06)
**Context:**
- Nested `with` statements (e.g., `with A: with B:`) are less readable and trigger linting errors (SIM117).
- Consistency across the codebase is required to satisfy pre-commit checks.
**Decision:**
- Always use a single `with` statement with multiple contexts separated by commas (e.g., `with A, B:`).
- This applies to database sessions, file handles, and mock patches.
**Consequences:**
- Pros: Cleaner code, guaranteed compliance with `ruff` (SIM117), reduced indentation levels.
