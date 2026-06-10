import pytest

from agentic_librarian.agents import runtime
from agentic_librarian.db.models import Suggestions
from agentic_librarian.db.session import DatabaseManager
from agentic_librarian.mcp.server import set_db_manager
from test.integration.seed_helpers import seed_recommendation_fixture


@pytest.mark.api_dependent
@pytest.mark.db_integration
def test_full_pipeline_recommends_and_logs(db_url):
    runtime._ensure_adk_credentials()
    dbm = DatabaseManager(db_url)
    set_db_manager(dbm)
    with dbm.get_session() as session:
        seed_recommendation_fixture(session)

    result = runtime.run_recommendation("I want a slow-burn enemies-to-lovers romance like the ones I've enjoyed.")

    assert isinstance(result, str)
    assert len(result.strip()) > 30
    assert result != "(no recommendation)"
    # The Logger step should have logged a suggestion.
    with dbm.get_session() as session:
        assert session.query(Suggestions).count() >= 1
