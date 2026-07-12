from unittest.mock import MagicMock, patch

import pytest

from agentic_librarian.db.models import Author, Work, WorkContributor, WorkTrope
from agentic_librarian.db.session import DatabaseManager
from agentic_librarian.enrichment import two_phase
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
    two_phase.set_db_manager(dbm)  # enrich_and_persist_work now routes through two_phase.enrich_fast
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
    two_phase.set_db_manager(dbm)  # enrich_and_persist_work now routes through two_phase.enrich_fast
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
        # Patch at the definition site: two_phase's fast pass uses create_fast_scout_manager
        # (the tiered scout, not the old all-scouts create_scout_manager).
        patch("agentic_librarian.enrichment.two_phase.create_fast_scout_manager", return_value=fake_manager),
        patch("agentic_librarian.mcp.server.enqueue_enrichment", return_value=False),
        patch("agentic_librarian.scouts.trope_manager.TropeManager._get_embedding", return_value=[0.1] * 1536),
    ):
        result = enrich_and_persist_work("Brand New Find", "New Author")
    assert result is not None
    fake_manager.enrich.assert_called_once()
    with dbm.get_session() as session:
        work = session.query(Work).filter_by(title="Brand New Find").first()
        assert work is not None
        # The enriched trope was persisted through the shared persist function.
        wt = session.query(WorkTrope).filter_by(work_id=work.id).first()
        assert wt is not None
        assert wt.relevance_score == pytest.approx(0.8)
