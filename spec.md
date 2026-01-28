# Project Specification: Agentic Librarian

## Overview
A system to provide personalized book recommendations by leveraging a user's reading history, enriched with deep metadata (themes, tropes, styles). The project serves as a testbed for agentic workflows and MLOps practices.

## Goals
- **User Goal**: Better book recommendations based on reading history.
- **Developer Goal**: Experiment with agentic workflows and MLOps using cutting-edge tools.

## Project Structure
- `src/` – Application source code
- `tests/` – Unit and integration tests
- `docs/` – Documentation
- `data/` - Raw and processed datasets

## Architecture

### Communication & Protocol Layers
- **Data Access (MCP)**: The database and internal utilities are exposed via the **Model Context Protocol (MCP)**. This allows any MCP-compliant agent to query the librarian's data using standardized tools.
- **Agent Collaboration (A2A)**: Multi-agent coordination (Search, Filter, Rank) follows the **A2A Protocol** (Linux Foundation standard). This ensures interoperability, secure discovery, and structured task delegation between the specialized librarian agents.

### Data Storage Strategy
- **Database**: PostgreSQL with `pgvector` extension.
- **Relational Structure**: See [schema.md](file:///c:/Users/Justin.Merrick/Python_Code/Projects/agentic_librarian/agentic_librarian/schema.md) for full table details.
- **Relational Overview**:
    - `Authors`: Name, Bio, Style attributes (pacing, tone, etc.).
    - `Works`: Title, Original Publication Date, Author links, Genres, Moods, Tropes (Vectorized).
    - `Editions`: ISBN, Format, Page Count, Audio Length, Work link.
    - `Narrators`: Name, Style attributes (pacing, voices, accent, pitch, tone).
    - `ReadingHistory`: Link to Edition, User rating/notes, Date completed.
- **Tropes**: Stored as both tags and vector embeddings to capture semantic relationships.

### Flow 1: Reading History Processing
1. **Ingest**: CSV data from `data/raw`.
2. **Scout**: 
    - Basic metadata from Google Books and Hardcover.
    - Audiobook/Narrator metadata via LLM-assisted scraping of Audible.
3. **Enrich**: 
    - Synthesis of LLM knowledge and internet search for deep metadata.
    - Tropes managed by a "Trope Manager" component that balances specificity vs. generalizability.
4. **versioning**: DVC for dataset versioning, orchestrated by Dagster.

### Flow 2: Book Recommendation Agent
1. **Request**: UI-based conversational input.
2. **Search**: Dual-mode exploration:
    - *Mode A (Internal tool)*: `google-genai` with search capability.
    - *Mode B (External A2A)*: A separate search service discovered via A2A.
3. **Filter**: 
    - Exclude `ReadingHistory` unless the book matches perfectly and was read >2 years ago (Re-read logic).
    - Checks against the `Suggestions` table to avoid repeating recent recommendations.
4. **Rank**: Use vector similarity of tropes and author/narrator style matches.
5. **Justify (Trope-RAG)**: 
    - Perform **Retrieval-Augmented Generation (RAG)** by pulling the names and descriptions of the top-matching tropes.
    - Feed this retrieved context into the LLM to provide grounded, evidence-based recommendations (e.g., "I'm recommending this because it features the 'Enemies to Lovers' trope, which you've rated 5 stars in 3 other books").
    - Log the result to `Suggestions`.

### MLOps & Infrastructure
- **Orchestration**: Dagster for data pipelines and monitoring updates.
- **Experiment Tracking**: MLFlow for prompt iteration, model selection, and trope clustering results.
- **Monitoring**: Drift detection for trope distributions or recommendation performance over time.
- **UI**: Extensible Web UI (Vite/Next.js) for recommendations and history analysis.

## Special Handling & Edge Cases
- **Multi-volume/Anthologies**: 
    - Multi-volume books by one author = 1 Work.
    - Short story collections by one author = 1 Work.
- **Multi-author collections**:
    - **Contributor Mapping (Robust)**: Use a `WorkContributors` junction table between `Works` and `Authors`. This allows assigning multiple authors to a single work (like an anthology) and even specifying roles (Author, Editor, Introduction).
- **Narrator Attributes (Definitions)**:
    - **Pacing**: The speed and rhythm of delivery. Does it feel rushed or sluggish?
    - **Voice Differentiation**: Ability to create distinct, recognizable voices for different characters.
    - **Accent & Dialect**: Accuracy and consistency of regional or fictional accents.
    - **Pitch & Tone**: The musicality of the voice—is it deep and gravelly, or high and melodic?
    - **Consistency**: Maintaining character voices and tone across long durations or entire series (e.g., Book 1 sounds like Book 10).
    - **Emotional Range**: The ability to convey complex emotions (grief, sarcasm, joy) beyond just reading the words.
    - **Gender Range**: The believability of performing characters of a different gender than the narrator.
    - **Production Quality**: Clarity of recording, lack of distracting mouth noises, and balanced volume levels.

## Boundaries
- ✅ Always: Run tests before commits, follow naming conventions
- ⚠️ Ask first: Database schema changes, adding dependencies
- 🚫 Never: Commit secrets, edit node_modules/, modify CI config
