"""Migration 0002's data backfill: pre-multi-user rows land on DEFAULT_USER_ID
(Lift 1, ADR-048). Owns its own scratch DB — the conftest test DB is always at head."""

import os
from uuid import UUID

import pytest
from alembic.config import Config
from sqlalchemy import create_engine, text

from agentic_librarian.core.user_context import DEFAULT_USER_ID
from alembic import command

pytestmark = pytest.mark.db_integration

SCRATCH_DB = "alembic_backfill_test"


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


def test_migration_0002_backfills_existing_rows_onto_default_user(scratch_db_url):
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", scratch_db_url)
    command.upgrade(cfg, "+1")  # baseline only — the pre-multi-user world

    engine = create_engine(scratch_db_url)
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO works (id, title) VALUES ('11111111-1111-4111-8111-111111111111', 'T')"))
        conn.execute(
            text(
                "INSERT INTO editions (id, work_id, format) VALUES "
                "('22222222-2222-4222-8222-222222222222', '11111111-1111-4111-8111-111111111111', 'ebook')"
            )
        )
        conn.execute(
            text(
                "INSERT INTO reading_history (id, edition_id, date_completed) VALUES "
                "('33333333-3333-4333-8333-333333333333', '22222222-2222-4222-8222-222222222222', '2024-01-01')"
            )
        )

    command.upgrade(cfg, "head")  # the multi-user migration runs against existing data

    with engine.connect() as conn:
        user_id = conn.execute(text("SELECT user_id FROM reading_history")).scalar()
        assert UUID(str(user_id)) == DEFAULT_USER_ID
        email = conn.execute(text("SELECT email FROM users WHERE id = :i"), {"i": str(DEFAULT_USER_ID)}).scalar()
        assert email == "jaydee829@gmail.com"
    engine.dispose()
