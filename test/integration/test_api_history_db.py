"""/history returns ONLY the authenticated user's read events (Lift 1, ADR-048)."""

from datetime import date
from uuid import UUID

import pytest
from agentic_librarian.api import main as api_main
from agentic_librarian.api.auth import AuthenticatedUser, get_current_user
from agentic_librarian.core.user_context import DEFAULT_USER_ID
from agentic_librarian.db.models import Author as AuthorModel
from agentic_librarian.db.models import Edition, ReadingHistory, User, Work, WorkContributor
from agentic_librarian.db.session import DatabaseManager
from fastapi.testclient import TestClient

pytestmark = pytest.mark.db_integration

FRIEND_ID = UUID("00000000-0000-4000-8000-000000000002")


@pytest.fixture()
def two_user_client(db_url, monkeypatch):
    manager = DatabaseManager(db_url)
    monkeypatch.setattr(api_main, "db_manager", manager)
    with manager.get_session() as session:
        session.add(User(id=FRIEND_ID, email="friend@example.com"))
        author = AuthorModel(name="A. Uthor")
        work = Work(title="Shared Book", contributors=[WorkContributor(author=author, role="Author")])
        edition = Edition(work=work, format="ebook")
        session.add_all([author, work, edition])
        session.flush()
        session.add(ReadingHistory(edition_id=edition.id, user_id=DEFAULT_USER_ID, date_completed=date(2021, 1, 1)))
        session.add(ReadingHistory(edition_id=edition.id, user_id=FRIEND_ID, date_completed=date(2022, 2, 2)))
        session.flush()

    def _as(user_id, email):
        api_main.app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(id=user_id, email=email)
        return TestClient(api_main.app)

    yield _as
    api_main.app.dependency_overrides.pop(get_current_user, None)


def test_history_is_scoped_to_the_caller(two_user_client):
    mine = two_user_client(DEFAULT_USER_ID, "jaydee829@gmail.com").get("/history").json()
    assert [h["date_completed"] for h in mine] == ["2021-01-01"]
    theirs = two_user_client(FRIEND_ID, "friend@example.com").get("/history").json()
    assert [h["date_completed"] for h in theirs] == ["2022-02-02"]
