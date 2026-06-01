"""Claude Agent SDK recommendation backend (Max-subscription quota). Explicit Python sequencing of
query() calls sharing the in-process librarian MCP tools; embeddings still go through Gemini via the DB
tools (out of scope to change)."""

from __future__ import annotations

import asyncio
import os

from agentic_librarian.agents import prompts
from agentic_librarian.agents.backends.claude_tools import build_librarian_mcp_server
from agentic_librarian.agents.candidates import coerce_schema_value, extract_candidate_ids, extract_discovery_pairs
from agentic_librarian.mcp.server import enrich_and_persist_work, log_suggestion
from claude_agent_sdk import ClaudeAgentOptions, query


def _model() -> str:
    return os.environ.get("CLAUDE_MODEL", "claude-sonnet-4-6")


async def _ask(prompt: str, *, system: str, allowed_tools: list[str], expect_json: bool, mcp_server) -> object:
    """Run one query() turn. Returns parsed dict (expect_json=True) or raw result text (expect_json=False).
    `mcp_server` is the prebuilt in-process librarian server (built once per run and reused)."""
    options = ClaudeAgentOptions(
        system_prompt=system,
        model=_model(),
        mcp_servers={"librarian": mcp_server},
        allowed_tools=allowed_tools,
    )
    text, structured = "", None
    async for message in query(prompt=prompt, options=options):
        if getattr(message, "structured_output", None) is not None:
            structured = message.structured_output
        # Use duck-typing rather than isinstance so unit-test fakes also work.
        # ResultMessage is the only message type with a non-None `.result` string.
        result_val = getattr(message, "result", None)
        if result_val and isinstance(result_val, str):
            text = result_val
    if expect_json:
        return structured if structured is not None else coerce_schema_value(text)
    return text


async def _arun(prompt: str) -> str:
    state: dict = {}
    librarian = build_librarian_mcp_server()  # in-process; built once, reused across the steps
    state["targets"] = await _ask(
        prompt,
        system=prompts.ANALYST_INSTRUCTION
        + '\nRespond with ONLY a JSON object: {"tropes":[], "styles":[], "session_constraints":[]}.',
        allowed_tools=["mcp__librarian__get_user_trope_preferences"],
        expect_json=True,
        mcp_server=librarian,
    )
    candidate_ids = extract_candidate_ids(state)
    state["discoveries"] = await _ask(
        f"{prompt}\nTarget vibes: {coerce_schema_value(state['targets'])}",
        system=prompts.EXPLORER_INSTRUCTION
        + '\nRespond with ONLY a JSON object: {"books":[{"title": "", "author": "", "why": ""}]}.',
        # "WebSearch" is Claude Code's built-in web-search tool (allowed_tools uses CLI tool names,
        # PascalCase — cf. ["Read", "Grep"]). Unverifiable offline; if the live Explorer doesn't
        # search, try the server-tool name "web_search". VERIFY on the first live run (REC-019).
        allowed_tools=["WebSearch"],
        expect_json=True,
        mcp_server=librarian,
    )
    for title, author in extract_discovery_pairs(state):
        wid = await asyncio.to_thread(enrich_and_persist_work, title, author)
        if wid and wid not in candidate_ids:
            candidate_ids.append(wid)
    critic_prompt = (
        f"Target vibes: {coerce_schema_value(state['targets'])}\n"
        f"Candidate work ids: {candidate_ids}\n"
        f"User request: {prompt}"
    )
    recommendation = await _ask(
        critic_prompt,
        system=prompts.CRITIC_INSTRUCTION,
        allowed_tools=[
            "mcp__librarian__search_internal_database",
            "mcp__librarian__get_work_details",
            "mcp__librarian__check_reading_history",
        ],
        expect_json=False,
        mcp_server=librarian,
    )
    recommendation = recommendation or "(no recommendation)"
    if recommendation != "(no recommendation)" and candidate_ids:
        await asyncio.to_thread(
            log_suggestion,
            work_id=candidate_ids[0],
            context="recommendation",
            justification=recommendation[:1000],
        )
    return recommendation


class ClaudeBackend:
    name = "claude"

    def run_recommendation(self, prompt: str, user_id: str = "local") -> str:
        return asyncio.run(_arun(prompt))
