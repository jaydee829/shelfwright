import os

from agentic_librarian.agents import prompts
from agentic_librarian.agents.schemas import Targets
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
from google.adk.tools.google_search_tool import GoogleSearchTool


def _model_name() -> str:
    """Generative model for the mesh agents (configurable; matches the scouts)."""
    return os.environ.get("GEMINI_MODEL", "gemini-2.5-flash-lite")


def _explorer_model() -> str:
    """The Explorer does grounded web discovery, which benefits from a stronger model
    than the flash-lite default used by the other agents."""
    return os.environ.get("EXPLORER_MODEL", "gemini-2.5-flash")


# --- SPECIALIST AGENTS ---


class AnalystAgent(LlmAgent):
    """The Strategist. Decomposes vibes into structured tropes/styles and manages User Profile."""

    def __init__(self):
        super().__init__(
            model=_model_name(),
            name="Analyst",
            description="Specializes in extracting structured book attributes and analyzing user taste.",
            instruction=prompts.ANALYST_INSTRUCTION,
            tools=[FunctionTool(get_user_trope_preferences)],
            output_schema=Targets,
            output_key="targets",
        )


class ExplorerAgent(LlmAgent):
    """The Scout. External web discovery via grounded search (ADR-035)."""

    def __init__(self):
        super().__init__(
            model=_explorer_model(),
            name="Explorer",
            description="Discovers new/recent books from the web using grounded search.",
            instruction=prompts.EXPLORER_INSTRUCTION,
            # NOTE: no output_schema here. google_search is a built-in tool, and Gemini rejects
            # combining a built-in tool with function-calling (which is how output_schema is
            # enforced) in one request. The Explorer therefore emits JSON-as-text; the pipeline's
            # Enrichment step parses it (coerce_schema_value -> json.loads). See ADR-040.
            tools=[GoogleSearchTool(bypass_multi_tools_limit=True)],
            output_key="discoveries",
        )


class CriticAgent(LlmAgent):
    """The Matchmaker. Nuanced ranking and history validation.

    output_key: when set (the recommendation pipeline passes "recommendation"), the Critic's final
    response is written to session state under that key so the pipeline can read it. The
    conversational mesh constructs it without an output_key (it reads the AgentTool return value)."""

    def __init__(self, output_key: str | None = None):
        super().__init__(
            model=_model_name(),
            name="Critic",
            description="Ranks book candidates using vector similarity and ensures no duplicates in history.",
            instruction=prompts.CRITIC_INSTRUCTION,
            output_key=output_key,
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
            model=_model_name(),
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
