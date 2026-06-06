import pytest
from datetime import date
from unittest.mock import MagicMock, patch

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


def test_get_history_empty():
    with patch("agentic_librarian.api.main.db_manager") as mock_db:
        mock_session = MagicMock()
        mock_db.get_session.return_value.__enter__.return_value = mock_session

        # Mock empty query result with the actual chain
        mock_query = mock_session.query.return_value
        mock_query.join.return_value = mock_query
        mock_query.filter.return_value = mock_query
        mock_query.options.return_value = mock_query
        mock_query.order_by.return_value = mock_query
        mock_query.all.return_value = []

        response = client.get("/history")
        assert response.status_code == 200
        assert response.json() == []


def test_get_history_with_data():
    with patch("agentic_librarian.api.main.db_manager") as mock_db:
        mock_session = MagicMock()
        mock_db.get_session.return_value.__enter__.return_value = mock_session

        # Setup mock data
        mock_history = MagicMock()
        mock_history.id = "test-id"
        mock_history.date_completed = date(2024, 1, 1)
        mock_history.user_rating = 5
        mock_history.edition.format = "hardcover"
        mock_history.edition.work.title = "Test Book"
        mock_contributor = MagicMock()
        mock_contributor.author.name = "Test Author"
        mock_contributor.role = "Author"
        mock_history.edition.work.contributors = [mock_contributor]

        # Mock the actual chain
        mock_query = mock_session.query.return_value
        mock_query.join.return_value = mock_query
        mock_query.filter.return_value = mock_query
        mock_query.options.return_value = mock_query
        mock_query.order_by.return_value = mock_query
        mock_query.all.return_value = [mock_history]

        response = client.get("/history")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["title"] == "Test Book"
        assert data[0]["authors"] == ["Test Author"]
        assert data[0]["date_completed"] == "2024-01-01"
        assert data[0]["rating"] == 5
        assert data[0]["format"] == "hardcover"


def test_get_history_no_date():
    with patch("agentic_librarian.api.main.db_manager") as mock_db:
        mock_session = MagicMock()
        mock_db.get_session.return_value.__enter__.return_value = mock_session

        # Setup mock data with None date
        mock_history = MagicMock()
        mock_history.id = "test-id"
        mock_history.date_completed = None
        mock_history.user_rating = None
        mock_history.edition.format = "eBook"
        mock_history.edition.work.title = "No Date Book"
        mock_contributor = MagicMock()
        mock_contributor.author.name = "Test Author"
        mock_contributor.role = "Author"
        mock_history.edition.work.contributors = [mock_contributor]

        mock_query = mock_session.query.return_value
        mock_query.join.return_value = mock_query
        mock_query.filter.return_value = mock_query
        mock_query.options.return_value = mock_query
        mock_query.order_by.return_value = mock_query
        mock_query.all.return_value = [mock_history]

        response = client.get("/history")
        assert response.status_code == 200
        data = response.json()
        assert data[0]["date_completed"] is None
