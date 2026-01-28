# Key Project Facts

This file tracks important project configuration, constants, and environment details.

## Project Overview
- **Project Name**: agentic_librarian
- **Description**: An agentic system for processing reading history and providing personalized book recommendations.

## Local Development
- **OS**: Windows

## Technology Stack
- **Database**: PostgreSQL with `pgvector` (1536 dims for tropes)
- **MLOps**: DVC (Data Versioning), MLFlow (Experiment Tracking), Dagster (Orchestration)
- **Interface**: Web UI (Vite/React/Next.js), FastAPI (Backend)
- **AI/LLM**: `google-genai` (Gemini), LangChain, FastMCP
- **Protocols**: MCP (Data Access), A2A (Agent Collaboration)
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

## Security Guidelines
- **DO NOT** store real passwords or secrets here.
- **DO NOT** store PII.
- Use environment variables or secret managers for sensitive info.
