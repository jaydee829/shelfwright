# Library Links + Live Availability (cut #1) — Design

**Date:** 2026-06-25
**GitHub:** Implements the core of [#57 "Links to works"]; relates to but is deliberately separate from [#56 "Processing web links in chat"].
**Status:** Approved design, ready for implementation plan.

## Goal

Every recommended book shows the reader *where to get it*: a **live availability badge** for their Libby (OverDrive) libraries, plus free→local→retail **links** (Libby/Hoopla → Bookshop.org → Amazon). The feature degrades gracefully — if the upstream availability source fails or changes, the book still shows links and nothing breaks.

## Scope

### In scope (cut #1)

- A shared backend **availability service** — the single module that talks to OverDrive's unofficial public Thunder API.
- A **read-through availability cache** (DB table, TTL-bounded) shared by all consumers.
- A **`user_libraries`** table + a **library picker** settings surface (search the public OverDrive directory, multi-select, ordered).
- Pure **link builders** (Libby per-library, Hoopla search, Bookshop.org, Amazon).
- A **`POST /availability`** batch endpoint (always returns 200; links always present; badge present only on a confident match).
- **Recommendations cards:** link row + live Libby availability badge, populated by lazy refresh on tab open.
- **Chat:** a **`check_availability` MCP tool** so the Librarian can narrate availability conversationally. It reads the same cache — no duplicate upstream calls.
- **Hoopla:** a national search deep-link only (no badge, no stored Hoopla library — see rationale below).

### Out of scope (separate future issues)

- **Checkout / place-hold** from the app — requires an official OverDrive/Hoopla **partner agreement** (Tier 2). File separately.
- **Hoopla availability badge** — requires the Hoopla **partner API** (library-provisioned key). No public per-library availability signal exists.
- **#56 inbound web-link reading** (agent ingests a user-pasted page and enriches books from it) — different feature, ~zero implementation overlap.
- **Pre-warming** availability via Cloud Tasks at suggestion-generation time (noted enhancement "B" below).
- **Affiliate tags** on Bookshop/Amazon links.

## Key facts that shape the design

- **Both APIs are partner/B2B, not consumer, APIs.** OverDrive approval is *"based on your existing customer relationship,"* and Hoopla's developer API is *"library and vendor-facing."* So official availability + checkout/holds are gated behind a business relationship, not an engineering effort.
- **OverDrive exposes a usable *unofficial* public API** — the same "Thunder" API Libby's own web frontend calls, no auth, with `x-client-id=dewey`:
  - Directory search (powers the picker): `GET https://thunder.api.overdrive.com/v2/libraries?query={q}&x-client-id=dewey` → items with `preferredKey`/`advantageKey` (slug, e.g. `kcls`), `name`, logo, brand colors.
  - Per-library title search **with availability inline**: `GET https://thunder.api.overdrive.com/v2/libraries/{slug}/media?query={q}&format=ebook-overdrive,audiobook-overdrive&perPage={n}&x-client-id=dewey` → items with `title`, `type.name` ("eBook"/"Audiobook"), `isAvailable`, `ownedCopies`, `availableCopies`, `holdsRatio`, `estimatedWaitDays`.
  - (Direct known-title check: `POST /v2/libraries/{slug}/media/availability` — not needed for cut #1; the media search above is sufficient.)
- **Hoopla exposes nothing public.** Whether a reader can borrow a Hoopla title depends on (a) does their library subscribe, (b) the library's monthly borrow cap and the reader's remaining borrows, (c) Instant vs the copy-limited "Flex" collection — all behind the partner API. Storing a Hoopla library would buy nothing in cut #1, so we don't.
- **Trade-off accepted:** using `x-client-id=dewey` impersonates Libby's web client against an undocumented endpoint. Fine and low-stakes for a friends-and-family beta. The risk (it can change or rate-limit) is contained by (1) isolating it in one module, (2) the cache + TTL + bounded concurrency limiting call volume, and (3) the links path being fully independent of it, so a Thunder outage degrades to links-only.

## Architecture

A **backend availability proxy** with **two consumers of one shared service**:

```
                         ┌───────────────────────────────┐
  Recs tab  ──REST──▶    │  availability service          │
  (cards)   POST /availability   ├─ link builders (pure)  │ ──▶ user_libraries (DB)
                         │   ├─ read-through cache ◀──────┼──▶ availability_cache (DB)
  Chat      ──tool──▶    │   └─ OverDrive Thunder client  │ ──▶ thunder.api.overdrive.com
  (agent)   check_availability   (the only gray module)   │     (only on cache miss)
                         └───────────────────────────────┘
```

Why this shape:

- **Backend proxy, not browser-direct:** Thunder won't allow browser cross-origin calls (CORS); it also keeps the `x-client-id` impersonation and the cache server-side.
- **One service, two consumers:** the Recommendations cards consume it over REST (deterministic UI); the chat agent consumes it through an MCP tool (conversational). Both read the same cache, so "recs and chat both want availability" costs **one** upstream fetch per book per TTL window, not two.
- **Links independent of Thunder:** link builders depend only on the user's saved library slugs (DB), never on Thunder. A Thunder failure returns links + a null badge — never an error. This is the "deep-links as fallback" requirement, satisfied structurally.

### Components (each independently testable)

**Backend — new package `src/agentic_librarian/availability/`**

- `links.py` — pure URL builders. No I/O. `libby_url(slug, title)`, `hoopla_url(title)`, `bookshop_url(title, author)`, `amazon_url(title, author)`. Returns the ordered link set for a book given the user's libraries.
- `overdrive.py` — the Thunder client. `search_libraries(query)` (for the picker) and `fetch_availability(slug, title, author)`. The **only** module that calls `thunder.api.overdrive.com`. Bounded timeout; raises a typed error on failure (caller degrades to null badge).
- `service.py` — orchestration: given a user + a list of books, load the user's `user_libraries`, read-through the `availability_cache`, fetch only stale/missing `(library, title)` pairs from `overdrive.py` (bounded concurrency), write through, and assemble the response (links always; per-library badge when a confident match exists). The title-matcher (normalized-title + author-overlap, with a confidence threshold) lives here.

**Backend — API (`src/agentic_librarian/api/`)**

- `availability.py` — `POST /availability` (body `{work_ids: [...]}`). Resolves works → title/author, calls the service, returns per-work `{links, libby}`. **Always 200.** Registered in `api/main.py` like the other routers.
- `libraries.py` — `GET /libraries/search?q=` (proxies `overdrive.search_libraries`, returns `[{slug, name}]`); `GET /me/libraries` (the user's saved list, ordered); `PUT /me/libraries` (replace the set with an ordered `[{slug, name}]`). All user-scoped via `get_current_user`.

**Backend — MCP tool (`src/agentic_librarian/mcp/server.py`)**

- `check_availability(title: str, author: str) -> dict` — new `@mcp.tool()`, same shape as the existing `check_reading_history(title, author)`. Validates inputs with `_valid_name` (SEC-002), resolves `get_required_user_id()`, calls the **same** `availability.service`. Returns a structured dict the agent narrates (per library, per format: available / copies / hold ratio / est. wait), or a `links`-only payload on no-match/failure so the agent can still offer a search link. A short prompt note tells the Librarian to use it when recommending or when asked "where can I get it."

**Backend — data model (`src/agentic_librarian/db/models.py` + Alembic)**

- `UserLibrary` → table `user_libraries`: `user_id` (FK users.id), `provider` (str, `'libby'` in cut #1), `library_slug` (str), `display_name` (str), `sort_order` (int), `created_at`. PK `(user_id, provider, library_slug)`. Holds **no secret** — slugs are public — so it does not touch `UserCredential`/the keyring.
- `AvailabilityCache` → table `availability_cache`: `provider` (str), `library_slug` (str), `norm_title` (str), `norm_author` (str), `payload` (JSONB), `fetched_at` (datetime). PK `(provider, library_slug, norm_title, norm_author)`. Keyed on **normalized title+author** (not `work_id`) so the recs consumer (has `work_id` → title/author) and the chat consumer (has title/author directly, incl. web-discovered books) **share rows**. Freshness = `now - fetched_at < TTL` (default 6h, configurable).
- One Alembic migration adds both tables (follow the existing `alembic/versions/` pattern).

**Frontend (`frontend/src/`)**

- `components/BookLinks.tsx` (+ css) — props `{ workId, title, authors }`. Renders the **link row always** (free→local→retail, in priority order). Fetches availability and renders the **badge** when `libby` data is present; shows a quiet "checking…" state while in flight; renders **link-only** on null/empty/error. Self-contained so it can later drop into other surfaces unchanged.
- `views/SettingsView.tsx` (+ route in `App.tsx`, + a Nav entry) — the **library picker**: a search box (debounced → `GET /libraries/search`), a results list to add libraries, a saved list that is removable and reorderable (drag or up/down), persisted via `PUT /me/libraries`. There is no settings surface today, so this is net-new.
- `api/client.ts` — add `getAvailability(workIds)`, `getMyLibraries()`, `saveMyLibraries(list)`, `searchLibraries(q)` and their types.

## Data flow

### Recommendations tab (lazy refresh)

1. The view loads recs as today (`GET /recommendations`).
2. It fires **one batch** `POST /availability` for the visible work_ids (not N per-card calls).
3. The service, per book × per saved library: **fresh cache → reuse**; **stale/missing → fetch from Thunder (bounded concurrency), write through**.
4. Cards render immediately with links; the badge area shows "checking…" then fills in, or falls back to link-only on miss/timeout/error.
5. TTL throttles it: repeated visits within the window are pure cache hits. The chat tool shares the cache, so anything recs warmed is free for chat and vice-versa.

**Refresh model = "A" (lazy).** The recs list is small (a few books × ≤3 libraries), so an all-cold batch is a couple seconds with bounded concurrency — cheap to do on demand, no background pipeline. **"B" (pre-warm via Cloud Tasks at `log_suggestion` time)** is a noted future enhancement, not built now.

### Chat (MCP tool)

The Librarian calls `check_availability(title, author)` when relevant. It hits the shared cache/service and returns structured data the agent turns into prose ("the audiobook's available now at KCLS; the ebook has a ~12-week hold — want the Libby link?"). No static link widget is injected into chat messages (book identity in free-form prose is unreliable to anchor a deterministic widget to — the conversational tool is the better fit).

## Error handling & resilience

- **Thunder failure/timeout/changed shape** → `overdrive.py` raises a typed error; `service.py` returns `libby: null` for that book; `links` are unaffected. The card/agent degrade to links-only. No 5xx surfaced to the user.
- **`POST /availability` is always 200.** Per-book availability may be `null`; links are always present.
- **No confident title match** → no badge (we under-claim rather than show a wrong "available now").
- **Bounded concurrency + per-request timeout** so one slow library can't hang a batch; unresolved books stay link-only and warm up next time.
- **MCP tool** validates inputs (SEC-002) and never throws into the agent loop — it returns a links-only dict on any failure.

## Title matching (the one real correctness risk)

`service.py` queries Thunder by title, then accepts a result only on a **confident match**: normalized-title equality (lowercase, collapse whitespace — reuse the existing `_normalize` approach from `mcp/server.py`) **plus** author overlap. Below threshold → no badge. The matcher is pure and unit-tested against captured Thunder payloads (fixtures), including the multi-edition case (e.g. English + translated editions of the same title).

## Testing

- **Unit (pytest):**
  - `links.py` — each builder for normal titles, punctuation, unicode, multiple authors; correct ordering.
  - title matcher — exact match, author-overlap accept, low-confidence reject, multi-edition payload.
  - `service.py` with mocked HTTP — cache hit (zero upstream calls), cache miss (one call + write-through), Thunder error → links-only/null badge, timeout, bounded concurrency.
- **Integration (pytest, `db_integration`):** `/availability` always-200 contract + shape; `/me/libraries` GET/PUT round-trip + ordering; `/libraries/search` proxy with mocked Thunder. `check_availability` MCP tool: structured result, degrade-on-error, SEC-002 input rejection.
- **Frontend (vitest):** `BookLinks` renders links-always / badge-when-present / link-only-when-null / "checking" state; `SettingsView` search → add → reorder → save; `api/client` calls. Follows the existing view-mock patterns (every view `vi.mock`'d so firebase `getAuth()` doesn't throw — see the App.test pattern).

## File map

**Create**
- `src/agentic_librarian/availability/__init__.py`, `links.py`, `overdrive.py`, `service.py`
- `src/agentic_librarian/api/availability.py`, `src/agentic_librarian/api/libraries.py`
- `alembic/versions/<rev>_library_links_availability.py`
- `frontend/src/components/BookLinks.tsx` (+ `.css`), `frontend/src/views/SettingsView.tsx`
- Tests alongside each (pytest under `test/`, vitest `*.test.tsx`).

**Modify**
- `src/agentic_librarian/db/models.py` — add `UserLibrary`, `AvailabilityCache`.
- `src/agentic_librarian/api/main.py` — register the two new routers.
- `src/agentic_librarian/mcp/server.py` — add `check_availability` tool.
- `frontend/src/App.tsx` — add the `settings` route; `frontend/src/components/Nav.tsx` — add the nav entry; `frontend/src/views/RecommendationsView.tsx` — render `<BookLinks>` in the card; `frontend/src/api/client.ts` — new calls/types.

## Open verification items (resolve in the first implementation task)

- **Exact Libby search deep-link path.** Candidate: `https://libbyapp.com/search/{slug}/search/query-{encoded}`. Confirm the precise segment names against a live Libby session; if brittle, fall back to the per-library OverDrive search URL. Stable, well-known format — just needs the precise path pinned before the link builder is finalized.
- **TTL value.** Default 6h; confirm it feels right against real hold-queue movement during testing.

## Issue split

- **This spec = cut #1**, one issue: deep-links + picker + **Libby live availability** (recs cards + chat tool), Hoopla as a deep-link.
- **Separate future issue (Tier 2):** official checkout/holds + Hoopla availability badge — gated on OverDrive/Hoopla partnership. The `user_libraries`/cache groundwork and the isolated `overdrive.py` boundary make that a clean swap to the official partner API later.
