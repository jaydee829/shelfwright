# Key Project Facts

This file tracks important project configuration, constants, and environment details.

## Project Overview
- **Project Name**: agentic_librarian
- **Description**: An agentic system for processing reading history and providing personalized book recommendations.

## Local Development
- **OS**: Windows
- **Conda Environment**: `agentic_librarian`
- **Execution Rule**: Always run commands (pytest, ruff, dagster, etc.) prefixed with `conda run -n agentic_librarian` to ensure the correct dependencies are used.

## Technology Stack
- **Database**: PostgreSQL with `pgvector` (1536 dims for tropes)
- **MLOps**: DVC (Data Versioning), MLFlow (Experiment Tracking), Dagster (Orchestration)
  - **Dagster Assets**:
    - `raw_history`: CSV loading and cleaning via `HistoryIngestor`.
    - `enriched_metadata`: Metadata enrichment via `ScoutManager`.
    - `vectorized_tropes`: Trope standardization and vectorization via `TropeManager`.
  - **Dagster Resources**:
    - `db_manager`: Provides SQLAlchemy sessions to assets.
  - **Dagster Partitions**:
    - `csv_files`: Dynamic partitions based on raw CSV filenames.
- **Interface**: Web UI (Vite/React/Next.js), FastAPI (Backend)
- **AI/LLM**: `google-genai` (Gemini), Google AI Agent SDK, LangChain
- **Protocols**: MCP (Data Access), A2A (Agent Mesh)
  - **Data Access Strategy**: Hybrid (Direct ORM for Flow 1 Ingest; Coarse-Grained MCP for Flow 2 Agents).
  - **Agent Mesh**: 4-Agent Specialist Model (Librarian, Analyst, Explorer, Critic).
- **Testing**: Pytest (Unit, Integration), Playwright (E2E)
- **Containerization**: Docker, Docker Compose
  - **Ports**:
    - Postgres: `5432` (Bound to `127.0.0.1`)
    - MLFlow: `5000` (Bound to `127.0.0.1`)
- **Configuration**:
  - `python-dotenv` loads `.env` files.
  - `DB_SSL_MODE`: Support for `sslmode` in SQLAlchemy (e.g., `require`).

## Core Entities
- **Authors**: Bio, JSONB style attributes (pacing, tone, style).
- **Works**: Metadata, Genres, Moods, Tropes (Vectorized).
- **Editions**: ISBN, Format, Page/Audio length.
- **Narrators**: JSONB style attributes (voice diff, accent, etc.).
- **ReadingHistory**: User ratings, notes, completion dates.
- **Suggestions**: Log of agent recommendations with justifications.

## Data Ingestion Assumptions
- **Chronological Density**: The system assumes that raw CSV rows are chronologically clustered.
- **Year Inference**: Dates missing a year (e.g., `4-Jan`) are contextually inferred from the nearest unambiguous date (e.g., `1/7/2020`) using forward and backward fills.
- **Reference Date**: If no contextual year is found, the system defaults to the current year.

## Security Guidelines
- **DO NOT** store real passwords or secrets here.
- **DO NOT** store PII.
- Use environment variables or secret managers for sensitive info.
