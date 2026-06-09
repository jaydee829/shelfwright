# Lift 2 — Front End (Friends & Family Beta)

**Date:** 2026-06-09
**Status:** Approved (brainstormed with user)
**Branch:** `feat/lift2-front-end`
**Roadmap:** ADR-046 (`docs/superpowers/specs/2026-06-05-product-roadmap-design.md`), lift 2 of 4. Builds on
Lift 0 (ADR-047, GCP walking skeleton) and Lift 1 (ADR-048, multi-user foundation). A new ADR-049
will record this lift's decisions at implementation time.

This is the design for the **friends & family beta**: a chat-centric web app over the conversational
Librarian, served from the existing FastAPI/Cloud Run service, gated solely by Firebase Auth.

---

## Decisions locked (brainstorm)

| # | Decision | Choice | Why |
|---|----------|--------|-----|
| D1 | Information architecture | **Chat-centric** | The Librarian conversation is the product's personality; ship depth in one place, not four thin screens. |
| D2 | Frontend framework | **Vite + React (TypeScript)** | Auth-gated app → no SSR/SEO need; a static SPA over the existing FastAPI is the least machinery. TS catches shape errors early. |
| D3 | SPA hosting | **Served from the FastAPI container** (same origin) | One origin (no CORS), one deploy, reuses the existing CD. Migrating to Firebase Hosting later is config-only (a `firebase.json` rewrite), so it's not a one-way door. |
| D4 | Chat transport | **SSE** (Server-Sent Events) | A mesh turn takes seconds across multiple agents; stream reply text **and** live agent/tool activity off the runtime's existing `on_event` hook. WebSocket is more than turn-based chat needs. |
| D5 | Conversation memory | **Owned transcript in Postgres** | Survives Cloud Run scale-to-zero / multi-instance recycling, is backend-agnostic, and *becomes* the resume / future chat-history feature. The Lift 1 `usage.conversation_id` column finally gets a table to reference. |
| D6 | Chat screen | **Single active thread** + Resume + New chat | Smallest UI surface; "resume my conversation" is a natural mental model. Transcript stored either way; the switchable list (option B) is a no-migration add later. |
| D7 | Navigation | **Chat · History · Recommendations · Analysis · Add** | Side icon rail on desktop; bottom nav bar on mobile. |
| D8 | Analysis views (beta) | **Reading snapshot · Genre & mood mix · Top tropes · Authors & narrators** | Buildable from existing data; *Top tropes* (the vectorized-trope fingerprint) is the signature view. Ratings-over-time and taste-profile deferred. |
| D9 | Recommendations view | **Actionable** | Reads the Lift 1 `Suggestions` table; **✓ I read this** → add-book prefilled → status `Read`; **Not for me** → status `Dismissed`. Closes the suggest→read→rate loop. |
| D10 | Add-a-book form | Title + Author (required) · Format/Rating/Notes (optional) · **Date finished pre-filled today, editable** | IMP-028. Look-up-on-submit (no autocomplete in beta). Re-reads = a new read-event row (existing model). |
| D11 | Enrichment timing | **Two-phase** | Fast pass (API scouts, ~seconds) creates the Work + logs the read immediately; deep pass (LLM scouts, ~2m30s) runs in the background. The user never waits on the LLM. |
| D12 | Deep-enrichment mechanism | **Cloud Tasks → internal endpoint** | Cloud Run throttles CPU after a response, so an in-process background thread is unreliable. A queued task re-enters the service as a fresh request with full CPU + long timeout. The async-work pattern needed at open signup, learned on a small surface. |
| D13 | Sign-in | **Google only** (Firebase JS SDK) | Every friend has a Google account; simplest path; already enabled in Lift 1. Email/password deferred to open signup. |
| D14 | Responsiveness | **Mobile + desktop browser** (one codebase) | A React SPA runs in any modern browser; responsive layout (mobile-first chat, rail→bottom-nav) makes it usable on a phone. Native apps and PWA are out of scope (PWA logged as future). |

---

## §1 Architecture overview

A Vite + React SPA is compiled during the Docker image build and served as static files by the existing
FastAPI app on Cloud Run — **one origin, one deploy**. The browser signs in with Firebase (Google), receives
an ID token, and attaches it as `Authorization: Bearer <token>` on every API/SSE call — the exact token the
Lift 1 auth dependency already verifies. The **Cloud Run IAM gate opens** (browsers cannot attach a Google IAM
identity token), making **Firebase the sole gate**; `SIGNUP_MODE=invite` keeps the door closed to the uninvited.

New FastAPI surface (all Firebase-gated unless noted):

| Route | Purpose |
|-------|---------|
| `POST /chat` | SSE stream — drives the mesh, streams activity + reply, persists transcript + usage |
| `GET /conversations/current` (+ messages) | Resume the active thread |
| `POST /conversations` | Start a new chat |
| `POST /books` | Add-a-book fast pass: identity match → log read-event → enqueue deep enrichment |
| `POST /internal/enrich/{work_id}` | **Cloud Tasks target** (queue-OIDC-gated, not Firebase) — deep LLM pass |
| `GET /recommendations` · `POST /recommendations/{id}/status` | Suggestions list + Read/Dismissed actions |
| `GET /analysis/...` | The four beta analysis views |
| `GET /history` | Now **paginated** (`limit`/`offset`) — closes INF-029 |
| `GET /health` | Stays open (unauthenticated) |

**Mesh is already in the image:** `google-adk`, `google-genai`, `mcp`, `fastmcp`, and the scout libraries
are base `dependencies`, so `pip install .` in `Dockerfile.api` already bakes them in. "Deploying the mesh"
is *wiring* (endpoints + prod API keys + env), not packaging.

**Cost reality:** this is when prod begins **spending LLM tokens** (chat + enrichment). Lift 1's usage metering
starts producing real rows; the $25/mo budget guardrail becomes live-relevant. The prod model stays on Gemini
Flash-Lite (cheapest), per the roadmap lock.

---

## §2 Frontend

**Build & language.** Vite builds the React app (TypeScript) into static files copied into the image. TypeScript
is chosen to catch typos/shape errors before runtime.

**App shell.** One persistent frame: a top bar (app name · avatar + sign-out) and navigation that renders as a
**left icon rail on desktop** and a **bottom nav bar on mobile** (same components, layout keyed off width).
Client-side routing (React Router) swaps views without full reloads.

**The five views, each a focused component:**
- **Chat** (home) — message thread, live activity chip, input box, New chat / resume controls.
- **History** — paginated reading log, newest first.
- **Recommendations** — the Suggestions list with ✓ I read this / Not for me.
- **Analysis** — the four views (snapshot, genre/mood, top tropes, authors & narrators).
- **Add a book** — the form panel, opens over the current view.

**Firebase sign-in flow:**
1. Initialize the Firebase JS SDK with the web config; show **Sign in with Google** when logged out.
2. On sign-in the SDK provides a user + ID token; attach the token on every API/SSE call (SDK auto-refreshes).
3. **403 (verified but not invited)** → a friendly "ask the operator for an invite" screen. Signed-out → the
   sign-in screen. A sign-out button clears state.

**SSE chat client.** The browser's built-in `EventSource` cannot POST or set an `Authorization` header, so the
client consumes the stream with **`fetch()` + a streaming reader**: POST the message, read the `text/event-stream`
response incrementally. It parses three event kinds — **activity** (`Explorer is searching…` → chip), **text**
(reply chunks appended live), **done** (turn complete) — mapping onto the runtime's `on_event` hook + final response.

**Responsiveness** is a beta requirement: mobile-first chat layout, rail→bottom-nav, usable on a phone browser.

---

## §3 Backend

### A chat turn (`POST /chat`, SSE)

1. The Lift 1 auth dependency resolves the user and sets `current_user_id` — every MCP tool the mesh calls is
   already scoped to that friend (no change to the security seam).
2. The request carries the message and which conversation it belongs to (active, or fresh from New chat).
3. The backend loads that conversation's prior **messages** and **rehydrates** context — replaying them into a
   fresh in-memory mesh session — then sends the new message. Memory is reconstructed from the DB each turn,
   so it survives Cloud Run recycling.
4. SSE events stream off `on_event` (activity) plus reply text, then `done`.
5. On completion, the user message and assistant reply are written to **messages**. Usage rows (Lift 1, per LLM
   call) now carry a real `conversation_id`.

Because the transcript is owned as plain text, the turn is **backend-agnostic** (ADK/Gemini in prod, Claude in
dev). *Deferred:* replaying the full thread each turn grows token cost on long conversations; windowing/
summarization is future work (beta conversations are short).

### Two-phase enrichment

- `POST /books` runs a **fast pass** — `ScoutManager` restricted to the API scouts (Hardcover, Google Books;
  priorities 1–2). On a match it persists the Work + Edition with basic metadata, logs the read-event (the
  existing `add_book_to_history` logic minus the slow scouts), **enqueues a Cloud Task** for the deep pass, and
  returns the logged book. No match → an honest "couldn't find it."
- `POST /internal/enrich/{work_id}` is the **Cloud Tasks target** — it runs the slow LLM scouts (priorities 3–6),
  then `persist_enriched_work` *updates* the same Work with tropes/styles and embeds them. It is **idempotent**
  (Cloud Tasks retries are safe) and **queue-OIDC-gated**: it verifies the OIDC token the queue attaches (only
  the queue's service account may call it), since it sits behind the now-open IAM gate rather than the Firebase gate.

A fast-only enrichment path is added to `ScoutManager` (e.g. an API-scouts-only subset) so the fast pass does not
invoke the LLM scouts.

### Coupled cleanups (roadmap-mandated)

- **Open the Cloud Run IAM gate** (`--allow-unauthenticated`); every user-facing route stays Firebase-gated,
  `/health` stays open, the internal enrich route is queue-OIDC-gated. `security.md` gains the updated boundary.
- **Consolidate the two `DatabaseManager` pools** — `api/main.py` and `api/auth.py` share one injected manager
  (the Lift 1 T5 note).
- **`/history` pagination** — `limit`/`offset` mirroring `/works` (INF-029).
- **Prod secrets/env** — add the Gemini, Google Books, and Hardcover API keys to Secret Manager and wire them
  (plus `GEMINI_MODEL` and the Gemini backend) into the Cloud Run service; provision the Cloud Tasks queue + SA.
- **Prod usage flow** goes live naturally — real chat/enrichment calls produce metered rows.

---

## §4 Data model & migration

One Alembic migration (down-revision = the Lift 1 head `c804d02d6fbb`):

- **`conversations`** — `id` (uuid PK), `user_id` (FK → users, indexed), `created_at`, `updated_at`, `title`
  (nullable — present now so the future switchable-list is a no-migration add). The **active** thread is the
  user's most-recent row; New chat inserts a new one.
- **`messages`** — `id` (uuid PK), `conversation_id` (FK → conversations, indexed), `role` (`user`/`assistant`),
  `content` (text), `created_at`.
- **`usage.conversation_id`** — add the FK constraint to `conversations.id` (the column exists from Lift 1).

Conftest builds the test schema via `alembic upgrade head` (Lift 1 pattern). User-scoping is enforced exactly as
in Lift 1: conversations/messages are read only for `current_user_id`.

---

## §5 Testing

**Frontend (new to the repo):** Vitest + React Testing Library for component tests — the sign-in gate, rendering
streamed chat events, the add-book form, acting on a recommendation — backend/SSE mocked. Playwright (already in
the stack) for a couple of end-to-end happy-paths against a running stack.

**Backend (pytest):**
- Chat endpoint — SSE events stream, transcript persists, usage records, **rehydration** replays prior messages.
- Transcript store + migration — schema via `alembic upgrade head`; resume vs new-chat.
- **User-scoping** — extend the Lift 1 isolation tests so one friend cannot read another's conversation.
- Two-phase enrichment — fast pass runs *only* API scouts and enqueues (Cloud Tasks client mocked); internal
  endpoint runs deep scouts, updates the Work, is **idempotent** on retry, and **rejects non-queue callers**.
- `/history` pagination; the single pooled `DatabaseManager`.

**Discipline (Lift 1 lessons):** the mutation-testing mindset (every scoping/security test must fail when its
filter is deleted); live/api_dependent tests (real Firebase, real Gemini) stay operator-run, never CI. The CD
smoke gains "`GET /` serves the SPA" alongside the existing 401-enforcement assertion.

---

## §6 Rollout (runbook, like Lift 1's)

- **Build change:** the image becomes a **multi-stage Docker build** — a Node stage compiles the Vite SPA; the
  Python runtime stage copies in the static output. The runtime still has no Node; only the build does.
- **Provision first:** the Cloud Tasks queue + its service account (OIDC invoker on the internal route); the
  Gemini / Google Books / Hardcover key secrets, granted to the Cloud Run runtime service account.
- **Migration with a backup:** prod's "no backup needed" reasoning (Lift 1 §6) **expires at the first prod
  write**, and chat is that write. From here on: **`gcloud sql export` before every migration**, then apply
  `conversations`/`messages`/the usage FK via the proxy + docker wrapper.
- **Deploy & flip:** merge → CD builds and deploys with the new env/secrets → **open the IAM gate**. Because
  `SIGNUP_MODE=invite` and Firebase still gate everything, opening the IAM gate exposes nothing to the uninvited.
- **Verify live:** gate open, Google sign-in, a real chat turn (streamed activity + reply), add-a-book, and that
  deep enrichment completes a couple minutes later (tropes appear). Confirm a metered usage row was written.
- **Cost watch:** confirm budget alerts; cap Cloud Run max-instances/concurrency to bound spend; eyeball the
  first real usage rows.

---

## §7 Future improvements (logged, not built now)

- Recommendation card: **jump-into-chat "tell me more"**, and a **bookseller / Amazon link**.
- **Switchable chat list** (brainstorm option B) — the `conversations` table already supports many-per-user.
- **PWA / add-to-home-screen** — make the web app installable and app-like.
- **Firebase Hosting** migration — CDN, instant first paint, easy custom domain (config-only).
- **Conversation windowing / summarization** for long threads (token-cost control).
- Analysis: **ratings-over-time** and **taste-profile** views.
- Add-book **title autocomplete**; **DNF/abandoned** status, page count, series.

## §8 Out of scope (this lift)

- Native mobile apps; the A2A external mesh (ADR-035 deferral stands).
- Per-user quota enforcement, Stripe billing, the BYOK feature, Claude prod enablement — all **Lift 3**.
- Bulk user-facing import (DEBT-001 stays operator-run local Dagster).
- Email/password sign-in (Lift 3, open signup).

---

## Plan staging note

This lift is large (frontend + chat backend + async enrichment + cleanups). It stays **one spec**, but the
implementation **plan** will be staged so each stage is independently testable:

1. **Backend chat + transcript store** (migration, `/chat` SSE, conversations/messages, rehydration, usage FK).
2. **Frontend SPA** built against that real contract (app shell, five views, Firebase sign-in, SSE client).
3. **Async enrichment** (two-phase `/books`, internal endpoint, Cloud Tasks).
4. **Cleanups + rollout** (IAM gate, pool consolidation, `/history` pagination, prod secrets, multi-stage build,
   runbook).
