import os

import pytest
from agentic_librarian.agents import runtime  # noqa: F401  (used by later tasks)
from agentic_librarian.agents.services import create_agent_mesh


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
