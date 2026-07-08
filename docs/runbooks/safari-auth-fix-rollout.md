# Rollout — Safari-mobile sign-in fix (GH #78, ADR-055)

**What ships:** a same-origin reverse-proxy for Firebase's `/__/auth/*` helper
(`src/agentic_librarian/api/firebase_auth_proxy.py`) + runtime `authDomain = window.location.host`.

**No migration. No pipeline/secret changes.** The proxy's upstream defaults in code to
`agentic-librarian-prod.firebaseapp.com`.

> ⚠️ **REQUIRED Google OAuth client edit (do NOT skip — earlier versions of this runbook wrongly
> said "No Google OAuth client edits").** Because `authDomain = window.location.host`, Firebase's
> Google sign-in `redirect_uri` becomes `https://{serving-host}/__/auth/handler` — the **app's own
> host**, not `firebaseapp.com`. Google rejects any `redirect_uri` not registered on the Web OAuth
> 2.0 client, so **new users** hit `Error 400: redirect_uri_mismatch` until it is added (existing
> users with live sessions don't re-run the Google grant, so this hides in smoke tests). This is a
> **different setting** from Firebase → Authentication → **Authorized domains** (which governs SDK
> domains and was already set in Lift 2 Stage 4); it does NOT cover the OAuth redirect URI.
>
> **In GCP Console → APIs & Services → Credentials → the Web OAuth 2.0 Client (project
> `agentic-librarian-prod`):** register **every hostname the app is served on** — Cloud Run exposes
> at least two by default:
> - **Authorized redirect URIs:**
>   `https://librarian-api-388776616965.us-central1.run.app/__/auth/handler`
>   `https://librarian-api-hnucndzntq-uc.a.run.app/__/auth/handler`
> - **Authorized JavaScript origins:**
>   `https://librarian-api-388776616965.us-central1.run.app`
>   `https://librarian-api-hnucndzntq-uc.a.run.app`
>
> Config-only, effective within minutes, no redeploy. Any future serving host (new Cloud Run alias
> or custom domain, GH #79) must be registered the same way or new-user sign-in re-breaks.

## Deploy
1. Merge the PR to `main`; CD builds + deploys the image as usual.
2. After the new revision is serving, smoke-test desktop sign-in (popup) — should be unchanged.

## Verify the actual fix (required)
3. On a **real Safari mobile device** (or iOS Simulator Safari), open the prod app URL and
   sign in with Google. Expected: the page loads and sign-in completes — no
   "missing initial state" error.
4. Sanity-check the proxy directly: `curl -sI https://<prod-host>/__/auth/iframe.js` →
   `200` with a JavaScript `content-type` (served first-party, not the SPA HTML shell).
5. **New-user sign-in (catches `redirect_uri_mismatch`).** Sign in with a Google account that has
   **never** authorized this app, on **each** serving hostname (both Cloud Run URLs above). An
   already-signed-in account will NOT exercise the Google OAuth redirect and will falsely pass —
   use a fresh account or a fresh incognito profile with revoked access. Expected: consent → app
   loads, no `Error 400: redirect_uri_mismatch`.

## Rollback
- Revert the PR and redeploy. There is no data or schema change, so rollback is clean and
  immediate; behavior returns to the prior `firebaseapp.com` authDomain.

## Future
- Custom domain / CDN / Firebase Hosting evolution is tracked in GH #79. The runtime
  `authDomain` carries forward unchanged; under Firebase Hosting this proxy can be retired.
