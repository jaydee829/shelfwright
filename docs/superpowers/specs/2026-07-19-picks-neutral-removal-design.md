# Picks Neutral Removal ("Not right now") + History→Picks Auto-Resolution — Design

**Date:** 2026-07-19
**Issue:** GH #130 — Remove title from picks without negative repercussions for that title resurfacing
**Status:** Approved (user-approved design, 2026-07-19)

## Problem

The Picks tab offers exactly two actions: "✓ I read this" and "Not for me" (→ status
`'Dismissed'`). A user tidying their shelf — a duplicate pick, or a book that already
landed in history through another path — is forced to record a false "not for me".
Separately, adding a book to history (add-a-book form, chat, CSV import) does not clear
a matching active pick, so stale picks accumulate; today only the "✓ I read this"
prefill flow clears the pick, and it does so client-side (two sequential calls — if the
second fails, the pick goes stale, which is exactly the staleness #130 describes).

## Verified facts (from code, 2026-07-19)

- Picks = `Suggestions` rows with `status = 'Suggested'`. A partial unique index
  (`user_id, work_id) WHERE status = 'Suggested'` guarantees at most one active pick
  per (user, work).
- Retrieval (`mcp/server.py` `search_internal_database`, `candidates.py`) excludes only
  ACTIVE `'Suggested'` works from fresh candidate sets. Any resolved status frees the
  work to resurface mechanically. The harm of `'Dismissed'` is semantic: it is a
  permanent "not for me" record that the Librarian's charter treats as negative
  feedback, and that any future preference feature would inherit.
- Every read-event write path flows through `two_phase.add_read_event`, which runs
  under `get_required_user_id()`: `POST /books` (books.py:67), chat MCP tools
  (server.py:679, server.py:741), CSV import worker (imports/worker.py:104).
- `add_read_event` has an `already_logged` early-return branch (same work + same
  `date_completed` → no new row).
- Status vocabularies today: API endpoint `ALLOWED_STATUS_UPDATES = {"Dismissed",
  "Read"}` (api/recommendations.py:21); MCP `_SUGGESTION_STATUSES = ("Accepted",
  "Dismissed", "Already Read")` (mcp/server.py:790), normalized via
  `_normalize_status`.

## Design

### 1. New neutral terminal status `'Removed'` (UI label: "Not right now")

Resolves the pick with no negative record. Frees the active-suggestion slot (the
partial unique index binds only `'Suggested'`), so the Librarian may legitimately
re-pitch the title later. Row is kept — provenance (what was pitched, when, why)
survives; a hard delete was considered and rejected (loses audit trail,
irreversible on a fat-finger).

- `api/recommendations.py`: `ALLOWED_STATUS_UPDATES = {"Dismissed", "Read", "Removed"}`.
  No other endpoint change.
- `mcp/server.py`: `_SUGGESTION_STATUSES` gains `"Removed"`.
- Librarian charter (`agents/prompts.py` and the parity copy in
  `agents/services.py`): one line distinguishing "take it off my list for now /
  maybe later" → `Removed` from "not for me / I hate this" → `Dismissed`. Both
  charter copies must stay in sync (existing parity-test pattern).

### 2. Pick auto-resolution in `two_phase.add_read_event`

In the same session/transaction as the read-event write: query the user's active
`'Suggested'` row for that `work_id`; if present, set `status = 'Read'`. Return
`pick_resolved: bool` in the result dict — **on both branches**, including the
`already_logged` early return (re-adding a book already in history still clears its
pick). Because it lives in `add_read_event`, all four write paths inherit it
uniformly. New invariant: **a book in your history is never simultaneously an
active pick.**

Safety notes:
- Flipping `'Suggested' → 'Read'` cannot violate any constraint (the unique index
  covers only `'Suggested'`), so there is no autoflush mutate-then-precheck hazard
  here (contrast: the history-format-edit lesson, bugs.md 2026-07-19).
- Only `'Suggested'` rows are ever touched: `'Dismissed'`/`'Removed'`/`'Read'` rows
  are never resurrected or rewritten by the resolution.
- Query is user-scoped (`get_required_user_id()`); other users' picks for the same
  work are untouched.

### 3. `POST /books` response

Gains `pick_resolved: bool` (passthrough from `add_read_event`). The existing
client-side `setRecommendationStatus(suggestionId, 'Read')` in the "✓ I read this"
prefill flow **stays**: it resolves the exact pick clicked even in the rare case
where fast enrichment resolves to a twin work, and double-resolution is idempotent
(both flips target `'Read'`). The server-side pass is the invariant; the client
call is a belt-and-suspenders exact-id fast path.

### 4. Frontend

- `RecommendationsView.tsx`: third action button **"Not right now"** (ghost style)
  → `setRecommendationStatus(id, 'Removed')` → remove card from the list (same
  busy/filter mechanics as dismiss). Button order: `✓ I read this` | `Not right
  now` | `Not for me`.
- `api/client.ts`: `setRecommendationStatus` status union gains `'Removed'`;
  `AddBookResult` gains `pick_resolved: boolean`.
- `AddBookView.tsx`: when `result.pick_resolved` is true, the success message
  appends " Also cleared it from your Picks." No dialog — auto-resolve was chosen
  over a post-submit dialog and over auto-resolve+undo (user decision: you just
  logged the book as read; there is no realistic "no" answer, and the new "Not
  right now" button is the escape hatch for the rare twin-work miss).

## Out of scope

- Any dialog or undo affordance on add-a-book (decided against, above).
- Bulk pick management / multi-select.
- Import-time interplay where one CSV both imports a book as read AND creates a
  to-read suggestion for it (`_upsert_suggestion`): row-processing order decides
  which wins; accepted as-is.
- Backfilling or reclassifying historical `'Dismissed'` rows.

## Error handling

- Status endpoint: unchanged 404 (not mine / missing), 422 (bad vocab — now three
  allowed values).
- `add_read_event` resolution adds no new failure mode: it is a read + in-session
  update on rows guarded by the same transaction as the history insert. If the
  transaction rolls back, both the read event and the resolution roll back
  together (no partial state).
- Frontend: "Not right now" uses the same optimistic-removal + `busy` guard as
  "Not for me"; a failed call leaves the card in place (existing error behavior).

## Testing

Per repo standards: parametrized atomic cases (never loops in a test body);
assert EVERY promised side effect (CLAUDE.md #1); db_integration tests execute in
CI against real Postgres.

- **Status endpoint (unit, parametrized):** `'Removed'` accepted (row updated,
  response echoes); junk / empty / lowercase `'removed'` → 422 (the endpoint is
  exact-match, no normalization — only the MCP tool normalizes); the three
  allowed values each atomic-tested.
- **`add_read_event` resolution (unit):** active pick → flipped to `'Read'` AND
  `pick_resolved` true AND history row written (assertion completeness); no pick →
  `pick_resolved` false; `already_logged` branch still resolves the pick;
  `'Dismissed'` row present → untouched and `pick_resolved` false; another user's
  active pick for the same work → untouched.
- **`POST /books` (unit):** `pick_resolved` passthrough present in response for
  both fresh-add and `already_logged` shapes.
- **MCP tool (unit):** `update_suggestion_status` accepts `'Removed'` (including
  via `_normalize_status` casing).
- **Charter parity:** existing prompts/services parity test extended to cover the
  new line (if the parity test asserts full-text equality, it passes untouched —
  verify).
- **Frontend (vitest):** RecommendationsView — "Not right now" renders, dispatches
  `'Removed'`, card removed, busy-guard; AddBookView — success message with and
  without `pick_resolved` (`...Once` mock variants per frontend-test-pitfalls).
- **db_integration (CI):** end-to-end `POST /books` with a seeded active pick →
  response `pick_resolved` true, suggestion row status `'Read'` in the DB, history
  row exists; and the status endpoint flipping a seeded pick to `'Removed'`.
