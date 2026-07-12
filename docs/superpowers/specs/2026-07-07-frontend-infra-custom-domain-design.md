# Frontend infra: custom domain via Cloud Run domain mapping (GH #79)

**Date:** 2026-07-07
**Issue:** GH #79 — Frontend infra evolution: custom domain + CDN
**Related:** #78 / ADR-055 (same-origin auth proxy), 2026-07-06 `redirect_uri_mismatch` regression (bugs.md)
**Status:** Design approved — implementation plan to follow

## Problem & drivers

Two concrete drivers make #79 worth doing now (not the CDN/perf trigger the issue also lists):

1. **Kill the OAuth multi-host fragility.** #78 (PR #85) set `authDomain = window.location.host`, so Firebase's Google sign-in `redirect_uri` is now the app's *serving host*. Cloud Run exposes the service on ≥2 hostnames (`librarian-api-hnucndzntq-uc.a.run.app` and `librarian-api-388776616965.us-central1.run.app`), so **every** host must be registered in the Web OAuth client or new users get `Error 400: redirect_uri_mismatch` (hit in prod 2026-07-06). A single **canonical host** eliminates this class of bug permanently.
2. **A branded custom domain** for the friends-and-family → wider launch, nicer to share than a `*.run.app` URL.

CDN/performance and retiring the #78 proxy are explicitly **not** drivers here.

## Decision

Adopt **Cloud Run custom domain mapping** (Option B): map a subdomain (e.g. `app.<domain>`) directly onto the existing Cloud Run service. Document the **Load Balancer + serverless NEG** path (Option C) as a costed, trigger-based future upgrade. This will be recorded as a new ADR (ADR-057).

### Why B over A and C

| Option | ~$/mo | Verdict |
|---|---|---|
| **A. Firebase Hosting + domain** | ~$1 | Rejected. Free CDN + retires the #78 proxy, but **splits the single-origin app** and routes the **SSE `/chat`** stream through Hosting rewrites (60s timeout / buffering risk) for a benefit (CDN) that isn't a driver. |
| **B. Cloud Run domain mapping + domain** ✅ | ~$1 | **Chosen.** Least change; single origin preserved so SSE and the #78 proxy are untouched. `us-central1` is in the supported region list. Accepts the "Preview" caveat at F&F scale. |
| **C. Load Balancer + serverless NEG (+ Cloud CDN) + domain** | ~$19–20 | Deferred. Production-grade, SSE-safe, CDN/WAF-ready, but the ~$18.25/mo forwarding-rule floor dominates at this scale. Additive upgrade from B — a swap-in, not a redo. |

All three deliver the single canonical host that fixes the OAuth fragility; B does it with the least disruption and cost.

## Architecture (unchanged, single-origin)

No change to serving topology. The FastAPI container keeps serving SPA + API + SSE. The custom domain is mapped onto the *same* Cloud Run service, so:

- The #78 same-origin `/__/auth/*` proxy (`api/firebase_auth_proxy.py`) and runtime `authDomain = window.location.host` (`frontend/src/auth/authDomain.ts`) **carry forward unchanged** — `authDomain` simply resolves to `app.<domain>`.
- SSE `/chat` is unaffected — no new proxy layer in the request path.
- The canonical host becomes `app.<domain>`; `*.run.app` hosts keep serving during and after transition (no regression).

## Cost breakdown (verified 2026-07, real sources)

**Domain registration (new).** Recommend **Cloudflare Registrar** — at-cost, no renewal markup: **~$10.44/yr `.com`**, **~$14/yr `.app`**. Alternatives: Namecheap (low intro / higher renewal), Squarespace Domains ($20/yr flat). ⚠️ **Google Cloud Domains is deprecated** — no new registrations. `.app` is HSTS-preloaded (HTTPS mandatory) — fine, all options provide free managed SSL.

- Cloudflare Registrar: https://www.cloudflare.com/products/registrar/
- Namecheap `.app`: https://www.namecheap.com/domains/registration/gtld/app/
- Squarespace Domains: https://domains.squarespace.com/google-domains
- Cloud Domains deprecation: https://docs.cloud.google.com/domains/docs/deprecations/feature-deprecations
- `.app` HTTPS/HSTS: https://www.registry.google/policies/pricing/app/

| Option | Recurring infra | + Domain | **~Total/mo** |
|---|---|---|---|
| **A. Firebase Hosting** ([pricing](https://firebase.google.com/pricing)) | $0 (Spark: 10 GB store, ~10 GB/mo transfer, free SSL+CDN) | ~$1 | ~$1 |
| **B. Cloud Run domain mapping** ([docs](https://docs.cloud.google.com/run/docs/mapping-custom-domains)) ✅ | $0 (free managed cert) | ~$1 | **~$1** |
| **C. LB + serverless NEG** ([net pricing](https://cloud.google.com/vpc/network-pricing)) + [Cloud CDN](https://cloud.google.com/cdn/pricing) | ~$18.25/mo LB base + $0.008/GiB data; CDN egress from $0.08/GiB | ~$1 | ~$19–20+ |

Firebase Blaze overage (for reference): $0.026/GB stored, $0.15/GB transferred.

## Auth / OAuth impact (closes the fragility)

Once users load `app.<domain>`, `authDomain` resolves there. Required registrations:

1. **Firebase → Authentication → Authorized domains:** add `app.<domain>`.
2. **GCP → APIs & Services → Credentials → Web OAuth 2.0 client:**
   - Authorized redirect URIs: add `https://app.<domain>/__/auth/handler`.
   - Authorized JavaScript origins: add `https://app.<domain>`.
3. **Keep the two `run.app` handlers registered** through the transition (existing sessions / bookmarks). Hand out only `app.<domain>` going forward — one canonical host stops the redirect_uri churn.

## DNS / registrar how-to

Registered at Cloudflare (Registrar forces Cloudflare DNS). Add the A/AAAA (+ CNAME) records Cloud Run's mapping provides as **"DNS only" / grey-cloud (unproxied)** — Cloudflare's orange-cloud proxy interferes with Google's managed-cert issuance. Managed-cert provisioning can take up to ~24h; sign-in on the custom domain is not ready until the cert is ACTIVE.

## Migration steps (ordered, reversible)

1. Register `<domain>` at Cloudflare.
2. `gcloud run domain-mappings create --service=<svc> --domain=app.<domain> --region=us-central1` → obtain DNS records.
3. Add the records in Cloudflare DNS as grey-cloud (DNS only); wait for the managed cert = ACTIVE.
4. Register `app.<domain>` in Firebase Authorized domains + the OAuth redirect URI / JS origin.
5. **Verify** (below) on `app.<domain>` with a fresh incognito Google account.
6. Hand out the new URL. *(Optional / later: redirect `*.run.app` → `app.<domain>`.)*

## Verification

- Fresh-incognito **new-user** Google sign-in on `app.<domain>` → completes, no `redirect_uri_mismatch`.
- SSE `/chat` streams correctly on `app.<domain>`.
- `*.run.app` URL still works (no transition regression).
- `curl -sI https://app.<domain>/__/auth/iframe.js` → `200` with JS content-type (proxy still same-origin).

## Option C — documented upgrade path (not built now)

Migrate to a **global external Application Load Balancer + serverless NEG** (optionally + Cloud CDN) when any trigger fires:

- Need production SLA / global anycast, **or**
- A serving region **outside** Cloud Run domain-mapping's ~10 supported regions, **or**
- Want Cloud CDN caching for static assets as traffic grows, **or**
- Need a custom TLS policy (mapping can't disable TLS 1.0/1.1 or upload own cert).

Cost floor: **~$18.25/mo** (forwarding rule) + $0.008/GiB processed. It preserves the same single origin, so it's additive — the `authDomain`/proxy logic and DNS target change, nothing in the app. Record as a new ADR when triggered.

## Acceptance criteria (from #79)

- ADR recorded (ADR-057) choosing Cloud Run domain mapping over LB/Hosting, with rationale.
- `app.<domain>` mapped + managed SSL ACTIVE.
- Firebase Authorized domains + Web OAuth redirect URI/origin updated for `app.<domain>`.
- #78 same-origin proxy **retained** (still same-origin under the custom domain).
- New-user sign-in verified on `app.<domain>`; `*.run.app` still functional.

## Rollback

Domain mapping is additive and `*.run.app` keeps serving throughout. Delete the mapping (`gcloud run domain-mappings delete`) to revert instantly — no data or schema change. The OAuth redirect URIs / Authorized domains can be left registered harmlessly.

## Open items (operator choices, not blockers)

- **Domain name / TLD** — pick the registrable name (`.com` ~$10/yr vs `.app` ~$14/yr).
- **`*.run.app` → custom-domain redirect** — scoped as optional/later.
