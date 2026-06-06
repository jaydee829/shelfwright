# Product Roadmap — Web App, GCP, Multi-User Sequencing

**Date:** 2026-06-05
**Status:** Approved (brainstormed with user)
**Branch:** `spec/product-roadmap`

This is a SEQUENCING spec: it decomposes three heavy lifts into ordered sub-projects and
locks the cross-cutting decisions. Each lift gets its own spec → plan → implementation
cycle when it starts. Nothing here is an implementation plan.

## Vision (user)

Start with friends & family soon; move to open signup with a subscription model that
covers tokens + hosting (maybe a small margin if popular). Users can BYO API key for a
much cheaper tier, or use the app's key at a higher price.

## The three lifts and their couplings

1. **Front end (Phase 4)** — FastAPI + web UI. The FastAPI surface is the contract the
   deployment ships and the multi-user boundary enforces. A scaffold exists
   (`13-phase-4-web-interface-and-analysis` branch: FastAPI app + `GET /history`).
2. **GCP deployment** — Cloud Run + Cloud SQL (pgvector supported). Coupling: the Claude
   backend's Max-subscription OAuth does NOT deploy; production Claude means
   `ANTHROPIC_API_KEY` (see LLM strategy). MLflow/Dagster are dev tools and do not deploy.
3. **Multi-user** — the most invasive: schema (`user_id`), identity, isolation — and it
   expires the "single-user system" assumptions recorded in `security.md` (SEC-001/002
   residual-risk reasoning, no rate limiting, no transport auth). Coupling: the auth
   provider choice is GCP-informed, so multi-user design follows platform contact.

## The sequence

| # | Lift | Delivers | Why this position |
|---|------|----------|-------------------|
| 0 | **Walking skeleton** — GCP project, Cloud Run service running the existing FastAPI scaffold, Cloud SQL Postgres+pgvector with the catalog restored from pg_dump, Secret Manager, GitHub Actions CD, access-gated (IAP or app-level gate — Lift-0 spec decides) | A real URL serving the enriched catalog | Learn ALL the unfamiliar GCP plumbing on a system too small to hide bugs; converts deployment from a finale into continuous delivery |
| 1 | **Multi-user foundation** — users table, `user_id` on reading_history/suggestions with a default-user migration (existing 331 history rows → user #1), Firebase Auth on the FastAPI layer, per-user scoping through the MCP tools, `usage` metering table (keyed by user AND key-source), `user_credentials` placeholder (KMS-encrypted, BYOK-ready, feature later) | Friends can sign in; data isolated; usage recorded from day one | The invasive schema work while the surface is small; auth must precede any sharing, so it precedes the FE |
| 2 | **Front end** — chat UI over the conversational Librarian via FastAPI, history/analysis views, add-book form (completion-date field AUTO-FILLED with today, visible+editable, per IMP-028) | 🎯 **Friends & family beta** | Built once against the real auth'd contract; framework choice (Vite SPA vs Next.js) decided in this lift's spec |
| 3 | **Productization** — per-user token metering enforcement + quotas, Stripe subscription, **BYOK feature** (key validation, per-key routing; pricing tiers: BYOK ≈ hosting-only, app-key ≈ tokens + margin), **Claude prod enablement** (see LLM strategy), **security posture re-review** (single-user assumptions formally expire), observability, cost tuning | 🎯 **Open signup** | The business layer, after real usage data exists |

## Cross-cutting decisions (locked)

- **Infrastructure: Cloud Run** (user decision) — scale-to-zero economics fit the
  project's stage; Cloud SQL Postgres with pgvector (schema ports cleanly).
- **Prod LLM: Gemini-first** (paid tier; the ADK backend is live-proven; cheapest
  per-conversation; one vendor to meter). **Claude-readiness is a standing constraint,
  not a later rewrite**: per the Anthropic billing change the user cites (paid users'
  Agent SDK usage moves to a monthly credit that exhausts before other billing,
  ~mid-June 2026), Agent-SDK-with-API-key becomes viable. The SDK already honors
  `ANTHROPIC_API_KEY`, so Claude prod enablement ≈ env config + bundled-CLI-in-container
  verification + second-vendor metering. Therefore: **Lift 0 keeps `AGENT_BACKEND`
  env-configurable in prod config**, and the enablement task sits in Lift 3 (may pull
  earlier once the billing change lands).
- **BYOK** (user decision): users may bring their own API key (cost drops to ~hosting)
  or use the app's key (higher tier). Schema accommodates it from Lift 1
  (`user_credentials`, encrypted at rest via Cloud KMS — never plaintext, never logged;
  security.md gains the handling rule); the FEATURE lands in Lift 3 with metering
  attribution by key source.
- **Auth: Firebase Auth in Lift 1.** (Clarified: Firebase Authentication and Identity
  Platform are distinct tiers of the SAME underlying Google identity service — Identity
  Platform adds enterprise features (SAML/OIDC, multi-tenancy, SLA) with per-MAU
  billing, and upgrading a project is an in-place toggle, not a migration. Start
  consumer-tier; the upgrade path is preserved.)
- **Shared catalog, per-user history.** Works/editions/tropes/styles are communal — one
  user's enrichment grows the library for everyone; reading_history/suggestions/usage
  are per-user. (The 326-work enriched catalog seeds the product.)
- **MLflow + Dagster stay local/dev.** They do not deploy.
- **Claude backend remains a local/dev capability** on the Max subscription until the
  Lift-3 enablement; the conversation recorder's MLflow capture is dev-only.

## Accepted technical debt

- **DEBT-001: Bulk enrichment service.** Bulk imports stay operator-run local Dagster
  (near term: friends send the operator a CSV). Future shape when user-facing bulk
  import earns its keep: a Cloud Run Job + queue running the same partition flow. Not
  designed now (user decision).

## What each lift's spec must decide (deferred, not forgotten)

- Lift 0: IAP vs app-level gate; GCP project/region/budget alarms; CD shape; how the
  pg_dump restore is performed and verified.
- Lift 1: exact user model; default-user migration mechanics; per-user scoping seam in
  the MCP tools (parameter vs context); Firebase Auth integration pattern on FastAPI;
  usage-table schema.
- Lift 2: FE framework (Vite SPA vs Next.js); hosting (static vs Cloud Run); chat
  transport (SSE/websocket vs request-response); which analysis views ship in beta.
- Lift 3: pricing numbers; Stripe integration shape; quota policy; rate limiting; the
  security re-review scope; Claude enablement verification.

## Out of scope (this roadmap)

- Mobile apps, A2A external mesh (ADR-035 deferral stands), series schema columns
  (separate tracked follow-up), the Explorer citation surfacing (pairs with a future
  security pass).
