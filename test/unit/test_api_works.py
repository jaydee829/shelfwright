import pytest
from unittest.mock import MagicMock, patch
from uuid import uuid4

from agentic_librarian.api.auth import AuthenticatedUser, get_current_user
from agentic_librarian.api.main import app
from agentic_librarian.core.user_context import DEFAULT_USER_EMAIL, DEFAULT_USER_ID
from fastapi.testclient import TestClient

client = TestClient(app)


@pytest.fixture(autouse=True)
def _authed():
    """Endpoints are auth-gated (Lift 1) — these tests exercise the data layer, so
    inject a verified identity via FastAPI's dependency-override seam."""
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(
        id=DEFAULT_USER_ID, email=DEFAULT_USER_EMAIL
    )
    yield
    app.dependency_overrides.pop(get_current_user, None)


def _mock_chain(mock_session, results):
    """Wire the query().options().order_by().offset().limit().all() chain."""
    mock_query = mock_session.query.return_value
    mock_query.options.return_value = mock_query
    mock_query.order_by.return_value = mock_query
    mock_query.offset.return_value = mock_query
    mock_query.limit.return_value = mock_query
    mock_query.all.return_value = results
    return mock_query


def _mock_work():
    work = MagicMock()
    work.id = uuid4()
    work.title = "Dune"
    work.original_publication_year = 1965
    work.genres = ["Science Fiction"]
    work.moods = ["epic"]
    contributor = MagicMock()
    contributor.role = "Author"
    contributor.author.name = "Frank Herbert"
    work.contributors = [contributor]
    work_trope = MagicMock()
    work_trope.trope.name = "Chosen One"
    work.tropes = [work_trope]
    work_style = MagicMock()
    work_style.attribute_type = "perspective"
    work_style.style.name = "Third Person Limited"
    work.styles = [work_style]
    return work


def test_get_works_empty():
    with patch("agentic_librarian.api.main.db_manager") as mock_db:
        mock_session = MagicMock()
        mock_db.get_session.return_value.__enter__.return_value = mock_session
        _mock_chain(mock_session, [])

        response = client.get("/works")
        assert response.status_code == 200
        assert response.json() == []


def test_get_works_shape():
    with patch("agentic_librarian.api.main.db_manager") as mock_db:
        mock_session = MagicMock()
        mock_db.get_session.return_value.__enter__.return_value = mock_session
        work = _mock_work()
        _mock_chain(mock_session, [work])

        response = client.get("/works")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        entry = data[0]
        assert entry["id"] == str(work.id)
        assert entry["title"] == "Dune"
        assert entry["authors"] == ["Frank Herbert"]
        assert entry["publication_year"] == 1965
        assert entry["genres"] == ["Science Fiction"]
        assert entry["moods"] == ["epic"]
        assert entry["tropes"] == ["Chosen One"]
        assert entry["styles"] == [{"attribute": "perspective", "name": "Third Person Limited"}]


def test_get_works_null_arrays_become_empty_lists():
    with patch("agentic_librarian.api.main.db_manager") as mock_db:
        mock_session = MagicMock()
        mock_db.get_session.return_value.__enter__.return_value = mock_session
        work = _mock_work()
        work.genres = None
        work.moods = None
        _mock_chain(mock_session, [work])

        response = client.get("/works")
        entry = response.json()[0]
        assert entry["genres"] == []
        assert entry["moods"] == []


def test_get_works_pagination_params_forwarded():
    # Structural test: the db_integration test verifies actual paging against the real schema.
    with patch("agentic_librarian.api.main.db_manager") as mock_db:
        mock_session = MagicMock()
        mock_db.get_session.return_value.__enter__.return_value = mock_session
        mock_query = _mock_chain(mock_session, [])

        response = client.get("/works?limit=10&offset=20")
        assert response.status_code == 200
        mock_query.offset.assert_called_once_with(20)
        mock_query.limit.assert_called_once_with(10)


def test_get_works_limit_cap_enforced():
    # limit above 200 and below 1, and negative offset, are rejected by validation
    assert client.get("/works?limit=500").status_code == 422
    assert client.get("/works?limit=0").status_code == 422
    assert client.get("/works?offset=-1").status_code == 422
