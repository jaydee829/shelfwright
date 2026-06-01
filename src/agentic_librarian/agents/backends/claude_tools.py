"""Expose the existing MCP tool functions to the Claude Agent SDK as an in-process SDK MCP server,
so the Claude backend reuses the SAME tool logic (and set_db_manager test injection) as ADK."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from agentic_librarian.mcp import server as mcp_server
from claude_agent_sdk import create_sdk_mcp_server, tool

_SERVER_NAME = "librarian"

# Per-tool parameter schemas matching the real function signatures in mcp/server.py.
# SDK accepts {"param_name": type} dict-style schemas (or a TypedDict class or a JSON Schema dict).
# Optional params are included so Claude can pass them; the fn(**args) call forwards whatever is given.
_TOOL_SCHEMAS: list[tuple[str, str, dict[str, Any], Any]] = [
    (
        "get_user_trope_preferences",
        "Aggregate the user's frequent tropes.",
        {"limit": int},
        mcp_server.get_user_trope_preferences,
    ),
    (
        "search_internal_database",
        "Vector search the catalog by tropes/styles.",
        {"target_tropes": list, "target_styles": list, "limit": int},
        mcp_server.search_internal_database,
    ),
    (
        "get_unacted_suggestions",
        "Prior unread suggestions ranked by vibe.",
        {"target_tropes": list, "target_styles": list, "limit": int},
        mcp_server.get_unacted_suggestions,
    ),
    (
        "get_work_details",
        "Deep metadata + tropes + styles for a work id.",
        {"work_id": str},
        mcp_server.get_work_details,
    ),
    (
        "check_reading_history",
        "Read status + re-read eligibility for a title.",
        {"title": str, "author": str},
        mcp_server.check_reading_history,
    ),
    (
        "log_suggestion",
        "Log a recommendation to the Suggestions table.",
        {"work_id": str, "context": str, "justification": str, "conversation_id": str},
        mcp_server.log_suggestion,
    ),
]

LIBRARIAN_TOOL_NAMES = [f"mcp__{_SERVER_NAME}__{short}" for short, _, _, _ in _TOOL_SCHEMAS]


def _wrap(short: str, description: str, schema: dict[str, Any], fn: Any):
    @tool(short, description, schema)
    async def _handler(args: dict[str, Any]) -> dict[str, Any]:
        result = await asyncio.to_thread(lambda: fn(**args))  # off-thread: blocking DB call
        return {"content": [{"type": "text", "text": json.dumps(result, default=str)}]}

    return _handler


def build_librarian_mcp_server():
    """Build the in-process SDK MCP server exposing the librarian DB tools."""
    return create_sdk_mcp_server(
        name=_SERVER_NAME,
        version="1.0.0",
        tools=[_wrap(short, desc, schema, fn) for short, desc, schema, fn in _TOOL_SCHEMAS],
    )
