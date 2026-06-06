from unittest.mock import MagicMock, patch

from agentic_librarian.api.main import app
from fastapi.testclient import TestClient

client = TestClient(app)


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
