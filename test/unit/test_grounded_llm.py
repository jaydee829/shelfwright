from unittest.mock import MagicMock

import agentic_librarian.scouts.grounded_llm as gl


def test_extract_text_prefers_text_then_parts():
    direct = MagicMock()
    direct.text = "hello"
    assert gl._extract_text(direct) == "hello"

    grounded = MagicMock()
    grounded.text = None
    part = MagicMock()
    part.text = '{"x": 1}'
    grounded.candidates = [MagicMock(content=MagicMock(parts=[part]))]
    assert gl._extract_text(grounded) == '{"x": 1}'

    empty = MagicMock()
    empty.text = None
    empty.candidates = []
    assert gl._extract_text(empty) is None


def test_gemini_generate_grounded_and_plain(monkeypatch):
    captured = {}

    class FakeModels:
        def generate_content(self, model, contents, config):
            captured["model"] = model
            captured["config"] = config
            resp = MagicMock()
            resp.text = "RESULT"
            return resp

    class FakeClient:
        def __init__(self, *a, **k):
            self.models = FakeModels()

    monkeypatch.setattr(gl.genai, "Client", FakeClient)
    monkeypatch.setenv("USE_SEARCH_GROUNDING", "1")
    monkeypatch.setenv("GROUNDING_MODEL", "gemini-test")

    llm = gl.GeminiGroundedLLM(api_key="k")
    assert llm.generate("p", grounded=True) == "RESULT"
    assert captured["config"]["tools"] == [{"google_search": {}}]
    assert captured["model"] == "gemini-test"

    llm.generate("p", grounded=False)
    assert captured["config"]["tools"] == []


def test_gemini_respects_use_search_grounding_flag(monkeypatch):
    captured = {}

    class FakeModels:
        def generate_content(self, model, contents, config):
            captured["config"] = config
            resp = MagicMock()
            resp.text = "x"
            return resp

    class FakeClient:
        def __init__(self, *a, **k):
            self.models = FakeModels()

    monkeypatch.setattr(gl.genai, "Client", FakeClient)
    monkeypatch.setenv("USE_SEARCH_GROUNDING", "0")
    gl.GeminiGroundedLLM(api_key="k").generate("p", grounded=True)
    assert captured["config"]["tools"] == []


def test_factory_selects_backend(monkeypatch):
    monkeypatch.setattr(gl.genai, "Client", lambda *a, **k: MagicMock())
    monkeypatch.setenv("AGENT_BACKEND", "adk")
    assert isinstance(gl.get_grounded_llm("k"), gl.GeminiGroundedLLM)
    monkeypatch.delenv("AGENT_BACKEND", raising=False)
    assert isinstance(gl.get_grounded_llm("k"), gl.GeminiGroundedLLM)
    monkeypatch.setenv("AGENT_BACKEND", "claude")
    assert isinstance(gl.get_grounded_llm("k"), gl.ClaudeGroundedLLM)


def test_factory_threads_model_name_to_gemini(monkeypatch):
    monkeypatch.setattr(gl.genai, "Client", lambda *a, **k: MagicMock())
    monkeypatch.delenv("AGENT_BACKEND", raising=False)
    llm = gl.get_grounded_llm("k", model_name="gemini-custom")
    assert llm.model_name == "gemini-custom"  # not silently ignored


def test_claude_generate_collects_result_and_sets_tools(monkeypatch):
    captured = {}

    class FakeMsg:
        def __init__(self, result):
            self.result = result

    async def fake_query(prompt, options):
        captured["allowed_tools"] = options.allowed_tools
        captured["model"] = options.model
        yield FakeMsg(None)
        yield FakeMsg("CLAUDE_JSON")

    fake_sdk = MagicMock()
    fake_sdk.query = fake_query
    fake_sdk.ClaudeAgentOptions = lambda **kw: MagicMock(**kw)
    monkeypatch.setitem(__import__("sys").modules, "claude_agent_sdk", fake_sdk)
    monkeypatch.setenv("CLAUDE_MODEL", "claude-test")

    llm = gl.ClaudeGroundedLLM()
    assert llm.generate("p", grounded=True) == "CLAUDE_JSON"
    assert captured["allowed_tools"] == ["WebSearch"]
    assert captured["model"] == "claude-test"
    assert llm.generate("p", grounded=False) == "CLAUDE_JSON"
    assert captured["allowed_tools"] == []
