"""Backend-selectable grounded-LLM provider for the enrichment scouts. The single AGENT_BACKEND knob
(shared with the recommendation mesh) picks Gemini grounding (default) or Claude WebSearch, so an
`AGENT_BACKEND=claude` run enriches off the Max subscription instead of the Gemini free-tier daily cap
(REC-024). Embeddings stay on Gemini (separate, higher quota)."""

from __future__ import annotations

import asyncio
import os
from typing import Protocol, runtime_checkable

from agentic_librarian.llm_retry import genai_http_options
from google import genai


@runtime_checkable
class GroundedLLM(Protocol):
    def generate(self, prompt: str, grounded: bool = True) -> str:
        """Return the model's text for `prompt`. When `grounded`, perform web-grounded generation."""
        ...


def _extract_text(response) -> str | None:
    """Return response text, falling back to concatenated candidate parts. Grounded/multi-part
    responses can leave ``response.text`` empty even though the answer is in the candidate parts."""
    if getattr(response, "text", None):
        return response.text
    try:
        parts = response.candidates[0].content.parts or []
    except (AttributeError, IndexError, TypeError):
        return None
    texts = [p.text for p in parts if getattr(p, "text", None)]
    return "".join(texts) if texts else None


class GeminiGroundedLLM:
    """Grounded generation via Gemini's google_search tool — the prior scout behavior, relocated."""

    def __init__(self, api_key: str | None = None, model_name: str | None = None):
        self.api_key = api_key or os.environ.get("GOOGLE_SEARCH_API_KEY")
        self.model_name = (
            model_name or os.environ.get("GROUNDING_MODEL") or os.environ.get("EXPLORER_MODEL") or "gemini-2.5-flash"
        )
        self._client = genai.Client(api_key=self.api_key, http_options=genai_http_options())

    def generate(self, prompt: str, grounded: bool = True) -> str:
        use_grounding = grounded and os.environ.get("USE_SEARCH_GROUNDING", "1") == "1"
        response = self._client.models.generate_content(
            model=self.model_name,
            contents=prompt,
            config={"tools": [{"google_search": {}}] if use_grounding else []},
        )
        return _extract_text(response) or ""


class ClaudeGroundedLLM:
    """Grounded generation via the Claude Agent SDK (WebSearch tool), Max-subscription quota."""

    _SYSTEM = (
        "You are a book-metadata extraction assistant. Use web search to verify facts; never invent "
        "details. Return ONLY the raw JSON object the user's instructions specify — no prose, no code fences."
    )

    def __init__(self, model: str | None = None):
        self.model = model or os.environ.get("CLAUDE_MODEL", "claude-sonnet-4-6")

    def generate(self, prompt: str, grounded: bool = True) -> str:
        return asyncio.run(self._agenerate(prompt, grounded))

    async def _agenerate(self, prompt: str, grounded: bool) -> str:
        from claude_agent_sdk import ClaudeAgentOptions, query

        options = ClaudeAgentOptions(
            system_prompt=self._SYSTEM,
            model=self.model,
            allowed_tools=["WebSearch"] if grounded else [],
        )
        text = ""
        async for message in query(prompt=prompt, options=options):
            result_val = getattr(message, "result", None)
            if result_val and isinstance(result_val, str):
                text = result_val
        return text


def get_grounded_llm(api_key: str | None = None, model_name: str | None = None) -> GroundedLLM:
    """Pick the grounding-LLM provider from AGENT_BACKEND (default/'adk' -> Gemini; 'claude' -> Claude).
    `model_name` applies only to the Gemini provider; the Claude provider uses CLAUDE_MODEL."""
    choice = os.environ.get("AGENT_BACKEND", "adk").strip().lower()
    if choice == "claude":
        return ClaudeGroundedLLM()
    if choice not in ("adk", ""):
        # Fail loudly on a typo rather than silently defaulting to Gemini (matches agents.get_backend).
        raise ValueError(f"Unknown AGENT_BACKEND={choice!r} (expected 'adk' or 'claude').")
    return GeminiGroundedLLM(api_key, model_name)
