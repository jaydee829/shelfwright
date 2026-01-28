# MCP Tools: Agentic Librarian

The following tools are exposed by the MCP server to allow agents (Search, Filter, Rank) to interact with the database.

## 1. Information Retrieval
*   **`get_similar_tropes(embedding: vector, limit: int)`**: 
    - Queries the `Tropes` table using cosine similarity.
    - *Purpose*: Map a user's prompt concepts to canonical tropes.
*   **`search_works(genres: list[str], moods: list[str], tropes: list[str])`**:
    - Queries the `Works` table for candidates matching high-level attributes.
*   **`get_reading_history_status(title: str, author: str)`**:
    - Checks `ReadingHistory` to see if a specific work/edition has been completed.
    - *Purpose*: Used by the `FilterAgent`.
*   **`get_work_details(work_id: UUID)`**:
    - Returns full metadata, including aggregated genres, moods, and descriptions.
*   **`get_work_tropes(work_id: UUID)`**:
    - Returns a list of Tropes linked to the work, including names, descriptions, and relevance scores.
    - *Purpose*: Essential for **Grounded RAG**; provides the specific "facts" for the justification engine.

## 2. Profile Fetching
*   **`get_author_profile(name: str)`**:
    - Returns `style_attributes` (JSONB) and bio.
*   **`get_narrator_profile(name: str)`**:
    - Returns performance metrics (pacing, range, quality).

## 3. Enrichment Tools (Internal)
*   **`upsert_trope(name: str, description: str, embedding: vector)`**:
    - Used by the `TropeManager` to update the canonical list.
*   **`add_reading_entry(edition_id: UUID, date_completed: date, rating: int)`**:
    - Updates the user's history from the INTAKE flow.

## 4. Recommendation & Memory
*   **`get_previous_suggestions(limit: int)`**:
    - Returns the most recent suggestions made to the user.
*   **`search_previous_suggestions(query_embedding: vector, limit: int)`**:
    - Performs a semantic search over the `context` and `justification` of previous suggestions.
    - *Purpose*: Helps the FilterAgent identify if a similar *vibe* or *reason* has been suggested recently.
*   **`get_intelligent_history_check(work_id: UUID)`**:
    - Returns completion date, user rating, and calculated "read recency".
    - *Purpose*: Supports the "Re-read" logic (deciding if an old book is worth suggesting again).
*   **`log_suggestion(work_id: UUID, context: text, justification: text, conversation_id: UUID)`**:
    - Writes a new recommendation to the `Suggestions` table for long-term memory.
*   **`get_user_trope_preferences(min_rating: int = 4, limit: int = 20)`**:
    - Returns the most significant tropes in the user's history.
    - **Logic**: Combines highly-rated books *and* trope frequency (prevalence). If ratings are missing, frequency serves as the primary proxy for preference (assuming more reads = higher affinity).
    - *Purpose*: Provides the "evidence" for personalized justification (e.g., "This book features X, a trope present in 30% of your library").
