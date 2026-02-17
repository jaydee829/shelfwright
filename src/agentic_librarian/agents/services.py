from agentic_librarian.mcp.server import (
    check_reading_history,
    get_unacted_suggestions,
    get_user_trope_preferences,
    get_work_details,
    log_suggestion,
    search_internal_database,
    update_reading_status,
)
from google.adk.agents import LlmAgent
from google.adk.tools import AgentTool, FunctionTool

# --- SPECIALIST AGENTS ---


class AnalystAgent(LlmAgent):
    """The Strategist. Decomposes vibes into structured tropes and manages User Profile."""

    def __init__(self):
        super().__init__(
            name="Analyst",
            description="Specializes in extracting structured book attributes and analyzing user taste.",
            instruction="""
            You are a literary analyst. Your job is to extract semantic concepts (tropes, genres, moods) from user requests.
            Use the 'get_user_trope_preferences' tool to understand the user's historical taste.
            Identify 'Negative Signals' (things the user wants to avoid) based on feedback.
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
            You are a book critic. You receive a list of candidate books.
            1. Use 'search_internal_database' to find similar existing books.
            2. Use 'get_work_details' to see deep metadata for candidates.
            3. Use 'check_reading_history' to ensure you don't recommend something already read.
            4. Provide a ranked list with brief justifications based on trope matches.
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
            1. Call the 'Analyst' to turn user vibes into structured tropes.
            2. Call 'get_unacted_suggestions' to see if we already have good unread matches.
            3. If new discovery is needed, call the 'Explorer'.
            4. Pass all candidates to the 'Critic' for final ranking and history checks.

            FEEDBACK HANDLING:
            - If user says "I read that", use 'update_reading_status'.
            - If user provides social feedback (friend's opinion), pass it to the Critic.

            Always log the final result using 'log_suggestion'.
            """,
            tools=[
                AgentTool(analyst),
                AgentTool(explorer),
                AgentTool(critic),
                FunctionTool(get_unacted_suggestions),
                FunctionTool(update_reading_status),
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
