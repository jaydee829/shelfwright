"""Expose the existing MCP tool functions to the Claude Agent SDK as an in-process SDK MCP server,
so the Claude backend reuses the SAME tool logic (and set_db_manager test injection) as ADK."""

from __future__ import annotations

import asyncio
import inspect
import json
from typing import Any

from agentic_librarian.mcp import server as mcp_server
from claude_agent_sdk import create_sdk_mcp_server, tool

_SERVER_NAME = "librarian"

_STR = {"type": "string"}
_INT = {"type": "integer"}
_STR_ARRAY = {"type": "array", "items": {"type": "string"}}


def _schema(properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    """Full JSON Schema object so we control `required` ourselves — the SDK's dict[str, type]
    shorthand marks EVERY key required, which would force Claude to supply optional params."""
    return {"type": "object", "properties": properties, "required": required}


# Per-tool JSON Schemas matching the real function signatures in mcp/server.py. `required` lists ONLY
# the params without defaults; optional params (target_styles, limit, conversation_id) are omitted from
# `required` so Claude may leave them out and the function's defaults apply. `fn(**args)` forwards them.
_TOOL_SCHEMAS: list[tuple[str, str, dict[str, Any], Any]] = [
    (
        "get_user_trope_preferences",
        "Aggregate the user's frequent tropes.",
        _schema({"limit": _INT}, required=[]),
        mcp_server.get_user_trope_preferences,
    ),
    (
        "search_internal_database",
        "Vector search the catalog by tropes/styles.",
        _schema({"target_tropes": _STR_ARRAY, "target_styles": _STR_ARRAY, "limit": _INT}, required=["target_tropes"]),
        mcp_server.search_internal_database,
    ),
    (
        "get_unacted_suggestions",
        "Prior unread suggestions ranked by vibe.",
        _schema({"target_tropes": _STR_ARRAY, "target_styles": _STR_ARRAY, "limit": _INT}, required=["target_tropes"]),
        mcp_server.get_unacted_suggestions,
    ),
    (
        "get_work_details",
        "Deep metadata + tropes + styles for a work id.",
        _schema({"work_id": _STR}, required=["work_id"]),
        mcp_server.get_work_details,
    ),
    (
        "check_reading_history",
        "Read status + re-read eligibility for a title.",
        _schema({"title": _STR, "author": _STR}, required=["title", "author"]),
        mcp_server.check_reading_history,
    ),
    (
        "log_suggestion",
        "Log a recommendation to the Suggestions table.",
        _schema(
            {"work_id": _STR, "context": _STR, "justification": _STR, "conversation_id": _STR},
            required=["work_id", "context", "justification"],
        ),
        mcp_server.log_suggestion,
    ),
    (
        "update_reading_status",
        "Update reading history from user feedback (e.g. 'I read that').",
        _schema(
            {"title": _STR, "author": _STR, "status": _STR, "notes": _STR},
            required=["title", "author", "status"],
        ),
        mcp_server.update_reading_status,
    ),
    (
        "update_suggestion_status",
        "Update a suggestion's status (Accepted / Dismissed / Already Read).",
        _schema({"work_id": _STR, "status": _STR}, required=["work_id", "status"]),
        mcp_server.update_suggestion_status,
    ),
    (
        "enrich_and_persist_work",
        "Verify + enrich a discovered book via the scouts and persist it to the catalog; returns the work id, or null if the title does not resolve.",
        _schema({"title": _STR, "author": _STR, "format": _STR}, required=["title", "author"]),
        mcp_server.enrich_and_persist_work,
    ),
    (
        "add_book_to_history",
        "Add ONE book to the reading history (enrich first if needed); a re-read with a new date adds a new read event.",
        _schema(
            {
                "title": _STR,
                "author": _STR,
                "date_completed": _STR,
                "rating": _INT,
                "format": _STR,
                "notes": _STR,
            },
            required=["title", "author"],
        ),
        mcp_server.add_book_to_history,
    ),
]

LIBRARIAN_TOOL_NAMES = [f"mcp__{_SERVER_NAME}__{short}" for short, _, _, _ in _TOOL_SCHEMAS]


def _wrap(short: str, description: str, schema: dict[str, Any], fn: Any):
    sig = inspect.signature(fn)
    has_kwargs = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values())

    @tool(short, description, schema)
    async def _handler(args: dict[str, Any] | None = None) -> dict[str, Any]:
        # Defensive: the model may call with no args or hallucinate extra keys. Default to {} and,
        # unless the function takes **kwargs, drop keys it doesn't accept — so fn(**actual) can't TypeError.
        actual = args or {}
        if not has_kwargs:
            actual = {k: v for k, v in actual.items() if k in sig.parameters}
        result = await asyncio.to_thread(fn, **actual)  # off-thread: blocking DB call
        return {"content": [{"type": "text", "text": json.dumps(result, default=str)}]}

    return _handler


def build_librarian_mcp_server():
    """Build the in-process SDK MCP server exposing the librarian DB tools."""
    return create_sdk_mcp_server(
        name=_SERVER_NAME,
        version="1.0.0",
        tools=[_wrap(short, desc, schema, fn) for short, desc, schema, fn in _TOOL_SCHEMAS],
    )
