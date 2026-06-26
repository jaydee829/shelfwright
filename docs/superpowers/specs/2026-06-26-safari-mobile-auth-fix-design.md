# Safari-Mobile Sign-In Fix — Same-Origin Firebase Auth Helper

**Date:** 2026-06-26
**Issue:** [#78](https://github.com/jaydee829/agentic_librarian/issues/78) (Safari mobile page not loading)
**Future evolution:** [#79](https://github.com/jaydee829/agentic_librarian/issues/79) (custom domain + CDN / Firebase Hosting)
**Decision record:** ADR-055

## Problem

A user on Safari mobile cannot load the app; Firebase Auth throws:

> Unable to process request due to missing initial state. This may happen if browser
> sessionStorage is inaccessible or accidentally cleared. Some specific scenarios are -
> 1) Using IDP-Initiated SAML SSO. 2) Using signInWithRedirect in a storage-partitioned
> browser environment.

### Root cause (confirmed)

This is a Firebase Auth **storage-partitioning** failure, not a bug in the app's sign-in code.

- The Firebase JS SDK loads its OAuth helper from `https://{authDomain}/__/auth/handler`
  and `https://{authDomain}/__/auth/iframe` (confirmed in
  `firebase-js-sdk/packages/auth/src/core/util/handler.ts`).
- Current `authDomain` = `agentic-librarian-prod.firebaseapp.com`.
- The app is served **same-origin from the FastAPI/Cloud Run container** on the raw
  `librarian-api-….run.app` host (Lift 2 design D3) — a **different registrable domain**
  from `firebaseapp.com`.
- The helper stores the OAuth "initial state" in `sessionStorage` on the `firebaseapp.com`
  origin. Safari mobile (ITP / storage partitioning) treats that origin's storage as
  third-party and isolates it, so when control returns the state is gone →
  "missing initial state."
- The message names `signInWithRedirect` even though the app calls `signInWithPopup`,
  because on Safari/iOS the SDK proactively initializes the redirect/iframe path
  regardless (`_shouldInitProactively` returns true for Safari/iOS in
  `platform_browser/popup_redirect.ts`). **Switching popup↔redirect does not fix it.**

### Scope of impact

- **Today:** Safari mobile (and anywhere ITP-style partitioning is enforced).
- **Trajectory:** Firefox Total Cookie Protection and Chrome Privacy Sandbox are moving the
  same direction; Firebase warns cross-domain auth will break broadly. This is **best
  practice**, not a Safari-specific band-aid. There is no client-only workaround — you
  cannot defeat storage partitioning from JS. The only real fix is a **same-origin auth
  helper**.

## Decision

Serve Firebase's OAuth helper (`/__/auth/*`) **first-party** from the FastAPI container
that already serves the SPA, so its `sessionStorage` is first-party. Point the browser's
`authDomain` at the app's own serving origin; FastAPI reverse-proxies the helper to the
Firebase project domain. (Option A in the brainstorm.)

This keeps the single-origin Cloud Run architecture, needs **no Google OAuth client edits**
(the registered `redirect_uri` stays on `firebaseapp.com`; the proxy forwards the final leg
back to our origin same-origin) and **no deploy-pipeline change**, and is forward-compatible
with a future custom domain or Firebase Hosting migration (#79).

### Alternatives considered

- **Option B — migrate frontend to Firebase Hosting** (Hosting serves `/__/auth/` natively):
  cleaner long-term and best CDN scaling, but adds a second deploy surface, API rewrites, and
  CORS/header-forwarding — too large for a production hotfix. **Deferred to #79.**
- **Option C — stand up a custom domain now** (same registrable domain for app + authDomain):
  forces a domain decision *now* to fix a bug, and still needs the proxy (= A + a domain
  purchase). **Deferred to #79.**
- **Switch popup→redirect (or vice-versa):** does not address partitioning; rejected.

## Architecture

```
Browser (Safari mobile)
  │  authDomain = window.location.host  (the app's own origin)
  ▼
FastAPI / Cloud Run  (same origin: serves SPA + API + auth proxy)
  ├─ /__/auth/{path}   ──proxy──▶  https://agentic-librarian-prod.firebaseapp.com/__/auth/{path}
  ├─ /api routes (unchanged)
  └─ /{full_path}  SPA catch-all (registered LAST)
```

Because the helper is now served from the app's own origin, its `sessionStorage` is
first-party and the OAuth state survives the round trip in every browser.

## Components

### 1. Backend proxy — `src/agentic_librarian/api/firebase_auth_proxy.py` (new)

A FastAPI router exposing `GET /__/auth/{path:path}`.

- **Upstream:** `https://{FIREBASE_AUTH_UPSTREAM}/__/auth/{path}?{query}`, where
  `FIREBASE_AUTH_UPSTREAM` is an env var defaulting to `agentic-librarian-prod.firebaseapp.com`.
  The path prefix (`/__/auth/`) and the upstream host are **fixed in code/config — never
  derived from request input** (closes the SSRF / open-proxy door).
- **Async:** uses `httpx.AsyncClient` (NOT the sync `requests` pattern used by
  `availability/overdrive.py`) so a burst of concurrent sign-ins does not exhaust worker
  threads. A module-level client with a bounded connection pool.
- **Passthrough:** forward the query string verbatim; return the upstream status code,
  `Content-Type`, and cache headers (so `iframe.js` and other static helper assets cache and
  do not re-hit upstream on every load). Stream the body.
- **Framing:** if the upstream response carries `X-Frame-Options: DENY`, relax it to
  `SAMEORIGIN` (the helper iframe is framed by our own same origin and must be allowed). The
  app's CSP must permit `frame-src 'self'`.
- **Registration:** `app.include_router(firebase_auth_proxy_router)` **before** the SPA
  catch-all `@app.get("/{full_path:path}")` in `api/main.py`, so `/__/auth/*` is proxied, not
  swallowed into the SPA shell.
- **Errors:** an upstream fetch failure returns `502`; the Firebase SDK then surfaces a normal
  auth error and sign-in degrades without crashing the page. Failures are not cached. The
  proxy is scoped strictly to `/__/auth/`, leaving all existing API and SPA routes untouched.

### 2. Frontend — `frontend/src/auth/firebase.ts` (modify)

- Resolve `authDomain` at runtime:
  - When running in a browser on a **non-localhost** host → `window.location.host` (the
    app's own origin, where the proxy lives). This transparently covers the current
    `*.run.app` host **and any future custom domain** with no rebuild.
  - Otherwise (local dev, tests, non-browser) → fall back to `VITE_FIREBASE_AUTH_DOMAIN`
    (local dev has no proxy and keeps using the Firebase domain; popups work on desktop dev).
- Keep `signInWithPopup` as the primary sign-in. Add `getRedirectResult(auth)` handling on
  app load as the mobile popup→redirect fallback — now functional because storage is
  first-party. A `null` result (the normal "no redirect pending" case) is a no-op.

### 3. Config / ops

- **Dependency:** `httpx` is currently declared only under `[project.optional-dependencies].dev`
  (it's pulled in for FastAPI's `TestClient`). The proxy is **runtime** code, so `httpx>=0.27`
  must be **promoted into the main `[project.dependencies]`** list. (Confirm the prod image
  installs the base deps, not the `dev` extra.)
- Backend env `FIREBASE_AUTH_UPSTREAM` wired through `Dockerfile.api` and
  `.github/workflows/deploy.yml` alongside the existing build args (default
  `agentic-librarian-prod.firebaseapp.com`; overridable per environment).
- The serving host must remain in Firebase Console → Authentication → **Authorized domains**
  (the `librarian-api-….run.app` host was already added during Lift 2 Stage 4 rollout). **No
  Google OAuth client changes.**
- Runbook update: add a "verify sign-in on a real Safari mobile device" post-deploy step.

## Data flow (sign-in)

1. SPA initializes Firebase with `authDomain = window.location.host`.
2. SDK loads `https://{host}/__/auth/iframe` → FastAPI proxies it from `firebaseapp.com`
   (same-origin to the browser).
3. User taps "Sign in with Google" → popup (or, on mobile, redirect) to
   `https://{host}/__/auth/handler` → proxied. The handler bounces to Google and back through
   the Firebase project's registered `redirect_uri` on `firebaseapp.com`, then returns to the
   same-origin handler.
4. OAuth "initial state" was written to `sessionStorage` on **our origin** (first-party) →
   it is present on return → sign-in completes. Firebase mints the ID token into IndexedDB on
   our origin as before.

## Concurrency & scaling notes

- Firebase auth state is **per-browser** (tokens in IndexedDB, OAuth state in that browser's
  `sessionStorage`). There is **no shared server-side auth state**, so simultaneous users do
  not contend — no new concurrency-correctness class is introduced.
- The proxy is **stateless**; each request is forwarded independently. The only concern is
  throughput under a burst of concurrent sign-ins, bounded by Cloud Run concurrency + the
  httpx connection pool; worst case is a transient `502`/auth error for one user, never
  corruption or cross-user leakage. The async client keeps this healthy.
- Auth-helper traffic is **per-sign-in / per-auth-init**, a small fraction of total traffic,
  and the static helper assets are cacheable — so the incremental Cloud Run load is modest.
  CDN-level optimization of static delivery is out of scope here and belongs to #79.

## Testing

### Backend (unit, mocked `httpx` — no real network)

- `GET /__/auth/handler?foo=bar` forwards to
  `https://{FIREBASE_AUTH_UPSTREAM}/__/auth/handler?foo=bar` (query string preserved, correct
  upstream).
- Upstream status code and `Content-Type` are passed through to the client.
- An upstream `X-Frame-Options: DENY` is rewritten to `SAMEORIGIN`.
- **Precedence:** a request to `/__/auth/iframe.js` is handled by the proxy (forwarded), not
  served the SPA shell by the catch-all.
- Upstream failure → `502`.
- The proxy does not handle paths outside `/__/auth/` (e.g. `/api/health`, `/history` still
  route to their existing handlers / the SPA catch-all).

### Frontend (unit)

- `authDomain` resolves to `window.location.host` on a simulated prod (non-localhost) host and
  to `VITE_FIREBASE_AUTH_DOMAIN` on `localhost`.
- `getRedirectResult` is invoked on load; a `null` result is a safe no-op.

### Manual (post-deploy)

- Sign in on a **real Safari mobile device** against prod; confirm the page loads and Google
  sign-in completes. (Per runbook step.)

## Out of scope (→ #79)

- Custom domain for the app, Firebase Hosting migration, and CDN-level static-asset delivery.
  Option A is forward-compatible with all of these; when #79 is picked up on Firebase Hosting,
  the small proxy can be retired (Hosting serves `/__/auth/` natively) and the runtime
  `authDomain` continues to work unchanged.
