"""Runtime for the recommendation agent mesh: host the Librarian in an ADK Runner
and expose a multi-turn conversation API (ADR-035 Spec 1, ADR-036)."""

import asyncio
import os
import uuid

from agentic_librarian.agents.backends import get_backend
from agentic_librarian.agents.services import create_agent_mesh
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

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


class LibrarianConversation:
    """A multi-turn conversation with the Librarian. Reusing one session across
    sends is what gives the agent conversational memory (ADR-036)."""

    def __init__(self, runner: Runner, user_id: str, session_id: str, on_event=None):
        self._runner = runner
        self.user_id = user_id
        self.session_id = session_id
        # Optional visibility hook (ADR-045): on_event(kind, detail) for ("tool", name) /
        # ("agent", author). Duck-typed event introspection so unit-test fakes keep working.
        self.on_event = on_event

    async def asend(self, message: str) -> str:
        content = types.Content(role="user", parts=[types.Part(text=message)])
        final = ""
        last_author = None
        async for event in self._runner.run_async(
            user_id=self.user_id, session_id=self.session_id, new_message=content
        ):
            if self.on_event:
                author = getattr(event, "author", None)
                if author and author != last_author:
                    self.on_event("agent", author)
                    last_author = author
                get_calls = getattr(event, "get_function_calls", None)
                for fc in (get_calls() if callable(get_calls) else []) or []:
                    name = getattr(fc, "name", None)
                    if name:
                        self.on_event("tool", name)
            if event.is_final_response() and event.content and event.content.parts:
                parts_text = [p.text for p in event.content.parts if p.text]
                if parts_text:
                    final = "".join(parts_text)
        return final or "(no response)"

    def send(self, message: str) -> str:
        return asyncio.run(self.asend(message))

    def close(self) -> None:
        """No session resources to release (InMemorySessionService); exists for
        BackendConversation conformance (ADR-045)."""


async def astart_conversation(
    user_id: str = "local", runner: Runner | None = None, on_event=None
) -> LibrarianConversation:
    runner = runner or build_runner()
    session_id = uuid.uuid4().hex
    await runner.session_service.create_session(app_name=APP_NAME, user_id=user_id, session_id=session_id)
    return LibrarianConversation(runner, user_id, session_id, on_event=on_event)


def start_conversation(user_id: str = "local", runner: Runner | None = None, on_event=None) -> LibrarianConversation:
    return asyncio.run(astart_conversation(user_id=user_id, runner=runner, on_event=on_event))


def run_recommendation(prompt: str, user_id: str = "local") -> str:
    """One-shot recommendation via the configured backend (AGENT_BACKEND)."""
    return get_backend().run_recommendation(prompt, user_id)
