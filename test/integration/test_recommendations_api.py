from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from agentic_librarian.api import auth
from agentic_librarian.api import main as api_main
from agentic_librarian.api import recommendations as rec_mod
from agentic_librarian.core.user_context import DEFAULT_USER_EMAIL, DEFAULT_USER_ID
from agentic_librarian.db.models import Author, Suggestions, User, Work, WorkContributor
from agentic_librarian.db.session import DatabaseManager

pytestmark = pytest.mark.db_integration


@pytest.fixture
def client(db_url, monkeypatch):
    manager = DatabaseManager(db_url)
    monkeypatch.setattr(api_main, "db_manager", manager)
    monkeypatch.setattr(rec_mod, "db_manager", manager)
    monkeypatch.setitem(
        api_main.app.dependency_overrides,
        auth.get_current_user,
        lambda: auth.AuthenticatedUser(id=DEFAULT_USER_ID, email=DEFAULT_USER_EMAIL),
    )
    yield TestClient(api_main.app)


def _seed_suggestion(manager, *, user_id, title, author, status="Suggested", justification="because"):
    with manager.get_session() as s:
        work = Work(title=title)
        s.add(work)
        s.flush()
        a = Author(name=author)
        s.add(a)
        s.flush()
        s.add(WorkContributor(work_id=work.id, author_id=a.id, role="Author"))
        sug = Suggestions(work_id=work.id, user_id=user_id, status=status, justification=justification)
        s.add(sug)
        s.flush()
        return sug.id, work.id


def test_lists_only_active_suggestions_for_the_user(client, db_url):
    manager = DatabaseManager(db_url)
    sid, wid = _seed_suggestion(manager, user_id=DEFAULT_USER_ID, title="Dune", author="Herbert")
    _seed_suggestion(manager, user_id=DEFAULT_USER_ID, title="Old Pick", author="X", status="Dismissed")

    body = client.get("/recommendations").json()

    assert [r["title"] for r in body] == ["Dune"]  # Dismissed one excluded
    row = body[0]
    assert row["id"] == str(sid)
    assert row["work_id"] == str(wid)
    assert row["authors"] == ["Herbert"]
    assert row["justification"] == "because"
    assert row["status"] == "Suggested"


def test_does_not_leak_another_users_suggestions(client, db_url):
    manager = DatabaseManager(db_url)
    other_id = uuid4()
    with manager.get_session() as s:
        s.add(User(id=other_id, email="other@example.com"))
        s.flush()
    _seed_suggestion(manager, user_id=other_id, title="Secret", author="Nobody")

    body = client.get("/recommendations").json()

    assert body == []  # the default user sees none of the other user's suggestions


def test_dismiss_marks_status_and_removes_from_list(client, db_url):
    manager = DatabaseManager(db_url)
    sid, _ = _seed_suggestion(manager, user_id=DEFAULT_USER_ID, title="Meh", author="Z")

    resp = client.post(f"/recommendations/{sid}/status", json={"status": "Dismissed"})
    assert resp.status_code == 200
    assert resp.json() == {"id": str(sid), "status": "Dismissed"}

    assert client.get("/recommendations").json() == []  # no longer active


def test_rejects_unknown_status(client, db_url):
    manager = DatabaseManager(db_url)
    sid, _ = _seed_suggestion(manager, user_id=DEFAULT_USER_ID, title="Meh", author="Z")

    resp = client.post(f"/recommendations/{sid}/status", json={"status": "Banished"})
    assert resp.status_code == 422


def test_cannot_dismiss_another_users_suggestion(client, db_url):
    manager = DatabaseManager(db_url)
    other_id = uuid4()
    with manager.get_session() as s:
        s.add(User(id=other_id, email="other2@example.com"))
        s.flush()
    sid, _ = _seed_suggestion(manager, user_id=other_id, title="Secret", author="Nobody")

    resp = client.post(f"/recommendations/{sid}/status", json={"status": "Dismissed"})
    assert resp.status_code == 404  # scoping: not the caller's suggestion


def test_mark_read_removes_from_active_list(client, db_url):
    manager = DatabaseManager(db_url)
    sid, _ = _seed_suggestion(manager, user_id=DEFAULT_USER_ID, title="Read It", author="Q")

    resp = client.post(f"/recommendations/{sid}/status", json={"status": "Read"})
    assert resp.status_code == 200
    assert resp.json() == {"id": str(sid), "status": "Read"}
    assert client.get("/recommendations").json() == []  # no longer "Suggested"
