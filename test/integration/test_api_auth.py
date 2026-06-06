"""The auth dependency (Lift 1, ADR-048): verified-token → user resolution policy.
verify_id_token is FAKED here (the logic that is ours); the real-Firebase path is the
live-marked test + the rollout runbook (spec §2)."""

import asyncio

import pytest
from agentic_librarian.api import auth as auth_module
from agentic_librarian.core.user_context import DEFAULT_USER_ID, current_user_id
from agentic_librarian.db.models import User
from agentic_librarian.db.session import DatabaseManager
from fastapi import HTTPException

pytestmark = pytest.mark.db_integration


@pytest.fixture(autouse=True)
def _wire_db(db_url):
    manager = DatabaseManager(db_url)
    original = auth_module.db_manager
    auth_module.set_db_manager(manager)
    yield manager
    auth_module.set_db_manager(original)


@pytest.fixture()
def fake_claims(monkeypatch):
    def _install(claims):
        if isinstance(claims, Exception):
            def _raise(token):
                raise claims
            monkeypatch.setattr(auth_module, "_verify_token", _raise)
        else:
            monkeypatch.setattr(auth_module, "_verify_token", lambda token: claims)
    return _install


def _call(authorization):
    """Run the dependency and capture the context it set, INSIDE the same task —
    asyncio.run copies the context, so out-of-task assertions on current_user_id are
    vacuous (the suite-wide fixture already holds DEFAULT_USER_ID)."""

    async def _run():
        current_user_id.set(None)  # shed the fixture value in this task's context copy
        result = await auth_module.get_current_user(authorization=authorization)
        return result, current_user_id.get()

    return asyncio.run(_run())


def test_missing_header_401():
    with pytest.raises(HTTPException) as exc:
        _call(None)
    assert exc.value.status_code == 401


def test_non_bearer_header_401():
    with pytest.raises(HTTPException) as exc:
        _call("Basic abc123")
    assert exc.value.status_code == 401


def test_invalid_token_401(fake_claims):
    fake_claims(ValueError("bad token"))
    with pytest.raises(HTTPException) as exc:
        _call("Bearer broken")
    assert exc.value.status_code == 401


def test_known_firebase_uid_resolves_and_sets_context(_wire_db, fake_claims):
    with _wire_db.get_session() as session:
        user = session.query(User).filter(User.id == DEFAULT_USER_ID).one()
        user.firebase_uid = "fb-uid-justin"
        session.flush()
    fake_claims({"uid": "fb-uid-justin", "email": "jaydee829@gmail.com", "email_verified": True})
    result, ctx_user = _call("Bearer good")
    assert result.id == DEFAULT_USER_ID
    assert ctx_user == DEFAULT_USER_ID  # the dependency itself set the context (not the fixture)


def test_claim_by_email_links_invited_row(_wire_db, fake_claims):
    # the conftest-seeded default user has firebase_uid NULL — a verified matching
    # email claims it on first sign-in (case-insensitively)
    fake_claims({"uid": "fb-uid-new", "email": "JayDee829@Gmail.com", "email_verified": True})
    result, ctx_user = _call("Bearer good")
    assert result.id == DEFAULT_USER_ID
    assert ctx_user == DEFAULT_USER_ID
    with _wire_db.get_session() as session:
        assert session.get(User, DEFAULT_USER_ID).firebase_uid == "fb-uid-new"


def test_unverified_email_cannot_claim_invite(fake_claims, monkeypatch):
    monkeypatch.delenv("SIGNUP_MODE", raising=False)  # default: invite
    fake_claims({"uid": "fb-uid-spoof", "email": "jaydee829@gmail.com", "email_verified": False})
    with pytest.raises(HTTPException) as exc:
        _call("Bearer good")
    assert exc.value.status_code == 403


def test_unknown_identity_invite_mode_403(fake_claims, monkeypatch):
    monkeypatch.setenv("SIGNUP_MODE", "invite")
    fake_claims({"uid": "fb-uid-stranger", "email": "stranger@example.com", "email_verified": True})
    with pytest.raises(HTTPException) as exc:
        _call("Bearer good")
    assert exc.value.status_code == 403


def test_unknown_identity_open_mode_autocreates(_wire_db, fake_claims, monkeypatch):
    monkeypatch.setenv("SIGNUP_MODE", "open")
    fake_claims(
        {"uid": "fb-uid-stranger", "email": "Stranger@Example.com", "email_verified": True, "name": "Stra Nger"}
    )
    result, ctx_user = _call("Bearer good")
    assert result.email == "stranger@example.com"  # lowercased
    assert ctx_user == result.id  # context set to the newly created user
    with _wire_db.get_session() as session:
        row = session.query(User).filter(User.email == "stranger@example.com").one()
        assert row.firebase_uid == "fb-uid-stranger"
        assert row.display_name == "Stra Nger"


def test_open_mode_without_email_403(fake_claims, monkeypatch):
    monkeypatch.setenv("SIGNUP_MODE", "open")
    fake_claims({"uid": "fb-uid-anon", "email_verified": False})
    with pytest.raises(HTTPException) as exc:
        _call("Bearer good")
    assert exc.value.status_code == 403


def test_garbage_signup_mode_fails_toward_invite(fake_claims, monkeypatch):
    monkeypatch.setenv("SIGNUP_MODE", "wide-open-please")
    fake_claims({"uid": "fb-uid-stranger2", "email": "s2@example.com", "email_verified": True})
    with pytest.raises(HTTPException) as exc:
        _call("Bearer good")
    assert exc.value.status_code == 403


def test_cert_fetch_outage_is_503_not_401(fake_claims):
    from firebase_admin import auth as firebase_auth

    fake_claims(firebase_auth.CertificateFetchError("certs unreachable", cause=None))
    with pytest.raises(HTTPException) as exc:
        _call("Bearer good")
    assert exc.value.status_code == 503


def test_empty_bearer_token_401(fake_claims):
    fake_claims({"uid": "should-never-be-reached"})
    with pytest.raises(HTTPException) as exc:
        _call("Bearer   ")
    assert exc.value.status_code == 401


def test_recreated_firebase_account_cannot_reclaim_linked_row(_wire_db, fake_claims):
    """One-shot claim: once an invite row is linked to a uid, a NEW uid with the same
    email must NOT re-claim it (uid rotation is not an account-takeover path). The
    operator recovers a genuinely lost account manually (runbook)."""
    with _wire_db.get_session() as session:
        user = session.query(User).filter(User.id == DEFAULT_USER_ID).one()
        user.firebase_uid = "fb-uid-original"
        session.flush()
    fake_claims({"uid": "fb-uid-recreated", "email": "jaydee829@gmail.com", "email_verified": True})
    with pytest.raises(HTTPException) as exc:
        _call("Bearer good")
    assert exc.value.status_code == 403


def test_open_mode_unverified_email_present_403(fake_claims, monkeypatch):
    monkeypatch.setenv("SIGNUP_MODE", "open")
    fake_claims({"uid": "fb-uid-x", "email": "x@example.com", "email_verified": False})
    with pytest.raises(HTTPException) as exc:
        _call("Bearer good")
    assert exc.value.status_code == 403
