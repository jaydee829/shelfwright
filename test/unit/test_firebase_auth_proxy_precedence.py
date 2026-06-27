"""The proxy must take precedence over the SPA catch-all: a /__/auth/* request is
forwarded upstream, NOT served the SPA index shell."""

from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient

from agentic_librarian.api import firebase_auth_proxy
from agentic_librarian.api.main import app


@pytest.fixture(autouse=True)
def _reset_proxy_client():
    """Reset the module-global httpx client after each test so a stub never leaks."""
    yield
    firebase_auth_proxy.set_client(None)


def test_auth_path_is_proxied_not_served_spa_shell():
    stub = firebase_auth_proxy_stub()
    # No `with` → skip lifespan (no DB), per api/main.py's TestClient note.
    client = TestClient(app)
    resp = client.get("/__/auth/iframe.js")
    assert resp.status_code == 200
    assert resp.content == b"PROXIED"
    assert stub.calls, "proxy was bypassed — the SPA catch-all swallowed /__/auth/*"
    assert stub.calls[0][0].endswith("/__/auth/iframe.js")


class _Stub:
    def __init__(self):
        self.calls = []

    async def get(self, url, params=None, headers=None):  # noqa: ANN001
        self.calls.append((url, dict(params or {}), dict(headers or {})))
        return httpx.Response(200, content=b"PROXIED")


def firebase_auth_proxy_stub() -> _Stub:
    stub = _Stub()
    firebase_auth_proxy.set_client(stub)
    return stub
