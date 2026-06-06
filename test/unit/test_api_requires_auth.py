"""Every endpoint except /health requires a verified identity (Lift 1, ADR-048)."""

from agentic_librarian.api.main import app
from fastapi.testclient import TestClient

client = TestClient(app)


def test_health_stays_open():
    assert client.get("/health").status_code == 200


def test_health_db_requires_auth():
    assert client.get("/health/db").status_code == 401


def test_history_requires_auth():
    assert client.get("/history").status_code == 401


def test_works_requires_auth():
    assert client.get("/works").status_code == 401
