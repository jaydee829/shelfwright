"""Pluggable recommendation backends. AGENT_BACKEND selects the implementation; the default is
the existing ADK/Gemini backend (no behavior change). A Claude Agent SDK backend (Max-subscription
quota) is selectable with AGENT_BACKEND=claude."""

from __future__ import annotations

import os
from typing import Callable, Protocol, runtime_checkable


@runtime_checkable
class BackendConversation(Protocol):
    """A stateful multi-turn Librarian session (ADR-045)."""

    def send(self, message: str) -> str:
        """Send one user message; return the Librarian's reply for this turn."""
        ...

    def close(self) -> None:
        """Release session resources. Idempotent."""
        ...


@runtime_checkable
class RecommendationBackend(Protocol):
    name: str

    def run_recommendation(self, prompt: str, user_id: str = "local") -> str:
        """Run the one-shot recommendation pipeline and return the recommendation text."""
        ...

    def start_conversation(
        self,
        user_id: str = "local",
        on_event: Callable[[str, str], None] | None = None,
    ) -> BackendConversation:
        """Open a multi-turn conversation. `on_event(kind, detail)` receives key events
        (e.g. ("tool", "search_internal_database"), ("agent", "Explorer"))."""
        ...


def get_backend() -> RecommendationBackend:
    """Return the configured backend (AGENT_BACKEND env var; default 'adk')."""
    choice = os.environ.get("AGENT_BACKEND", "adk").strip().lower()
    if choice == "adk":
        from agentic_librarian.agents.backends.adk import ADKBackend

        return ADKBackend()
    if choice == "claude":
        from agentic_librarian.agents.backends.claude import ClaudeBackend

        return ClaudeBackend()
    raise ValueError(f"Unknown AGENT_BACKEND={choice!r} (expected 'adk' or 'claude').")
