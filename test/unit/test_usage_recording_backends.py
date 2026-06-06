"""Both backends report per-call token usage into the recorder (Lift 1, ADR-048).
ADK: usage_metadata on events. Claude: usage on the ResultMessage. The Claude
conversation runs turns on a background loop thread — identity is explicitly
captured at construction and re-applied per turn (do not rely on implicit
context propagation through run_coroutine_threadsafe)."""

from types import SimpleNamespace
from uuid import uuid4

from agentic_librarian.core.user_context import DEFAULT_USER_ID, as_user


def test_adk_conversation_records_usage(monkeypatch):
    calls = []
    monkeypatch.setattr("agentic_librarian.agents.runtime.record_llm_call", lambda **kw: calls.append(kw))

    class FakeEvent:
        usage_metadata = SimpleNamespace(prompt_token_count=10, candidates_token_count=4)
        model_version = "gemini-test"
        author = None
        content = None

        def is_final_response(self):
            return False

    class FakeRunner:
        async def run_async(self, **kwargs):
            yield FakeEvent()

    from agentic_librarian.agents.runtime import LibrarianConversation

    convo = LibrarianConversation(FakeRunner(), "local", uuid4().hex)
    assert convo.send("hi") == "(no response)"
    assert calls == [
        {
            "vendor": "gemini",
            "model": "gemini-test",
            "input_tokens": 10,
            "output_tokens": 4,
            "conversation_id": convo.conversation_id,
        }
    ]


def test_claude_conversation_records_usage(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "agentic_librarian.agents.backends.claude.record_llm_call", lambda **kw: calls.append(kw)
    )

    class FakeResult:
        result = "hi"
        usage = {"input_tokens": 11, "output_tokens": 7}
        content = []

    class FakeClient:
        async def connect(self):
            pass

        async def query(self, message):
            pass

        async def receive_response(self):
            yield FakeResult()

        async def disconnect(self):
            pass

    from agentic_librarian.agents.backends.claude import ClaudeConversation

    with as_user(DEFAULT_USER_ID):
        convo = ClaudeConversation(client_factory=FakeClient)
        try:
            assert convo.send("hello") == "hi"
        finally:
            convo.close()
    assert len(calls) == 1
    assert calls[0]["vendor"] == "anthropic"
    assert calls[0]["input_tokens"] == 11
    assert calls[0]["output_tokens"] == 7
    assert calls[0]["conversation_id"] == convo.conversation_id


def test_claude_loop_thread_sees_the_user_context():
    """The trap this guards: identity must be explicitly re-applied on the background
    loop thread for every turn — tools and the recorder read it from context there."""
    seen = []

    class FakeClient:
        async def connect(self):
            pass

        async def query(self, message):
            pass

        async def receive_response(self):
            from agentic_librarian.core.user_context import get_required_user_id

            seen.append(get_required_user_id())
            return
            yield  # makes this an async generator

        async def disconnect(self):
            pass

    from agentic_librarian.agents.backends.claude import ClaudeConversation

    with as_user(DEFAULT_USER_ID):
        convo = ClaudeConversation(client_factory=FakeClient)
        try:
            convo.send("hello")
        finally:
            convo.close()
    assert seen == [DEFAULT_USER_ID]
