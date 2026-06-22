"""persist_enriched_work stores cleaned genres/moods (Spec 2026-06-22)."""

import pytest

from agentic_librarian.db.models import Work
from agentic_librarian.db.session import DatabaseManager
from agentic_librarian.etl.persist import persist_enriched_work

pytestmark = pytest.mark.db_integration

UUID = "4c14c349-8d52-4893-aaf0-34f7e33bf275"


class _NullManager:
    """Stand-in for Trope/Style managers — never actually called for a skip_enrichment row,
    but persist takes them as args."""

    def standardize_trope(self, *a, **k):
        return None

    def standardize_style(self, *a, **k):
        return None


def test_persist_cleans_genres_and_moods(db_url):
    manager = DatabaseManager(db_url)
    row = {
        "Title": "Tag Cleaning Test",
        "Author_1": "T. Author",
        "genres": [f"science-fiction-fantasy-{UUID}", f"audiobook-{UUID}", "Fiction"],
        "moods": [f"dark-{UUID}", "Dark"],
        "skip_enrichment": True,
    }
    with manager.get_session() as session:
        persist_enriched_work(session, row, _NullManager(), _NullManager())
        session.flush()
        work = session.query(Work).filter_by(title="Tag Cleaning Test").one()
        assert work.genres == ["Science Fiction", "Fantasy"]
        assert work.moods == ["Dark"]
