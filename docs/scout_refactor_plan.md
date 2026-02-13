# Refactor Plan: Abstract Scout Architecture

## 1. Objective
Transform the current book metadata enrichment system from a collection of loose functions and a monolithic facade (`MultiSourceScout`) into a modular, hierarchical, and extensible architecture based on the **Strategy Pattern**.

## 2. Proposed Architecture

### Hierarchy
- **`BaseScout` (ABC)**: Defines the core contract (`search(title, author)`) and shared utilities (credential validation, standard error logging).
- **`APIScout` (Abstract)**: Inherits from `BaseScout`. Optimized for structured data. Handles HTTP status codes, rate limiting, and response mapping.
    - *Implementations*: `GoogleBooksScout`, `HardcoverScout`.
- **`LLMScout` (Abstract)**: Inherits from `BaseScout`. Optimized for unstructured/semantic data. Handles prompt templates, JSON repair, and LLM retry logic.
    - *Implementations*: `AudiobookScout`, `DirectKnowledgeScout`.
- **`ScoutManager`**: The "Brain". Orchestrates multiple registered scouts, manages merging priorities, and handles fallback logic.

## 3. Implementation Steps

### Phase 1: Core Abstractions
1. Define `BaseScout` in `metadata_scout.py`.
2. Implement `APIScout` with shared `requests` logic.
3. Migrate existing `BaseLLMScout` logic into the new hierarchy.

### Phase 2: Implementation Migration
1. Refactor `fetch_google_books_metadata` into `GoogleBooksScout(APIScout)`.
2. Refactor `fetch_hardcover_metadata` into `HardcoverScout(APIScout)`.
3. Update `AudiobookScout` and `DirectKnowledgeScout` to inherit from `LLMScout`.

### Phase 3: The Manager
1. Implement `ScoutManager`.
2. Support registration of scouts: `manager.register_scout(scout_instance, priority=1)`.
3. Implement `manager.enrich(title, author, format)` which iterates through scouts and performs a "Deep Merge" of the resulting dictionaries.

### Phase 4: Orchestration (Dagster)
1. **Dependency Injection**: Refactor the `enriched_metadata` asset to accept a `ScoutManager` as a Dagster Resource.
2. **Configuration**: Update `definitions.py` to initialize the `ScoutManager` with environment-specific scouts (e.g., use a `MockScout` for certain CI environments).

## 4. Impact Analysis

### Codebase Impacts
- **`src/agentic_librarian/scouts/metadata_scout.py`**: Significant cleanup. Loose functions will be removed. The file will become a structured library of scout classes.
- **`src/agentic_librarian/orchestration/assets.py`**: The `enriched_metadata` asset will become much cleaner. It will no longer instantiate scouts directly but will use the provided resource.
- **`src/agentic_librarian/orchestration/definitions.py`**: Will become the centralized "Registry" for which scouts are active in the current deployment.

### Testing Impacts
- **Unit Tests**: All existing tests in `test/test_metadata_scout.py` will need to be refactored to mock class methods (`GoogleBooksScout.search`) rather than module functions.
- **Mocking**: It will be easier to write "Fake" scouts for integration tests, improving test stability.

## 5. Project Memory Updates

### `decisions.md` (ADR-017)
Document the transition to the Abstract Scout Architecture.
- **Context**: Need for extensibility as we add more data sources (e.g., OpenLibrary, StoryGraph).
- **Decision**: Use a hierarchy of specialized classes managed by a central registry.
- **Requirement**: No module-level API functions allowed; all new sources must implement `BaseScout`.

### `key_facts.md`
Update the "Technology Stack" or "Architecture" section to include the **Scout Registry Pattern** as a core design principle.

### `GEMINI.md`
Add a protocol: **"Adding Data Sources"**.
- "When adding a new metadata source, create a class inheriting from either `APIScout` or `LLMScout`. Register it in the `ScoutManager` resource within `definitions.py`."

## 6. Success Criteria
1. `metadata_scout.py` contains 0 module-level functions for data fetching.
2. `ScoutManager` correctly merges data from at least one `APIScout` and one `LLMScout`.
3. Dagster can launch the `enrich_job` using a `ScoutManager` provided via Resources.
4. Test suite coverage remains >= 80% for the enrichment logic.
