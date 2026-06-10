import pytest

from agentic_librarian.db.models import Trope, Work, WorkTrope
from agentic_librarian.db.session import DatabaseManager
from agentic_librarian.etl.persist import persist_enriched_work
from agentic_librarian.scouts.style_manager import StyleManager
from agentic_librarian.scouts.trope_manager import TropeManager


@pytest.mark.db_integration
def test_persist_enriched_work_creates_graph(db_url, monkeypatch):
    monkeypatch.setenv("GOOGLE_SEARCH_API_KEY", "dummy-key")
    dbm = DatabaseManager(db_url)
    row = {
        "Title": "Test Persisted Book",
        "Author_1": "Persist Author",
        "format": "ebook",
        "skip_enrichment": False,
        "contributors": [{"name": "Persist Author", "role": "Author"}],
        "genres": ["Fantasy"],
        "moods": [],
        "enriched_tropes": [
            {
                "trope_name": "Chosen One",
                "description": "a chosen hero",
                "relevance_score": 0.9,
                "justification": "the hero is chosen",
            }
        ],
        "author_style": {},
        "work_style": {},
        "narrator_names": [],
        "narrator_styles": {},
        "date_completed": None,
    }
    with dbm.get_session() as session:
        tm = TropeManager(session=session)
        sm = StyleManager(session=session)
        monkeypatch.setattr(tm, "_get_embedding", lambda text: [0.1] * 1536)
        work = persist_enriched_work(session, row, tm, sm)
        session.commit()
        assert work is not None
        assert work.title == "Test Persisted Book"
    with dbm.get_session() as session:
        w = session.query(Work).filter_by(title="Test Persisted Book").first()
        assert w is not None
        wt = session.query(WorkTrope).filter_by(work_id=w.id).first()
        assert wt is not None
        assert wt.relevance_score == 0.9
        assert wt.justification == "the hero is chosen"
        assert session.query(Trope).filter_by(id=wt.trope_id).first().name == "Chosen One"
