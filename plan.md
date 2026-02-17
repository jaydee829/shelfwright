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
