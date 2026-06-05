from unittest.mock import patch

import pytest

pytest.importorskip("claude_agent_sdk")  # the `claude` optional extra; skip if not installed

from agentic_librarian.agents.backends.claude import ClaudeBackend  # noqa: E402


class _Result:
    def __init__(self, result=None, structured_output=None):
        self.result = result
        self.structured_output = structured_output


def _fake_query_factory(scripted):
    calls = {"i": 0}

    async def fake_query(prompt=None, options=None):
        msgs = scripted[calls["i"]]
        calls["i"] += 1
        for m in msgs:
            yield m

    return fake_query, calls


def test_claude_backend_sequences_pipeline_and_returns_recommendation():
    # Analyst -> targets ; Explorer -> discoveries (empty) ; Critic -> recommendation text.
    scripted = [
        [
            _Result(
                result='{"tropes": ["heist"], "styles": [], "session_constraints": []}',
                structured_output={"tropes": ["heist"], "styles": [], "session_constraints": []},
            )
        ],  # Analyst
        [_Result(result='{"books": []}', structured_output={"books": []})],  # Explorer (no discoveries)
        [_Result(result="I recommend The Long War because it features grimdark war.")],  # Critic
    ]
    fake_query, calls = _fake_query_factory(scripted)

    with (
        patch("agentic_librarian.agents.backends.claude.query", fake_query),
        patch("agentic_librarian.agents.backends.claude.extract_candidate_ids", return_value=["w1"]),
        patch("agentic_librarian.agents.backends.claude.log_suggestion") as mock_log,
    ):
        out = ClaudeBackend().run_recommendation("a heist book")

    assert "recommend" in out.lower()
    assert calls["i"] == 3  # Analyst, Explorer, Critic each queried once
    mock_log.assert_called_once()  # the top candidate was logged


class _FakeTextBlock:
    def __init__(self, text):
        self.text = text


class _FakeToolUseBlock:
    def __init__(self, name):
        self.name = name
        self.input = {}


class _FakeAssistantMessage:
    def __init__(self, blocks):
        self.content = blocks


class _FakeResultMessage:
    def __init__(self, result):
        self.result = result
        self.content = []


class _FakeSDKClient:
    """Duck-typed ClaudeSDKClient: connect/query/receive_response/disconnect."""

    def __init__(self):
        self.connected = False
        self.disconnected = False
        self.queries = []

    async def connect(self):
        self.connected = True

    async def query(self, prompt, session_id="default"):
        self.queries.append(prompt)

    async def receive_response(self):
        task_block = _FakeToolUseBlock("Task")
        task_block.input = {"subagent_type": "explorer", "prompt": "find books"}
        yield _FakeAssistantMessage(
            [
                task_block,
                _FakeToolUseBlock("mcp__librarian__get_unacted_suggestions"),
                _FakeTextBlock("thinking..."),
            ]
        )
        yield _FakeResultMessage(f"reply-{len(self.queries)}")

    async def disconnect(self):
        self.disconnected = True


def test_claude_conversation_reuses_one_session_and_fires_events():
    from agentic_librarian.agents.backends.claude import ClaudeConversation

    fake = _FakeSDKClient()
    seen = []
    conv = ClaudeConversation(on_event=lambda k, d: seen.append((k, d)), client_factory=lambda: fake)
    try:
        assert conv.send("first") == "reply-1"
        assert conv.send("second") == "reply-2"
    finally:
        conv.close()
    assert fake.queries == ["first", "second"]  # ONE client, one session, two turns
    assert ("agent", "explorer") in seen  # Task delegation maps to an agent event (ADK parity)
    assert ("tool", "mcp__librarian__get_unacted_suggestions") in seen
    assert fake.disconnected


def test_conversation_options_wire_the_specialist_mesh():
    from agentic_librarian.agents import prompts
    from agentic_librarian.agents.backends.claude import _conversation_options

    options = _conversation_options()
    assert set(options.agents) == {"analyst", "explorer", "critic"}
    assert options.agents["analyst"].prompt == prompts.ANALYST_INSTRUCTION
    assert options.agents["explorer"].prompt == prompts.EXPLORER_INSTRUCTION
    assert options.agents["explorer"].tools == ["WebSearch"]
    assert options.agents["critic"].prompt == prompts.CRITIC_INSTRUCTION
    assert "mcp__librarian__search_internal_database" in options.agents["critic"].tools
    from agentic_librarian.agents.backends.claude_tools import LIBRARIAN_TOOL_NAMES

    # Session-level allowlist must PERMIT the whole mesh: every librarian MCP tool (the
    # subagents' AgentDefinition.tools only scope, they don't grant permission — live-verified)
    # plus web search for the explorer and both delegation tool names.
    for name in LIBRARIAN_TOOL_NAMES:
        assert name in options.allowed_tools
    assert "WebSearch" in options.allowed_tools
    assert "Task" in options.allowed_tools
    assert "Agent" in options.allowed_tools
    assert options.system_prompt == prompts.LIBRARIAN_INSTRUCTION


def test_claude_conversation_close_is_idempotent():
    from agentic_librarian.agents.backends.claude import ClaudeConversation

    conv = ClaudeConversation(client_factory=_FakeSDKClient)
    conv.close()
    conv.close()  # second close must not raise


def test_claude_backend_start_conversation_satisfies_protocol():
    from agentic_librarian.agents.backends import BackendConversation
    from agentic_librarian.agents.backends.claude import ClaudeBackend

    conv = ClaudeBackend().start_conversation(client_factory=_FakeSDKClient)
    try:
        assert isinstance(conv, BackendConversation)
        assert conv.send("hi") == "reply-1"
    finally:
        conv.close()


def test_claude_conversation_connect_failure_stops_loop_thread():
    import threading

    from agentic_librarian.agents.backends.claude import ClaudeConversation

    before = {t.ident for t in threading.enumerate()}
    with pytest.raises(RuntimeError, match="no auth"):
        ClaudeConversation(client_factory=lambda: (_ for _ in ()).throw(RuntimeError("no auth")))
    leaked = [t for t in threading.enumerate() if t.ident not in before and t.is_alive()]
    assert not leaked, f"connect failure leaked loop thread(s): {leaked}"


def test_claude_conversation_close_survives_disconnect_error(capsys):
    from agentic_librarian.agents.backends.claude import ClaudeConversation

    class _ExplodingDisconnectClient(_FakeSDKClient):
        async def disconnect(self):
            raise RuntimeError("socket gone")

    conv = ClaudeConversation(client_factory=_ExplodingDisconnectClient)
    conv.close()  # must not raise
    assert "disconnect failed" in capsys.readouterr().out


def test_claude_conversation_close_closes_the_loop():
    from agentic_librarian.agents.backends.claude import ClaudeConversation

    conv = ClaudeConversation(client_factory=_FakeSDKClient)
    loop = conv._loop
    conv.close()
    assert loop.is_closed()  # selector resources released, not just stopped (PR #33 review)


def test_claude_conversation_connect_failure_closes_the_loop(monkeypatch):
    import asyncio as _asyncio

    from agentic_librarian.agents.backends import claude as claude_mod

    created = []
    real_new_event_loop = _asyncio.new_event_loop

    def _capturing_new_event_loop():
        loop = real_new_event_loop()
        created.append(loop)
        return loop

    monkeypatch.setattr(claude_mod.asyncio, "new_event_loop", _capturing_new_event_loop)
    with pytest.raises(RuntimeError, match="no auth"):
        claude_mod.ClaudeConversation(client_factory=lambda: (_ for _ in ()).throw(RuntimeError("no auth")))
    assert created and created[0].is_closed()


def test_agent_tool_block_maps_to_agent_event():
    # The current SDK names the delegation tool "Agent" (older: "Task") — both must map to
    # ("agent", subagent_type) so delegations show as agent events, not tool events.
    from agentic_librarian.agents.backends.claude import ClaudeConversation

    class _AgentBlockClient(_FakeSDKClient):
        async def receive_response(self):
            agent_block = _FakeToolUseBlock("Agent")
            agent_block.input = {"subagent_type": "analyst", "prompt": "get tropes"}
            yield _FakeAssistantMessage([agent_block])
            yield _FakeResultMessage("done")

    seen = []
    conv = ClaudeConversation(on_event=lambda k, d: seen.append((k, d)), client_factory=_AgentBlockClient)
    try:
        conv.send("hi")
    finally:
        conv.close()
    assert ("agent", "analyst") in seen


def test_agent_block_with_non_dict_input_does_not_crash_the_turn():
    # Defensive (PR #34 review): a malformed delegation block whose `input` is a non-dict
    # truthy value (e.g. a raw string) must not raise inside the event emitter.
    from agentic_librarian.agents.backends.claude import ClaudeConversation

    class _MalformedAgentBlockClient(_FakeSDKClient):
        async def receive_response(self):
            bad_block = _FakeToolUseBlock("Agent")
            bad_block.input = "analyst"  # non-dict truthy input
            yield _FakeAssistantMessage([bad_block])
            yield _FakeResultMessage("done")

    seen = []
    conv = ClaudeConversation(on_event=lambda k, d: seen.append((k, d)), client_factory=_MalformedAgentBlockClient)
    try:
        assert conv.send("hi") == "done"
    finally:
        conv.close()
    assert ("agent", "subagent") in seen  # falls back to the generic label
