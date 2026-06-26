"""Unit tests for the same-origin Firebase auth proxy (GH #78). No real network:
a stub client is injected via set_client()."""

from __future__ import annotations

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agentic_librarian.api import firebase_auth_proxy
from agentic_librarian.api.firebase_auth_proxy import router

UPSTREAM = "agentic-librarian-prod.firebaseapp.com"


class _StubClient:
    """Stands in for httpx.AsyncClient.get."""

    def __init__(self, response: httpx.Response | None = None, exc: Exception | None = None):
        self._response = response
        self._exc = exc
        self.calls: list[tuple[str, dict, dict]] = []

    async def get(self, url, params=None, headers=None):  # noqa: ANN001
        self.calls.append((url, dict(params or {}), dict(headers or {})))
        if self._exc is not None:
            raise self._exc
        return self._response


def _client_for(stub: _StubClient) -> TestClient:
    app = FastAPI()
    app.include_router(router)
    firebase_auth_proxy.set_client(stub)
    return TestClient(app)


def test_forwards_to_fixed_upstream_with_query_preserved():
    stub = _StubClient(httpx.Response(200, content=b"OK"))
    resp = _client_for(stub).get("/__/auth/handler", params={"foo": "bar"})
    assert resp.status_code == 200
    assert resp.content == b"OK"
    url, params, _ = stub.calls[0]
    assert url == f"https://{UPSTREAM}/__/auth/handler"
    assert params == {"foo": "bar"}


def test_passthrough_status_and_content_type():
    stub = _StubClient(httpx.Response(201, content=b"x=1", headers={"content-type": "application/javascript"}))
    resp = _client_for(stub).get("/__/auth/iframe.js")
    assert resp.status_code == 201
    assert resp.headers["content-type"].startswith("application/javascript")


def test_relaxes_x_frame_options_deny_to_sameorigin():
    stub = _StubClient(httpx.Response(200, content=b"<html></html>", headers={"x-frame-options": "DENY"}))
    resp = _client_for(stub).get("/__/auth/iframe")
    assert resp.headers["x-frame-options"] == "SAMEORIGIN"


def test_upstream_failure_returns_502():
    stub = _StubClient(exc=httpx.ConnectError("boom"))
    resp = _client_for(stub).get("/__/auth/handler")
    assert resp.status_code == 502


def test_no_x_frame_options_when_absent():
    """When upstream sends no X-Frame-Options, the proxy must not inject one."""
    stub = _StubClient(httpx.Response(200, content=b"<html></html>"))
    resp = _client_for(stub).get("/__/auth/iframe")
    assert "x-frame-options" not in resp.headers
