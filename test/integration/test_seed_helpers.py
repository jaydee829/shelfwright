from test.integration.seed_helpers import seed_recommendation_fixture

import pytest
from agentic_librarian.db.models import ReadingHistory, Suggestions, Work
from agentic_librarian.db.session import DatabaseManager


@pytest.mark.db_integration
def test_seed_recommendation_fixture_populates(db_url):
    dbm = DatabaseManager(db_url)
    with dbm.get_session() as session:
        titles = seed_recommendation_fixture(session)
    with dbm.get_session() as session:
        assert session.query(Work).count() == 3
        assert session.query(ReadingHistory).count() == 1
        assert session.query(Suggestions).filter_by(status="Suggested").count() == 1
        assert titles["read"] == "The Long War"
