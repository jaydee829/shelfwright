# Rollout — Safari-mobile sign-in fix (GH #78, ADR-055)

**What ships:** a same-origin reverse-proxy for Firebase's `/__/auth/*` helper
(`src/agentic_librarian/api/firebase_auth_proxy.py`) + runtime `authDomain = window.location.host`.

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
