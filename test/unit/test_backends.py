import pytest
from agentic_librarian.agents.backends import RecommendationBackend, get_backend


def test_default_backend_is_adk(monkeypatch):
    monkeypatch.delenv("AGENT_BACKEND", raising=False)
    backend = get_backend()
    assert isinstance(backend, RecommendationBackend)
    assert backend.name == "adk"


def test_explicit_adk_backend(monkeypatch):
    monkeypatch.setenv("AGENT_BACKEND", "adk")
    assert get_backend().name == "adk"


def test_unknown_backend_raises(monkeypatch):
    monkeypatch.setenv("AGENT_BACKEND", "bogus")
    with pytest.raises(ValueError, match="Unknown AGENT_BACKEND"):
        get_backend()
