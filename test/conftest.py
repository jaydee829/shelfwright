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

    # 2. Ensure the pgvector extension and ORM schema exist in the test database.
    from agentic_librarian.db.models import Base

    engine = create_engine(_test_db_url())
    with engine.begin() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    Base.metadata.create_all(engine)
    engine.dispose()
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
    engine.dispose()
    yield


def pytest_configure(config):
    config.addinivalue_line("markers", "db_integration: mark test as requiring a live database (e.g. Docker)")


def pytest_collection_modifyitems(config, items):
    if is_db_reachable():
        return

    skip_db = pytest.mark.skip(reason="Database not reachable or SKIP_DB_TESTS set")
    for item in items:
        if "db_integration" in item.keywords:
            item.add_marker(skip_db)
