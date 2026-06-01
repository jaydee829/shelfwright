import os
from test.integration.seed_helpers import seed_recommendation_fixture

import pytest
from agentic_librarian.agents.backends.claude import ClaudeBackend
from agentic_librarian.db.session import DatabaseManager
from agentic_librarian.mcp.server import set_db_manager


@pytest.mark.api_dependent
@pytest.mark.db_integration
@pytest.mark.skipif("claude" not in os.environ.get("CLAUDE_E2E", ""), reason="set CLAUDE_E2E=claude to run")
def test_claude_backend_live(db_url):
    dbm = DatabaseManager(db_url)
    set_db_manager(dbm)
    with dbm.get_session() as session:
        seed_recommendation_fixture(session)
    result = ClaudeBackend().run_recommendation("a slow-burn enemies-to-lovers romance")
    assert isinstance(result, str) and len(result.strip()) > 30
