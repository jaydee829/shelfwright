# MCP Tools: Agentic Librarian

The following tools are exposed by the MCP server to allow the Agent Mesh (Librarian, Analyst, Explorer, Critic) to interact with the database using coarse-grained operations.

## 1. Discovery & Search
*   **`search_internal_database(target_tropes: list[str], limit: int = 10)`**:
    - **Logic**: Performs a `pgvector` similarity search across the `Works` table.
    - *Purpose*: High-speed retrieval of books already in our deep metadata cache.
*   **`get_unacted_suggestions(target_tropes: list[str], limit: int = 5)`**:
    - **Logic**: Queries the `Suggestions` table for books that match the current vibe but have a status of "Suggested" (not yet read or ignored).
    - *Purpose*: Prioritizes "Persistence" in recommendations—remembers what was suggested before.

## 2. History & Validation
*   **`check_reading_history(title: str, author: str)`**:
    - **Logic**: Checks the `ReadingHistory` table for exact matches.
    - *Purpose*: The definitive "Guardrail" tool to prevent suggesting books the user already knows.
*   **`get_intelligent_history_check(work_id: UUID)`**:
    - **Logic**: Returns completion date, user rating, and calculated "read recency" (e.g., "Read 3.5 years ago").
    - *Purpose*: Provides raw data for the "Re-read Decay" reasoning.

## 3. Feedback & State Management
*   **`update_reading_status(title: str, author: str, status: str, notes: str = None)`**:
    - **Logic**: Upserts a record into `ReadingHistory` (if status is "Read") or `Suggestions` (if status is "Avoid").
    - *Purpose*: Implements "Conversational Correction"—allows the user to update their history in real-time (e.g., "I read that years ago").
*   **`log_suggestion(work_id: UUID, context: text, justification: text, conversation_id: UUID)`**:
    - **Logic**: Atomic transaction to save a new recommendation.
    - *Purpose*: Persistent memory of current agent reasoning.

## 4. Profile Retrieval
*   **`get_user_trope_preferences(limit: int = 20)`**:
    - **Logic**: Aggregates trope frequency and weights by user ratings.
    - *Purpose*: Provides the "Evidence" for the Analyst and Critic agents.
*   **`get_work_details(work_id: UUID)`**:
    - Returns full metadata and linked Tropes for Grounded RAG.
