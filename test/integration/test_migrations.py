"""Prove `alembic upgrade head` builds the complete schema from an empty database
(Lift 1, ADR-048). The conftest also builds the test DB via Alembic, but this test
pins the property explicitly and owns its own scratch database."""

import os

import pytest
from agentic_librarian.db.models import Base
from sqlalchemy import create_engine, inspect, text

from alembic import command
from alembic.config import Config

pytestmark = pytest.mark.db_integration

SCRATCH_DB = "alembic_migration_test"


def _server_url(db_name: str) -> str:
    user = os.getenv("POSTGRES_USER", "librarian")
    password = os.getenv("POSTGRES_PASSWORD", "librarian_secret_password")
    host = os.getenv("POSTGRES_HOST", "localhost")
    port = os.getenv("POSTGRES_PORT", "5432")
    return f"postgresql://{user}:{password}@{host}:{port}/{db_name}"


@pytest.fixture()
def scratch_db_url():
    admin = create_engine(_server_url("postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{SCRATCH_DB}" WITH (FORCE)'))
        conn.execute(text(f'CREATE DATABASE "{SCRATCH_DB}"'))
    admin.dispose()
    yield _server_url(SCRATCH_DB)
    admin = create_engine(_server_url("postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{SCRATCH_DB}" WITH (FORCE)'))
    admin.dispose()


def test_upgrade_head_builds_full_schema(scratch_db_url):
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", scratch_db_url)
    command.upgrade(cfg, "head")

    engine = create_engine(scratch_db_url)
    tables = set(inspect(engine).get_table_names())
    engine.dispose()
    expected = set(Base.metadata.tables) | {"alembic_version"}
    assert tables == expected


def test_upgrade_head_matches_models_exactly(scratch_db_url):
    """Column-level fidelity: an empty autogenerate diff between the migrated schema
    and Base.metadata. The baseline is frozen the moment it is stamped onto prod —
    this is the last automated gate against a missing column/constraint (and it will
    guard every future hand-written migration the same way)."""
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext

    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", scratch_db_url)
    command.upgrade(cfg, "head")

    engine = create_engine(scratch_db_url)
    with engine.connect() as conn:
        ctx = MigrationContext.configure(conn)
        diff = compare_metadata(ctx, Base.metadata)
    engine.dispose()
    assert diff == [], f"schema drift between migrations and models: {diff}"
