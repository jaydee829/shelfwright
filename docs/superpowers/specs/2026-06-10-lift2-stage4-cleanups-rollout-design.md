# Lift 2 Stage 4 — Cleanups + Rollout (design)

**Date:** 2026-06-10
**Status:** Approved (brainstormed with user)
**Branch:** `feat/lift2-stage4-cleanups-rollout` (PR-A) → held PR-B → operator rollout
**Parent spec:** `docs/superpowers/specs/2026-06-09-lift2-front-end-design.md` (§3 cleanups, §5 testing, §6 rollout).
**Roadmap:** ADR-046, Lift 2 of 4. Stages 1–3 are merged to `main`; this is the final stage that
**opens the Cloud Run IAM gate** and takes the friends-and-family beta **live**.

This stage carries the roadmap-mandated cleanups (DB-pool consolidation, off-loop writes, `/history`
pagination), the build/serving change that puts the SPA same-origin behind FastAPI, and the live GCP
rollout (Cloud Tasks queue/SA, prod secrets/env, the IAM-gate flip, the first prod migration, and live
verification). It introduces **no new database schema** — its migration step *applies the already-authored*
Stage 1 transcript migration to prod.

---

## Decisions locked (brainstorm)

| # | Decision | Choice | Why |
|---|----------|--------|-----|
| D1 | Decomposition | **Two code PRs + a separate operator rollout** | Isolate the seam-touching refactor (PR-A) from the rollout-enabling code (PR-B); keep the irreversible live-infra steps in a deliberate operator session, not bundled with code review. |
| D2 | IAM-gate-open mechanism | **Bundle `--allow-unauthenticated` into PR-B** | The durable open is a one-line `deploy.yml` flag flip (the deploy re-applies all flags, so a manual `gcloud` flip is reverted on the next merge). Bundling makes merging PR-B the deliberate gate-opening act — which **forces provisioning + the prod migration to happen *before* PR-B merges** (see §C). |
| D3 | Pool consolidation depth | **All 4 pools → one lifespan-injected `DatabaseManager`** | Fully closes the Stage 1 review note (`main`, `auth`, `chat/transcript`, `core/usage` each own a lazy pool today). INF-030's off-loop writes land cleanly on the one shared pool. |
| D4 | Live e2e verification | **CD `GET /` serves-SPA smoke + manual runbook checklist** | Playwright is only *transitively* present (`@vitest/browser-playwright`); automating Google-only Firebase sign-in is costly and low-ROI for a single-operator beta. The cheap automated guard catches the static-serving regression; the auth/LLM path is verified manually. Playwright logged as **TEST-034** (do before open signups). |
| D5 | Prod model/backend | **`AGENT_BACKEND=gemini`, `GEMINI_MODEL=gemini-3.1-flash-lite`** | Roadmap cost lock — cheapest model; Claude-in-prod is Lift 3. (`gemini-3.1-flash-lite` is already the code default in `runtime.py`/`services.py`/`cli.py` + `.env.example`; we set it explicitly in prod so the choice is pinned, not implicit.) |
| D6 | Instance cap | **`--max-instances` 1 → 2** | A ~2m30s deep-enrichment task and a user's chat should not contend for a single instance; still bounds spend. |
| D7 | `/history` pagination scope | **End-to-end (backend params + frontend "Load more")** | Backend-only would be a **regression**: `/history` returns *all* rows today, so adding a default `limit` while the frontend stays param-less would silently truncate a large history (the operator has 330 books). |

---

## §A — PR-A: the seam refactor (behavior-preserving, no infra)

**Goal:** consolidate the four `DatabaseManager` pools into one, and move the two per-chat-turn DB writes
off the asyncio event loop. No endpoint behavior changes; prod stays IAM-gated throughout.

**Pool consolidation (INF-030 companion).** Today four modules each construct a lazy `DatabaseManager`
(`api/main.py:25`, `api/auth.py:27`, `chat/transcript.py`, `core/usage.py`). Build **one** manager in a
FastAPI **lifespan** handler, store it on `app.state.db_manager`, and inject it into all four modules.
Keep the existing `set_db_manager(...)` override seams (the `mcp/server.py` pattern) so the test fixtures
(`test/integration/test_books_api.py` monkeypatches `api_main.db_manager` + `two_phase.db_manager`;
transcript/usage have their own) keep working — update them to point at the shared manager.

**Off-loop writes (INF-030).** The SSE chat turn issues synchronous INSERTs on the event loop in two
places — `transcript.append_message` (via `on_persist` in `api/main.py:188`) and
`usage.record_llm_call` (in `runtime._record_event_usage`). Wrap **both** in `asyncio.to_thread(...)`
so they run on a worker thread. `asyncio.to_thread` copies the current context, so `current_user_id` /
`as_user` still resolve inside the worker — **prove it with a test** (a scoping assertion from inside the
threaded write), since metering and transcript writes are user-scoped.

**Tests (TDD):**
- One shared engine/pool: the four modules resolve to the *same* `DatabaseManager` instance after startup.
- Off-loop write still resolves the user context: a `record_llm_call` / `append_message` invoked through
  the `to_thread` path writes under the correct `user_id` and fails closed with no identity.
- The full fast suite (`pytest -m "not api_dependent and not slow and not live"`) stays green.

**Merge:** reviewed/merged like Stages 1–3 (Gemini review + CI). Merging PR-A triggers a normal prod
deploy — **safe**, because the change is behavior-preserving and the gate stays **closed**.

---

## §B — PR-B: rollout-enabling code (reviewed, then **held** until the operator rollout is done)

PR-B is opened and reviewed but **not merged** until §C's provisioning + migration are complete — because
merging it opens the IAM gate (D2).

1. **Multi-stage `Dockerfile.api`.** Add a Node build stage that compiles the Vite SPA from `frontend/`
   (`npm ci && npm run build`); the existing Python runtime stage copies the `dist/` output into the image.
   The runtime still has **no Node** — only the build stage does.

2. **Same-origin static serving.** FastAPI serves the built SPA at `/`: mount the static assets and add an
   `index.html` fallback for non-API paths so React-Router deep links (`/add`, `/history`, …) resolve to the
   app shell. API routes (`/health`, `/health/db`, `/chat`, `/conversations*`, `/books`, `/internal/*`,
   `/recommendations*`, `/analysis*`, `/history`, `/works`) keep precedence over the fallback. `/health`
   stays unauthenticated.

3. **`/history` pagination (INF-029) — end-to-end.**
   - Backend (`api/main.py:get_history`): add `limit: int = Query(50, ge=1, le=200)` and
     `offset: int = Query(0, ge=0)`, applied with `.offset(offset).limit(limit)` after the existing
     `order_by(date_completed.desc())` — mirroring `/works`.
   - Frontend: `client.ts` `getHistory(limit, offset)` passes the params; `HistoryView` gains a simple
     **"Load more"** that appends the next page, so every row stays reachable (no truncation regression).

4. **`deploy.yml` changes.**
   - `paths:` filter gains `frontend/**` (and the multi-stage `Dockerfile.api` already matches) so a
     SPA-only change redeploys.
   - Flip `--no-allow-unauthenticated` → `--allow-unauthenticated` (**the gate open**, D2).
   - `--set-secrets` gains the three deep-scout API keys (Gemini, Google Books, Hardcover) alongside
     `DATABASE_URL`.
   - `--set-env-vars` gains the enrichment group + model/backend:
     `CLOUD_TASKS_QUEUE`, `ENRICH_TARGET_BASE_URL`, `ENRICH_INVOKER_SA`, `ENRICH_OIDC_AUDIENCE`,
     `AGENT_BACKEND=gemini`, `GEMINI_MODEL=gemini-3.1-flash-lite` (plus the existing
     `SIGNUP_MODE=invite`, `GOOGLE_CLOUD_PROJECT`).
   - `--max-instances` 1 → 2.
   - Live smoke gains an **unauthenticated `GET /` returns the SPA shell** assertion (HTML containing the
     app root) alongside the existing `/health` + `401`-without-Firebase checks.

5. **Committed infra scripts** (operator-run in §C), following the existing `infra/0N` numbering:
   - `infra/08-cloud-tasks.sh` — create the Cloud Tasks queue, the **invoker service account**, and grant it
     `roles/run.invoker` (it calls the internal route as an OIDC-signed request).
   - `infra/09-prod-secrets.sh` — create the three API-key secrets and grant the Cloud Run **runtime** SA
     `roles/secretmanager.secretAccessor` on each.

6. **`security.md` update.** Record the new boundary: the **Cloud Run IAM gate is OPEN**; Firebase is the
   **sole** gate on every user-facing route; `/health` is open; the internal enrich route is **queue-OIDC-gated**
   (verifies the queue SA's OIDC token, hard-requires the audience). Mark the transport-level single-user
   assumptions (SEC-001/SEC-002 residual-risk arguments, no rate limiting) as **now expiring** — full
   re-review is Lift 3.

7. **Rollout runbook.** `docs/runbooks/lift2-stage4-rollout.md`, modeled on the Lift 1 runbook: the ordered
   §C steps, the `ENRICH_OIDC_AUDIENCE` gotcha (below), the manual verify checklist, and the rollback plan.

**The `ENRICH_OIDC_AUDIENCE` gotcha (capture in the runbook).** Both the enqueue side
(`enrichment/tasks.py:44`, which **defaults the OIDC audience to the per-task URL if the var is unset**) and
the receiver (`api/internal.py:40`, which **fails closed if the var is unset** and verifies the token's `aud`
against it) read the **same** env var on the **same** service. Setting `ENRICH_OIDC_AUDIENCE` **once** to the
Cloud Run service base URL makes the stamped audience and the verified audience agree automatically. Leaving
it unset on the receiver is a hard 403; leaving it unset on the enqueue side (default-to-task-URL) while the
receiver expects a fixed value is a silent mismatch → 403 on every enrichment. **Always set it explicitly.**

---

## §C — Operator rollout (separate session; gate stays CLOSED until the final merge)

This ordering is **forced by D2** — merging PR-B opens the gate atomically with the new image, so everything
the newly-reachable surface depends on (schema, secrets, queue) must exist first.

1. **(gate closed) Provision.** Run `infra/08-cloud-tasks.sh` + `infra/09-prod-secrets.sh`: queue + invoker
   SA + `run.invoker` grant; the three key secrets + `secretAccessor` grants. Decide the
   `ENRICH_OIDC_AUDIENCE` value (the Cloud Run service base URL) — it goes into PR-B's `deploy.yml` env.
2. **Back up prod Cloud SQL** (`gcloud sql export …`). Per spec §6, this rollout is the **"first prod write"**
   boundary — from here, back up before every migration.
3. **Apply the migration.** Bring prod from the Lift 1 head `c804d02d6fbb` to the Stage 1 head
   **`30f1e46533e9`** (chat transcript store: `conversations`, `messages`, `usage.conversation_id` FK) via
   cloud-sql-proxy + the docker `alembic upgrade head` wrapper. Verify prod is at head.
4. **Sanity.** The current (old, Stage 1–3 code) image is still healthy and the gate is still closed.
5. **Merge PR-B.** CD builds the multi-stage image, deploys with the new env/secrets, and **opens the gate**.
   CD live smoke passes: `/health` green, `401`-without-Firebase, `GET /` serves the SPA.
6. **Manual live verify** (the §6 checklist): Google sign-in → a streamed chat turn (live activity + reply)
   → add-a-book → **deep enrichment completes ~2 min later** (tropes appear; confirms the Cloud Task fired and
   the queue-OIDC internal route accepted it) → a **metered usage row** was written. Confirm `/health` open and
   the SPA loads.
7. **Cost watch.** Confirm budget alerts are live; eyeball the first real usage rows; confirm the
   `max-instances=2` cap.

**Rollback (contingency — only if step 6 fails or the deploy is broken):** revert PR-B. That re-closes the
gate (`--no-allow-unauthenticated`) and reverts the image in one move. The applied migration is **additive
and safe to leave** (the unused tables are harmless); no down-migration is run.

---

## §D — Testing

**PR-A (offline, CI):** the shared-pool assertion; the off-loop-write user-scoping assertions (incl.
fail-closed with no identity); the full fast suite stays green.

**PR-B (offline, CI):** `/history` pagination — `limit`/`offset` bounds (422 outside range), ordering
preserved, page windows correct; the frontend `HistoryView` "Load more" appends a page (Vitest + RTL, backend
mocked); the multi-stage image builds and the smoke (`/health`, `401`, **`GET /` SPA**) passes in the deploy
job's runner step.

**Live (operator-run, never CI):** the §C-6 manual checklist. Playwright happy-paths are **deferred** to
TEST-034 (before open signups).

**Discipline (Lift 1 lessons):** scoping/security tests stay mutation-minded (a deleted filter must fail a
test); live/api_dependent tests never run in CI. The CD smoke now asserts **both** "`GET /` serves the SPA"
and the existing `401`-enforcement.

---

## §E — Deliverables checklist

- **PR-A:** lifespan-injected shared `DatabaseManager` (4 → 1); `asyncio.to_thread` for `append_message` +
  `record_llm_call`; updated test seams + new shared-pool / off-loop-scoping tests.
- **PR-B:** multi-stage `Dockerfile.api`; FastAPI SPA static serving + fallback; `/history` pagination
  (backend + frontend "Load more"); `deploy.yml` (paths, gate flip, secrets, env, max-instances, `GET /`
  smoke); `infra/08-cloud-tasks.sh`; `infra/09-prod-secrets.sh`; `security.md` boundary update;
  `docs/runbooks/lift2-stage4-rollout.md`.
- **Operator rollout:** provision → backup → migrate → sanity → merge PR-B (gate opens) → manual verify →
  cost watch.

---

## §F — Out of scope / deferred

- **Playwright e2e harness — TEST-034** (do before open signups, Lift 3): real `@playwright/test` + config +
  browsers + a Firebase-token-injection sign-in helper; operator-run, never CI.
- **DOC-031** (consolidated dev-setup doc) — Lift 2 wrap-up, tracked separately; may ride alongside the
  Stage 4 runbook but is not gated by it.
- Per-user quotas / rate limiting, Stripe billing, BYOK, Claude-in-prod, email/password sign-in — **Lift 3**.
- Conversation windowing/summarization, switchable chat list, PWA, Firebase Hosting migration — future-logged
  in the parent spec §7.

---

## §G — Risks & sequencing notes

- **The provision-and-migrate-*before*-merge ordering (D2) is the load-bearing constraint.** If PR-B merges
  before the migration, the freshly-opened gate exposes a `/chat` that 500s on a missing `conversations`
  table; before the secrets exist, `deploy.yml`'s `--set-secrets` fails the deploy. The runbook front-loads
  both.
- **Opening the gate exposes nothing new even if a downstream piece is misconfigured:** `SIGNUP_MODE=invite`
  + Firebase gate every user route, `/health` is intentionally open, and the internal route is queue-OIDC-gated
  independent of the IAM gate. A missing/incomplete Cloud Tasks setup degrades to **enrichment no-ops**
  (`enqueue_enrichment` returns `False` and logs), not an exposure.
- **No down-migration risk:** the migration is additive; rollback leaves it in place.
