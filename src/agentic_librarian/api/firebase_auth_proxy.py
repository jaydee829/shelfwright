"""Reverse-proxy Firebase's hosted OAuth helper (`/__/auth/*`) so it is served
SAME-ORIGIN from this container (GH #78).

Firebase loads its sign-in handler/iframe from `https://{authDomain}/__/auth/...`.
When authDomain != the app's own origin, Safari mobile's storage partitioning isolates
the helper's sessionStorage as third-party → sign-in fails with "missing initial state".
Serving the helper first-party fixes it in every browser.

Security: the upstream host and the `/__/auth/` path prefix are FIXED (never derived from
request input), so this is not an open proxy / SSRF vector. On any upstream failure we
return 502; the Firebase SDK then surfaces a normal auth error and sign-in degrades
without crashing the page."""

from __future__ import annotations

import logging
import os

import httpx
from fastapi import APIRouter, Request, Response

logger = logging.getLogger(__name__)

router = APIRouter()

_DEFAULT_UPSTREAM = "agentic-librarian-prod.firebaseapp.com"

# RFC 7230 hop-by-hop headers + body-shape headers httpx already resolved (it decodes the
# body, so a forwarded content-encoding/length would mismatch). content-type is re-set via
# media_type below.
_DROP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
    "content-encoding",
    "content-length",
    "content-type",
}

_client: httpx.AsyncClient | None = None


def _upstream_host() -> str:
    return os.environ.get("FIREBASE_AUTH_UPSTREAM", _DEFAULT_UPSTREAM)


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(timeout=10.0, limits=httpx.Limits(max_connections=20))
    return _client


def set_client(client) -> None:  # noqa: ANN001  (test seam — accepts any get()-able stub)
    """Inject a client (tests). Production lazily builds a real httpx.AsyncClient."""
    global _client
    _client = client


@router.get("/__/auth/{path:path}")
async def proxy_firebase_auth(path: str, request: Request) -> Response:
    upstream = f"https://{_upstream_host()}/__/auth/{path}"
    try:
        resp = await _get_client().get(
            upstream,
            params=dict(request.query_params),
            headers={"accept": request.headers.get("accept", "*/*")},
        )
        # We do NOT call raise_for_status(), so an upstream HTTP error *response* (4xx/5xx)
        # is a normal `resp` and is passed through below with its real status. This except
        # only fires on transport/network failures (connect/timeout/protocol) → 502.
    except httpx.HTTPError as exc:
        logger.warning("Firebase auth proxy upstream failed for /__/auth/%s: %s", path, exc)
        return Response(status_code=502, content=b"auth helper upstream unavailable")

    headers = {k: v for k, v in resp.headers.items() if k.lower() not in _DROP_HEADERS}
    # The helper iframe is framed by our OWN origin → permit same-origin framing.
    if headers.get("x-frame-options", "").upper() == "DENY":
        headers["x-frame-options"] = "SAMEORIGIN"

    return Response(
        content=resp.content,
        status_code=resp.status_code,
        headers=headers,
        media_type=resp.headers.get("content-type"),
    )
