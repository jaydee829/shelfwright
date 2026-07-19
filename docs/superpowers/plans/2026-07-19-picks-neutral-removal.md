# Picks Neutral Removal ("Not right now") Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A neutral "Not right now" removal for Picks (status `'Removed'`, no negative record) plus automatic pick-resolution whenever a book lands in reading history (GH #130).

**Architecture:** New terminal `Suggestions.status` value `'Removed'` exposed through the existing status endpoint, the MCP tool, and a third Picks button. Pick auto-resolution lives in `two_phase.add_read_event` (the single choke point all four history-writing paths flow through), flipping the user's active `'Suggested'` row to `'Read'` in the same transaction and returning `pick_resolved`, which `POST /books` passes through to the UI.

**Tech Stack:** FastAPI + SQLAlchemy 2 (Postgres-only models), React + vitest frontend.

**Spec:** `docs/superpowers/specs/2026-07-19-picks-neutral-removal-design.md`

## Global Constraints

- Status literal is exactly `'Removed'` (capital R) everywhere; UI button label is exactly `Not right now`; add-book success suffix is exactly ` Also cleared it from your Picks.` (leading space, appended to the existing message).
- The API status endpoint stays exact-match (no case normalization); only the MCP tool normalizes case.
- Only `'Suggested'` rows may ever be flipped by auto-resolution — never rewrite `'Dismissed'`/`'Removed'`/`'Read'`/any other status.
- App models are Postgres-only — never instantiate them against sqlite. DB-touching tests go in `test/integration/` with `pytestmark = pytest.mark.db_integration` (they deselect locally without Postgres and execute in CI — CI is the merge gate for them; a local run that collects them and reports `deselected` is the expected local outcome).
- Case-driven tests are parametrized atomic tests (`pytest.mark.parametrize`, one named case each) — never loops inside a test body.
- Tests: `.venv/Scripts/python -m pytest ...` from the repo root. Frontend: `npx vitest run <file>` from `frontend/`.
- Before every commit: `uvx ruff check <files>` AND `uvx ruff format <files>` (format is enforced by CI pre-commit). Frontend edits must also pass `npm run build` (the build's tsc uses `erasableSyntaxOnly` — no TS-only runtime syntax like constructor parameter properties).
- No `[skip ci]` in commit messages. End every commit message with:
  `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`
  `Claude-Session: https://claude.ai/code/session_01DJm935pp6vTjw6j2rATxY9`

---

### Task 1: Pick auto-resolution in `add_read_event` + `POST /books` passthrough

**Files:**
- Modify: `src/agentic_librarian/enrichment/two_phase.py:152-179` (`add_read_event`; add helper `_resolve_active_pick` directly above it)
- Modify: `src/agentic_librarian/api/books.py:79-85` (response dict)
- Test: `test/integration/test_books_api.py` (append)

**Interfaces:**
- Consumes: existing `add_read_event(work_id, *, completed, rating, notes, fmt) -> dict` and `get_required_user_id()` (already imported in two_phase.py).
- Produces: `add_read_event` result dict gains key `"pick_resolved": bool` on BOTH return branches; `POST /books` response gains `"pick_resolved": bool`. Task 4's frontend relies on the response key name `pick_resolved`.

**Context:** `two_phase.py` already imports models — extend its existing `from agentic_librarian.db.models import ...` line with `Suggestions`. The `Suggestions` table has a partial unique index on `(user_id, work_id) WHERE status = 'Suggested'`, so at most one active pick exists per (user, work); flipping it OFF `'Suggested'` cannot violate any constraint, so no guarded-flush choreography is needed (contrast the history-format-edit autoflush lesson — it does not apply here, and a reviewer asking about it should be pointed at the index predicate).

- [ ] **Step 1: Write the failing tests**

Append to `test/integration/test_books_api.py`. Extend the existing models import line to include `Suggestions` (it already imports `Author, Edition, ReadingHistory, User, Work, WorkContributor`).

```python
def _seed_picked_work(db_url, *, title, author, status="Suggested", user_id=DEFAULT_USER_ID):
    """A catalog work with an Author link and one suggestion row for user_id."""
    manager = DatabaseManager(db_url)
    with manager.get_session() as s:
        work = Work(title=title)
        s.add(work)
        s.flush()
        a = Author(name=author)
        s.add(a)
        s.flush()
        s.add(WorkContributor(work_id=work.id, author_id=a.id, role="Author"))
        sug = Suggestions(work_id=work.id, user_id=user_id, status=status, justification="pitched")
        s.add(sug)
        s.flush()
        return work.id, sug.id


def test_add_book_resolves_active_pick(client, db_url, monkeypatch):
    # GH #130 invariant: a book in your history is never simultaneously an active pick.
    monkeypatch.setattr(books_mod, "enqueue_enrichment", lambda wid: True)
    work_id, sug_id = _seed_picked_work(db_url, title="The Fifth Season", author="N. K. Jemisin")
    _stub_fast(monkeypatch, {"title": "The Fifth Season", "contributors": [{"name": "N. K. Jemisin", "role": "Author"}]})

    resp = client.post("/books", json={"title": "The Fifth Season", "author": "N. K. Jemisin", "format": "ebook"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["pick_resolved"] is True
    assert body["work_id"] == str(work_id)  # fast pass dedup'd onto the seeded work
    with DatabaseManager(db_url).get_session() as s:
        assert s.get(Suggestions, sug_id).status == "Read"  # pick resolved, not deleted
        mine = s.query(ReadingHistory).filter(ReadingHistory.user_id == DEFAULT_USER_ID).all()
        assert len(mine) == 1  # the read event was still written (assertion completeness)


def test_add_book_without_pick_reports_not_resolved(client, monkeypatch):
    monkeypatch.setattr(books_mod, "enqueue_enrichment", lambda wid: True)
    _stub_fast(monkeypatch, {"title": "Piranesi", "contributors": [{"name": "Susanna Clarke", "role": "Author"}]})

    resp = client.post("/books", json={"title": "Piranesi", "author": "Susanna Clarke"})
    assert resp.status_code == 200
    assert resp.json()["pick_resolved"] is False


def test_add_book_duplicate_still_resolves_pick(client, db_url, monkeypatch):
    # The already_logged early-return branch must ALSO resolve (re-adding a book
    # already in history still clears its stale pick).
    monkeypatch.setattr(books_mod, "enqueue_enrichment", lambda wid: True)
    work_id, sug_id = _seed_picked_work(db_url, title="Annihilation", author="Jeff VanderMeer")
    manager = DatabaseManager(db_url)
    with manager.get_session() as s:
        edition = Edition(work_id=work_id, format="ebook")
        s.add(edition)
        s.flush()
        s.add(
            ReadingHistory(
                edition_id=edition.id, user_id=DEFAULT_USER_ID, date_completed=date(2021, 5, 1)
            )
        )
        s.flush()
    _stub_fast(monkeypatch, {"title": "Annihilation", "contributors": [{"name": "Jeff VanderMeer", "role": "Author"}]})

    resp = client.post(
        "/books",
        json={"title": "Annihilation", "author": "Jeff VanderMeer", "format": "ebook", "date_completed": "2021-05-01"},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["already_logged"] is True
    assert body["pick_resolved"] is True
    with manager.get_session() as s:
        assert s.get(Suggestions, sug_id).status == "Read"


def test_add_book_leaves_dismissed_pick_untouched(client, db_url, monkeypatch):
    # Resolution only touches 'Suggested' rows — it never rewrites resolved statuses.
    monkeypatch.setattr(books_mod, "enqueue_enrichment", lambda wid: True)
    _work_id, sug_id = _seed_picked_work(db_url, title="Uprooted", author="Naomi Novik", status="Dismissed")
    _stub_fast(monkeypatch, {"title": "Uprooted", "contributors": [{"name": "Naomi Novik", "role": "Author"}]})

    resp = client.post("/books", json={"title": "Uprooted", "author": "Naomi Novik"})

    assert resp.status_code == 200
    assert resp.json()["pick_resolved"] is False
    with DatabaseManager(db_url).get_session() as s:
        assert s.get(Suggestions, sug_id).status == "Dismissed"


def test_add_book_leaves_other_users_pick_untouched(client, db_url, monkeypatch):
    monkeypatch.setattr(books_mod, "enqueue_enrichment", lambda wid: True)
    other = uuid4()
    manager = DatabaseManager(db_url)
    with manager.get_session() as s:
        s.add(User(id=other, email="other-pick@example.com"))
        s.flush()
    _work_id, sug_id = _seed_picked_work(db_url, title="Circe", author="Madeline Miller", user_id=other)
    _stub_fast(monkeypatch, {"title": "Circe", "contributors": [{"name": "Madeline Miller", "role": "Author"}]})

    resp = client.post("/books", json={"title": "Circe", "author": "Madeline Miller"})

    assert resp.status_code == 200
    assert resp.json()["pick_resolved"] is False
    with manager.get_session() as s:
        assert s.get(Suggestions, sug_id).status == "Suggested"  # the other user's pick survives
```

- [ ] **Step 2: Run the tests — expect deselection locally (or FAIL if Postgres is up)**

Run: `.venv/Scripts/python -m pytest test/integration/test_books_api.py -v`
Expected locally (no Postgres): tests collected then `deselected` (db_integration marker). If a compose Postgres is up, run with `POSTGRES_HOST=localhost` and expect the 5 new tests to FAIL (`KeyError: 'pick_resolved'` / missing helper). Either outcome is acceptable to proceed; CI executes them for real.

- [ ] **Step 3: Implement**

In `src/agentic_librarian/enrichment/two_phase.py`, extend the models import with `Suggestions`, then add the helper above `add_read_event` and modify `add_read_event`:

```python
def _resolve_active_pick(session, *, work_id: UUID, user_id) -> bool:
    """GH #130: flip the user's active 'Suggested' pick for work_id to 'Read'. The
    partial unique index (user_id, work_id) WHERE status='Suggested' guarantees at
    most one row; flipping OFF 'Suggested' cannot violate any constraint. Only
    'Suggested' rows are touched — resolved statuses are never rewritten."""
    pick = (
        session.query(Suggestions)
        .filter(
            Suggestions.work_id == work_id,
            Suggestions.user_id == user_id,
            Suggestions.status == "Suggested",
        )
        .first()
    )
    if pick is None:
        return False
    pick.status = "Read"
    return True


def add_read_event(work_id: UUID, *, completed, rating: int | None, notes: str | None, fmt: str) -> dict:
    """Log a read-event for the current user against work_id (the existing
    add_book_to_history semantics: a re-read on a new date is a new row; the same
    work+date is a no-op). Requires user context (as_user / the auth dependency).

    GH #130 invariant: a book in the user's history is never simultaneously an
    active pick — any 'Suggested' row for (user, work_id) is resolved to 'Read' in
    the same transaction, on BOTH branches (a duplicate add still clears a stale
    pick). All four history-writing paths (POST /books, both chat MCP tools, the
    CSV import worker) flow through here and inherit it."""
    user_id = get_required_user_id()
    with db_manager.get_session() as session:
        pick_resolved = _resolve_active_pick(session, work_id=work_id, user_id=user_id)
        prior_reads = (
            session.query(ReadingHistory)
            .join(Edition)
            .filter(Edition.work_id == work_id, ReadingHistory.user_id == user_id)
            .all()
        )
        if any(r.date_completed == completed for r in prior_reads):
            return {"read_number": len(prior_reads), "already_logged": True, "pick_resolved": pick_resolved}
        # GH #95: uq_editions_work_format backstops this get-then-create against a
        # concurrent add_read_event/persist race for the same (work_id, format).
        edition, _created = get_or_create(session, Edition, work_id=work_id, format=fmt)
        session.add(
            ReadingHistory(
                edition_id=edition.id,
                user_id=user_id,
                date_completed=completed,
                user_rating=rating,
                user_notes=notes,
            )
        )
        session.flush()
        return {"read_number": len(prior_reads) + 1, "already_logged": False, "pick_resolved": pick_resolved}
```

In `src/agentic_librarian/api/books.py`, add the passthrough to the response dict:

```python
    return {
        "work_id": str(work_id),
        "title": req.title,
        "read_number": event["read_number"],
        "already_logged": event["already_logged"],
        "pick_resolved": event["pick_resolved"],
        "enrichment_enqueued": enqueued,
    }
```

Note: the two MCP call sites and the import worker read only `read_number`/`already_logged` from this dict — the added key is invisible to them and their existing mocks; do NOT modify them.

- [ ] **Step 4: Run the tests + the unit suite**

Run: `.venv/Scripts/python -m pytest test/integration/test_books_api.py -v` (deselected locally is expected; PASS if Postgres is up), then the full unit suite `.venv/Scripts/python -m pytest test/unit -q`.
Expected: unit suite green except the 5 known env-dependent failures (live-network scouts ×2, agent-runtime live ×1, `claude_agent_sdk` usage-recording ×2) — name them explicitly in the report if they appear.

- [ ] **Step 5: Lint, format, commit**

```bash
uvx ruff check src/agentic_librarian/enrichment/two_phase.py src/agentic_librarian/api/books.py test/integration/test_books_api.py
uvx ruff format src/agentic_librarian/enrichment/two_phase.py src/agentic_librarian/api/books.py test/integration/test_books_api.py
git add src/agentic_librarian/enrichment/two_phase.py src/agentic_librarian/api/books.py test/integration/test_books_api.py
git commit -m "feat(api): auto-resolve active picks when a book lands in history (GH #130)"
```

---

### Task 2: `'Removed'` in the status endpoint

**Files:**
- Modify: `src/agentic_librarian/api/recommendations.py:20-21`
- Test: `test/integration/test_recommendations_api.py` (append)

**Interfaces:**
- Consumes: existing `POST /recommendations/{suggestion_id}/status` endpoint and the file's `_seed_suggestion(manager, *, user_id, title, author, status=..., justification=..., genres=None)` helper (returns `(sug.id, work.id)`).
- Produces: the endpoint accepts `'Removed'`. Task 4's frontend sends exactly `'Removed'`.

- [ ] **Step 1: Write the failing tests**

Append to `test/integration/test_recommendations_api.py`:

```python
@pytest.mark.parametrize("status", ["Dismissed", "Read", "Removed"])
def test_status_endpoint_accepts_each_allowed_value(client, db_url, status):
    manager = DatabaseManager(db_url)
    sug_id, _work_id = _seed_suggestion(manager, user_id=DEFAULT_USER_ID, title=f"Allowed {status}", author="A. Uthor")

    resp = client.post(f"/recommendations/{sug_id}/status", json={"status": status})

    assert resp.status_code == 200
    assert resp.json() == {"id": str(sug_id), "status": status}
    with manager.get_session() as s:
        assert s.get(Suggestions, sug_id).status == status


@pytest.mark.parametrize("bad", ["removed", "Skipped", "", "Suggested"])
def test_status_endpoint_rejects_bad_vocab(client, db_url, bad):
    # Exact-match vocabulary: lowercase 'removed' is rejected (only the MCP tool
    # normalizes case), and re-activating to 'Suggested' is not a client verb.
    manager = DatabaseManager(db_url)
    sug_id, _work_id = _seed_suggestion(manager, user_id=DEFAULT_USER_ID, title=f"Bad {bad!r}", author="A. Uthor")

    resp = client.post(f"/recommendations/{sug_id}/status", json={"status": bad})

    assert resp.status_code == 422
    with manager.get_session() as s:
        assert s.get(Suggestions, sug_id).status == "Suggested"  # untouched on rejection


def test_remove_marks_status_and_removes_from_list(client, db_url):
    # The 'Not right now' flow end-to-end: Removed resolves the pick out of the
    # active list while keeping the row (neutral, auditable — GH #130).
    manager = DatabaseManager(db_url)
    sug_id, _work_id = _seed_suggestion(manager, user_id=DEFAULT_USER_ID, title="Neutral Exit", author="A. Uthor")

    resp = client.post(f"/recommendations/{sug_id}/status", json={"status": "Removed"})
    assert resp.status_code == 200

    listed = client.get("/recommendations").json()
    assert all(item["id"] != str(sug_id) for item in listed)
    with manager.get_session() as s:
        assert s.get(Suggestions, sug_id).status == "Removed"  # row kept, not deleted
```

Note: `Dismissed`/`Read` acceptance was previously covered by `test_dismiss_marks_status_and_removes_from_list` / `test_mark_read_removes_from_active_list`; the parametrized acceptance test adds the response-body assertion uniformly — keep the existing tests as-is (they assert list-removal behavior).

- [ ] **Step 2: Run to verify the new cases fail (or deselect locally)**

Run: `.venv/Scripts/python -m pytest test/integration/test_recommendations_api.py -v`
Expected locally: deselected. With Postgres up: the `Removed` cases FAIL with 422; the rest pass.

- [ ] **Step 3: Implement**

In `src/agentic_librarian/api/recommendations.py` replace lines 20-21:

```python
# Stage 3 wires the '✓ I read this' flow (add-book → status Read); 'Dismissed' = 'Not for me';
# 'Removed' = 'Not right now' (GH #130): neutral shelf-tidying, no negative record —
# the work is freed to resurface and the Librarian may legitimately re-pitch it.
ALLOWED_STATUS_UPDATES = {"Dismissed", "Read", "Removed"}
```

- [ ] **Step 4: Run the tests**

Run: `.venv/Scripts/python -m pytest test/integration/test_recommendations_api.py -v` (deselected locally / PASS with Postgres), then `.venv/Scripts/python -m pytest test/unit -q`.

- [ ] **Step 5: Lint, format, commit**

```bash
uvx ruff check src/agentic_librarian/api/recommendations.py test/integration/test_recommendations_api.py
uvx ruff format src/agentic_librarian/api/recommendations.py test/integration/test_recommendations_api.py
git add src/agentic_librarian/api/recommendations.py test/integration/test_recommendations_api.py
git commit -m "feat(api): accept neutral 'Removed' suggestion status (GH #130)"
```

---

### Task 3: MCP vocabulary + Librarian charter parity

**Files:**
- Modify: `src/agentic_librarian/mcp/server.py:790` (`_SUGGESTION_STATUSES`)
- Modify: `src/agentic_librarian/agents/prompts.py:165` (FEEDBACK HANDLING block)
- Modify: `src/agentic_librarian/agents/services.py:207` (the ADK parity copy of the same block)
- Test: `test/unit/test_prompts.py` (extend `CHARTER_PARITY_PHRASES`), `test/unit/test_mcp_tools.py` (append)

**Interfaces:**
- Consumes: `_normalize_status(value, allowed)` (mcp/server.py:67) — case-insensitive canonicalization.
- Produces: `_SUGGESTION_STATUSES == ("Accepted", "Dismissed", "Already Read", "Removed")`.

- [ ] **Step 1: Write the failing tests**

In `test/unit/test_prompts.py`, add one phrase to `CHARTER_PARITY_PHRASES` (the parametrized parity test picks it up for both backends automatically):

```python
CHARTER_PARITY_PHRASES = [
    "a turn may legitimately contain ZERO recommendations",
    "Clarifying questions are encouraged",
    "ACT on that reaction",
    "MULTIPLE ROUNDS",
    "exclude_tropes/exclude_styles",
    "never pitched again",
    "'update_suggestion_status' (Removed)",
]
```

Append to `test/unit/test_mcp_tools.py`:

```python
@pytest.mark.parametrize("raw", ["Removed", "removed", "REMOVED"])
def test_suggestion_statuses_include_neutral_removed(raw):
    # GH #130: the chat door to neutral removal — 'Removed' is canonical vocabulary
    # and case-normalizes like the other statuses.
    from agentic_librarian.mcp.server import _SUGGESTION_STATUSES, _normalize_status

    assert "Removed" in _SUGGESTION_STATUSES
    assert _normalize_status(raw, _SUGGESTION_STATUSES) == "Removed"
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/Scripts/python -m pytest test/unit/test_prompts.py test/unit/test_mcp_tools.py -v`
Expected: the two new parity cases FAIL (phrase absent from both charters) and the three normalize cases FAIL (`"Removed" not in _SUGGESTION_STATUSES`).

- [ ] **Step 3: Implement**

`src/agentic_librarian/mcp/server.py` line 790:

```python
_SUGGESTION_STATUSES = ("Accepted", "Dismissed", "Already Read", "Removed")
```

`src/agentic_librarian/agents/prompts.py` — insert one line after the Dismissed line (line 165), same list indentation:

```text
- "Not for me" / "I hate this" -> 'update_suggestion_status' (Dismissed).
- "Take it off my list for now" / "maybe later" -> 'update_suggestion_status' (Removed):
  neutral shelf-tidying, NOT a negative signal — the title may come back later.
```

`src/agentic_librarian/agents/services.py` — the identical two lines in the ADK copy (after line 207), preserving that block's 12-space indentation:

```text
            - "Not for me" / "I hate this" -> 'update_suggestion_status' (Dismissed).
            - "Take it off my list for now" / "maybe later" -> 'update_suggestion_status' (Removed):
              neutral shelf-tidying, NOT a negative signal — the title may come back later.
```

Also update the tool description in `src/agentic_librarian/agents/backends/claude_tools.py:106` so the Claude backend's tool schema names the new status:

```python
        "Update a suggestion's status (Accepted / Dismissed / Already Read / Removed).",
```

- [ ] **Step 4: Run the tests**

Run: `.venv/Scripts/python -m pytest test/unit/test_prompts.py test/unit/test_mcp_tools.py -v` → all PASS. Then `.venv/Scripts/python -m pytest test/unit -q` (watch for any charter-text assertion elsewhere — `test_backend_contract`/`test_agent_services` compare instruction texts and must stay green).

- [ ] **Step 5: Lint, format, commit**

```bash
uvx ruff check src/agentic_librarian/mcp/server.py src/agentic_librarian/agents/prompts.py src/agentic_librarian/agents/services.py src/agentic_librarian/agents/backends/claude_tools.py test/unit/test_prompts.py test/unit/test_mcp_tools.py
uvx ruff format src/agentic_librarian/mcp/server.py src/agentic_librarian/agents/prompts.py src/agentic_librarian/agents/services.py src/agentic_librarian/agents/backends/claude_tools.py test/unit/test_prompts.py test/unit/test_mcp_tools.py
git add -u
git commit -m "feat(chat): neutral 'Removed' status in MCP vocabulary + Librarian charter (GH #130)"
```

---

### Task 4: Frontend — "Not right now" button + add-book pick notice

**Files:**
- Modify: `frontend/src/api/client.ts` (status union, `AddBookResult`)
- Modify: `frontend/src/views/RecommendationsView.tsx`
- Modify: `frontend/src/views/AddBookView.tsx`
- Test: `frontend/src/views/RecommendationsView.test.tsx`, `frontend/src/views/AddBookView.test.tsx`

**Interfaces:**
- Consumes: backend accepts status `'Removed'` (Task 2) and `POST /books` returns `pick_resolved: boolean` (Task 1).
- Produces: `setRecommendationStatus(id: string, status: 'Dismissed' | 'Read' | 'Removed')`; `AddBookResult.pick_resolved: boolean`.

- [ ] **Step 1: Write the failing tests**

In `frontend/src/views/RecommendationsView.test.tsx`, append inside the `describe`:

```tsx
  it('"Not right now" removes the card with the neutral Removed status', async () => {
    renderWithRouter()
    await screen.findByText('Project Hail Mary')
    await userEvent.click(screen.getByRole('button', { name: /not right now/i }))
    expect(vi.mocked(setRecommendationStatus)).toHaveBeenCalledWith('r1', 'Removed')
    await waitFor(() => expect(screen.queryByText('Project Hail Mary')).not.toBeInTheDocument())
  })
```

In `frontend/src/views/AddBookView.test.tsx`, add `pick_resolved: false` to `okResult` and append two tests:

```tsx
  const okResult = {
    work_id: 'w1', title: 'Dune', read_number: 1, already_logged: false, enrichment_enqueued: true,
    pick_resolved: false,
  }
```

```tsx
  it('mentions the cleared pick when the server resolved one', async () => {
    vi.mocked(addBook).mockResolvedValueOnce({ ...okResult, pick_resolved: true })
    renderView()
    await userEvent.type(screen.getByLabelText(/title/i), 'Dune')
    await userEvent.type(screen.getByLabelText(/author/i), 'Frank Herbert')
    await userEvent.click(screen.getByRole('button', { name: /add to history/i }))
    expect(await screen.findByText(/Also cleared it from your Picks/i)).toBeInTheDocument()
  })

  it('does not mention Picks when nothing was resolved', async () => {
    vi.mocked(addBook).mockResolvedValueOnce(okResult)
    renderView()
    await userEvent.type(screen.getByLabelText(/title/i), 'Dune')
    await userEvent.type(screen.getByLabelText(/author/i), 'Frank Herbert')
    await userEvent.click(screen.getByRole('button', { name: /add to history/i }))
    await screen.findByText(/Enriching in the background/i)
    expect(screen.queryByText(/cleared it from your Picks/i)).toBeNull()
  })
```

- [ ] **Step 2: Run to verify they fail**

Run (from `frontend/`): `npx vitest run src/views/RecommendationsView.test.tsx src/views/AddBookView.test.tsx`
Expected: the "Not right now" test FAILS (no such button); the "mentions the cleared pick" test FAILS (message never rendered). The "does not mention" test may pass vacuously — that is fine; it exists to pin the negative once the feature lands.

- [ ] **Step 3: Implement**

`frontend/src/api/client.ts` — widen the status union and extend `AddBookResult`:

```ts
export async function setRecommendationStatus(id: string, status: 'Dismissed' | 'Read' | 'Removed'): Promise<void> {
```

```ts
export interface AddBookResult {
  work_id: string
  title: string
  read_number: number
  already_logged: boolean
  enrichment_enqueued: boolean
  pick_resolved: boolean
}
```

`frontend/src/views/RecommendationsView.tsx` — generalize `dismiss` into `resolve` and add the button (keep the busy/filter mechanics identical):

```tsx
  async function resolve(id: string, status: 'Dismissed' | 'Removed') {
    setBusy(id)
    try {
      await setRecommendationStatus(id, status)
      setRecs((cur) => (cur ? cur.filter((r) => r.id !== id) : cur))
    } finally { setBusy(null) }
  }
```

```tsx
            <div className="rec-actions">
              <button className="btn" onClick={() => readThis(r)}>✓ I read this</button>
              <button className="btn btn--ghost" onClick={() => void resolve(r.id, 'Removed')} disabled={busy === r.id}>Not right now</button>
              <button className="btn btn--ghost" onClick={() => void resolve(r.id, 'Dismissed')} disabled={busy === r.id}>Not for me</button>
            </div>
```

(The old `dismiss` function is replaced by `resolve`; update its former call site as shown. Existing tests asserting `('r1', 'Dismissed')` must stay green.)

`frontend/src/views/AddBookView.tsx` — append the notice to the success message:

```tsx
      setDone(
        `Added "${result.title}"! Enriching in the background (~a minute) — its tropes will appear in your History.` +
          (result.pick_resolved ? ' Also cleared it from your Picks.' : ''),
      )
```

- [ ] **Step 4: Run tests + build**

Run (from `frontend/`): `npx vitest run` (whole suite — App-level tests render these views), then `npm run build` (erasableSyntaxOnly gate).
Expected: all tests PASS, build clean.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/api/client.ts frontend/src/views/RecommendationsView.tsx frontend/src/views/AddBookView.tsx frontend/src/views/RecommendationsView.test.tsx frontend/src/views/AddBookView.test.tsx
git commit -m "feat(frontend): 'Not right now' neutral pick removal + cleared-pick notice (GH #130)"
```

---

### Task 5: Project notes — ADR + work log

**Files:**
- Modify: `docs/project_notes/decisions.md` (append ADR-062)
- Modify: `docs/project_notes/issues.md` (append work-log entry under the current section)

**Interfaces:** none — documentation only, but it is part of this feature's deliverable (institutional memory lives in the repo).

- [ ] **Step 1: Append ADR-062 to `docs/project_notes/decisions.md`** (follow the file's existing ADR format; next free number — verify ADR-061 is the current last and bump if not):

```markdown
### ADR-062: Neutral pick removal ('Removed') + history→picks auto-resolution (2026-07-19)

**Context:**
- GH #130: removing a pick for shelf-tidying (duplicate, already in history) forced a false
  "Not for me" ('Dismissed') — a permanent negative record the Librarian charter acts on.
- Books added to history (add-a-book, chat, CSV import) left matching active picks stale;
  only the "✓ I read this" prefill flow cleared its pick, client-side and racily.

**Decision:**
- New terminal `Suggestions.status` value `'Removed'` (UI: "Not right now"): neutral, keeps
  the row (provenance/audit), frees the partial-unique active slot so the title may resurface.
- Pick auto-resolution in `two_phase.add_read_event` (the choke point all four history-writing
  paths share): active 'Suggested' row for (user, work) flips to 'Read' in the same
  transaction, on both branches (incl. already_logged). `POST /books` reports `pick_resolved`.
- Chat parity: `'Removed'` added to MCP `_SUGGESTION_STATUSES` + one charter line (both
  prompt copies) distinguishing neutral removal from 'Dismissed'.

**Alternatives Considered:**
- Hard-delete the suggestion row -> rejected: loses pitch provenance, irreversible fat-finger.
- Post-submit dialog / auto-resolve+undo on add-a-book -> rejected (user decision): the book
  was just logged as read; no realistic "no" answer. "Not right now" is the escape hatch for
  the rare twin-work miss.
- Resolution only in POST /books -> rejected: chat + import adds would keep leaving stale picks.

**Consequences:**
- Invariant: a book in the user's history is never simultaneously an active pick.
- 'Suggested'→'Read'/'Removed' flips cannot violate the partial unique index (predicate binds
  only 'Suggested') — no guarded-flush choreography needed, unlike the history-edit path.
- The client-side exact-id resolution in the "✓ I read this" prefill flow is retained as a
  belt-and-suspenders for fast-pass twin-work misses; double-resolution is idempotent.
- Historical 'Dismissed' rows are not reclassified.
```

- [ ] **Step 2: Append to `docs/project_notes/issues.md`** (follow the file's existing entry format):

```markdown
### 2026-07-19 - GH #130: Picks neutral removal ("Not right now") + history→picks auto-resolution
- **Status**: Completed (PR pending merge)
- **Description**: New neutral 'Removed' suggestion status (third Picks button) + automatic
  pick-resolution in add_read_event covering add-a-book, chat, and CSV-import paths;
  POST /books reports pick_resolved and the add-book UI mentions the cleared pick.
- **URL**: https://github.com/jaydee829/shelfwright/issues/130
- **Notes**: ADR-062. Spec docs/superpowers/specs/2026-07-19-picks-neutral-removal-design.md.
  Dismissed-row history intentionally not reclassified.
```

- [ ] **Step 3: Commit**

```bash
git add docs/project_notes/decisions.md docs/project_notes/issues.md
git commit -m "docs(project-notes): ADR-062 + work log for picks neutral removal (GH #130)"
```
