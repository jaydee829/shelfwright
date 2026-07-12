"""Startup migration guard (ADR-058): mismatch fails startup, unreachable DB does not."""

import pytest
from sqlalchemy import text

from agentic_librarian.db.migration_guard import (
    MigrationMismatchError,
    check_migrations,
    expected_head,
)
from agentic_librarian.db.session import DatabaseManager


@pytest.fixture()
def sqlite_manager(tmp_path):
    # File-based (NOT :memory:) so every new connection sees the same database.
    return DatabaseManager(f"sqlite:///{tmp_path}/guard.db")


def _stamp(manager, version):
    with manager.get_session() as s:
        s.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)"))
        s.execute(text("INSERT INTO alembic_version (version_num) VALUES (:v)"), {"v": version})


def test_env_off_skips_everything(monkeypatch):
    monkeypatch.setenv("MIGRATION_GUARD", "off")
    # Would raise on any real check (nonexistent config + unreachable DB) — off must short-circuit.
    check_migrations(DatabaseManager("postgresql://x:x@nohost:1/x"), config_path="no-such.ini")


def test_unreachable_db_warns_and_continues(monkeypatch, caplog):
    monkeypatch.setenv("MIGRATION_GUARD", "on")
    check_migrations(DatabaseManager("postgresql+psycopg2://x:x@nohost:1/x"))
    assert any("unreachable" in r.message for r in caplog.records)


def test_missing_alembic_version_table_raises(monkeypatch, sqlite_manager):
    monkeypatch.setenv("MIGRATION_GUARD", "on")
    with pytest.raises(MigrationMismatchError, match="not stamped"):
        check_migrations(sqlite_manager)


def test_version_mismatch_raises(monkeypatch, sqlite_manager):
    monkeypatch.setenv("MIGRATION_GUARD", "on")
    _stamp(sqlite_manager, "0000deadbeef")
    with pytest.raises(MigrationMismatchError, match="0000deadbeef"):
        check_migrations(sqlite_manager)


def test_matching_version_passes(monkeypatch, sqlite_manager):
    monkeypatch.setenv("MIGRATION_GUARD", "on")
    _stamp(sqlite_manager, expected_head())
    check_migrations(sqlite_manager)  # must not raise


def test_missing_config_raises(monkeypatch, sqlite_manager):
    monkeypatch.setenv("MIGRATION_GUARD", "on")
    with pytest.raises(Exception):  # noqa: B017 - any failure loading alembic config must be loud (packaging bug)
        check_migrations(sqlite_manager, config_path="does-not-exist.ini")


def test_lifespan_calls_guard(monkeypatch):
    """The app lifespan must run the guard before serving (ADR-058)."""
    from fastapi.testclient import TestClient

    from agentic_librarian.api import main as main_mod

    calls = []
    monkeypatch.setattr(main_mod, "check_migrations", lambda mgr: calls.append(mgr))
    with TestClient(main_mod.app):
        pass
    assert len(calls) == 1
