from agentic_librarian.mcp.server import (
    check_reading_history,
    get_unacted_suggestions,
    get_user_trope_preferences,
    get_work_details,
    log_suggestion,
    search_internal_database,
    update_reading_status,
    update_suggestion_status,
)
from google.adk.agents import LlmAgent
from google.adk.tools import AgentTool, FunctionTool

# --- SPECIALIST AGENTS ---


class AnalystAgent(LlmAgent):
    """The Strategist. Decomposes vibes into structured tropes/styles and manages User Profile."""

    def __init__(self):
        super().__init__(
            name="Analyst",
            description="Specializes in extracting structured book attributes and analyzing user taste.",
            instruction="""
            You are a literary analyst. Your job is to extract semantic concepts from user requests.
            1. Identify 'Target Vibes' (Tropes/Styles the user wants).
            2. Identify 'Session Constraints' (Moods/Tropes the user wants to avoid *just for now*, e.g., "Nothing too violent today").
            3. Identify 'Permanent Negative Signals' (Things the user explicitly says they always hate).

            Use the 'get_user_trope_preferences' tool to understand the user's historical taste.
            """,
            tools=[FunctionTool(get_user_trope_preferences)],
        )


class ExplorerAgent(LlmAgent):
    """The Scout. Web-based discovery using search grounding."""

    def __init__(self):
        super().__init__(
            name="Explorer",
            description="Discovers new books from the internet using search grounding.",
            instruction="""
            You are a book scout. Use your internal search grounding capabilities to find real books.
            If a book is found, return its title, author, and a brief description.
            Focus on discovery of titles NOT likely to be in a standard personal library.
            """,
            # Search grounding is an internal capability of the LlmAgent if configured,
            # or we can add a specific search tool if the ADK requires it.
        )


class CriticAgent(LlmAgent):
    """The Matchmaker. Nuanced ranking and history validation."""

    def __init__(self):
        super().__init__(
            name="Critic",
            description="Ranks book candidates using vector similarity and ensures no duplicates in history.",
            instruction="""
            You are a book critic. You receive a list of candidate books and target vibes (tropes/styles).
            1. Use 'search_internal_database' with both target tropes and target styles.
            2. Use 'get_work_details' to see deep metadata for candidates.
            3. Use 'check_reading_history' to check re-read eligibility (>2 years).
            4. Rank candidates by similarity to Target Vibes.
            5. APPLY PENALTY: If a candidate matches a 'Session Constraint', lower its rank.

            6. JUSTIFY (Trope-RAG): For each recommended book, provide a grounded justification.
               - Anchor your reasoning in the 'name' and 'description' of the top-matching tropes.
               - Include the 'justification' (evidence) from the database to explain how the trope manifests in that specific book.
               - Format: "I recommend [Title] because it features [Trope Name] ([Description]). Specifically, [Justification Evidence]."
            """,
            tools=[
                FunctionTool(search_internal_database),
                FunctionTool(get_work_details),
                FunctionTool(check_reading_history),
            ],
        )


# --- THE ORCHESTRATOR ---


class LibrarianAgent(LlmAgent):
    """The Orchestrator. Manages delegation and conversational feedback."""

    def __init__(self, analyst, explorer, critic):
        super().__init__(
            name="Librarian",
            description="The entry point for users. Orchestrates the recommendation process.",
            instruction="""
            You are the Head Librarian. You provide personalized book recommendations and manage history.

            DELEGATION STRATEGY:
            1. Call the 'Analyst' to turn user vibes into structured targets and session constraints.
            2. Call 'get_unacted_suggestions' with target vibes to see if we have good matches.
            3. If new discovery is needed, call the 'Explorer'.
            4. Pass all candidates, targets, and session constraints to the 'Critic' for final ranking.
               - NOTE: Books read >2 years ago are eligible for re-read suggestions.

            FEEDBACK HANDLING:
            - If user says "I read that", use 'update_reading_status' AND 'update_suggestion_status(Already Read)'.
            - If user says "Not for me" or "I hate this", use 'update_suggestion_status(Dismissed)'.
            - If user provides mood feedback ("Not in the mood for X"), pass it to the Analyst/Critic.

            Always log the final result using 'log_suggestion'.
            """,
            tools=[
                AgentTool(analyst),
                AgentTool(explorer),
                AgentTool(critic),
                FunctionTool(get_unacted_suggestions),
                FunctionTool(update_reading_status),
                FunctionTool(update_suggestion_status),
                FunctionTool(log_suggestion),
            ],
        )


# --- THE MESH FACTORY ---


def create_agent_mesh():
    """Initializes and connects the agents using the AgentTool delegation pattern."""
    analyst = AnalystAgent()
    explorer = ExplorerAgent()
    critic = CriticAgent()

    # The Librarian is initialized with its staff of specialists
    librarian = LibrarianAgent(analyst=analyst, explorer=explorer, critic=critic)

    return {"librarian": librarian, "analyst": analyst, "explorer": explorer, "critic": critic}
