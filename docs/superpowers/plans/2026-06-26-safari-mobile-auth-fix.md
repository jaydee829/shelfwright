# Safari-Mobile Sign-In Fix — Same-Origin Firebase Auth Helper — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix Safari-mobile sign-in (GH #78) by serving Firebase's `/__/auth/*` OAuth helper same-origin through a FastAPI reverse-proxy and pointing `authDomain` at the app's own host at runtime, so the helper's `sessionStorage` is first-party.

**Architecture:** A new FastAPI router proxies `GET /__/auth/{path}` to the Firebase project domain (fixed upstream + fixed path prefix → no open-proxy), registered before the SPA catch-all. The frontend resolves `authDomain = window.location.host` on a non-localhost browser host (env fallback otherwise) and completes any pending redirect on load. No migration, no Google OAuth client edits, no deploy-pipeline change.

**Tech Stack:** FastAPI, `httpx.AsyncClient` (async proxy), pytest + FastAPI `TestClient`; React + Firebase JS SDK v12, Vitest.

**Spec:** `docs/superpowers/specs/2026-06-26-safari-mobile-auth-fix-design.md` · **ADR-055** · Future evolution: GH #79.

**Context for the implementer:**
- The prod image installs base deps only (`Dockerfile.api` → `pip install .`), so any runtime import must be a base dependency, NOT a `[dev]` extra.
- `api/main.py` includes routers at lines 74–80 and defines the SPA catch-all `@app.get("/{full_path:path}")` later (≈ line 355). Route matching is by registration order, so a router included among 74–80 is matched before the catch-all.
- `FIREBASE_AUTH_UPSTREAM`'s default in code is the prod authDomain (`agentic-librarian-prod.firebaseapp.com`), so the proxy works with zero extra config. (This intentionally supersedes the spec's optional Dockerfile/deploy wiring — we keep the change pipeline-free.)
- Frontend tests: Vitest with `globals: true`, `environment: 'jsdom'`. The auth-domain logic is extracted into a pure module (`authDomain.ts`) so it is unit-tested WITHOUT importing `firebase.ts` (which calls `getAuth()` at module load and throws without env — the documented App.test pitfall).
- Run a single backend test: `pytest test/unit/test_firebase_auth_proxy.py -v`. Run frontend tests: `cd frontend && npx vitest run <file>`.

---

## File Structure

- **Create** `src/agentic_librarian/api/firebase_auth_proxy.py` — the reverse-proxy router (only module that talks to the Firebase helper domain).
- **Create** `test/unit/test_firebase_auth_proxy.py` — proxy unit tests (mocked httpx).
- **Modify** `pyproject.toml` — promote `httpx>=0.27` from `[dev]` to base `dependencies`.
- **Modify** `src/agentic_librarian/api/main.py` — register the proxy router before the SPA catch-all.
- **Create** `test/unit/test_firebase_auth_proxy_precedence.py` — proves `/__/auth/*` is proxied, not swallowed by the SPA catch-all.
- **Create** `frontend/src/auth/authDomain.ts` — pure `resolveAuthDomain()` helper.
- **Create** `frontend/src/auth/authDomain.test.ts` — its unit tests.
- **Modify** `frontend/src/auth/firebase.ts` — use `resolveAuthDomain` + `getRedirectResult` on load.
- **Create** `docs/runbooks/safari-auth-fix-rollout.md` — deploy + Safari-mobile verification + rollback.

---

## Task 1: Promote `httpx` to a runtime dependency

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Add `httpx` to base dependencies**

In `pyproject.toml`, find the API-surface deps in the main `dependencies` list:

```toml
    # API surface (Lift 0 walking skeleton — served by uvicorn in the prod image)
    "fastapi>=0.115",
    "uvicorn>=0.34",
```

Change to:

```toml
    # API surface (Lift 0 walking skeleton — served by uvicorn in the prod image)
    "fastapi>=0.115",
    "uvicorn>=0.34",
    # Async HTTP client — used by the same-origin Firebase auth proxy (api/firebase_auth_proxy.py).
    # Runtime dep (the prod image installs base deps only), not just a test dep.
    "httpx>=0.27",
```

- [ ] **Step 2: Remove the now-duplicate `httpx` from the `[dev]` extra**

In `[project.optional-dependencies]`, delete the `"httpx>=0.27",` line from the `dev = [...]` list (it is now a base dep; `TestClient` still gets it transitively).

- [ ] **Step 3: Verify it imports as a base dep**

Run: `python -c "import httpx; print(httpx.__version__)"`
Expected: prints a version (≥ 0.27), no error.

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml
git commit -m "build: promote httpx to a runtime dependency for the auth proxy"
```

---

## Task 2: The same-origin Firebase auth proxy

**Files:**
- Create: `src/agentic_librarian/api/firebase_auth_proxy.py`
- Test: `test/unit/test_firebase_auth_proxy.py`

- [ ] **Step 1: Write the failing tests**

Create `test/unit/test_firebase_auth_proxy.py`:

```python
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
    stub = _StubClient(
        httpx.Response(201, content=b"x=1", headers={"content-type": "application/javascript"})
    )
    resp = _client_for(stub).get("/__/auth/iframe.js")
    assert resp.status_code == 201
    assert resp.headers["content-type"].startswith("application/javascript")


def test_relaxes_x_frame_options_deny_to_sameorigin():
    stub = _StubClient(
        httpx.Response(200, content=b"<html></html>", headers={"x-frame-options": "DENY"})
    )
    resp = _client_for(stub).get("/__/auth/iframe")
    assert resp.headers["x-frame-options"] == "SAMEORIGIN"


def test_upstream_failure_returns_502():
    stub = _StubClient(exc=httpx.ConnectError("boom"))
    resp = _client_for(stub).get("/__/auth/handler")
    assert resp.status_code == 502
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest test/unit/test_firebase_auth_proxy.py -v`
Expected: FAIL — `ModuleNotFoundError: agentic_librarian.api.firebase_auth_proxy`.

- [ ] **Step 3: Implement the proxy module**

Create `src/agentic_librarian/api/firebase_auth_proxy.py`:

```python
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest test/unit/test_firebase_auth_proxy.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Lint**

Run: `uvx ruff@0.15.16 format src/agentic_librarian/api/firebase_auth_proxy.py test/unit/test_firebase_auth_proxy.py` then `uvx ruff@0.15.16 check src/agentic_librarian/api/firebase_auth_proxy.py test/unit/test_firebase_auth_proxy.py`
Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add src/agentic_librarian/api/firebase_auth_proxy.py test/unit/test_firebase_auth_proxy.py
git commit -m "feat(auth): same-origin Firebase /__/auth proxy (#78)"
```

---

## Task 3: Wire the proxy router before the SPA catch-all

**Files:**
- Modify: `src/agentic_librarian/api/main.py:13-24` (imports) and `:74-80` (router includes)
- Test: `test/unit/test_firebase_auth_proxy_precedence.py`

- [ ] **Step 1: Write the failing precedence test**

Create `test/unit/test_firebase_auth_proxy_precedence.py`:

```python
"""The proxy must take precedence over the SPA catch-all: a /__/auth/* request is
forwarded upstream, NOT served the SPA index shell."""

from __future__ import annotations

import httpx
from fastapi.testclient import TestClient

from agentic_librarian.api import firebase_auth_proxy
from agentic_librarian.api.main import app


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
```

- [ ] **Step 2: Run it to verify it fails**

Run: `pytest test/unit/test_firebase_auth_proxy_precedence.py -v`
Expected: FAIL — the SPA catch-all returns the index (or a 500/404 from a missing static dir), and `stub.calls` is empty.

- [ ] **Step 3: Register the router**

In `src/agentic_librarian/api/main.py`, add the import alongside the other router imports (after line 19's `availability` import is a natural spot):

```python
from agentic_librarian.api.firebase_auth_proxy import router as firebase_auth_proxy_router
```

Then in the `app.include_router(...)` block (lines 74–80), add it **first** so it clearly precedes the catch-all:

```python
app.include_router(firebase_auth_proxy_router)
app.include_router(recommendations_router)
```

(The SPA catch-all is defined later in the file and is unchanged.)

- [ ] **Step 4: Run the precedence test to verify it passes**

Run: `pytest test/unit/test_firebase_auth_proxy_precedence.py -v`
Expected: PASS.

- [ ] **Step 5: Run the full proxy + main API test set for regressions**

Run: `pytest test/unit/test_firebase_auth_proxy.py test/unit/test_firebase_auth_proxy_precedence.py test/unit/test_spa_serving.py -v`
Expected: PASS (proxy, precedence, and existing SPA-serving tests all green).

- [ ] **Step 6: Lint + commit**

```bash
uvx ruff@0.15.16 format src/agentic_librarian/api/main.py test/unit/test_firebase_auth_proxy_precedence.py
uvx ruff@0.15.16 check src/agentic_librarian/api/main.py test/unit/test_firebase_auth_proxy_precedence.py
git add src/agentic_librarian/api/main.py test/unit/test_firebase_auth_proxy_precedence.py
git commit -m "feat(auth): register the /__/auth proxy before the SPA catch-all (#78)"
```

---

## Task 4: Pure `resolveAuthDomain` helper (frontend)

**Files:**
- Create: `frontend/src/auth/authDomain.ts`
- Test: `frontend/src/auth/authDomain.test.ts`

- [ ] **Step 1: Write the failing tests**

Create `frontend/src/auth/authDomain.test.ts`:

```ts
import { describe, expect, it } from 'vitest'

import { resolveAuthDomain } from './authDomain'

describe('resolveAuthDomain', () => {
  it('uses the current host on a real (non-localhost) browser host', () => {
    expect(
      resolveAuthDomain('proj.firebaseapp.com', {
        hostname: 'librarian-api-abc.run.app',
        host: 'librarian-api-abc.run.app',
      }),
    ).toBe('librarian-api-abc.run.app')
  })

  it('preserves a port in the host', () => {
    expect(
      resolveAuthDomain('proj.firebaseapp.com', { hostname: 'app.example.com', host: 'app.example.com:8443' }),
    ).toBe('app.example.com:8443')
  })

  it('falls back to the configured domain on localhost', () => {
    expect(
      resolveAuthDomain('proj.firebaseapp.com', { hostname: 'localhost', host: 'localhost:5173' }),
    ).toBe('proj.firebaseapp.com')
  })

  it('falls back to the configured domain on 127.0.0.1', () => {
    expect(
      resolveAuthDomain('proj.firebaseapp.com', { hostname: '127.0.0.1', host: '127.0.0.1:5173' }),
    ).toBe('proj.firebaseapp.com')
  })

  it('falls back when there is no location (non-browser / SSR / test)', () => {
    expect(resolveAuthDomain('proj.firebaseapp.com', undefined)).toBe('proj.firebaseapp.com')
  })
})
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd frontend && npx vitest run src/auth/authDomain.test.ts`
Expected: FAIL — cannot resolve `./authDomain`.

- [ ] **Step 3: Implement the helper**

Create `frontend/src/auth/authDomain.ts`:

```ts
export interface LocationLike {
  hostname: string
  host: string
}

const LOCAL_HOSTS = new Set(['localhost', '127.0.0.1'])

/**
 * Resolve the Firebase `authDomain`.
 *
 * In a browser on a real (non-localhost) host, Firebase's `/__/auth/*` helper is served
 * SAME-ORIGIN by our FastAPI proxy, so `authDomain` must be our own host — that makes the
 * helper's sessionStorage first-party and fixes Safari-mobile sign-in (GH #78). It also
 * follows any future custom domain automatically. Local dev / tests / non-browser have no
 * proxy, so fall back to the build-time configured Firebase domain.
 */
export function resolveAuthDomain(
  envDomain: string | undefined,
  loc: LocationLike | undefined,
): string | undefined {
  if (loc && !LOCAL_HOSTS.has(loc.hostname)) {
    return loc.host
  }
  return envDomain
}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd frontend && npx vitest run src/auth/authDomain.test.ts`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/auth/authDomain.ts frontend/src/auth/authDomain.test.ts
git commit -m "feat(auth): runtime authDomain resolver (#78)"
```

---

## Task 5: Use the resolver + complete redirect on load (frontend)

**Files:**
- Modify: `frontend/src/auth/firebase.ts`

- [ ] **Step 1: Update `firebase.ts`**

Replace the import block and `initializeApp` call, and add the redirect-completion side effect. The full updated file:

```ts
import { initializeApp } from 'firebase/app'
import {
  GoogleAuthProvider,
  getAuth,
  getRedirectResult,
  onAuthStateChanged,
  signInWithPopup,
  signOut,
  type User,
} from 'firebase/auth'

import { resolveAuthDomain } from './authDomain'

const app = initializeApp({
  apiKey: import.meta.env.VITE_FIREBASE_API_KEY,
  // Same-origin authDomain on real hosts (served via the /__/auth proxy) fixes
  // Safari-mobile sign-in (GH #78); env fallback for local dev / tests.
  authDomain: resolveAuthDomain(
    import.meta.env.VITE_FIREBASE_AUTH_DOMAIN,
    typeof window !== 'undefined' ? window.location : undefined,
  ),
  projectId: import.meta.env.VITE_FIREBASE_PROJECT_ID,
  appId: import.meta.env.VITE_FIREBASE_APP_ID,
})

export const auth = getAuth(app)

// Safari/iOS may down-convert signInWithPopup to a redirect; complete any pending redirect
// on load. Harmless no-op (result === null) otherwise. This works now because the
// same-origin authDomain makes the helper's storage first-party (GH #78). Never throw into
// app load — sign-in errors surface via onAuthStateChanged / the sign-in UI.
void getRedirectResult(auth).catch(() => {})

export function onAuth(callback: (user: User | null) => void): () => void {
  return onAuthStateChanged(auth, callback)
}

export function signInWithGoogle(): Promise<unknown> {
  return signInWithPopup(auth, new GoogleAuthProvider())
}

export function signOutUser(): Promise<void> {
  return signOut(auth)
}

/** The current user's Firebase ID token, or null when signed out. The SDK auto-refreshes. */
export async function getIdToken(): Promise<string | null> {
  return auth.currentUser ? auth.currentUser.getIdToken() : null
}
```

(Note: `firebase.ts` is intentionally not unit-tested directly — importing it runs `getAuth()` at module load, which throws without Firebase env, the documented App.test pitfall. The pure resolver is covered in Task 4; the `getRedirectResult` side effect is verified manually on Safari in Task 6.)

- [ ] **Step 2: Type-check + run the frontend suite for regressions**

Run: `cd frontend && npx tsc -b && npm test`
Expected: PASS — `tsc` clean; the existing suite (incl. `AuthContext.test.tsx`, `App.test.tsx`, which mock `./firebase`) is unaffected.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/auth/firebase.ts
git commit -m "feat(auth): same-origin authDomain + redirect completion on load (#78)"
```

---

## Task 6: Rollout runbook

**Files:**
- Create: `docs/runbooks/safari-auth-fix-rollout.md`

- [ ] **Step 1: Write the runbook**

Create `docs/runbooks/safari-auth-fix-rollout.md`:

```markdown
# Rollout — Safari-mobile sign-in fix (GH #78, ADR-055)

**What ships:** a same-origin reverse-proxy for Firebase's `/__/auth/*` helper
(`api/firebase_auth_proxy.py`) + runtime `authDomain = window.location.host`.

**No migration. No Google OAuth client edits. No pipeline/secret changes.** The proxy's
upstream defaults in code to `agentic-librarian-prod.firebaseapp.com`. The serving host is
already in Firebase Console → Authentication → **Authorized domains** (added during Lift 2
Stage 4).

## Deploy
1. Merge the PR to `main`; CD builds + deploys the image as usual.
2. After the new revision is serving, smoke-test desktop sign-in (popup) — should be unchanged.

## Verify the actual fix (required)
3. On a **real Safari mobile device** (or iOS Simulator Safari), open the prod app URL and
   sign in with Google. Expected: the page loads and sign-in completes — no
   "missing initial state" error.
4. Sanity-check the proxy directly: `curl -sI https://<prod-host>/__/auth/iframe.js` →
   `200` with a JavaScript `content-type` (served first-party, not the SPA HTML shell).

## Rollback
- Revert the PR and redeploy. There is no data or schema change, so rollback is clean and
  immediate; behavior returns to the prior `firebaseapp.com` authDomain.

## Future
- Custom domain / CDN / Firebase Hosting evolution is tracked in GH #79. The runtime
  `authDomain` carries forward unchanged; under Firebase Hosting this proxy can be retired.
```

- [ ] **Step 2: Commit**

```bash
git add docs/runbooks/safari-auth-fix-rollout.md
git commit -m "docs(runbook): Safari-mobile auth fix rollout + verification (#78)"
```

---

## Final verification (after all tasks)

- [ ] **Backend:** `pytest -m "not api_dependent and not slow and not live and not db_integration"` — green (includes the new proxy + precedence tests).
- [ ] **Frontend:** `cd frontend && npx tsc -b && npm test` — green.
- [ ] **Lint:** `uvx ruff@0.15.16 format --check .` and `uvx ruff@0.15.16 check .` — clean.
- [ ] **Manual (post-deploy):** Safari-mobile sign-in per the runbook.
