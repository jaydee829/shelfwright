# MCP Tools: Agentic Librarian

The following tools are exposed by the MCP server to allow agents (Search, Filter, Rank) to interact with the database using a **Coarse-Grained** strategy that encapsulates complex logic and transactions.

## 1. Recommendation & Search (Coarse-Grained)
*   **`find_recommendations(target_tropes: list[str], limit: int = 5)`**:
    - **Logic**: Performs a `pgvector` similarity search across `Works` using the provided tropes AND automatically filters out books found in `ReadingHistory` (unless they meet re-read criteria).
    - *Purpose*: Encapsulates the entire search + deduplication + filtering transaction into one call.
*   **`analyze_external_work(title: str, author: str)`**:
    - **Logic**: Triggers the `ScoutManager` and `TropeManager` to generate a temporary vector/profile for a book found via external search (Pathway B). Returns a "Compatibility Score" against user preferences without necessarily saving to the DB.
    - *Purpose*: Allows agents to evaluate books they find "in the wild" against the user's history.

## 2. Information Retrieval & Facts
*   **`get_user_trope_preferences(min_rating: int = 4, limit: int = 20)`**:
    - **Logic**: Aggregates the most frequent and highly-rated tropes from the user's library.
    - *Purpose*: Provides the "evidence" for personalized justification.
*   **`get_work_details(work_id: UUID)`**:
    - Returns full metadata and linked `Tropes` (names and descriptions).
    - *Purpose*: Essential for **Grounded RAG**; provides facts for the justification engine.
*   **`get_author_profile(name: str)`**:
    - Returns `style_attributes` (JSONB) and bio.

## 3. History & State Management
*   **`get_reading_history_status(title: str, author: str)`**:
    - Checks if a specific work/edition has been completed and returns the most recent completion date.
*   **`log_suggestion(work_id: UUID, context: text, justification: text, conversation_id: UUID)`**:
    - Atomic transaction to save a recommendation to the `Suggestions` table.
*   **`search_previous_suggestions(query_embedding: vector, limit: int)`**:
    - Semantic search over past recommendations to avoid repetition.

## 4. Enrichment (Internal/System)
*Note: Flow 1 (Ingest) uses direct ORM access for performance. These tools are provided for Agent-driven enrichment.*
*   **`upsert_trope(name: str, description: str)`**:
    - Standardizes and embeds a new trope via the `TropeManager`.
