# History Edit / Delete — Design (D1b)

**Date:** 2026-06-16
**Status:** Approved (brainstormed), pending plan
**Origin:** Friends-and-family beta feedback, item D1b (see project memory `beta-feedback-triage`).

---

## 1. Background & Problem

The operator wants to **edit and delete reading-history entries from the UI, without talking to the
Librarian** — concretely, to remove a read-event the Librarian added in error (the duplicate
*Book of Jhereg*). Today there is **no mutation path for reading history**: `grep` confirms no
DELETE / PATCH / PUT endpoint anywhere in `src/agentic_librarian/api`, and history is only ever created
(`add_book_to_history` / `add_read_event` / `update_reading_status`), never removed or edited.

**Code reality:** `ReadingHistory` is one row per read-event — `id`, `edition_id`, `user_id`,
`date_started` (nullable, unused in UI), `date_completed` (NOT NULL), `user_rating` (nullable),
`user_notes` (nullable). `/history` already returns each row's `id` plus title/authors/date/rating/format
(and now genre/tropes from C1/C2). `format` lives on `Edition` (shared across reads), not on the
read-event.

## 2. Goals

- Delete a single reading-history entry (read-event) from the UI, with a confirmation step.
- Edit a single entry's **rating / date finished / notes** from the UI.
- Both strictly scoped to the authenticated user (ADR-048).

## 3. Non-Goals (YAGNI)

- **No new MCP/Librarian tools** — API + UI only (the agent keeps its add/update tools). Parity can come later.
- **No editing of `format`** (Edition-level, shared across reads) or title/author (Work-level).
- No bulk select/delete; no undo (a confirm dialog covers the destructive case).
- No DB migration (uses existing columns).
- No GET-by-id endpoint (the edit view receives the row via router state; see D4).

## 4. Decisions (from brainstorm)

- **D1 — Scope:** delete **and** edit (rating / date_completed / notes).
- **D2 — Edit UX:** a **dedicated edit view** at route `/history/:id/edit` (not inline, not a modal).
- **D3 — Affordance:** a **per-row ⋮ (kebab) menu** with **Edit** and **Delete** — one small icon per row,
  no selection mode.
- **D4 — Edit data source:** the row is passed to the edit view via **router state** (the `AddBookView`
  prefill pattern); a direct load with no state **redirects to `/history`** (no GET-by-id endpoint).
- **D5 — Delete requires a confirmation dialog** before the call.
- **D6 — API + UI only:** no Librarian delete/edit tools this pass.

## 5. Architecture

Two user-scoped endpoints + frontend (kebab menu, confirm dialog, new edit view). No schema change.

### 5.1 Backend (`src/agentic_librarian/api/main.py`)

Mirror the existing user-scoping pattern (`/recommendations/{id}/status`, `/history`): resolve the row by
id **and** `user_id == user.id`; 404 if not found/not theirs.

- **`DELETE /history/{entry_id}`** → finds the caller's `ReadingHistory` row, deletes it, returns
  `{"id": str(entry_id), "deleted": true}`. 404 (not 403) when the id isn't the caller's — don't leak
  existence (matches the recommendations endpoint).
- **`PATCH /history/{entry_id}`** with a Pydantic body `HistoryUpdate { date_completed: date | None,
  rating: int | None, notes: str | None }` → updates only the provided fields on the caller's row.
  Validation reuses the `AddBookRequest` rules: `rating` 1–5 and **reject bool** (`field_validator(mode="before")`),
  `date_completed` not in the future. `date_completed` is NOT NULL — a PATCH may change it but a request
  that explicitly sets it null is rejected. Returns the updated row in the same shape `/history` uses
  (id/title/authors/date_completed/rating/format/genre/tropes) so the client can refresh in place.
- Both run inside `with as_user(user.id)` only if a tool needs context; here they query directly with
  `user.id` from the auth dependency (no MCP tools involved).

### 5.2 Frontend

- **`client.ts`:**
  - `deleteHistory(id: string): Promise<void>` → `DELETE /history/${id}` (throws on non-ok).
  - `updateHistory(id, body: { date_completed?: string | null; rating?: number | null; notes?: string | null }): Promise<HistoryItem>`
    → `PATCH /history/${id}`, returns the updated `HistoryItem`.
- **`HistoryView.tsx`:** each row gets a **⋮ button** opening a small menu (Edit / Delete).
  - **Delete** → a confirm dialog (e.g. "Delete your read of '<title>' finished <date>? This can't be
    undone." → Delete / Cancel). On confirm → `deleteHistory(id)` → remove the row from local state.
  - **Edit** → `navigate(`/history/${id}/edit`, { state: row })`.
  - The open/closed kebab state is per-row local UI state; clicking elsewhere / a second action closes it.
- **`HistoryEditView.tsx`** (new) at route `/history/:id/edit`:
  - Reads the row from `useLocation().state`; if absent → `<Navigate to="/history" replace />`.
  - Form fields: **Rating** (—/1–5, like AddBookView), **Date finished** (date input), **Notes** (textarea),
    prefilled from the row. Title/author/format shown read-only for context.
  - **Save** → `updateHistory(id, { date_completed, rating, notes })` → on success `navigate('/history')`.
  - A `useParams()` `id` is the source of truth for the call (router state only prefills the form).
- **`App.tsx`:** register `<Route path="/history/:id/edit" element={<HistoryEditView />} />`.
  **`App.test.tsx` must add `vi.mock('./views/HistoryEditView', …)`** (the established "mock every view"
  rule — otherwise the real `client.ts`→`firebase.ts` import throws under test).
- **CSS:** kebab button + menu + confirm-dialog styles (reuse the app's existing visual language;
  a lightweight dialog, not a new dependency).

## 6. Data Flow

```
HistoryView row ⋮ -> Delete -> confirm dialog -> DELETE /history/:id -> drop row from list state
HistoryView row ⋮ -> Edit   -> navigate(/history/:id/edit, {state: row})
HistoryEditView (prefill from state; id from useParams) -> Save
   -> PATCH /history/:id {date_completed?, rating?, notes?} -> navigate(/history)
direct load /history/:id/edit with no router state -> <Navigate to="/history" replace />
```

## 7. Error / Edge Handling

- Cross-user or unknown `entry_id` → **404** on both endpoints; the UI shows a generic error and the row
  stays/refreshes.
- PATCH validation failures → 422 (bool rating, out-of-range rating, future date); the form surfaces the
  error and keeps the user's input.
- Deleting one read of a multi-read work removes only that event; the Work/Edition stay in the catalog
  (and other reads of it remain in History).
- Edit view opened directly (no router state) → redirect to `/history` (no crash, no GET-by-id needed).
- A PATCH `date_completed` that happens to match another read of the same work is allowed (no
  work+date uniqueness constraint exists; this edits an existing event, it doesn't run the add-path dedupe).

## 8. Testing Strategy

**Backend (integration, DB):**
- DELETE removes the caller's row and 404s for another user's row (and leaves that row intact).
- PATCH updates rating/date/notes on the caller's row; 404 for another user's row; 422 on bool rating /
  out-of-range rating / future date.

**Frontend (vitest):**
- `HistoryView`: the ⋮ menu reveals Edit + Delete; Delete opens the confirm dialog, and confirming calls
  `deleteHistory` and removes the row; canceling calls nothing.
- `HistoryEditView`: prefills from router state; Save calls `updateHistory` with the edited fields; a
  render with no router state redirects to `/history`.
- Remember the vitest-4 `...Once` mock rule and add the `App.test.tsx` mock for the new view.

## 9. Files Touched

- `src/agentic_librarian/api/main.py` — `DELETE /history/{id}` + `PATCH /history/{id}` (+ `HistoryUpdate` model).
- `frontend/src/api/client.ts` — `deleteHistory`, `updateHistory`.
- `frontend/src/views/HistoryView.tsx` (+ `.css`) — per-row ⋮ menu + confirm dialog.
- `frontend/src/views/HistoryEditView.tsx` (+ `.css`) — new edit view.
- `frontend/src/App.tsx` — new route; `frontend/src/App.test.tsx` — mock the new view.
- Tests: `test/integration/test_api_history_db.py` (extend), `frontend/src/views/HistoryView.test.tsx`,
  `frontend/src/views/HistoryEditView.test.tsx` (new).

## 10. Out of Scope / Future

- Librarian/MCP delete & edit tools (parity).
- Editing `format` / converting a read to a different edition.
- A DEBT-035 "retry enrichment" action (deferred; this view is its natural future home).
- The remaining beta item: E1 dark mode (separate spec).
