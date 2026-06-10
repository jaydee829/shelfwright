from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from agentic_librarian.api import internal as internal_mod
from agentic_librarian.api import main as api_main
from agentic_librarian.db.models import Author, Edition, Work, WorkContributor
from agentic_librarian.db.session import DatabaseManager
from agentic_librarian.enrichment import two_phase

pytestmark = pytest.mark.db_integration

VALID_AUD = "https://librarian.example.run.app/internal/enrich/x"
QUEUE_SA = "queue-invoker@p.iam.gserviceaccount.com"


@pytest.fixture
def client(db_url, monkeypatch):
    manager = DatabaseManager(db_url)
    monkeypatch.setattr(two_phase, "db_manager", manager)
    monkeypatch.setenv("ENRICH_INVOKER_SA", QUEUE_SA)
    monkeypatch.setenv("ENRICH_OIDC_AUDIENCE", VALID_AUD)
    yield TestClient(api_main.app)


def _seed_work(manager):
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
        return work.id


def test_valid_queue_token_runs_deep_enrich(client, db_url, monkeypatch):
    manager = DatabaseManager(db_url)
    work_id = _seed_work(manager)
    monkeypatch.setattr(internal_mod, "_verify_oidc",
                        lambda token, audience: {"email": QUEUE_SA, "email_verified": True})
    called = {}
    monkeypatch.setattr(internal_mod.two_phase, "enrich_deep",
                        lambda wid: called.setdefault("wid", wid) or True)

    resp = client.post(f"/internal/enrich/{work_id}", headers={"Authorization": "Bearer good"})
    assert resp.status_code == 200
    assert str(called["wid"]) == str(work_id)


def test_missing_token_is_rejected(client):
    resp = client.post(f"/internal/enrich/{uuid4()}")
    assert resp.status_code == 401


def test_wrong_service_account_is_forbidden(client, monkeypatch):
    monkeypatch.setattr(internal_mod, "_verify_oidc",
                        lambda token, audience: {"email": "attacker@evil.com", "email_verified": True})
    resp = client.post(f"/internal/enrich/{uuid4()}", headers={"Authorization": "Bearer x"})
    assert resp.status_code == 403


def test_bad_token_signature_is_forbidden(client, monkeypatch):
    def _boom(token, audience):
        raise ValueError("invalid signature")

    monkeypatch.setattr(internal_mod, "_verify_oidc", _boom)
    resp = client.post(f"/internal/enrich/{uuid4()}", headers={"Authorization": "Bearer x"})
    assert resp.status_code == 403


def test_unknown_work_returns_404(client, monkeypatch):
    monkeypatch.setattr(internal_mod, "_verify_oidc",
                        lambda token, audience: {"email": QUEUE_SA, "email_verified": True})
    monkeypatch.setattr(internal_mod.two_phase, "enrich_deep", lambda wid: False)
    resp = client.post(f"/internal/enrich/{uuid4()}", headers={"Authorization": "Bearer good"})
    assert resp.status_code == 404


def test_unverified_email_is_forbidden(client, monkeypatch):
    # Mutation guard: a token with the correct SA email but email_verified=False must still 403.
    # Deleting the `not claims.get("email_verified")` check would let this through.
    monkeypatch.setattr(internal_mod, "_verify_oidc",
                        lambda token, audience: {"email": QUEUE_SA, "email_verified": False})
    resp = client.post(f"/internal/enrich/{uuid4()}", headers={"Authorization": "Bearer x"})
    assert resp.status_code == 403
