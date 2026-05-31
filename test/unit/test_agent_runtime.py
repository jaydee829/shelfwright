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
