# Database Schema: Agentic Librarian

## Core Entities
*   **`Authors`**:
    *   `id` (UUID, PK)
    *   `name` (Text)
    *   `bio` (Text)
    *   `style_attributes` (JSONB): Pacing, tone, consistency, etc.
*   **`Works`**:
    *   `id` (UUID, PK)
    *   `title` (Text)
    *   `original_publication_year` (Int)
    *   `description` (Text)
    *   `genres` (Text[]): Aggregated from various sources.
    *   `moods` (Text[]): e.g., "Dark", "Epic", "Humorous".
*   **`Narrators`**:
    *   `id` (UUID, PK)
    *   `name` (Text)
    *   `style_attributes` (JSONB): Pacing, voice differentiation, accent, consistency, emotional range, gender range, production quality.
*   **`Tropes`**:
    *   `id` (UUID, PK)
    *   `name` (Text, Unique)
    *   `description` (Text)
    *   `embedding` (Vector): 1536 dims.

## Relationships & Editions
*   **`WorkContributors`**: Junction for authorship.
    *   `work_id` (FK -> Works)
    *   `author_id` (FK -> Authors)
    *   `role` (Text): e.g., "Primary", "Editor", "Contributor".
*   **`Editions`**: Specific book versions.
    *   `id` (UUID, PK)
    *   `work_id` (FK -> Works)
    *   `isbn_13` (Text, Nullable)
    *   `format` (Text): e.g., "Hardcover", "E-book", "Audiobook".
    *   `page_count` (Int, Nullable)
    *   `audio_minutes` (Int, Nullable)
    *   `publication_date` (Date)
*   **`EditionNarrators`**: Simple junction for multiple narrators.
    *   `edition_id` (FK -> Editions)
    *   `narrator_id` (FK -> Narrators)
*   **`WorkTropes`**:
    *   `work_id` (FK -> Works)
    *   `trope_id` (FK -> Tropes)
    *   `relevance_score` (Float): How prominent is this trope? (0.0 - 1.0)

## User Activity
*   **`ReadingHistory`**:
    *   `id` (UUID, PK)
    *   `edition_id` (FK -> Editions)
    *   `date_started` (Date, Nullable)
    *   `date_completed` (Date)
    *   `user_rating` (Int, Nullable)
    *   `user_notes` (Text, Nullable)

*   **`Suggestions`**:
    *   `id` (UUID, PK)
    *   `work_id` (FK -> Works)
    *   `suggested_at` (Timestamp)
    *   `context` (Text): The user request or prompt that triggered this suggestion.
    *   `justification` (Text): The agent's reasoning for this specific recommendation.
    *   `status` (Text): e.g., "Ignored", "Wishlisted", "Purchased", "Read".
    *   `conversation_id` (UUID): To group suggestions from the same session.
