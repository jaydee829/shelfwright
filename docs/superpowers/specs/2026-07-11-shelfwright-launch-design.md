# Shelfwright launch: domain, repo rename, PyPI claim, UI branding (GH #79 + rebrand)

**Date:** 2026-07-11
**Issue:** GH #79 — Frontend infra evolution: custom domain (+ new rebrand scope)
**Related:** [2026-07-07 custom-domain spec](2026-07-07-frontend-infra-custom-domain-design.md) (approved), #78 / ADR-055 (same-origin auth proxy), 2026-07-06 `redirect_uri_mismatch` regression
**Status:** Design approved — implementation plan to follow

## Context

The 2026-07-07 spec approved Cloud Run domain mapping (Option B) with one open item: the
domain name. That's now resolved — the product is named **Shelfwright**, and the operator
has registered **shelfwright.app** (confirmed on Cloudflare DNS) and created an empty
`shelfwright` GitHub repo. `shelfwright` is unclaimed on PyPI (verified 2026-07-11).

This spec is an **addendum**: it adapts the approved domain design to the chosen name and
adds three rebrand workstreams. The base spec's architecture, option analysis, auth
mechanics, verification, and rollback all carry forward — only deltas are recorded here.

## Decisions

1. **Canonical host = apex `shelfwright.app`** (not `app.<domain>` as the base spec
   sketched). `www.shelfwright.app` redirects to the apex via Cloudflare.
2. **Rename the existing GitHub repo** `agentic_librarian` → `shelfwright` (delete the
   empty placeholder repo first). Code-level renames (Python package, imports,
   directories) are **out of scope** — later or never.
3. **Claim `shelfwright` on PyPI** with a real-but-minimal 0.0.1 package, uploaded
   manually once via `twine` + API token.
4. **UI branding: Shelfwright product, Librarian persona.** Product surfaces (tab title,
   top bar, sign-in) say Shelfwright; the chat character stays "the Librarian".

## Workstream 1 — apex domain mapping (delta from base spec)

Two mechanics change because the mapped host is an apex, not a subdomain:

- **Ownership verification first.** Apex mappings require verifying the domain with
  Google: `gcloud domains verify shelfwright.app` → Search Console flow → TXT record in
  Cloudflare. Operator step (browser + Google account).
- **A/AAAA records, not CNAME.** The mapping returns 4 A + 4 AAAA records. Add them in
  Cloudflare as **DNS only / grey-cloud** — orange-cloud proxying blocks Google's
  managed-cert issuance (per base spec).

Then per the base spec, unchanged:

1. `gcloud beta run domain-mappings create --service=librarian-api --domain=shelfwright.app --region=us-central1`
2. Wait for managed cert = ACTIVE (up to ~24h).
3. Firebase → Authentication → Authorized domains: add `shelfwright.app`.
4. GCP Web OAuth 2.0 client: add redirect URI `https://shelfwright.app/__/auth/handler`
   and JS origin `https://shelfwright.app`. Keep both `run.app` handlers registered
   through the transition.
5. Record **ADR-056** (Cloud Run domain mapping over LB/Hosting, apex host, rationale).

**www redirect (new):** in Cloudflare, add a proxied (orange-cloud) placeholder DNS
record for `www` and a redirect rule `www.shelfwright.app/*` → `https://shelfwright.app/$1`
(301). Cloudflare serves the redirect itself — it never reaches Cloud Run, so it can't
interfere with the grey-cloud apex records or cert issuance. Cloudflare Universal SSL
covers `www.shelfwright.app` (required — `.app` is HSTS-preloaded, HTTPS-only).

Verification and rollback: as in the base spec (fresh-incognito new-user sign-in, SSE
`/chat` stream, `run.app` still serving, `curl -sI https://shelfwright.app/__/auth/iframe.js`
→ 200), plus `curl -sI https://www.shelfwright.app` → 301 to the apex.

## Workstream 2 — repo rename

1. Confirm the placeholder `shelfwright` repo is truly empty; delete it (frees the name).
2. `gh repo rename shelfwright` on `agentic_librarian`. GitHub redirects old URLs;
   issues/PRs/stars/history are preserved.
3. **Fix the GCP Workload Identity Federation binding** — the critical non-obvious step.
   Deploys authenticate via WIF (`deploy.yml` uses `vars.GCP_WIF_PROVIDER`), and the
   provider's attribute condition and/or the deploy service account's
   `principalSet://…/attribute.repository/jaydee829/agentic_librarian` IAM binding pins
   the **repo path**. GitHub's rename redirect does not apply: post-rename OIDC tokens
   carry the new `repository` claim, so deploys fail until the GCP-side reference is
   updated to `jaydee829/shelfwright`. Update it immediately after the rename.
4. **Verify with a manual `workflow_dispatch` deploy** (smoke green) before considering
   the rename done.
5. Update local remotes (Windows clone at `C:\dev` and the WSL clone) and repo-URL
   references in docs/README.

## Workstream 3 — PyPI claim

PyPI prohibits empty name-squatting (PEP 541), so the claim is a real, minimal,
installable package with genuine project intent:

- Committed under `packaging/pypi-stub/`: `pyproject.toml` (name `shelfwright`, version
  `0.0.1`, description pointing at shelfwright.app and the repo), stub
  `shelfwright/__init__.py` with `__version__`, README linking site + repo.
- Build with `python -m build`, validate with `twine check`.
- **Operator uploads once** with `twine upload` using a pypi.org API token (account +
  token are operator prerequisites; exact steps in the plan).
- Trusted Publishing (OIDC from GitHub Actions) deferred until real releases exist.

## Workstream 4 — UI branding (Shelfwright product, Librarian persona)

Frontend-only PR:

- **Change to Shelfwright:** `frontend/index.html` `<title>`, `TopBar.tsx` title,
  `SignIn.tsx` heading; sweep for PWA manifest / meta tags naming the app.
- **Keep as Librarian (persona):** `ChatView.tsx` "Ask the Librarian…" placeholder,
  `activityLabels.ts` phrases, `client.ts` error copy, `RecommendationsView.tsx` "ask the
  Librarian in Chat" hint.
- QC via the existing headless visual-QC harness.

## Sequencing

All four workstreams are independent. Order chosen to overlap the domain's wait states:

1. Domain verification + mapping + DNS (cert provisions in background, up to ~24h)
2. Repo rename + WIF fix + deploy verification
3. PyPI stub PR + upload
4. Branding PR
5. When cert is ACTIVE: Firebase/OAuth registrations → live verification → ADR-056 →
   hand out `shelfwright.app`

## Operator runbook

Step-by-step operator guide (what/why/how/done-when for every console and account step):
[`docs/runbooks/shelfwright-launch.md`](../../runbooks/shelfwright-launch.md).

## Operator prerequisites (needed at execution time)

1. Search Console domain verification (browser, Google account)
2. Cloudflare DNS edits (manual, or an API token to script them)
3. Firebase Authorized-domains + Web OAuth client edits (same consoles as the #116 fix)
4. pypi.org account + API token for the one-time upload

## Acceptance criteria

- ADR-056 recorded.
- `https://shelfwright.app` serves the app with managed SSL ACTIVE; new-user Google
  sign-in verified fresh-incognito; SSE `/chat` streams; `run.app` hosts still work.
- `https://www.shelfwright.app` 301-redirects to the apex.
- GitHub repo is `shelfwright`; a post-rename `workflow_dispatch` deploy succeeds
  (WIF binding updated).
- `pip install shelfwright` installs the 0.0.1 stub from PyPI.
- App UI shows Shelfwright on product surfaces; Librarian persona copy unchanged.

## Rollback

- Domain: delete the mapping (`gcloud run domain-mappings delete`) — `run.app` hosts
  serve throughout; DNS/OAuth entries can remain harmlessly.
- Repo: rename back (GitHub allows it); restore the WIF binding string.
- PyPI: releases can be yanked, but the name claim is the point — no rollback needed.
- Branding: revert the PR.
