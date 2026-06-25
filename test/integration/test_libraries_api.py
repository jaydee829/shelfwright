import pytest
from fastapi.testclient import TestClient

from agentic_librarian.api import auth
from agentic_librarian.api import libraries as libraries_mod
from agentic_librarian.api import main as api_main
from agentic_librarian.availability import overdrive
from agentic_librarian.core.user_context import DEFAULT_USER_EMAIL, DEFAULT_USER_ID
from agentic_librarian.db.session import DatabaseManager

pytestmark = pytest.mark.db_integration


@pytest.fixture
def client(db_url, monkeypatch):
    manager = DatabaseManager(db_url)
    monkeypatch.setattr(api_main, "db_manager", manager)
    monkeypatch.setattr(libraries_mod, "db_manager", manager)
    monkeypatch.setitem(
        api_main.app.dependency_overrides,
        auth.get_current_user,
        lambda: auth.AuthenticatedUser(id=DEFAULT_USER_ID, email=DEFAULT_USER_EMAIL),
    )
    yield TestClient(api_main.app)


def test_search_libraries_returns_list_from_overdrive(client, monkeypatch):
    """GET /libraries/search?q=king returns the list from a monkeypatched overdrive."""
    fake_results = [{"slug": "king-county", "name": "King County Library System"}]
    monkeypatch.setattr(overdrive, "search_libraries", lambda q: fake_results)

    resp = client.get("/libraries/search?q=king")
    assert resp.status_code == 200
    assert resp.json() == fake_results


def test_search_libraries_503_when_thunder_down(client, monkeypatch):
    """GET /libraries/search returns 503 when overdrive raises ThunderError."""
    from agentic_librarian.availability.overdrive import ThunderError

    def _raise(_q):
        raise ThunderError("down")

    monkeypatch.setattr(overdrive, "search_libraries", _raise)

    resp = client.get("/libraries/search?q=king")
    assert resp.status_code == 503


def test_put_then_get_libraries_round_trips_in_order(client, db_url):
    """PUT /me/libraries then GET /me/libraries preserves the full list in order."""
    payload = {
        "libraries": [
            {"slug": "seattle", "name": "Seattle Public Library"},
            {"slug": "nypl", "name": "New York Public Library"},
        ]
    }
    put_resp = client.put("/me/libraries", json=payload)
    assert put_resp.status_code == 200
    assert put_resp.json() == payload

    get_resp = client.get("/me/libraries")
    assert get_resp.status_code == 200
    assert get_resp.json() == payload


def test_put_libraries_replaces_previous_set(client, db_url):
    """A second PUT fully replaces the previous set (no stale rows remain)."""
    client.put(
        "/me/libraries",
        json={"libraries": [{"slug": "old-lib", "name": "Old Library"}]},
    )

    new_payload = {"libraries": [{"slug": "new-lib", "name": "New Library"}]}
    client.put("/me/libraries", json=new_payload)

    get_resp = client.get("/me/libraries")
    assert get_resp.status_code == 200
    assert get_resp.json() == new_payload


def test_get_libraries_empty_when_none_saved(client, db_url):
    """GET /me/libraries returns an empty list when the user has no saved libraries."""
    resp = client.get("/me/libraries")
    assert resp.status_code == 200
    assert resp.json() == {"libraries": []}


def test_put_libraries_422_on_duplicate_slugs(client):
    """PUT /me/libraries returns 422 when the request body contains duplicate slugs."""
    payload = {
        "libraries": [
            {"slug": "seattle", "name": "Seattle Public Library"},
            {"slug": "seattle", "name": "Seattle Public Library (duplicate)"},
        ]
    }
    resp = client.put("/me/libraries", json=payload)
    assert resp.status_code == 422
