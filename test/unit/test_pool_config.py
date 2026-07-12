"""#102: pool flags on the engine; all in-process modules share the lifespan pool."""

from fastapi.testclient import TestClient

from agentic_librarian.db.session import DatabaseManager


def test_engine_pool_flags():
    # Postgres URL, lazily initialized: create_engine builds the pool WITHOUT connecting.
    m = DatabaseManager("postgresql+psycopg2://x:x@nohost:1/x")
    e = m.engine
    assert e.pool._pre_ping is True
    assert e.pool._recycle == 1800
    assert e.pool.size() == 5
    assert e.pool._max_overflow == 2


def test_lifespan_shares_one_manager_everywhere():
    from agentic_librarian.api import main as main_mod
    from agentic_librarian.enrichment import two_phase
    from agentic_librarian.imports import worker
    from agentic_librarian.mcp import server as mcp_server

    with TestClient(main_mod.app):
        shared = main_mod.app.state.db_manager
        assert main_mod.db_manager is shared
        assert mcp_server.db_manager is shared
        assert two_phase.db_manager is shared
        assert worker.db_manager is shared
