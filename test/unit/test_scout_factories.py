from agentic_librarian.orchestration.definitions import (
    create_deep_scout_manager,
    create_fast_scout_manager,
)
from agentic_librarian.scouts.metadata_scout import (
    AudiobookScout,
    DirectKnowledgeScout,
    GoogleBooksScout,
    HardcoverScout,
    LLMTropeScout,
    StyleScout,
)


def test_fast_manager_has_only_api_scouts_in_priority_order():
    mgr = create_fast_scout_manager()
    types = [type(s) for s, _ in mgr.scouts]
    assert types == [HardcoverScout, GoogleBooksScout]


def test_deep_manager_has_only_llm_scouts_in_priority_order(monkeypatch):
    # LLM scouts require a Google key at construction.
    monkeypatch.setenv("GOOGLE_SEARCH_API_KEY", "dummy-key-for-construction")
    mgr = create_deep_scout_manager()
    types = [type(s) for s, _ in mgr.scouts]
    assert types == [AudiobookScout, DirectKnowledgeScout, StyleScout, LLMTropeScout]
