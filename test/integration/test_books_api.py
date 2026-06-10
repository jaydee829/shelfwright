from datetime import date, timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from agentic_librarian.api import auth
from agentic_librarian.api import books as books_mod
from agentic_librarian.api import main as api_main
from agentic_librarian.core.user_context import DEFAULT_USER_EMAIL, DEFAULT_USER_ID
from agentic_librarian.db.models import Author, Edition, ReadingHistory, User, Work, WorkContributor
from agentic_librarian.db.session import DatabaseManager
from agentic_librarian.enrichment import two_phase

pytestmark = pytest.mark.db_integration


@pytest.fixture
def client(db_url, monkeypatch):
    manager = DatabaseManager(db_url)
    monkeypatch.setattr(api_main, "db_manager", manager)
    monkeypatch.setattr(books_mod, "db_manager", manager)
    monkeypatch.setattr(two_phase, "db_manager", manager)
    monkeypatch.setitem(
        api_main.app.dependency_overrides,
        auth.get_current_user,
        lambda: auth.AuthenticatedUser(id=DEFAULT_USER_ID, email=DEFAULT_USER_EMAIL),
    )
    yield TestClient(api_main.app)


class _FakeManager:
    def __init__(self, result):
        self._result = result

    def enrich(self, title, author, format="Paperback", **kwargs):
        return self._result


def _stub_fast(monkeypatch, result):
    monkeypatch.setattr(two_phase, "create_fast_scout_manager", lambda: _FakeManager(result))


def test_add_book_persists_logs_and_enqueues(client, monkeypatch):
    enqueued = []
    monkeypatch.setattr(books_mod, "enqueue_enrichment", lambda wid: enqueued.append(wid) or True)
    _stub_fast(monkeypatch, {"title": "Project Hail Mary",
                             "contributors": [{"name": "Andy Weir", "role": "Author"}], "genres": [], "moods": []})

    resp = client.post("/books", json={"title": "Project Hail Mary", "author": "Andy Weir",
                                        "format": "ebook", "rating": 5})

    assert resp.status_code == 200
    body = resp.json()
    assert body["title"] == "Project Hail Mary"
    assert body["read_number"] == 1
    assert body["already_logged"] is False
    assert body["enrichment_enqueued"] is True
    assert enqueued == [body["work_id"]]  # newly created → deep pass enqueued


def test_add_book_not_found_returns_404(client, monkeypatch):
    monkeypatch.setattr(books_mod, "enqueue_enrichment", lambda wid: True)
    _stub_fast(monkeypatch, {})  # scouts find nothing

    resp = client.post("/books", json={"title": "Nope", "author": "Nobody"})
    assert resp.status_code == 404


def test_add_book_rereads_do_not_reenqueue(client, db_url, monkeypatch):
    calls = []
    monkeypatch.setattr(books_mod, "enqueue_enrichment", lambda wid: calls.append(wid) or True)
    manager = DatabaseManager(db_url)
    with manager.get_session() as s:
        work = Work(title="Dune")
        s.add(work)
        s.flush()
        a = Author(name="Frank Herbert")
        s.add(a)
        s.flush()
        s.add(WorkContributor(work_id=work.id, author_id=a.id, role="Author"))
        s.add(Edition(work_id=work.id, format="ebook"))
        s.flush()
    _stub_fast(monkeypatch, {"title": "Dune", "contributors": [{"name": "Frank Herbert", "role": "Author"}]})

    resp = client.post("/books", json={"title": "Dune", "author": "Frank Herbert",
                                       "format": "ebook", "date_completed": "2020-01-01"})
    assert resp.status_code == 200
    assert resp.json()["enrichment_enqueued"] is False  # de-dup hit → no re-enqueue
    assert calls == []


def test_add_book_rejects_future_date(client, monkeypatch):
    monkeypatch.setattr(books_mod, "enqueue_enrichment", lambda wid: True)
    _stub_fast(monkeypatch, {"title": "X", "contributors": [{"name": "Y", "role": "Author"}]})
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    resp = client.post("/books", json={"title": "X", "author": "Y", "date_completed": tomorrow})
    assert resp.status_code == 422


def test_add_book_rejects_blank_title(client):
    resp = client.post("/books", json={"title": "   ", "author": "Y"})
    assert resp.status_code == 422


def test_add_book_is_user_scoped(client, db_url, monkeypatch):
    # The read-event lands on the authenticated user, not another.
    monkeypatch.setattr(books_mod, "enqueue_enrichment", lambda wid: True)
    other = uuid4()
    manager = DatabaseManager(db_url)
    with manager.get_session() as s:
        s.add(User(id=other, email="other@example.com"))
        s.flush()
    _stub_fast(monkeypatch, {"title": "Hyperion", "contributors": [{"name": "Dan Simmons", "role": "Author"}]})

    client.post("/books", json={"title": "Hyperion", "author": "Dan Simmons"})
    with manager.get_session() as s:
        rows = s.query(ReadingHistory).filter(ReadingHistory.user_id == other).all()
        assert rows == []  # nothing logged to the other user
        mine = s.query(ReadingHistory).filter(ReadingHistory.user_id == DEFAULT_USER_ID).all()
        assert len(mine) == 1
