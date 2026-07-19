"""OIDC gate + status mapping for the edition-completion internal endpoint.

Mirrors test_internal_enrich_api.py: db_integration because the FastAPI app import
chain needs real settings, but complete_edition itself is monkeypatched — the pass's
own behavior is covered by test/unit/test_edition_completion.py."""

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from agentic_librarian.api import internal as internal_mod
from agentic_librarian.api import main as api_main

pytestmark = pytest.mark.db_integration

VALID_AUD = "https://librarian.example.run.app/internal/enrich/x"
QUEUE_SA = "queue-invoker@p.iam.gserviceaccount.com"


@pytest.fixture
def client(db_url, monkeypatch):
    monkeypatch.setenv("ENRICH_INVOKER_SA", QUEUE_SA)
    monkeypatch.setenv("ENRICH_OIDC_AUDIENCE", VALID_AUD)
    yield TestClient(api_main.app)


def _as_queue(monkeypatch):
    monkeypatch.setattr(
        internal_mod, "_verify_oidc", lambda token, audience: {"email": QUEUE_SA, "email_verified": True}
    )


def test_valid_queue_token_runs_completion(client, monkeypatch):
    _as_queue(monkeypatch)
    called = {}

    def fake_complete(wid, fmt):
        called["args"] = (wid, fmt)
        return "done"

    monkeypatch.setattr(internal_mod.two_phase, "complete_edition", fake_complete)
    wid = uuid4()
    resp = client.post(f"/internal/complete-edition/{wid}?format=audiobook", headers={"Authorization": "Bearer ok"})
    assert resp.status_code == 200
    assert resp.json() == {"work_id": str(wid), "format": "audiobook", "status": "done"}
    assert called["args"] == (wid, "audiobook")


def test_missing_work_is_404_non_retryable(client, monkeypatch):
    _as_queue(monkeypatch)
    monkeypatch.setattr(internal_mod.two_phase, "complete_edition", lambda wid, fmt: "missing")
    resp = client.post(f"/internal/complete-edition/{uuid4()}?format=ebook", headers={"Authorization": "Bearer ok"})
    assert resp.status_code == 404


def test_empty_scouts_is_200_final(client, monkeypatch):
    _as_queue(monkeypatch)
    monkeypatch.setattr(internal_mod.two_phase, "complete_edition", lambda wid, fmt: "empty")
    resp = client.post(f"/internal/complete-edition/{uuid4()}?format=ebook", headers={"Authorization": "Bearer ok"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "empty"


def test_missing_token_is_401(client):
    assert client.post(f"/internal/complete-edition/{uuid4()}?format=ebook").status_code == 401


def test_wrong_service_account_is_403(client, monkeypatch):
    monkeypatch.setattr(
        internal_mod, "_verify_oidc", lambda token, audience: {"email": "attacker@evil.com", "email_verified": True}
    )
    resp = client.post(f"/internal/complete-edition/{uuid4()}?format=ebook", headers={"Authorization": "Bearer x"})
    assert resp.status_code == 403


def test_missing_format_param_is_422(client, monkeypatch):
    _as_queue(monkeypatch)
    resp = client.post(f"/internal/complete-edition/{uuid4()}", headers={"Authorization": "Bearer ok"})
    assert resp.status_code == 422
