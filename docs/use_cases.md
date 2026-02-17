# Test Use Cases

This document defines standardized use cases to guide the development and testing of the Agentic Librarian. These use cases are categorized by complexity and should be used as the basis for Test-Driven Development (TDD).

## Level 1: Basic Metadata & Search
*Focus: Precision and correctness of core data retrieval.*

- **UC1.1: Author Lookup**
    - **Prompt:** "Find all works by 'Ursula K. Le Guin'."
    - **Expected Outcome:** A list containing 'A Wizard of Earthsea', 'The Left Hand of Darkness', etc.
- **UC1.2: Edition Details**
    - **Prompt:** "Retrieve the ISBN and page count for the hardcover edition of 'Project Hail Mary'."
    - **Expected Outcome:** ISBN-13: 978-0593135204, Page Count: ~448.

## Level 2: Semantic Discovery
*Focus: Vector search and trope-based filtering.*

- **UC2.1: Trope-Driven Discovery**
    - **Prompt:** "Recommend an epic fantasy book with a 'magic school' trope but set in a non-Western world."
    - **Expected Outcome:** Books like 'The Poppy War' or 'A Master of Djinn'.
- **UC2.2: Mood-Based Search**
    - **Prompt:** "I want a sci-fi book that feels 'melancholic' and 'atmospheric'."
    - **Expected Outcome:** Books like 'Never Let Me Go' or 'Solaris'.

## Level 3: Complex Constraints
*Focus: Multi-agent coordination and negative constraints.*

- **UC3.1: Comparative Exclusion (User Example)**
    - **Prompt:** "I'd like the first book in a series that is like 'The Way of Kings', but isn't by Brandon Sanderson, and is a bit more gritty."
    - **Expected Outcome:** Books like 'The Blade Itself' or 'Gardens of the Moon'.
- **UC3.2: Highly Filtered Search**
    - **Prompt:** "Find a standalone historical fiction novel about the Silk Road, published in the last 5 years, with a female protagonist."
    - **Expected Outcome:** Specific matches meeting all 4 criteria.

## Level 4: Personalized Recommendations
*Focus: Integration with Reading History and re-read logic.*

- **UC4.1: Re-read vs. Discovery**
    - **Prompt:** "I read 'Dune' 5 years ago and loved it. Is it a good time for a re-read, or is there something similar I haven't read yet?"
    - **Expected Outcome:** System provides logic-based decision (e.g., "It's been >2 years, re-read is viable") AND suggests a similar unread title like 'Hyperion'.
- **UC4.2: Vectorized Preferences**
    - **Prompt:** "Based on my 5-star rating of 'Neuromancer', what other 'cyberpunk' books with 'noir' elements would I like?"
    - **Expected Outcome:** Recommendations high in vector similarity for 'cyberpunk' and 'noir' tropes.

## Level 5: Style & Narrative Attributes
*Focus: Narrator performance and authorial style.*

- **UC5.1: Narrator Performance**
    - **Prompt:** "Suggest an audiobook where the narrator has an excellent 'emotional range' and 'voice differentiation'."
    - **Expected Outcome:** Recommendations like Steven Pacey (First Law) or Jefferson Mays (The Expanse).
- **UC5.2: Authorial Style Match**
    - **Prompt:** "I love the 'dry wit' and 'fast pacing' of Andy Weir. What other authors share that style?"
    - **Expected Outcome:** Authors like Martha Wells or John Scalzi.

## Level 6: Feedback & Evolution
*Focus: Long-term memory, corrections, and social signals.*

- **UC6.1: Historical Correction**
    - **Scenario:** User is suggested 'The Martian' and replies "I've already read that, but it was before I started this list."
    - **Expected Outcome:** System calls `update_reading_status` to add the book to history and removes it from the current suggestion set.
- **UC6.2: Social Signal Processing**
    - **Prompt:** "My friend who likes the same books as me said 'Red Rising' was way too violent for their taste right now."
    - **Expected Outcome:** Analyst extracts 'Violence' as a negative weight; Critic lowers the rank of 'Red Rising' and similar high-violence titles for the current session.
- **UC6.3: Suggestion Persistence**
    - **Scenario:** User asks for a heist book. System previously suggested 'Six of Crows' (unread).
    - **Expected Outcome:** System identifies 'Six of Crows' as a high-match unacted suggestion and prioritizes it in the new response.
