import pytest
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


def test_health_check():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_db_connection_health():
    with patch("agentic_librarian.api.main.db_manager") as mock_db:
        mock_session = MagicMock()
        mock_db.get_session.return_value.__enter__.return_value = mock_session

        response = client.get("/health/db")
        assert response.status_code == 200
        assert response.json()["status"] == "connected"


def test_db_connection_health_failure_returns_503():
    with patch("agentic_librarian.api.main.db_manager") as mock_db:
        mock_db.get_session.side_effect = Exception("connection refused")

        response = client.get("/health/db")
        assert response.status_code == 503
        assert response.json()["status"] == "error"
        assert "connection refused" in response.json()["detail"]
