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

### ADR-026: Temporal Re-read Decay Logic (2026-02-17)
**Context:**
- Need to allow recommendations of previously read books without spamming the user with recent reads.
**Decision:**
- Implement a 2.0-year "decay" threshold. Books read >2 years ago are marked as `is_re_read_candidate`.
**Consequences:**
- Pros: Surfaces beloved classics while maintaining discovery of new titles.

### ADR-027: Hybrid Trope-Style Semantic Search (2026-02-17)
**Context:**
- Recommendations based on tropes alone are too plot-focused; styles alone are too vibe-focused.
**Decision:**
- Combine Trope and Style vector similarity into a single discovery tool (`search_internal_database`).
- Average vectors within categories and merge candidate sets.
**Consequences:**
- Pros: Recommendations that match both the "what" (plot) and "how" (prose/tone) of user taste.

### ADR-028: Tiered Feedback (Mood vs. Preference) (2026-02-17)
**Context:**
- User mood ("not in the mood for violence") shouldn't permanently block tropes or books.
**Decision:**
- Handle "Moods" as transient session constraints passed to the Critic for rank penalties.
- Handle "Preferences" (Already Read, Dismissed) as persistent database status updates.
**Consequences:**
- Pros: Responsive to immediate state without losing long-term accuracy.

### ADR-029: Trope-RAG Justification (2026-02-17)
**Context:**
- Users need to know *why* a book was suggested to build trust in the agent.
**Decision:**
- Retrieve trope names, general descriptions, and book-specific "justification" evidence from the DB.
- Force the Critic agent to anchor its reasoning in these specific facts.
**Consequences:**
- Pros: Transparent, grounded, and evidence-based recommendations.

### ADR-030: Lazy Initialization for Global Service Managers (2026-02-17)
**Context:**
- Instantiating global managers (like `DatabaseManager`) at the module level caused crashes during test collection in environments without full configuration (CI).
**Decision:**
- Use lazy initialization for all heavy service managers. Defer credential validation and resource allocation until the first actual use.
- Add override hooks (`set_db_manager`) to allow test-time dependency injection.
**Consequences:**
- Pros: Safe imports across all environments, improved testability.
- Cons: Slightly more complex internal state management.

### ADR-031: Composite Primary Keys for Style Link Tables (2026-02-18)
**Context:**
- An author or work can have multiple style attributes (e.g., 'pacing' and 'tone') associated with the same `style_id` or different ones.
- The previous schema `(author_id, style_id)` as the primary key prevented linking the same style to different attributes for the same entity.
**Decision:**
- Include `attribute_type` in the primary key for `AuthorStyle`, `NarratorStyle`, and `WorkStyle`.
- The new composite primary key is `(entity_id, style_id, attribute_type)`.
**Consequences:**
- Pros: Enables rich, multi-dimensional style tagging; aligns with the `WorkContributor` pattern.
- Cons: Requires database migration for existing installations.

### ADR-032: SQL-Level Vector Similarity with pgvector (2026-02-18)
**Context:**
- Calculating cosine similarity in-memory by loading all tropes or styles is a performance bottleneck as the database grows.
**Decision:**
- Use `pgvector`'s `cosine_distance` operator directly in SQLAlchemy queries (`.order_by(Style.embedding.cosine_distance(vec))`).
- Implement an `lru_cache` for embedding generation to reduce API costs and latency.
**Consequences:**
- Pros: Significant performance gains (uses database indexing), lower memory usage, reduced API costs.
- Cons: Tightens dependency on `pgvector` functionality.

### ADR-033: Eager Loading to Prevent N+1 Queries in Agent Tools (2026-02-18)
**Context:**
- MCP tools like `get_unacted_suggestions` were performing recursive queries for work metadata, tropes, and styles within loops.
**Decision:**
- Use SQLAlchemy `joinedload` and `selectinload` to eagerly fetch all required relationships in a single optimized query.
**Consequences:**
- Pros: Massive reduction in database round-trips; improved response time for the Librarian agent.
- Cons: Slightly more complex query definitions.

### ADR-034: Isolated Database for `db_integration` Tests (2026-05-30)
**Status:** Accepted and implemented (2026-05-30) in `test/conftest.py` — a dedicated `*_test` database is auto-created and all tables are truncated before each `db_integration` test.

**Context:**
- The `db_integration` suite had never been executed before the stack ran under Docker on a single machine. Once it ran, several isolation problems surfaced:
  - Tests run against the **same** database the application uses (`POSTGRES_DB=agentic_librarian`), so they pollute real data.
  - There is **no cleanup** between tests, so rows accumulate across runs. This caused non-deterministic passes (e.g. `search_internal_database` returned results from a *previous* test's committed rows rather than the current test's seed).
  - The MCP tools intentionally open their own independent sessions and commit (coarse-grained design, ADR-013), so a single outer transaction cannot wrap a test for rollback — the tools' commits escape it.

**Decision:**
- Run `db_integration` tests against a **dedicated test database** (e.g. `agentic_librarian_test` via a `TEST_POSTGRES_DB` / `DATABASE_URL` override), never the application database.
- Reset state with a **function-scoped autouse fixture that truncates all tables** before each `db_integration` test (the session-scoped fixture from the schema-creation work continues to create the schema + `vector` extension once).

**Alternatives Considered:**
- Per-test transactional rollback (bind the session to a SAVEPOINT) → rejected: the MCP tools open new sessions on the engine and commit, so their writes bypass and survive the test's transaction.
- `drop_all` + `create_all` per test → rejected: slower than `TRUNCATE`, and still unsafe against the app DB without a separate database.
- Status quo (shared DB, commit seed before tool calls — the stopgap applied in commit 43ed411) → insufficient: it makes the current assertions pass but leaves data pollution and run-to-run drift.

**Consequences:**
- Pros: Deterministic, repeatable integration tests; no risk to application/dev data; aligns with the Dual-Verification mandate (ADR-018).
- Cons: Requires provisioning a separate database in local/compose/CI environments and a truncation fixture; marginally more setup per test run.

### ADR-035: Internal (DB) vs External (Web) Search Clarification & Phased Mesh Delivery (2026-05-30)
**Context:**
- The spec and `agents/search_strategies.py` used "Internal" (Mode A) and "External" (Mode B) to mean *where the web search runs* — in-process `google-genai` grounding vs a separate A2A microservice. **Both are web search.** This conflicts with the intuitive meaning ("internal" = our database) and caused real confusion.
- The 4-agent mesh does not actually run yet: no `model` is set on the `LlmAgent`s, there is no ADK Runner/session entrypoint, and the Explorer has no search capability. Phase 3 was marked COMPLETED but only the *components* exist; they are not wired to run.

**Decision:**
- **Adopt functional naming.** *Internal = retrieval from our Postgres DB* (existing/read/suggested books — already wired via the Critic's `search_internal_database` and the Librarian's `get_unacted_suggestions` / `get_user_trope_preferences`). *External = web discovery via search grounding* (finding new/unread books — the Explorer's job, currently the gap).
- The earlier "Mode A (in-process grounding) vs Mode B (external A2A service)" distinction is an **implementation detail of External web discovery**, not a functional split. The MVP uses Mode A (in-process Gemini grounding); the external A2A service (Mode B) is deferred.
- **Deliver the runnable mesh as four phased specs** (each its own spec → plan → implementation), order 1 → (2, 3) → 4:
    1. **Mesh runtime foundation** — set models on all agents (via `GEMINI_MODEL`), build the ADK Runner/session + a `run_recommendation(prompt)` entrypoint so the Librarian executes and delegates.
    2. **Explorer = external web discovery** — grounded web search on the Explorer; verify real new-book discovery ("ENV-015 part 2").
    3. **Internal retrieval readiness** — DB-backed tools (preferences, vector search, history) return real results against a seeded DB (needs a real Flow 1 enrichment run); rename internal/external in code + docs.
    4. **End-to-end recommendation + Trope-RAG** — full Librarian→Analyst→Explorer→Critic chain yields a justified, logged recommendation; e2e test.

**Consequences:**
- Clearer architecture and naming; removes the Internal/External ambiguity.
- ENV-015 part 2 is scoped to Spec 2 (Explorer external discovery).
- `search_strategies.py`'s A/B benchmark is decoupled from the live mesh (kept as an experiment or removed later).
- Phase 3's "COMPLETED" status was component-level, not runnable-mesh-level; these four specs close that gap.

### ADR-036: The Librarian is a Multi-Turn Conversational Agent (2026-05-30)
**Context:**
- The Librarian should feel like talking to a librarian who remembers the exchange and knows the user's reading history (use_cases.md Levels 4–6: follow-ups, "I already read that" corrections, social signals). A single prompt→response call is too restrictive, and responses may legitimately be a list of authors, authors + an example book, or specific titles depending on the request.

**Decision:**
- The recommendation runtime hosts the Librarian in an ADK `Runner` with a **reusable session**. The public abstraction is a multi-turn `LibrarianConversation` (`start_conversation` → `send` → `send` …) that reuses one `(user_id, session_id)` so the agent remembers prior turns. `run_recommendation(prompt)` remains a one-shot convenience over the same path.
- **Two layers of memory:** *within-conversation memory* = the ADK session; *durable "knows everything you've read"* = the Postgres reading-history DB via the agents' tools, independent of any conversation.
- **Response shape** (authors, authors + an example book, or specific books) is Librarian instruction/behavior decided per request — not constrained by the runtime.
- Spec 1 uses `InMemorySessionService` (ephemeral conversations); `DatabaseSessionService` (Postgres, resumable conversations across restarts) is a deferred upgrade.

**Consequences:**
- Supports the Level 4–6 conversational use cases within a session; the durable user profile persists in the DB regardless of session lifetime.
- Conversations are not resumable across app restarts until `DatabaseSessionService` is adopted.
- Realized by ADR-035 Spec 1; design at `docs/superpowers/specs/2026-05-30-mesh-runtime-foundation-design.md`.

### ADR-037: Pin ADK at 2.1.0 for the Mesh; Defer Upgrade (2026-05-31)
**Context:**
- Spec 2 needs grounded web search on the Explorer, which is a *sub-agent*. ADK forbids built-in tools in sub-agents, but `GoogleSearchTool(bypass_multi_tools_limit=True)` converts the built-in search into a function-calling tool that is allowed there.
- A live spike on the **installed ADK 2.1.0** verified this works — both standalone and via Librarian→Explorer (`AgentTool`) — returning real recent (2024) titles. Note: `use_interactions_api` (the newer documented form) is **not** a real parameter in 2.1.0 (it is silently ignored); `bypass_multi_tools_limit` works on its own.

**Decision:**
- Keep `google-adk` pinned at **2.1.0** for now. Implement the Explorer's grounded search with `GoogleSearchTool(bypass_multi_tools_limit=True)` (no Interactions API).
- Do **not** upgrade ADK mid-feature: the Spec 1 mesh runtime (`Runner`, `LlmAgent`, sessions, `AgentTool`) is built and verified against 2.1.0, and ADK is fast-moving with pinned siblings (`a2a-sdk`, `google-genai`).

**Consequences:**
- Pros: minimal, spike-verified path; no risk to the just-merged Spec 1 runtime.
- **Tech debt (deferred):** a benign `[EXPERIMENTAL] JSON_SCHEMA_FOR_FUNC_DECL` warning; we forgo newer native multi-tool / Interactions-API ergonomics and easy grounding-citation surfacing. **An ADK upgrade is its own future task** — do it when a newer capability is actually needed (e.g. grounding citations for Spec 4), with a full re-verification of the mesh.

**Update (2026-05-31, during Spec 4 brainstorming):** Investigated whether to upgrade ADK for `output_schema`+tools. Findings: **2.1.0 is already the latest stable** on PyPI (line: 2.1.0 › 1.34.1 › …; nothing newer to upgrade to short of unreleased git `main`). The capabilities we thought we lacked actually shipped *below* 2.1.0 and are present: **`output_schema` together with tools** (improvements in 1.26.0, 2026-02-26) and **grounding/citation metadata + interactions-API** (1.27.0, 2026-03-12). Empirically verified in the container: `LlmAgent(tools=[...], output_schema=...)` constructs cleanly and the LlmAgent source states *"The ADK supports using output_schema and tools together … enforcing structure only on the reply."* So the earlier "forgo output_schema" framing was based on stale general docs, not our installed version. **Decision:** stay on 2.1.0, **use `output_schema`+tools natively** (Spec 4), and raise the dependency floor to `google-adk>=2.1.0` / `google-genai>=1.72` so a fresh resolve keeps the capability. The `bypass_multi_tools_limit` approach for `google_search` in a *sub-agent* remains correct (a separate built-in-tools-in-sub-agents constraint). Grounding-citation surfacing is available if a later spec wants it — no upgrade required.

### ADR-038: Security Review is a Per-Spec Practice (2026-05-31)
**Context:**
- The system is an agentic mesh with live web grounding (Spec 2) and a mutable database. The two classic exposures are SQL injection (DB) and prompt injection (LLM mesh acting on untrusted web text). A PR #20 review prompted making security review a standing practice rather than ad hoc.
- Current posture audited: SQLi is mitigated by construction (SQLAlchemy ORM, parameterized queries, no raw/`text()` SQL); crash-on-bad-input was the class closed by the `get_work_details` UUID guard.

**Decision:**
- Run a lightweight threat-model checklist (untrusted inputs, trust boundaries, tool input validation, write authorization, secret handling) during **each spec's review**, and log findings in `docs/project_notes/security.md`.
- Logged two open findings now: **SEC-001** (prompt injection via the Explorer's web grounding) and **SEC-002** (write-tool authorization). Both are slated for **Spec 4**, where the full write-path mesh comes together.

**Consequences:**
- Pros: security gets a recurring, low-overhead review without front-loading a heavy security spec or slipping the recommendation MVP; findings are tracked, not lost.
- Cons: concrete hardening of SEC-001/002 is deferred to Spec 4 — acceptable given the single-user, bounded blast radius today.

### ADR-039: Remove the Orphaned `search_strategies.py` Experiment; Functional Naming is Canonical (2026-05-31)
**Context:**
- `agents/search_strategies.py` defined `InternalSearchAgent` (Mode A: in-process genai
  grounding) and `ExternalA2AAgent` (Mode B: simulated A2A) — both *web* search in the old,
  confusing sense flagged by ADR-035. It was a standalone MLflow experiment with no
  production importer, used the quota-dead `gemini-2.0-flash`, and was superseded by the
  Spec 2 Explorer (grounded `GoogleSearchTool`).

**Decision:**
- Remove the module and its unit test. Functional naming is canonical: **internal =
  retrieval from our Postgres DB** (`search_internal_database`, `get_user_trope_preferences`,
  `get_unacted_suggestions`, `check_reading_history`); **external = web discovery** (the
  Explorer). `agents/services.py` already conforms — no rename needed there.

**Consequences:**
- Less dead code and one fewer source of the internal/external ambiguity. The deferred
  in-process-vs-A2A (Mode A/B) comparison, if ever revisited, is an implementation detail of
  external discovery (ADR-035), not a functional split.

### ADR-040: One-Shot Recommendation is a Fixed-Order SequentialAgent Pipeline (2026-05-31)
**Context:**
- The fully LLM-driven Librarian orchestration was non-deterministic (REC-016): one-shot calls
  sometimes asked a clarifying question instead of answering, and delegation runs sometimes ended
  on a tool/transfer event yielding "(no response)". Web discoveries (no DB id) could not be ranked
  by the Critic.

**Decision:**
- `run_recommendation` runs a fixed-order ADK `SequentialAgent` pipeline (Analyst →
  InternalCandidates → Explorer → Enrichment → Critic → Logger) and returns
  `state["recommendation"]`. The sequence is code, not an LLM decision, so ordering is deterministic
  and the final text is read from session state (not the last event). The conversational multi-turn
  Librarian (ADR-036) is unchanged for interactive chat.
- Web discoveries are de-duped + enriched + persisted (`enrich_and_persist_work` + the shared
  `persist_enriched_work`) so the Critic ranks them with DB-backed Trope-RAG.

**ADK 2.1.0 mechanics (verified empirically during implementation):**
- `output_schema` works together with **function** tools, so the **Analyst** uses `output_schema=Targets`.
  But the **Explorer's `google_search` is a built-in tool**, and Gemini rejects combining a built-in tool
  with function-calling (which is how `output_schema` is enforced) — so the Explorer has NO `output_schema`;
  it emits a JSON `{"books":[...]}` object as text, parsed by the pipeline's Enrichment step.
- Custom (non-LLM) pipeline steps write state via `Event(actions=EventActions(state_delta={...}))`; direct
  `ctx.session.state` mutation does NOT persist in 2.1.0.
- `SequentialAgent` logs a benign deprecation warning (the `Workflow` replacement is not shipped in 2.1.0);
  it remains the correct API for our pinned version.

**Consequences:**
- Deterministic, testable one-shot recommendations; discoveries become first-class catalog Works.
- Security hardening (SEC-001/002) is deferred to Spec 5 but structured for: discoveries are consumed as
  data and all writes funnel through MCP tools (`enrich_and_persist_work` is the single new write surface).
- The live end-to-end test (`test_recommendation_e2e.py`) is `api_dependent`; live verification is gated on
  Gemini quota (free-tier). Each pipeline piece is independently covered by deterministic offline tests.

### ADR-041: Pluggable Agent Backend (ADK + Claude Agent SDK) (2026-06-01)
**Context:**
- The ADK mesh calls models via API keys (Gemini), which cannot reach the user's Claude Max
  *subscription* quota — only the Claude Agent SDK's auto-detected Claude Code auth can. The Gemini
  free-tier 429 wall repeatedly blocked live verification.

**Decision:**
- Introduce a `RecommendationBackend` Strategy seam at the `run_recommendation` entrypoint
  (`AGENT_BACKEND=adk|claude`, default `adk`). `ADKBackend` wraps the existing SequentialAgent pipeline
  verbatim; `ClaudeBackend` is explicit Python sequencing of Claude Agent SDK `query()` calls (Analyst →
  internal candidates → Explorer-with-web-search → enrich → Critic → log), exposing the SAME in-process MCP
  tools via `create_sdk_mcp_server`. Prompts (`agents/prompts.py`), schemas (`agents/schemas.py`), and pure
  helpers (`agents/candidates.py`) are shared so the two backends never drift; the shared Explorer prompt was
  made tool-agnostic ("use your web search tool") so it reads correctly for both google_search (ADK) and
  WebSearch (Claude).
- Structured agent output on the Claude backend uses **JSON-as-text parsed by `coerce_schema_value`** (the
  SDK's `output_format` semantics were undocumented at v0.2.87; the text approach is the robust fallback).
- Embeddings stay on Gemini (pgvector / `gemini-embedding-001`) for both backends — separate, low-volume
  quota. `claude-agent-sdk` is an optional extra; the `claude` CLI is installed in the devcontainer and
  authenticated in-container (one-time manual login) for Max-quota calls.

**Consequences:**
- The recurring Gemini quota wall is bypassable by flipping one config value; the ADK work is preserved as
  the default backend. Two agent implementations to maintain. Using subscription quota for a personal app is
  a ToS gray area (acceptable for personal use; not a supported product path). Conversational Librarian on
  Claude and security hardening (SEC-001/002) remain out of scope.
- Live validation of the Claude backend is deferred until the `claude` CLI is authenticated; the
  `allowed_tools` web-search identifier ("WebSearch" vs "web_search") must be verified on the first live run
  (issues.md REC-019). Each non-LLM piece is covered by deterministic offline tests.

### ADR-042: Model Routing (grounding vs non-grounding) + Transient-Error Retry (2026-06-01)
**Context:**
- The first live e2e run crashed on an uncaught `503 UNAVAILABLE` ("model experiencing high demand") from
  `gemini-2.5-flash` — Google appears to be squeezing gemini-2.5 free-tier capacity. gemini-3.1-flash-lite is
  stable with markedly higher free-tier throughput, but is below 3 Flash on quality and free-tier Search
  *grounding* on the 3.x family is currently unreliable/ambiguous. Two distinct needs (grounded discovery vs
  plain generation) were sharing one model config (`GEMINI_MODEL`), so they couldn't be routed separately.

**Decision:**
- Split model config into two roles. `GEMINI_MODEL` (default **gemini-3.1-flash-lite**) drives the
  NON-grounding mesh agents (Analyst, Critic, Librarian) — high throughput, off the squeezed 2.5 capacity.
  New `GROUNDING_MODEL` (default **gemini-2.5-flash**, honouring `EXPLORER_MODEL` as a back-compat alias)
  drives everything that uses Gemini Search grounding: the Explorer agent AND the LLM scouts
  (StyleScout/LLMTropeScout/audiobook), which were previously (incorrectly) pinned to `GEMINI_MODEL`.
- Add a single shared `HttpRetryOptions` (`llm_retry.py`: 5 attempts, exp backoff, codes 429/500/502/503/504)
  applied everywhere Gemini is called: ADK agents via `Gemini(model=..., retry_options=...)`, and the scout /
  embedding `genai.Client(http_options=...)`. This rides through transient demand spikes instead of crashing
  the run (resolves REC-020). Embeddings stay on `gemini-embedding-001`.

**Consequences:**
- The 503 that crashed the run is now retried with backoff; non-grounding load shifts to the higher-limit
  3.1-flash-lite. Critic ranking quality on 3.1-flash-lite is unverified vs 2.5 — if it regresses, bump
  `GEMINI_MODEL` back to a flash-class model (config-only). If free-tier 3.x grounding later proves reliable,
  the Explorer/scouts can move to `GROUNDING_MODEL=gemini-3.x` for higher limits. Gemma 4 31B was considered
  as a high-limit grounding option but rejected: Gemma on the Gemini API generally lacks the grounding tool
  and is a smaller model (weaker for the grounding/reasoning roles).

### ADR-043: Hardcover Lookup via Fuzzy Search + Book-by-Id (2026-06-01)
**Context:**
- `HardcoverScout` filtered editions with three exact-match clauses (`book.title _eq` AND
  `edition_format _eq "ebook"` AND US `country _eq`). These almost never all matched real data, so
  Hardcover (priority-1 scout) silently contributed nothing to web-discovered books (REC-022). Hasura
  blocks `_ilike`/fuzzy operators on the editions filter, but Hardcover exposes a fuzzy `search` query.

**Decision:**
- Two-step lookup: (1) `search(query: <title>, query_type:"Book")` — by title only (adding the author
  surfaces companion "workbook" entries) — then select the hit whose `author_names` matches and that
  has the most `users_read_count`, excluding companion titles; (2) `books(where:{id:{_eq}})` for
  description/pages/contributions/cached_tags/editions. Format/country preference is applied in Python
  over the returned editions (prefer requested format + US, else format, else any). Note: the live API
  exposes a scalar `contribution` field on `contributions` (not `author_role { name }`).

**Consequences:**
- Hardcover now returns real metadata for known titles, including ones whose stored title differs
  (`&` vs "and", articles) since matching is fuzzy. Two API calls per book instead of one (acceptable —
  priority-1 short-circuits the other scouts; Hardcover quota is generous). Companion/workbook hits are
  filtered heuristically; a future refinement could weight series/edition signals.

### ADR-044: GroundedLLM Seam — Enrichment Scouts Follow AGENT_BACKEND (2026-06-02)
**Context:**
- ADR-041 made the recommendation mesh backend-selectable, but the enrichment LLM scouts stayed on
  Gemini. An `AGENT_BACKEND=claude` run (and the Flow-1 ETL) still hit the Gemini free-tier
  `generate_content` daily cap (20/day), stretching a full reading-history ingest into ~weeks (REC-024).

**Decision:**
- Introduce a `GroundedLLM` provider seam (`scouts/grounded_llm.py`: `generate(prompt, grounded=)`) with
  `GeminiGroundedLLM` (google_search) and `ClaudeGroundedLLM` (Agent SDK WebSearch, run synchronously via
  `asyncio.run`), chosen by `get_grounded_llm()` reading the SAME `AGENT_BACKEND` knob. `LLMScout` takes
  the provider (injectable); all four scouts call `self._llm.generate(...)`. Prompts, JSON parsing, merge
  and persistence are unchanged. Embeddings stay on Gemini (separate, higher quota — not the bottleneck).

**Consequences:**
- One knob flips the whole pipeline (recommendation + batch ETL) between Gemini and Claude; default is
  byte-for-byte Gemini. Claude extraction quality vs Gemini grounding is validated by a live check.
  `ClaudeGroundedLLM.generate` must be called from a synchronous context (it uses `asyncio.run`); scouts
  always are. A full ETL on Claude issues many WebSearch calls — validate Agent SDK rate limits on a
  small batch first.

### ADR-045: Conversation Seam on the Backend Protocol + CLI Chat Harness (2026-06-05)
**Context:**
- No command-line way to exercise the conversational piece. `RecommendationBackend` (ADR-041) is
  one-shot only; multi-turn (`LibrarianConversation`, ADR-036) exists only on ADK, and the Claude
  backend is a fixed pipeline of independent `query()` calls (ADR-040) with no conversational mode.
  Spec: docs/superpowers/specs/2026-06-05-cli-chat-design.md.

**Decision:**
- Extend the strategy seam with `start_conversation(user_id, on_event) -> BackendConversation`
  (`send`/`close`), so multi-turn means the SAME thing on both backends: a stateful Librarian session
  calling DB/web tools on demand. ADK wraps the existing `LibrarianConversation` (+ optional event
  callback in `asend`); Claude gains a true conversational mode via a persistent `ClaudeSDKClient`
  session (LIBRARIAN_INSTRUCTION + full librarian MCP toolset + WebSearch) on a background event-loop
  thread (PR #26 async precedent). Rejected: CLI-managed transcript replay over the one-shot pipeline
  (re-runs the full pipeline per turn: slow, quota-hungry, logs a spurious Suggestion per turn).
- New `librarian` console script (argparse REPL; `--once`, `--backend`, `--quiet`, `--no-mlflow`)
  printing replies plus a compact key-event trace (`on_event(kind, detail)`).
- Each conversation is one MLflow run (experiment `librarian_conversations`: params backend/model/
  mode, per-turn latency metrics, `transcript.jsonl` artifact) owned by the CLI-layer
  `ConversationRecorder` — backends stay pure. Degradation posture: MLflow failures warn once and
  never block the chat; transcript falls back to a local gitignored `.chat_logs/<ts>.jsonl`
  (informed by the 2026-05-31 MLflow 403 bug).

**Consequences:**
- The conversational piece becomes testable on both quota pools, and ADK-vs-Claude conversations are
  comparable in the MLflow UI. The Claude one-shot pipeline is untouched. The new protocol method is
  additive (existing callers unaffected).
