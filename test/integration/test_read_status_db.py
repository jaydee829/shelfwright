"""get_read_status: batch read-status by work id (Lift 2 rec novelty/labels)."""

from datetime import date, timedelta
from uuid import uuid4

import pytest

from agentic_librarian.core.user_context import DEFAULT_USER_ID, as_user
from agentic_librarian.db.models import Author as AuthorModel
from agentic_librarian.db.models import Edition, ReadingHistory, Work, WorkContributor
from agentic_librarian.db.session import DatabaseManager
from agentic_librarian.mcp import server as mcp_server

pytestmark = pytest.mark.db_integration


@pytest.fixture()
def seeded_status_works(db_url):
    """Point the MCP module at the test DB; seed an old read (>2y), a recent read (<2y),
    and an unread catalog work — all under the default user."""
    manager = DatabaseManager(db_url)
    original = mcp_server.db_manager
    mcp_server.set_db_manager(manager)
    today = date.today()
    with manager.get_session() as session:
        author = AuthorModel(name="Read Status Author")
        session.add(author)
        session.flush()

        def make_work(title):
            work = Work(title=title, contributors=[WorkContributor(author=author, role="Author")])
            edition = Edition(work=work, format="ebook")
            session.add_all([work, edition])
            session.flush()
            return work, edition

        old_work, old_ed = make_work("Old Read")
        recent_work, recent_ed = make_work("Recent Read")
        unread_work, _ = make_work("Never Read")
        session.add(
            ReadingHistory(
                edition_id=old_ed.id, user_id=DEFAULT_USER_ID,
                date_completed=today - timedelta(days=int(3 * 365.25)), user_rating=5,
            )
        )
        session.add(
            ReadingHistory(
                edition_id=recent_ed.id, user_id=DEFAULT_USER_ID, date_completed=today - timedelta(days=200)
            )
        )
        session.flush()
        ids = {"old_read": str(old_work.id), "recent_read": str(recent_work.id), "unread": str(unread_work.id)}
    yield ids
    mcp_server.set_db_manager(original)


def test_get_read_status_partitions_read_unread_and_recent(seeded_status_works):
    ids = seeded_status_works
    with as_user(DEFAULT_USER_ID):
        status = mcp_server.get_read_status(list(ids.values()))
    assert status[ids["old_read"]]["status"] == "Read"
    assert status[ids["old_read"]]["is_re_read_candidate"] is True
    assert status[ids["old_read"]]["rating"] == 5
    assert status[ids["old_read"]]["last_read"] is not None
    assert status[ids["recent_read"]]["status"] == "Read"
    assert status[ids["recent_read"]]["is_re_read_candidate"] is False
    assert status[ids["unread"]]["status"] == "Unread"
    assert status[ids["unread"]]["is_re_read_candidate"] is True
    assert status[ids["unread"]]["last_read"] is None


def test_get_read_status_unknown_ids_are_unread():
    with as_user(DEFAULT_USER_ID):
        status = mcp_server.get_read_status([str(uuid4())])
    only = next(iter(status.values()))
    assert only["status"] == "Unread"
