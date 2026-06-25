from uuid import uuid4

import pytest

from agentic_librarian.core.user_context import as_user
from agentic_librarian.db.models import User, UserLibrary
from agentic_librarian.db.session import DatabaseManager
from agentic_librarian.mcp import server


def _user_with_library(session):
    user = User(id=uuid4(), email="t@example.com")
    session.add(user)
    session.add(
        UserLibrary(
            user_id=user.id,
            provider="libby",
            library_slug="kcls",
            display_name="KCLS",
            sort_order=0,
        )
    )
    session.flush()
    return user


@pytest.mark.db_integration
def test_check_availability_returns_links_and_badge(db_url, monkeypatch):
    from agentic_librarian.availability import service

    test_db_manager = DatabaseManager(db_url)
    server.set_db_manager(test_db_manager)

    with test_db_manager.get_session() as session:
        user = _user_with_library(session)
        session.commit()

    monkeypatch.setattr(
        service,
        "availability_for",
        lambda *a, **k: [{"format": "Audiobook", "available": True}],
    )
    with as_user(user.id):
        out = server.check_availability("Project Hail Mary", "Andy Weir")

    assert out["libraries"][0]["formats"][0]["available"] is True
    assert any(link["kind"] == "amazon" for link in out["links"])


def test_check_availability_rejects_bad_input():
    out = server.check_availability("", "Andy Weir")
    assert out["note"].startswith("Error")
    assert out["libraries"] == []
    assert out["links"] == []


@pytest.mark.db_integration
def test_check_availability_no_libraries_note(db_url):
    test_db_manager = DatabaseManager(db_url)
    server.set_db_manager(test_db_manager)

    with test_db_manager.get_session() as session:
        user = User(id=uuid4(), email="nolibrary@example.com")
        session.add(user)
        session.commit()

    with as_user(user.id):
        out = server.check_availability("Dune", "Frank Herbert")

    assert out["libraries"] == []
    assert any(link["kind"] == "amazon" for link in out["links"])  # links always present
    assert "Settings" in out["note"]  # the "no libraries saved" guidance
