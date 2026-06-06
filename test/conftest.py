import os

import pytest
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

load_dotenv()


def _server_components():
    user = os.getenv("POSTGRES_USER", "librarian")
    password = os.getenv("POSTGRES_PASSWORD", "librarian_secret_password")
    host = os.getenv("POSTGRES_HOST", "localhost")
    port = os.getenv("POSTGRES_PORT", "5432")
    return user, password, host, port


def _test_db_name():
    """Dedicated test database, isolated from the application database (ADR-034)."""
    return os.getenv("TEST_POSTGRES_DB") or f"{os.getenv('POSTGRES_DB', 'agentic_librarian')}_test"


def _server_url(db_name="postgres"):
    user, password, host, port = _server_components()
    return f"postgresql://{user}:{password}@{host}:{port}/{db_name}"


def _test_db_url():
    override = os.getenv("TEST_DATABASE_URL")
    if override:
        return override
    return _server_url(_test_db_name())


def is_db_reachable():
    """Check whether the Postgres *server* is reachable (not a specific database)."""
    if os.getenv("SKIP_DB_TESTS"):
        return False
    try:
        engine = create_engine(_server_url())
        with engine.connect():
            return True
    except Exception:
        return False


@pytest.fixture(scope="session")
def db_url():
    """URL of the dedicated test database (never the application database)."""
    return _test_db_url()


@pytest.fixture(scope="session", autouse=True)
def _create_test_database():
    """Create the dedicated test database, extension, and schema once per session (ADR-034)."""
    if not is_db_reachable():
        yield
        return

    # 1. Create the test database if it does not exist (CREATE DATABASE needs autocommit).
    db_name = _test_db_name()
    server_engine = create_engine(_server_url(), isolation_level="AUTOCOMMIT")
    with server_engine.connect() as conn:
        exists = conn.execute(text("SELECT 1 FROM pg_database WHERE datname = :n"), {"n": db_name}).scalar()
        if not exists:
            conn.execute(text(f'CREATE DATABASE "{db_name}"'))
    server_engine.dispose()

    # 2. Build the schema via Alembic (Lift 1, ADR-048): every CI run proves the
    #    migrations construct a correct schema from scratch. (CREATE EXTENSION also
    #    lives in the baseline migration; doing it here too is harmless and keeps
    #    this fixture self-sufficient.)
    from alembic import command
    from alembic.config import Config

    engine = create_engine(_test_db_url())
    with engine.begin() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    engine.dispose()

    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", _test_db_url())
    command.upgrade(cfg, "head")
    yield


@pytest.fixture(autouse=True)
def _clean_db_tables(request):
    """Truncate all tables before each db_integration test for deterministic isolation (ADR-034)."""
    if "db_integration" not in request.keywords or not is_db_reachable():
        yield
        return

    from agentic_librarian.db.models import Base

    sorted_tables = Base.metadata.sorted_tables
    if not sorted_tables:
        yield
        return

    engine = create_engine(_test_db_url())
    tables = ", ".join(f'"{t.name}"' for t in sorted_tables)
    with engine.begin() as conn:
        conn.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
        # Reseed the default user (Migration 0002 inserts it; TRUNCATE removed it).
        conn.execute(
            text(
                "INSERT INTO users (id, email, display_name, created_at) "
                "VALUES (:id, 'jaydee829@gmail.com', 'Justin', now())"
            ),
            {"id": "00000000-0000-4000-8000-000000000001"},
        )
    engine.dispose()
    yield


def pytest_configure(config):
    config.addinivalue_line("markers", "db_integration: mark test as requiring a live database (e.g. Docker)")


@pytest.fixture(autouse=True)
def _default_user_context():
    """Every test runs as the default user (Lift 1, ADR-048) — mirroring the CLI/dev
    entrypoints. Isolation tests use as_user(other); fail-closed tests set None."""
    from agentic_librarian.core.user_context import DEFAULT_USER_ID, current_user_id

    token = current_user_id.set(DEFAULT_USER_ID)
    yield
    current_user_id.reset(token)


def pytest_collection_modifyitems(config, items):
    if is_db_reachable():
        return

    skip_db = pytest.mark.skip(reason="Database not reachable or SKIP_DB_TESTS set")
    for item in items:
        if "db_integration" in item.keywords:
            item.add_marker(skip_db)
