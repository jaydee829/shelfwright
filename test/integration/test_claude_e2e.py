import os

import pytest

from agentic_librarian.db.session import DatabaseManager
from agentic_librarian.mcp.server import set_db_manager
from test.integration.seed_helpers import seed_recommendation_fixture

pytest.importorskip("claude_agent_sdk")  # the `claude` optional extra; skip if not installed

from agentic_librarian.agents.backends.claude import ClaudeBackend  # noqa: E402


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
