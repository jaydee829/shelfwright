# Enrichment Visibility + Tropes in History — Design (C1/C2)

**Date:** 2026-06-16
**Status:** Approved (brainstormed), pending plan
**Origin:** Friends-and-family beta feedback, items C1 + C2 (see project memory `beta-feedback-triage`).

---

## 1. Background & Problem

Adding a book runs **two-phase enrichment**: `POST /books` does the fast API-scout pass (seconds —
persists the Work + genres/description, logs the read) and enqueues a Cloud Task for the slow LLM
**deep pass** (`enrich_deep`), which adds tropes/styles ~1–2 minutes later. The operator reported two
gaps:

- **C1:** after adding a book, there's no signal that the background enrichment finished or succeeded —
  `POST /books` only returns `enrichment_enqueued` ("task queued").
- **C2:** the History tab shows title/authors/date/rating/format but **no tropes**, so there's nothing
  that reveals whether enrichment landed.

The operator's own framing ties them together: showing tropes in History *is* the "enrichment
succeeded" signal.

**Code reality (confirmed):** there is no enrichment status/timestamp on `Work` (fields: title, year,
description, genres, moods) and no `created_at`. Tropes attach via `WorkTrope`, which carries a
**`relevance_score`** (Float) — so "top tropes" is a clean ordered query. The fast pass writes genres
but **no tropes**; tropes appear only after the deep pass. `/history` (`api/main.py`) currently eager-loads
contributors only.

## 2. Goals

- **C1:** History clearly distinguishes a book whose deep enrichment has landed from one still working,
  and the add screen sets the expectation that enrichment runs in the background.
- **C2:** Each History row shows a genre + its top tropes once enriched.

## 3. Non-Goals (YAGNI)

- **No DB migration / new columns.** Status is derived from trope presence.
- No add-screen polling or per-work status endpoint (the add flow uses a static expectation message).
- No live polling/refresh of the History view (it reflects state at load).
- No distinct "failed" state or retry action (see Decisions / debt).

## 4. Decisions (from brainstorm)

- **D1 — C1 status is DERIVED:** a work is "enriched" iff it has ≥1 trope. No schema change, no
  backfill; existing books are correct automatically.
- **D2 — Add-flow signal is a static expectation message** in `AddBookView` after a successful add.
- **D3 — History trope line:** if the work has tropes → **genre + top 3 tropes** (by
  `relevance_score` desc); if it has **no tropes → an "Enriching…" indicator** (we do NOT show the
  fast-pass genre alone while tropes are pending — "Enriching…" is the cleaner done/not-done signal).
- **D4 — No live History polling:** state reflects load time; reloading/revisiting shows current state.
- **D5 — DEBT (logged, not built):** derive-only cannot distinguish a failed/empty or never-ran
  enrichment from one still in flight, so a stuck work shows "Enriching…" indefinitely. Future cleanup:
  a timeout/sweep (needs a creation timestamp) to flag long-pending works as failed/retryable. Recorded
  as **DEBT-035** in `docs/project_notes/issues.md`.

## 5. Architecture

Backend payload change + frontend rendering. No mesh/enrichment-logic changes; no schema change.

### 5.1 `/history` payload (`src/agentic_librarian/api/main.py`)

- The current query eager-loads contributors via a chained `joinedload` (safe under LIMIT because the
  paginated root is `ReadingHistory`). Add the trope collection via **`selectinload`** (NOT `joinedload`):
  tropes are a second to-many under `Work`, and a second `joinedload` would cartesian-multiply rows.
  Concretely add, alongside the existing contributor chain:
  `.options(selectinload(ReadingHistory.edition).selectinload(Edition.work).selectinload(Work.tropes).joinedload(WorkTrope.trope))`
  — keep the existing contributor `joinedload` chain too. (Verify both option chains coexist; if SQLAlchemy
  objects to mixed strategies on the shared `edition→work` path, load `Work.contributors` via
  `selectinload` as well — both are to-many and selectin is correct under the paginated root.)
- Per row, compute and add to the dict:
  - `tropes`: the work's tropes sorted by `relevance_score` desc, take 3, output `Trope.name`.
  - `genre`: `work.genres[0]` if `work.genres` else `None`.
- Existing fields (`id`, `title`, `authors`, `date_completed`, `rating`, `format`) are unchanged.

### 5.2 `HistoryView` (`frontend/src/views/HistoryView.tsx`) + `client.ts`

- `HistoryItem` gains `genre?: string | null` and `tropes?: string[]`.
- Each row renders a new line under the authors:
  - **tropes present** → a genre chip (if `genre`) followed by up to 3 trope chips.
  - **tropes empty/absent** → a single muted **"Enriching…"** chip.
- `HistoryView.css` gets chip styles (reuse the existing visual language; trope chips muted, the
  "Enriching…" chip italic/subtle).

### 5.3 `AddBookView` (`frontend/src/views/AddBookView.tsx`)

- On a successful add, the success state shows the **expectation message**:
  "Added '<title>'! Enriching in the background (~a minute) — its tropes will appear in your History."
  (Wording final in the plan.) No polling.

## 6. Data Flow

```
POST /books -> fast pass persists Work (+genres) + logs read, enqueues deep pass; returns enrichment_enqueued
AddBookView success -> shows the static "enriching in the background" expectation message
... deep pass (Cloud Task -> /internal/enrich) adds tropes to the Work ~1-2 min later ...
GET /history -> per row: top-3 tropes (relevance_score desc) + first genre
HistoryView -> tropes present ? [genre + 3 trope chips] : ["Enriching..." chip]
```

## 7. Error / Edge Handling

- A work with genres but no tropes (fast done, deep pending OR deep found none OR deep failed) → renders
  "Enriching…" (the known derive-only limitation; DEBT-035).
- A re-read (multiple `ReadingHistory` rows for the same work) → each row shows the same work's tropes
  (correct; they share the Work).
- Trope load must not regress `/history` pagination — the paginated root stays `ReadingHistory`; tropes
  load via `selectinload` (separate query keyed by the page's work ids).
- Empty/duplicate genres: take the first genre as-is; trope list de-duped is unnecessary (WorkTrope is
  unique per work+trope).

## 8. Testing Strategy

**Backend (integration, DB):**
- Seed a work with 4 tropes at varying `relevance_score` + a read for the user → `/history` returns the
  top 3 names in score-desc order and the first genre.
- Seed a work with NO tropes + a read → `/history` returns `tropes: []` (drives the "Enriching…" state).
- Pagination unaffected: a multi-read / multi-contributor work still paginates to exact `limit` rows
  (existing guard tests stay green).

**Frontend (vitest):**
- `HistoryView`: a row with `tropes` renders the genre + trope chips; a row with empty `tropes` renders
  "Enriching…".
- `AddBookView`: a successful add shows the enrichment expectation message.
- Mind the vitest-4 `...Once` mock rule; `App.test.tsx` already mocks both views (no new route).

## 9. Files Touched

- `src/agentic_librarian/api/main.py` — `/history` eager-load + payload (`tropes`, `genre`).
- `frontend/src/api/client.ts` — `HistoryItem` fields.
- `frontend/src/views/HistoryView.tsx` (+ `.css`) — trope/genre chips + "Enriching…" state.
- `frontend/src/views/AddBookView.tsx` — success expectation message.
- `docs/project_notes/issues.md` — log DEBT-035.
- Tests: `test/integration/test_api_history_db.py` (extend), `frontend/src/views/HistoryView.test.tsx`,
  `frontend/src/views/AddBookView.test.tsx`.

## 10. Out of Scope / Future

- **DEBT-035:** stuck/failed-enrichment detection (timeout sweep + a `created_at`/status), and a retry
  action — pairs naturally with **D1b** (history edit/delete) actions.
- Showing the fast-pass genre while tropes are still pending (decided against for a clean signal).
- Live History polling so "Enriching…" flips to tropes without a reload.
- The remaining beta items: D1b history edit/delete, E1 dark mode (separate specs).
