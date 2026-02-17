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

### ADR-016: Explicit Session Flushing for Dependency Management (2026-02-06)
**Context:**
- In complex transactions, new entities are often created and immediately used as foreign keys for subsequent records (e.g., creating a Work and then checking for its Edition).
- SQLAlchemy does not populate the `id` field of a new object until the session is flushed to the database.
**Decision:**
- Explicitly call `session.flush()` after adding a new entity if its ID is required for a subsequent query or relationship within the same transaction.
**Consequences:**
- Pros: Prevents "ID is None" race conditions and data integrity issues.
- Cons: Minor performance overhead of an extra database round-trip (though usually negligible compared to the risk of data corruption).

### ADR-017: Abstract Scout Architecture (2026-02-06)
**Context:**
- As we add more metadata sources (OpenLibrary, StoryGraph, etc.), the monolithic `MultiSourceScout` and loose functions become difficult to maintain and test.
- Need a standardized way to define new sources with consistent error handling and initialization.
**Decision:**
- Adopt a hierarchical abstract architecture:
    - `BaseScout` (ABC): Core contract and shared utilities.
    - `APIScout`: Specialized for structured REST/GraphQL APIs.
    - `LLMScout`: Specialized for unstructured/semantic data using LLMs.
- All metadata sources MUST be implemented as classes inheriting from this hierarchy.
- **ScoutManager**: A central coordinator will handle the registration and merging of multiple scouts.
**Consequences:**
- Pros: Highly modular, easy to add/remove sources, standardized error handling, better testability via class mocking.
- Cons: Slightly more boilerplate for simple APIs.

### ADR-015: Prohibition of Broad Except-Pass Blocks (2026-02-06)
**Context:**
- The use of `except Exception: pass` (or broad `except: pass`) swallows all errors, including keyboard interrupts and unexpected logic failures, making debugging difficult.
**Decision:**
- Broad `except-pass` blocks are strictly prohibited.
- Error handling must be specific (e.g., `except ValueError`) or log the error before continuing.
- When using libraries that provide safety flags (like `errors="coerce"` in Pandas), rely on those instead of broad try-except blocks.
**Consequences:**
- Pros: Better error visibility, easier debugging, more robust code.
- Cons: Requires more explicit handling of edge cases.

### ADR-018: Dual-Verification Pattern for Environment-Dependent Logic (2026-02-06)
**Context:**
- Development occurs in varied environments (Local Windows without Docker vs Docker-ready containers).
- Database-dependent logic (vector search, complex SQL) is difficult to verify without a live instance.
**Decision:**
- Adopt a **Dual-Verification Pattern** for all database-dependent components:
    1.  **Mock Verification**: Use unit tests with mocks to verify code logic and flow. These must run in all CI environments.
    2.  **Live Verification**: Use `@pytest.mark.db_integration` tests to verify actual SQL and data behavior. These run only when a database is reachable.
- Both test types must be implemented simultaneously to ensure parity.
**Consequences:**
- Pros: Guaranteed logic verification in CI, robust data verification in staging/local-docker, clear documentation of environmental dependencies.
- Cons: Increased testing overhead (writing tests twice).

### ADR-019: 4-Agent Specialist Mesh (2026-02-13)
**Context:**
- Monolithic agents are difficult to tune and suffer from "prompt bloat."
- Need to separate "Strategic Planning" from "Data Scouting" and "Nuanced Ranking."
**Decision:**
- Adopt a 4-agent cognitive mesh:
    1. **Librarian**: Orchestrator (Delegation).
    2. **Analyst**: Strategist (Parameter extraction).
    3. **Explorer**: Scout (External discovery).
    4. **Critic**: Matchmaker (Ranking & Feedback).
**Consequences:**
- Pros: Specialized tuning for each agent, modular reasoning, easier to debug failure points.

### ADR-020: Google AI Agent SDK for Mesh Communication (2026-02-13)
**Context:**
- Need a standardized protocol for discovery and delegation between agents.
- The project is already in the Google/Gemini ecosystem.
**Decision:**
- Use the **Google AI Agent SDK** (implements the A2A protocol) to power the agent services.
**Consequences:**
- Pros: Native A2A support, seamless Gemini integration, scalable to Vertex AI Agent Engine.

### ADR-021: Association Object for Contributor Roles (2026-02-17)
**Context:**
- Need to support multiple roles per work (e.g., Author, Editor, Translator) which a simple junction table or direct relationship cannot handle well.
**Decision:**
- Refactor `work_contributors` into a full association object `WorkContributor` with a `role` field.
**Consequences:**
- Pros: Robust support for anthologies and translated works.
- Cons: Slightly more complex queries (extra join).

### ADR-022: Relational Style Model & Vectorization (2026-02-17)
**Context:**
- Literary and performance styles (pacing, tone, voice diff) were stored in `JSONB` blobs, making them difficult to deduplicate or use for vector similarity.
**Decision:**
- Create a standardized `Style` model with embeddings.
- Link Authors, Narrators, and Works to this model via association tables (`AuthorStyle`, `NarratorStyle`, `WorkStyle`).
**Consequences:**
- Pros: Semantic deduplication via `StyleManager`, enabling high-precision vector-based recommendations.

### ADR-023: Informed Scouting (Contextual baseline) (2026-02-17)
**Context:**
- Scouting a book's style without knowing the author's general profile leads to redundant or inconsistent "deltas."
**Decision:**
- Pass the existing Author's styles from the database into the LLM prompt as a "baseline."
- Instruct the LLM to only report stylistic deviations (deltas) from this baseline for the specific work.
**Consequences:**
- Pros: Cleaner work-specific metadata, higher accuracy in identifying "stylistic drift."

### ADR-024: Style Inheritance & Override Pattern (2026-02-17)
**Context:**
- Most books by an author share their core style, but some vary (e.g., Running Man vs The Stand).
**Decision:**
- Implement an inheritance pattern in MCP tools (`get_work_details`):
    1. Retrieve `WorkStyle` overrides first.
    2. Inherit missing attributes from the primary `AuthorStyle` profile.
**Consequences:**
- Pros: Most granular data is always used; avoids massive data duplication.

### ADR-025: Strict Import Hierarchy (2026-02-17)
**Context:**
- Circular dependencies were common between Models, Scouts, and Assets.
**Decision:**
- Move most just-in-time (JIT) imports to the top level to establish a clear hierarchy (Models -> Managers -> Scouts -> Assets).
- Use service layers or manual `sys.path` injection only where absolutely necessary to maintain tool alignment.
**Consequences:**
- Pros: Better IDE support, predictable load order.
- Cons: Requires careful management of model dependencies.
