"""Internal deep-enrichment endpoint (Lift 2 Stage 3) — the Cloud Tasks target.

POST /internal/enrich/{work_id} runs the slow LLM scouts and updates the Work. It is NOT
Firebase-gated: it sits behind the (Stage-4) open IAM gate and is protected instead by the
OIDC token the Cloud Tasks queue attaches — only the queue's service account may call it.
Idempotent: Cloud Tasks may redeliver, and two_phase.enrich_deep is retry-safe."""

from __future__ import annotations

import logging
import os
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException

from agentic_librarian.enrichment import two_phase

logger = logging.getLogger(__name__)
router = APIRouter()


def _verify_oidc(token: str, audience: str) -> dict:
    """Seam for tests: monkeypatch THIS to fake the queue's OIDC token. Verifies the
    Google-signed ID token's signature, expiry, issuer, and audience, returning its claims."""
    from google.auth.transport import requests as ga_requests
    from google.oauth2 import id_token

    return id_token.verify_oauth2_token(token, ga_requests.Request(), audience=audience)


def _require_queue_caller(authorization: str | None) -> None:
    """Fail-closed OIDC gate: 401 if no bearer token, 403 if it isn't a valid token from
    the configured queue service account."""
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token.")
    token = authorization[7:].strip()
    if not token:
        raise HTTPException(status_code=401, detail="Missing bearer token.")
    expected_sa = os.environ.get("ENRICH_INVOKER_SA")
    audience = os.environ.get("ENRICH_OIDC_AUDIENCE")
    if not expected_sa:
        # Misconfigured deployment — fail closed, never open.
        logger.error("ENRICH_INVOKER_SA unset; refusing internal enrichment call")
        raise HTTPException(status_code=403, detail="Internal endpoint not configured.")
    try:
        claims = _verify_oidc(token, audience)
    except Exception as e:  # noqa: BLE001 - any verification failure is a rejection
        logger.info("internal OIDC verification rejected: %s: %s", type(e).__name__, e)
        raise HTTPException(status_code=403, detail="Caller is not the enrichment queue.") from e
    if claims.get("email") != expected_sa or not claims.get("email_verified", False):
        logger.info("internal call from unexpected principal: %s", claims.get("email"))
        raise HTTPException(status_code=403, detail="Caller is not the enrichment queue.")


@router.post("/internal/enrich/{work_id}")
def enrich(work_id: UUID, authorization: str | None = Header(None)):  # noqa: B008
    _require_queue_caller(authorization)
    if not two_phase.enrich_deep(work_id):
        # Non-retryable: the work no longer exists. 404 stops Cloud Tasks from retrying.
        raise HTTPException(status_code=404, detail="work not found")
    return {"work_id": str(work_id), "status": "enriched"}
