from test.integration.seed_helpers import seed_recommendation_fixture

import pytest
from agentic_librarian.agents.backends import get_backend
from agentic_librarian.db.models import Suggestions
from agentic_librarian.db.session import DatabaseManager
from agentic_librarian.mcp.server import set_db_manager


@pytest.mark.api_dependent
@pytest.mark.db_integration
def test_configured_backend_recommends_and_logs(db_url):
    dbm = DatabaseManager(db_url)
    set_db_manager(dbm)
    with dbm.get_session() as session:
        seed_recommendation_fixture(session)

    result = get_backend().run_recommendation("a slow-burn enemies-to-lovers romance")

    assert isinstance(result, str) and len(result.strip()) > 30
    assert result != "(no recommendation)"
    with dbm.get_session() as session:
        assert session.query(Suggestions).count() >= 1
