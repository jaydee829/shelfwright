from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from agentic_librarian.api import internal as internal_mod
from agentic_librarian.api import main as api_main
from agentic_librarian.db.models import Author, Edition, Trope, Work, WorkContributor, WorkTrope
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


def _seed_work(manager, *, with_real_trope=False):
    with manager.get_session() as s:
        work = Work(title="Dune")
        s.add(work)
        s.flush()
        a = Author(name="Frank Herbert")
        s.add(a)
        s.flush()
        s.add(WorkContributor(work_id=work.id, author_id=a.id, role="Author"))
        s.add(Edition(work_id=work.id, format="ebook"))
        if with_real_trope:
            # "Found Family" cleans to something outside this work's (empty) genres/moods, so
            # is_fallback_trope_name returns False — a genuine narrative trope.
            trope = Trope(name="Found Family")
            s.add(trope)
            s.flush()
            s.add(WorkTrope(work_id=work.id, trope_id=trope.id))
        s.flush()
        return work.id


def test_valid_queue_token_runs_deep_enrich(client, db_url, monkeypatch):
    manager = DatabaseManager(db_url)
    work_id = _seed_work(manager)
    monkeypatch.setattr(
        internal_mod, "_verify_oidc", lambda token, audience: {"email": QUEUE_SA, "email_verified": True}
    )
    called = {}

    def _fake_enrich_deep(wid):
        called["wid"] = wid
        return "done"

    monkeypatch.setattr(internal_mod.two_phase, "enrich_deep", _fake_enrich_deep)

    resp = client.post(f"/internal/enrich/{work_id}", headers={"Authorization": "Bearer good"})
    assert resp.status_code == 200
    assert resp.json() == {"work_id": str(work_id), "status": "enriched"}
    assert str(called["wid"]) == str(work_id)


def test_missing_token_is_rejected(client):
    resp = client.post(f"/internal/enrich/{uuid4()}")
    assert resp.status_code == 401


def test_wrong_service_account_is_forbidden(client, monkeypatch):
    monkeypatch.setattr(
        internal_mod, "_verify_oidc", lambda token, audience: {"email": "attacker@evil.com", "email_verified": True}
    )
    resp = client.post(f"/internal/enrich/{uuid4()}", headers={"Authorization": "Bearer x"})
    assert resp.status_code == 403


def test_bad_token_signature_is_forbidden(client, monkeypatch):
    def _boom(token, audience):
        raise ValueError("invalid signature")

    monkeypatch.setattr(internal_mod, "_verify_oidc", _boom)
    resp = client.post(f"/internal/enrich/{uuid4()}", headers={"Authorization": "Bearer x"})
    assert resp.status_code == 403


def test_unknown_work_returns_404(client, monkeypatch):
    monkeypatch.setattr(
        internal_mod, "_verify_oidc", lambda token, audience: {"email": QUEUE_SA, "email_verified": True}
    )
    monkeypatch.setattr(internal_mod.two_phase, "enrich_deep", lambda wid: "missing")
    resp = client.post(f"/internal/enrich/{uuid4()}", headers={"Authorization": "Bearer good"})
    assert resp.status_code == 404


def test_empty_deep_pass_on_tropeless_work_returns_503(client, db_url, monkeypatch):
    """GH #97: a work with no real trope after an empty deep pass is a retryable poison
    task — Cloud Tasks must see a 5xx so it retries with backoff."""
    manager = DatabaseManager(db_url)
    work_id = _seed_work(manager, with_real_trope=False)
    monkeypatch.setattr(
        internal_mod, "_verify_oidc", lambda token, audience: {"email": QUEUE_SA, "email_verified": True}
    )
    monkeypatch.setattr(internal_mod.two_phase, "enrich_deep", lambda wid: "empty")

    resp = client.post(f"/internal/enrich/{work_id}", headers={"Authorization": "Bearer good"})
    assert resp.status_code == 503
    assert resp.json() == {"detail": {"work_id": str(work_id), "status": "empty_deep_pass"}}


def test_empty_deep_pass_on_work_with_real_trope_returns_200(client, db_url, monkeypatch):
    """GH #97: an empty pass on a work that already has a real trope from a prior pass is
    NOT a failure — it just means this pass added nothing new. Don't retry forever."""
    manager = DatabaseManager(db_url)
    work_id = _seed_work(manager, with_real_trope=True)
    monkeypatch.setattr(
        internal_mod, "_verify_oidc", lambda token, audience: {"email": QUEUE_SA, "email_verified": True}
    )
    monkeypatch.setattr(internal_mod.two_phase, "enrich_deep", lambda wid: "empty")

    resp = client.post(f"/internal/enrich/{work_id}", headers={"Authorization": "Bearer good"})
    assert resp.status_code == 200
    assert resp.json() == {"work_id": str(work_id), "status": "already_enriched"}


def test_unverified_email_is_forbidden(client, monkeypatch):
    # Mutation guard: a token with the correct SA email but email_verified=False must still 403.
    # Deleting the `not claims.get("email_verified")` check would let this through.
    monkeypatch.setattr(
        internal_mod, "_verify_oidc", lambda token, audience: {"email": QUEUE_SA, "email_verified": False}
    )
    resp = client.post(f"/internal/enrich/{uuid4()}", headers={"Authorization": "Bearer x"})
    assert resp.status_code == 403


def test_unconfigured_audience_is_forbidden(client, monkeypatch):
    # Fail-closed: without ENRICH_OIDC_AUDIENCE, google-auth would skip audience verification.
    # Mutation guard: _verify_oidc is mocked to SUCCEED, so if the `or not audience` config guard
    # were removed, the call would pass verification and reach enrich_deep → 404. A 403 here proves
    # the config guard fired BEFORE verification.
    monkeypatch.delenv("ENRICH_OIDC_AUDIENCE", raising=False)
    monkeypatch.setattr(
        internal_mod, "_verify_oidc", lambda token, audience: {"email": QUEUE_SA, "email_verified": True}
    )
    resp = client.post(f"/internal/enrich/{uuid4()}", headers={"Authorization": "Bearer x"})
    assert resp.status_code == 403
