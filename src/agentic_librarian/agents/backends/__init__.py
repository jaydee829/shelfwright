"""Pluggable recommendation backends. AGENT_BACKEND selects the implementation; the default is
the existing ADK/Gemini backend (no behavior change). A Claude Agent SDK backend (Max-subscription
quota) is selectable with AGENT_BACKEND=claude."""

from __future__ import annotations

import os
from typing import Protocol, runtime_checkable


@runtime_checkable
class RecommendationBackend(Protocol):
    name: str

    def run_recommendation(self, prompt: str, user_id: str = "local") -> str:
        """Run the one-shot recommendation pipeline and return the recommendation text."""
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
