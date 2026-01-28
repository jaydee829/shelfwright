# Execution Plan: Agentic Librarian

## Phase 1: Infrastructure & Data Layer
**Goal**: Establish the storage foundation and data models.
*   **Order of Work**:
    1.  Provision Postgres + `pgvector` and MLFlow via Docker.
    2.  Implement SQLAlchemy models and connection string management.
    3.  Set up DVC for tracking the evolution of `data/raw`.
*   **Packages**: `sqlalchemy`, `pgvector`, `psycopg2-binary`, `mlflow`.
*   **Files/Classes**:
    *   `docker-compose.yml`: Container orchestration for PG, pgvector, and MLFlow.
    *   `src/agentic_librarian/db/session.py`: Class `DatabaseManager` for session and engine lifecycle.
    *   `src/agentic_librarian/db/models.py`: Declarative classes for `Author`, `Work`, `Edition`, `Narrator`, `Trope`, `ReadingHistory`.
    *   [schema.md](file:///c:/Users/Justin.Merrick/Python_Code/Projects/agentic_librarian/agentic_librarian/schema.md): Reference for full table structures.

## Phase 2: Flow 1 - Intake & Enrichment (ETL)
**Goal**: Transform raw CSV reading history into a deep, vectorized knowledge base.
*   **Order of Work**:
    1.  Refactor CSV cleaning to map rows into internal `Edition` / `Work` objects.
    2.  Implement the `MetadataScout` for basic facts (ISBN, Page Count).
    3.  Implement the `TropeManager` for semantic tag deduplication and embedding.
    4.  Orchestrate the flow with Dagster.
*   **Packages**: `pandas`, `dagster`, `google-genai`, `requests`.
*   **Files/Classes**:
    *   `src/agentic_librarian/etl/ingest.py`: Class `HistoryIngestor` to read CSV and stage jobs.
    *   `src/agentic_librarian/scouts/metadata_scout.py`: Update existing file; implement Class `MultiSourceScout` (Google Books + Hardcover).
    *   `src/agentic_librarian/scouts/trope_manager.py`: [NEW] Class `TropeManager` to handle trope seeding, vectorization, and similarity deduplication.
    *   `src/agentic_librarian/orchestration/assets.py`: [NEW] Dagster Asset definitions for `raw_history`, `enriched_metadata`, `vectorized_tropes`.

## Phase 3: Flow 2 - Recommendation Engine (A2A & MCP)
**Goal**: Build a user-facing agentic system using A2A for collaboration and MCP for data tools.
*   **Order of Work**:
    1.  **MCP Server Implementation**:
        -   Expose the Postgres/pgvector database as an MCP server.
        -   Add tools for checking `Suggestions` and `ReadingHistory`.
    2.  **Experiment: Search Strategies**:
        -   Implement internal search tool using `google-genai`.
        -   Implement standalone search service with A2A interface.
        -   Compare results in MLFlow.
    3.  **Intelligent Filter Agent**:
        -   Implement Re-read logic (Time-based decay on history).
        -   Implement duplicate avoidance using the `Suggestions` table.
    4.  **Trope-RAG Justification Engine**:
        -   Implement retrieval logic to pull trope descriptions for the top N matches.
        -   Build "Justification Prompts" that anchor the LLM's reasoning in retrieved trope facts.
    5.  **A2A Agent Mesh**:
        -   Coordinate Search, Filter, Rank agents via A2A messaging.
*   **Packages**: `langchain`, `fastmcp`, `a2a-sdk` (or equivalent LF implementation), `google-genai`.
*   **Files/Classes**:
    *   `src/agentic_librarian/mcp/server.py`: [NEW] FastMCP server defining tools like `get_similar_tropes`, `check_read_status`.
    *   `src/agentic_librarian/agents/a2a_mesh.py`: [NEW] Agent discovery and communication logic following A2A spec.
    *   `src/agentic_librarian/agents/specialized/`: Individual agent logic for search, filter, and rank.

## Phase 4: Web Interface & Analysis
**Goal**: Visualize the reading history and interact with the Librarian.
*   **Order of Work**:
    1.  Initialize Vite/React frontend.
    2.  Build "Trope Cloud" and "Author Style" radar charts.
    3.  Implement the Chat UI for recommendation requests.
*   **Packages**: `vite`, `react`, `recharts`, `fastapi` (for the backend API).
*   **Files/Classes**:
    *   `src/agentic_librarian/api/main.py`: [NEW] FastAPI wrapper for the Recommendation Agent.
    *   `src/agentic_librarian/ui/src/components/`: Reaction components for `ChatWindow`, `HistoryDashboard`.

## Phase 5: MLOps & Verification
**Goal**: Monitor performance and data health.
*   **Order of Work**:
    1.  Log prompt versions and completion rates to MLFlow.
    2.  Implement a drift detection script for the `Tropes` table.
*   **Files/Classes**:
    *   `src/agentic_librarian/monitoring/drift_detector.py`: [NEW] Script to analyze trope distribution over time.
    *   `test/system/test_full_flow.py`: Integration test from CSV update to Recommendation result.
