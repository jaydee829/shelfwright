from unittest.mock import MagicMock, patch

import pytest
from agentic_librarian.db.models import Author, Work, WorkContributor
from agentic_librarian.db.session import DatabaseManager
from agentic_librarian.mcp.server import enrich_and_persist_work, set_db_manager


def _existing_work(session, title, author_name):
    author = Author(name=author_name)
    session.add(author)
    session.flush()
    work = Work(title=title)
    session.add(work)
    session.flush()
    session.add(WorkContributor(work=work, author=author, role="Author"))
    session.commit()
    return work


@pytest.mark.db_integration
def test_enrich_dedups_existing_work(db_url, monkeypatch):
    monkeypatch.setenv("GOOGLE_SEARCH_API_KEY", "dummy-key")
    dbm = DatabaseManager(db_url)
    set_db_manager(dbm)
    with dbm.get_session() as session:
        existing = _existing_work(session, "Known Book", "Known Author")
        existing_id = str(existing.id)
    # De-dup returns the existing work BEFORE enrichment, so no scout is constructed/called.
    result = enrich_and_persist_work("known book", "  Known Author  ")  # different case/whitespace
    assert result == existing_id


@pytest.mark.db_integration
def test_enrich_persists_new_discovery(db_url, monkeypatch):
    monkeypatch.setenv("GOOGLE_SEARCH_API_KEY", "dummy-key")
    dbm = DatabaseManager(db_url)
    set_db_manager(dbm)
    fake_enriched = {
        "title": "Brand New Find",
        "contributors": [{"name": "New Author", "role": "Author"}],
        "genres": ["Fantasy"],
        "moods": [],
        "enriched_tropes": [{"trope_name": "Heist", "relevance_score": 0.8}],
        "author_style": {},
        "work_style": {},
        "narrator_names": [],
        "narrator_styles": {},
    }
    fake_manager = MagicMock()
    fake_manager.enrich.return_value = fake_enriched
    with (
        patch("agentic_librarian.orchestration.definitions.create_scout_manager", return_value=fake_manager),
        patch("agentic_librarian.mcp.server.TropeManager._get_embedding", return_value=[0.1] * 1536),
    ):
        result = enrich_and_persist_work("Brand New Find", "New Author")
    assert result is not None
    fake_manager.enrich.assert_called_once()
    with dbm.get_session() as session:
        assert session.query(Work).filter_by(title="Brand New Find").first() is not None
