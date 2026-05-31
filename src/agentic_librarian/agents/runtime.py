"""Runtime for the recommendation agent mesh: host the Librarian in an ADK Runner
and expose a multi-turn conversation API (ADR-035 Spec 1, ADR-036)."""

import os

from agentic_librarian.agents.services import create_agent_mesh
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

APP_NAME = "agentic_librarian"


def _ensure_adk_credentials() -> None:
    """ADK's Gemini model authenticates via GOOGLE_API_KEY. Populate it from the
    project's existing keys if it isn't set (GOOGLE_SEARCH_API_KEY has access to both
    Custom Search and the Gemini API in this GCP project)."""
    if not os.environ.get("GOOGLE_API_KEY"):
        key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_SEARCH_API_KEY")
        if key:
            os.environ["GOOGLE_API_KEY"] = key


def build_runner() -> Runner:
    """Build a Runner hosting the Librarian (root of the agent mesh)."""
    _ensure_adk_credentials()
    mesh = create_agent_mesh()
    return Runner(
        agent=mesh["librarian"],
        app_name=APP_NAME,
        session_service=InMemorySessionService(),
    )
