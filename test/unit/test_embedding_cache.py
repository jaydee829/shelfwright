"""#101: the embedding cache must hit across manager instances (keyed (model, text))."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from agentic_librarian.scouts import utils


@pytest.fixture(autouse=True)
def _clean_cache(monkeypatch):
    utils.get_cached_embedding.cache_clear()
    monkeypatch.setattr(utils, "_shared_client", None)
    yield
    utils.get_cached_embedding.cache_clear()


def _fake_client(counter):
    def embed_content(model, contents, config):
        counter.append(contents)
        return SimpleNamespace(embeddings=[SimpleNamespace(values=[0.1] * utils.EMBEDDING_DIMENSIONS)])

    return SimpleNamespace(models=SimpleNamespace(embed_content=embed_content))


def test_cache_hits_across_callers(monkeypatch):
    calls = []
    monkeypatch.setattr(utils, "_shared_client", _fake_client(calls))
    v1 = utils.get_cached_embedding("gemini-embedding-001", "Found Family")
    v2 = utils.get_cached_embedding("gemini-embedding-001", "Found Family")
    assert v1 == v2
    assert len(calls) == 1  # second call was a cache hit


def test_cache_misses_on_different_text(monkeypatch):
    calls = []
    monkeypatch.setattr(utils, "_shared_client", _fake_client(calls))
    utils.get_cached_embedding("gemini-embedding-001", "Found Family")
    utils.get_cached_embedding("gemini-embedding-001", "Slow Burn")
    assert len(calls) == 2


def test_shared_client_is_singleton(monkeypatch):
    monkeypatch.setenv("GOOGLE_SEARCH_API_KEY", "test-key")
    built = []

    class FakeClient:
        def __init__(self, **kwargs):
            built.append(kwargs)

    monkeypatch.setattr(utils.genai, "Client", FakeClient)
    c1 = utils.get_shared_genai_client()
    c2 = utils.get_shared_genai_client()
    assert c1 is c2
    assert len(built) == 1
    assert built[0]["api_key"] == "test-key"


def test_shared_client_requires_key(monkeypatch):
    monkeypatch.delenv("GOOGLE_SEARCH_API_KEY", raising=False)
    with pytest.raises(ValueError, match="GOOGLE_SEARCH_API_KEY"):
        utils.get_shared_genai_client()


def test_managers_share_the_module_cache(monkeypatch):
    calls = []
    monkeypatch.setattr(utils, "_shared_client", _fake_client(calls))
    from agentic_librarian.scouts.style_manager import StyleManager
    from agentic_librarian.scouts.trope_manager import TropeManager

    tm = TropeManager(session=MagicMock(), api_key="k")
    sm = StyleManager(session=MagicMock(), api_key="k")
    tm._get_embedding("Enemies to Lovers")
    sm._get_embedding("Enemies to Lovers")  # same model+text -> cache hit
    assert len(calls) == 1
