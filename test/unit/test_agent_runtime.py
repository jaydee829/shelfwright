import os

import pytest
from agentic_librarian.agents import runtime
from agentic_librarian.agents.services import create_agent_mesh
from google.genai import types


@pytest.fixture(autouse=True)
def _adk_key(monkeypatch, request):
    """ADK's Gemini model reads GOOGLE_API_KEY. Set a dummy for offline tests so
    agent/runner construction never needs a real key. Live tests opt out."""
    if "api_dependent" not in request.keywords:
        monkeypatch.setenv("GOOGLE_API_KEY", "test-adk-key")


def test_all_mesh_agents_have_a_model():
    mesh = create_agent_mesh()
    for name in ("librarian", "analyst", "explorer", "critic"):
        assert mesh[name].model, f"{name} agent has no model"


def test_ensure_adk_credentials_falls_back(monkeypatch):
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("GOOGLE_SEARCH_API_KEY", "fallback-key-123")
    runtime._ensure_adk_credentials()
    assert os.environ["GOOGLE_API_KEY"] == "fallback-key-123"


def test_build_runner_constructs():
    r = runtime.build_runner()
    assert r is not None
    assert r.app_name == runtime.APP_NAME


class _FakeEvent:
    def __init__(self, text: str):
        self.content = types.Content(role="model", parts=[types.Part(text=text)])

    def is_final_response(self) -> bool:
        return True


class _FakeSessionService:
    def __init__(self):
        self.created = []

    async def create_session(self, app_name, user_id, session_id):
        self.created.append((app_name, user_id, session_id))
        return None


class _FakeRunner:
    def __init__(self, reply="Recommended: Dune"):
        self.app_name = runtime.APP_NAME
        self.session_service = _FakeSessionService()
        self.calls = []
        self._reply = reply

    async def run_async(self, user_id, session_id, new_message):
        self.calls.append((user_id, session_id, new_message.parts[0].text))
        yield _FakeEvent(self._reply)


def test_send_returns_final_response_text():
    conv = runtime.LibrarianConversation(_FakeRunner(reply="Try Hyperion"), "u", "s")
    assert conv.send("recommend sci-fi") == "Try Hyperion"


def test_asend_concatenates_multiple_text_parts():
    class _MultiPartRunner(_FakeRunner):
        async def run_async(self, user_id, session_id, new_message):
            event = _FakeEvent("")
            event.content = types.Content(role="model", parts=[types.Part(text="Hello "), types.Part(text="world")])
            yield event

    conv = runtime.LibrarianConversation(_MultiPartRunner(), "u", "s")
    assert conv.send("hi") == "Hello world"


def test_two_sends_reuse_the_same_session():
    fake = _FakeRunner()
    conv = runtime.LibrarianConversation(fake, "u", "sess-1")
    conv.send("first")
    conv.send("second")
    assert [sid for (_, sid, _) in fake.calls] == ["sess-1", "sess-1"]
    assert [msg for (_, _, msg) in fake.calls] == ["first", "second"]


def test_start_conversation_creates_a_session():
    fake = _FakeRunner()
    conv = runtime.start_conversation(user_id="alice", runner=fake)
    assert conv.user_id == "alice"
    assert fake.session_service.created
    assert fake.session_service.created[0][1] == "alice"


def test_run_recommendation_one_shot(monkeypatch):
    fake = _FakeRunner(reply="Recommended: Dune")
    monkeypatch.setattr(runtime, "build_runner", lambda: fake)
    assert runtime.run_recommendation("something like Dune") == "Recommended: Dune"
    assert fake.calls[0][2] == "something like Dune"


@pytest.mark.api_dependent
def test_live_conversation_runs():
    conv = runtime.start_conversation()
    first = conv.send("Recommend a sci-fi novel like Dune in one sentence.")
    assert isinstance(first, str) and first.strip()
    # Second turn shares the session (memory).
    second = conv.send("Actually, something more recent.")
    assert isinstance(second, str) and second.strip()


def test_explorer_uses_explorer_model_env(monkeypatch):
    monkeypatch.setenv("EXPLORER_MODEL", "gemini-test-explorer")
    mesh = create_agent_mesh()
    assert mesh["explorer"].model == "gemini-test-explorer"


def test_explorer_model_defaults_to_flash(monkeypatch):
    monkeypatch.delenv("EXPLORER_MODEL", raising=False)
    mesh = create_agent_mesh()
    assert mesh["explorer"].model == "gemini-2.5-flash"


def test_explorer_has_a_google_search_tool():
    mesh = create_agent_mesh()
    tool_types = [type(t).__name__ for t in mesh["explorer"].tools]
    assert any("GoogleSearch" in name for name in tool_types), tool_types
