import pytest

from agentic_librarian.db.models import ReadingHistory, Suggestions, Trope, Work, WorkTrope
from agentic_librarian.db.session import DatabaseManager
from test.integration.seed_helpers import seed_recommendation_fixture


@pytest.mark.db_integration
def test_seed_recommendation_fixture_populates(db_url):
    dbm = DatabaseManager(db_url)
    with dbm.get_session() as session:
        titles = seed_recommendation_fixture(session)
    with dbm.get_session() as session:
        assert session.query(Work).count() == 3
        assert session.query(ReadingHistory).count() == 1
        assert session.query(Suggestions).filter_by(status="Suggested").count() == 1
        # 4 canonical tropes (2 grimdark + 2 romance, shared via get-or-create), 6 links (2 per work).
        assert session.query(Trope).count() == 4
        assert session.query(WorkTrope).count() == 6
        assert titles["read"] == "The Long War"
        assert titles["suggested"] == "A Courtship"
        assert titles["backlist"] == "Second Chances"
