import pytest

from agentic_librarian.db.models import Trope, WorkTrope
from agentic_librarian.db.session import DatabaseManager
from agentic_librarian.etl.persist import persist_enriched_work

pytestmark = pytest.mark.db_integration

UUID = "4c14c349-8d52-4893-aaf0-34f7e33bf275"


class _PassthroughTrope:
    """standardize_trope returns an exact-name Trope (no embedding) so we can assert names."""

    def __init__(self, session):
        self.session = session

    def standardize_trope(self, name, *a, **k):
        t = self.session.query(Trope).filter_by(name=name).first()
        if t is None:
            t = Trope(name=name)
            self.session.add(t)
            self.session.flush()
        return t

    def standardize_style(self, *a, **k):
        return None


def test_fallback_tropes_are_cleaned(db_url):
    manager = DatabaseManager(db_url)
    row = {
        "Title": "Fallback Trope Test",
        "Author_1": "T. Author",
        "format": "ebook",
        "genres": [f"science-fiction-fantasy-{UUID}"],
        "moods": [],
        # no enriched_tropes -> fallback path fires; skip_enrichment must be falsy
    }
    with manager.get_session() as session:
        tm = _PassthroughTrope(session)
        work = persist_enriched_work(session, row, tm, tm)
        session.flush()
        names = {
            session.get(Trope, wt.trope_id).name for wt in session.query(WorkTrope).filter_by(work_id=work.id).all()
        }
        assert names == {"Science Fiction", "Fantasy"}  # cleaned + split, NOT the raw slug
