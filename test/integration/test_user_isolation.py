"""Per-user isolation through the MCP tools (Lift 1, ADR-048): under as_user(A) a tool
must see only A's rows; with no context every scoped tool fails CLOSED; and no tool
schema may expose a user_id parameter for the LLM to inject."""

import asyncio
from datetime import UTC, date, datetime
from uuid import UUID, uuid4

import pytest

from agentic_librarian.core.user_context import DEFAULT_USER_ID, as_user, current_user_id
from agentic_librarian.db.models import Author as AuthorModel
from agentic_librarian.db.models import (
    Edition,
    ReadingHistory,
    Suggestions,
    Trope,
    User,
    Work,
    WorkContributor,
    WorkTrope,
)
from agentic_librarian.db.session import DatabaseManager
from agentic_librarian.mcp import server as mcp_server

pytestmark = pytest.mark.db_integration

FRIEND_ID = UUID("00000000-0000-4000-8000-000000000002")


@pytest.fixture()
def scoped_db(db_url):
    """Point the MCP module at the test DB; seed one work read by BOTH users."""
    manager = DatabaseManager(db_url)
    original = mcp_server.db_manager
    mcp_server.set_db_manager(manager)
    with manager.get_session() as session:
        session.add(User(id=FRIEND_ID, email="friend@example.com"))
        author = AuthorModel(name="Frank Herbert")
        work = Work(title="Dune", contributors=[WorkContributor(author=author, role="Author")])
        edition = Edition(work=work, format="ebook")
        session.add_all([author, work, edition])
        session.flush()
        session.add(
            ReadingHistory(
                edition_id=edition.id, user_id=DEFAULT_USER_ID, date_completed=date(2020, 1, 1), user_rating=5
            )
        )
        session.add(
            ReadingHistory(edition_id=edition.id, user_id=FRIEND_ID, date_completed=date(2024, 6, 1), user_rating=3)
        )
        session.add(
            Suggestions(
                work_id=work.id,
                user_id=DEFAULT_USER_ID,
                justification="for me",
                suggested_at=datetime(2026, 6, 2, tzinfo=UTC),  # NEWEST — an unscoped .first() would pick THIS
            )
        )
        session.add(
            Suggestions(
                work_id=work.id,
                user_id=FRIEND_ID,
                justification="for friend",
                suggested_at=datetime(2026, 6, 1, tzinfo=UTC),
            )
        )
        # A second work read ONLY by the default user, carrying a trope — proves
        # trope preferences aggregate over MY history, not everyone's.
        solo_work = Work(title="Solo Book", contributors=[WorkContributor(author=author, role="Author")])
        solo_edition = Edition(work=solo_work, format="ebook")
        trope = Trope(name="Found Family")
        session.add_all([solo_work, solo_edition, trope])
        session.flush()
        session.add(WorkTrope(work_id=solo_work.id, trope_id=trope.id))
        session.add(
            ReadingHistory(edition_id=solo_edition.id, user_id=DEFAULT_USER_ID, date_completed=date(2021, 3, 3))
        )
        session.flush()
        work_id = str(work.id)
    yield work_id
    mcp_server.set_db_manager(original)


def test_check_reading_history_sees_only_my_rows(scoped_db):
    with as_user(DEFAULT_USER_ID):
        mine = mcp_server.check_reading_history("Dune", "Frank Herbert")
    assert mine["date_completed"] == "2020-01-01"  # NOT the friend's newer 2024 read
    with as_user(FRIEND_ID):
        theirs = mcp_server.check_reading_history("Dune", "Frank Herbert")
    assert theirs["date_completed"] == "2024-06-01"


def test_get_unacted_suggestions_isolated(scoped_db):
    with as_user(FRIEND_ID):
        result = mcp_server.get_unacted_suggestions([], [])
    assert [s["justification"] for s in result] == ["for friend"]


def test_update_suggestion_status_cannot_touch_other_users(scoped_db):
    with as_user(FRIEND_ID):
        result = mcp_server.update_suggestion_status(scoped_db, "Dismissed")
    assert result.startswith("Updated suggestion"), result  # positive: found THEIR OWN row
    # the DEFAULT user's (newer) suggestion must still be active — an unscoped query
    # would have dismissed it instead (mutation-proven test design)
    with as_user(DEFAULT_USER_ID):
        result = mcp_server.get_unacted_suggestions([], [])
    assert [s["justification"] for s in result] == ["for me"]


def test_get_user_trope_preferences_scoped(scoped_db):
    # 'Found Family' is attached to a work read ONLY by the default user.
    with as_user(DEFAULT_USER_ID):
        assert "Found Family" in mcp_server.get_user_trope_preferences()
    with as_user(FRIEND_ID):
        assert "Found Family" not in mcp_server.get_user_trope_preferences()


def test_add_book_duplicate_guard_is_per_user(scoped_db):
    """A friend logging a book on the SAME date the operator read it is NOT a duplicate
    (review finding: reads that gate writes must be user-scoped too)."""
    with as_user(FRIEND_ID):
        result = mcp_server.add_book_to_history("Dune", "Frank Herbert", date_completed="2020-01-01")
    assert result.startswith("Added"), result
    assert "read #2" in result  # friend already has the 2024 read — count is per-user


def test_scoped_tools_fail_closed_without_context(scoped_db):
    token = current_user_id.set(None)
    try:
        with pytest.raises(RuntimeError, match="No user identity"):
            mcp_server.check_reading_history("Dune", "Frank Herbert")
        with pytest.raises(RuntimeError, match="No user identity"):
            mcp_server.get_unacted_suggestions([], [])
        with pytest.raises(RuntimeError, match="No user identity"):
            mcp_server.get_user_trope_preferences()
        with pytest.raises(RuntimeError, match="No user identity"):
            mcp_server.update_suggestion_status(str(uuid4()), "Dismissed")
        with pytest.raises(RuntimeError, match="No user identity"):
            mcp_server.log_suggestion(str(uuid4()), "c", "j")
        with pytest.raises(RuntimeError, match="No user identity"):
            mcp_server.update_reading_status("Dune", "Frank Herbert", "read")
        with pytest.raises(RuntimeError, match="No user identity"):
            mcp_server.add_book_to_history("Dune", "Frank Herbert", date_completed="2019-01-01")
    finally:
        current_user_id.reset(token)


def test_no_tool_schema_exposes_user_id():
    """SEC-001 extension: the LLM must never see a user_id parameter to inject into."""
    tools = asyncio.run(mcp_server.mcp.list_tools())
    assert tools, "expected the FastMCP server to expose tools"
    for tool in tools:
        properties = (tool.inputSchema or {}).get("properties", {})
        assert "user_id" not in properties, f"{tool.name} exposes user_id to the LLM"


def test_check_reading_history_unread_when_only_friend_read_it(scoped_db):
    """Presence must not leak: if only the friend read it, I see Unread."""
    with as_user(FRIEND_ID):
        result = mcp_server.check_reading_history("Solo Book", "Frank Herbert")
    assert result["status"] == "Unread"
