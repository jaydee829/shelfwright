"""Firebase Auth verification + signup policy for the FastAPI layer (Lift 1, ADR-048).

Trust boundary: identity comes ONLY from a verified Firebase ID token. The decoded
token's uid/email drive user resolution; SIGNUP_MODE decides what happens to verified
strangers (invite → 403, open → auto-create). On success the user context is set —
the same channel the MCP tools read (core/user_context.py).

401 = missing/invalid/expired token. 403 = valid identity, not invited."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import threading
from typing import NamedTuple
from uuid import UUID

import firebase_admin
from fastapi import Header, HTTPException
from firebase_admin import auth as firebase_auth
from sqlalchemy.exc import IntegrityError

from agentic_librarian.core.user_context import current_user_id
from agentic_librarian.db.models import User
from agentic_librarian.db.session import DatabaseManager

logger = logging.getLogger(__name__)

db_manager = DatabaseManager()
_firebase_init_lock = threading.Lock()


def set_db_manager(new_manager: DatabaseManager):
    """Override the global db_manager (primarily for testing) — mcp/server.py pattern."""
    global db_manager
    db_manager = new_manager


class AuthenticatedUser(NamedTuple):
    """Detached identity snapshot — endpoints never touch the ORM row."""

    id: UUID
    email: str


def _ensure_firebase_app() -> None:
    """Lazy one-time init with Application Default Credentials (the Cloud Run runtime
    SA in prod; gcloud ADC in dev). Lazy so tests that fake _verify_token never need
    Firebase at all.

    Serialized (GH #93 follow-up): auth now runs in real threads, so two cold-start
    requests could both attempt initialize_app — the loser's ValueError would surface
    as a spurious 401 for a valid token."""
    with _firebase_init_lock:
        try:
            firebase_admin.get_app()
        except ValueError:
            # another thread won the init race between our check and call
            with contextlib.suppress(ValueError):
                firebase_admin.initialize_app()


def _verify_token(token: str) -> dict:
    """Seam for tests: monkeypatch THIS to fake Firebase. Verification is offline —
    JWT signature against Google's published certs; no per-request Google API call."""
    _ensure_firebase_app()
    return firebase_auth.verify_id_token(token)


def _signup_mode() -> str:
    """'invite' unless explicitly 'open' — any other value fails toward closed."""
    return "open" if os.environ.get("SIGNUP_MODE", "invite").strip().lower() == "open" else "invite"


def _resolve_user(token: str) -> AuthenticatedUser:
    """Sync body of the auth dependency: verify the Firebase token and resolve (or
    provision) the user row. Runs via asyncio.to_thread (GH #93) — verify_id_token's
    JWT check and the DB query/insert otherwise block the event loop on EVERY request.
    HTTPExceptions raised here propagate through to_thread unchanged."""
    try:
        decoded = _verify_token(token)
    except (ValueError, firebase_auth.InvalidIdTokenError) as e:
        # ExpiredIdTokenError/RevokedIdTokenError subclass InvalidIdTokenError;
        # ValueError covers malformed token strings. Log the cause — FastAPI does
        # not surface HTTPException.__cause__ anywhere.
        logger.info("token verification rejected: %s: %s", type(e).__name__, e)
        raise HTTPException(status_code=401, detail="Invalid or expired credentials.") from e
    except firebase_auth.CertificateFetchError as e:
        # Google's public-cert fetch failed — an OUR-SIDE outage, not the user's
        # credentials. 503 keeps the 2am pager signal honest (T5 review).
        logger.warning("certificate fetch failed during token verification: %s", e)
        raise HTTPException(status_code=503, detail="Authentication service unavailable.") from e
    except Exception as e:
        # Unknown failure (ADC misconfig, SDK surprise): fail CLOSED but loudly.
        logger.exception("unexpected token verification failure")
        raise HTTPException(status_code=401, detail="Invalid or expired credentials.") from e

    uid = decoded["uid"]
    email = (decoded.get("email") or "").strip().lower()
    email_verified = bool(decoded.get("email_verified"))

    with db_manager.get_session() as session:
        user = session.query(User).filter(User.firebase_uid == uid).first()
        if user is None and email and email_verified:
            # Claim-by-email: an invited row (firebase_uid NULL) is linked on first
            # sign-in. email_verified is REQUIRED — no claiming invites via spoofed,
            # unverified emails (ADR-048).
            user = session.query(User).filter(User.email == email, User.firebase_uid.is_(None)).first()
            if user is not None:
                user.firebase_uid = uid
                if not user.display_name:
                    user.display_name = decoded.get("name")
                session.flush()
        if user is None:
            if _signup_mode() != "open":
                raise HTTPException(status_code=403, detail="This account has not been invited.")
            if not email or not email_verified:
                raise HTTPException(status_code=403, detail="A verified email address is required to sign up.")
            # Known accepted race (friends-scale): two concurrent first requests can
            # both reach this insert. Now genuinely parallel in-process (auth runs in
            # real threads, GH #93) rather than just cross-request — handled by catch
            # + re-query instead of 500ing.
            try:
                user = User(email=email, firebase_uid=uid, display_name=decoded.get("name"))
                session.add(user)
                session.flush()
            except IntegrityError:
                session.rollback()
                user = session.query(User).filter(User.firebase_uid == uid).first()
                if user is None:
                    raise HTTPException(status_code=401, detail="Invalid or expired credentials.") from None
        result = AuthenticatedUser(id=user.id, email=user.email)
    return result


async def get_current_user(authorization: str | None = Header(None)) -> AuthenticatedUser:
    """FastAPI dependency: verify the Firebase ID token, resolve (or provision) the
    user row, set the user context, return the identity (ADR-048).

    MUST stay `async def`: a sync dependency runs in a threadpool, and a ContextVar
    set there is invisible to the endpoint. As a coroutine it shares the request
    task's context, which Starlette propagates into sync endpoints. The blocking
    verify+DB body runs via to_thread (GH #93); ONLY the ContextVar set lives here."""
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token.")
    token = authorization[7:].strip()
    if not token:
        raise HTTPException(status_code=401, detail="Missing bearer token.")
    result = await asyncio.to_thread(_resolve_user, token)
    current_user_id.set(result.id)
    return result
