"""Claude Agent SDK recommendation backend (Max-subscription quota). Explicit Python sequencing of
query() calls sharing the in-process librarian MCP tools; embeddings still go through Gemini via the DB
tools (out of scope to change)."""

from __future__ import annotations

import asyncio
import os
import threading

from agentic_librarian.agents import prompts
from agentic_librarian.agents.backends.claude_tools import LIBRARIAN_TOOL_NAMES, build_librarian_mcp_server
from agentic_librarian.agents.candidates import coerce_schema_value, extract_candidate_ids, extract_discovery_pairs
from agentic_librarian.mcp.server import enrich_and_persist_work, log_suggestion
from claude_agent_sdk import AgentDefinition, ClaudeAgentOptions, query


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
        # Normalize to a dict regardless of how the result arrived (dict / pydantic model / JSON text).
        return coerce_schema_value(structured if structured is not None else text)
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


def _conversation_options() -> ClaudeAgentOptions:
    """Options for the conversational Librarian (ADR-045): the SAME specialist mesh as ADK,
    as programmatic SDK subagents invoked via the Task tool (the analogue of ADK's AgentTool).
    Specialist prompts are reused verbatim; tools are scoped like the ADK mesh. VERIFY on the
    first live run (REC-019 pattern): subagents must see the in-process 'librarian' MCP server
    via mcpServers=["librarian"]; if not, rescope those tools onto the Librarian itself."""
    agents = {
        "analyst": AgentDefinition(
            description="Turns user vibes into structured trope/style targets and constraints.",
            prompt=prompts.ANALYST_INSTRUCTION,
            tools=["mcp__librarian__get_user_trope_preferences"],
            mcpServers=["librarian"],
        ),
        "explorer": AgentDefinition(
            description="Discovers new candidate books on the web.",
            prompt=prompts.EXPLORER_INSTRUCTION,
            tools=["WebSearch"],
        ),
        "critic": AgentDefinition(
            description="Ranks candidates and writes a grounded Trope-RAG justification.",
            prompt=prompts.CRITIC_INSTRUCTION,
            tools=[
                "mcp__librarian__search_internal_database",
                "mcp__librarian__get_work_details",
                "mcp__librarian__check_reading_history",
            ],
            mcpServers=["librarian"],
        ),
    }
    return ClaudeAgentOptions(
        system_prompt=prompts.LIBRARIAN_INSTRUCTION,
        model=_model(),
        mcp_servers={"librarian": build_librarian_mcp_server()},
        agents=agents,
        # Session-level PERMISSION for the whole mesh: AgentDefinition.tools above only SCOPES
        # what each subagent may use — it does not grant permission (live-verified, PR #33
        # follow-up: the analyst's tool call was permission-denied). Subagent scoping still
        # applies, and the Librarian's instruction still routes specialist work through Task.
        allowed_tools=["Task", "Agent", *LIBRARIAN_TOOL_NAMES, "WebSearch"],
    )


class ClaudeConversation:
    """Multi-turn conversational Librarian on a persistent ClaudeSDKClient session (ADR-045).
    The SDK is async and the REPL is sync, and the session must outlive each send — so the
    client lives on a dedicated background event-loop thread (cf. the running-loop constraint
    ClaudeGroundedLLM solved in PR #26; asyncio.run per send would tear down the session)."""

    def __init__(self, user_id: str = "local", on_event=None, client_factory=None):
        self.user_id = user_id
        self.on_event = on_event
        self._client_factory = client_factory or self._default_client
        self._client = None
        self._closed = False
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, daemon=True)
        self._thread.start()
        try:
            self._run(self._connect())
        except BaseException:
            # Don't leak the loop thread when the client can't connect (e.g. no `claude` auth).
            self._teardown_loop()
            raise

    @staticmethod
    def _default_client():
        from claude_agent_sdk import ClaudeSDKClient

        return ClaudeSDKClient(options=_conversation_options())

    def _run(self, coro):
        # Blocks the calling (REPL) thread until the turn completes; Ctrl-C in the main
        # thread interrupts the wait (the CLI maps it to "turn aborted").
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result()

    async def _connect(self):
        self._client = self._client_factory()
        await self._client.connect()

    def _emit_block_event(self, block) -> None:
        """Map a ToolUseBlock to the trace: Task/Agent delegations -> ("agent", subagent),
        everything else -> ("tool", name) — matching the ADK event shape."""
        tool_name = getattr(block, "name", None)
        if not (tool_name and hasattr(block, "input") and self.on_event):
            return
        if tool_name in ("Task", "Agent"):
            # block.input is normally a dict, but never trust a parsed payload's shape —
            # a non-dict input must not raise inside the event emitter (PR #34 review).
            subagent_type = "subagent"
            if isinstance(block.input, dict):
                subagent_type = block.input.get("subagent_type", "subagent")
            self.on_event("agent", subagent_type)
        else:
            self.on_event("tool", str(tool_name))

    async def _asend(self, message: str) -> str:
        await self._client.query(message)
        text_parts: list[str] = []
        result_text = ""
        async for msg in self._client.receive_response():
            # ResultMessage carries the authoritative turn result (same duck-typing as _ask).
            result_val = getattr(msg, "result", None)
            if result_val and isinstance(result_val, str):
                result_text = result_val
            for block in getattr(msg, "content", None) or []:
                block_text = getattr(block, "text", None)
                if isinstance(block_text, str):
                    text_parts.append(block_text)
                self._emit_block_event(block)
        return result_text or "".join(text_parts) or "(no response)"

    def send(self, message: str) -> str:
        return self._run(self._asend(message))

    def _teardown_loop(self) -> None:
        """Stop the background loop, join its thread, then close the loop so selector
        resources are released (PR #33 review). Close only once the thread is gone —
        closing a still-running loop raises RuntimeError."""
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=5)
        if not self._thread.is_alive():
            self._loop.close()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            if self._client is not None:
                self._run(self._client.disconnect())
        except Exception as e:
            # A failed disconnect must not mask the caller's exception path; the session is
            # over either way. Warn for visibility (matches the project's no-silent-except rule).
            print(f"warning: claude conversation disconnect failed ({type(e).__name__}: {e})")
        finally:
            self._teardown_loop()


class ClaudeBackend:
    name = "claude"

    def run_recommendation(self, prompt: str, user_id: str = "local") -> str:
        return asyncio.run(_arun(prompt))

    def start_conversation(self, user_id: str = "local", on_event=None, client_factory=None):
        """Multi-turn conversational Librarian on a persistent SDK session (ADR-045).
        `client_factory` is injectable for tests."""
        return ClaudeConversation(user_id=user_id, on_event=on_event, client_factory=client_factory)
