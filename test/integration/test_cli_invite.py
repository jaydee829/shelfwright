"""Operator invite tooling (Lift 1, ADR-048): adding a friend is a command, not psql."""

import pytest
from agentic_librarian.cli import main
from agentic_librarian.db.models import User
from agentic_librarian.db.session import DatabaseManager

pytestmark = pytest.mark.db_integration


@pytest.fixture(autouse=True)
def _cli_db(db_url, monkeypatch):
    manager = DatabaseManager(db_url)
    monkeypatch.setattr("agentic_librarian.cli._invite_db_manager", lambda: manager)
    yield manager


def test_invite_creates_lowercased_row(_cli_db, capsys):
    assert main(["user", "invite", "Friend@Example.COM", "--name", "Pat"]) == 0
    with _cli_db.get_session() as session:
        row = session.query(User).filter(User.email == "friend@example.com").one()
        assert row.firebase_uid is None
        assert row.display_name == "Pat"
    assert "Invited friend@example.com" in capsys.readouterr().out


def test_invite_existing_email_is_idempotent(_cli_db, capsys):
    assert main(["user", "invite", "friend@example.com"]) == 0
    assert main(["user", "invite", "friend@example.com"]) == 0
    out = capsys.readouterr().out
    assert "already exists" in out
    with _cli_db.get_session() as session:
        assert session.query(User).filter(User.email == "friend@example.com").count() == 1


def test_invite_rejects_non_email(capsys):
    assert main(["user", "invite", "not-an-email"]) == 2
