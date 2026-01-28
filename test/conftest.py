import os

import pytest
from dotenv import load_dotenv
from sqlalchemy import create_engine

load_dotenv()


def is_db_reachable():
    """Check if the database is reachable."""
    if os.getenv("SKIP_DB_TESTS"):
        return False

    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        user = os.getenv("POSTGRES_USER", "librarian")
        password = os.getenv("POSTGRES_PASSWORD", "librarian_secret_password")
        host = os.getenv("POSTGRES_HOST", "localhost")
        port = os.getenv("POSTGRES_PORT", "5432")
        db_name = os.getenv("POSTGRES_DB", "agentic_librarian")
        db_url = f"postgresql://{user}:{password}@{host}:{port}/{db_name}"

    try:
        engine = create_engine(db_url)
        with engine.connect():
            return True
    except Exception:
        return False


@pytest.fixture(scope="session")
def db_url():
    url = os.getenv("DATABASE_URL")
    if not url:
        user = os.getenv("POSTGRES_USER", "librarian")
        password = os.getenv("POSTGRES_PASSWORD", "librarian_secret_password")
        host = os.getenv("POSTGRES_HOST", "localhost")
        port = os.getenv("POSTGRES_PORT", "5432")
        db_name = os.getenv("POSTGRES_DB", "agentic_librarian")
        url = f"postgresql://{user}:{password}@{host}:{port}/{db_name}"
    return url


def pytest_configure(config):
    config.addinivalue_line("markers", "db_integration: mark test as requiring a live database (e.g. Docker)")


def pytest_collection_modifyitems(config, items):
    if is_db_reachable():
        return

    skip_db = pytest.mark.skip(reason="Database not reachable or SKIP_DB_TESTS set")
    for item in items:
        if "db_integration" in item.keywords:
            item.add_marker(skip_db)
