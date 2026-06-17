# History Edit / Delete — Implementation Plan (D1b)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a user delete a reading-history entry (with confirmation) and edit its rating/date/notes, from the UI, scoped to themselves.

**Architecture:** Two user-scoped endpoints (`DELETE` + `PATCH /history/{id}`); a per-row ⋮ menu in `HistoryView` (Delete → confirm dialog; Edit → a dedicated `/history/:id/edit` view prefilled via router state). No DB migration, no new MCP tools.

**Tech Stack:** FastAPI + SQLAlchemy (backend), React 19 + react-router + Vitest (frontend).

**Spec:** `docs/superpowers/specs/2026-06-16-history-edit-delete-design.md`

---

## Test commands

Run via the **PowerShell tool**.
- **Backend integration** (DB up): `docker run --rm -v "C:\dev\agentic_librarian:/app" -w /app --network agentic_librarian_default -e POSTGRES_HOST=db agentic_librarian-app:latest python -m pytest <path> -q`
- **Frontend** (Windows host, from `C:\dev\agentic_librarian\frontend`): `cd C:\dev\agentic_librarian\frontend; npx vitest run <path>` ; `npm run build` ; `npm run lint`

vitest-4: `mockResolvedValueOnce` for per-test returns. Adding a new view route → **`App.test.tsx` must mock the new view** (Task 3).

## File Structure

- `src/agentic_librarian/api/main.py` — `_history_item` serializer (refactor + add `notes`), `DELETE /history/{id}`, `PATCH /history/{id}` (+ `HistoryUpdate`).
- `frontend/src/api/client.ts` — `deleteHistory`, `updateHistory`, `HistoryItem.notes`.
- `frontend/src/views/HistoryView.tsx` (+ `.css`) — per-row ⋮ menu + confirm dialog.
- `frontend/src/views/HistoryEditView.tsx` — new edit view (reuses `AddBookView.css`).
- `frontend/src/App.tsx` (route) + `frontend/src/App.test.tsx` (mock).
- Tests: `test/integration/test_api_history_db.py`, `frontend/src/views/HistoryView.test.tsx`, `frontend/src/views/HistoryEditView.test.tsx` (new).

---

### Task 1: Backend — DELETE + PATCH /history/{id}

**Files:** Modify `src/agentic_librarian/api/main.py`; Test `test/integration/test_api_history_db.py`.

- [ ] **Step 1: Write failing integration tests** — APPEND to `test/integration/test_api_history_db.py` (it has `two_user_client`, `FRIEND_ID`, `DEFAULT_USER_ID`, `ReadingHistory`, `DatabaseManager`, `UUID`, `date`, `pytest.mark.db_integration`):

```python
def test_delete_history_removes_only_callers_row(two_user_client):
    client = two_user_client(DEFAULT_USER_ID, "jaydee829@gmail.com")
    entry_id = client.get("/history").json()[0]["id"]
    assert client.delete(f"/history/{entry_id}").status_code == 200
    assert entry_id not in [h["id"] for h in client.get("/history").json()]


def test_delete_history_other_users_row_is_404(two_user_client, db_url):
    manager = DatabaseManager(db_url)
    with manager.get_session() as session:
        friend_id = str(session.query(ReadingHistory).filter(ReadingHistory.user_id == FRIEND_ID).first().id)
    assert two_user_client(DEFAULT_USER_ID, "jaydee829@gmail.com").delete(f"/history/{friend_id}").status_code == 404
    with manager.get_session() as session:
        assert session.get(ReadingHistory, UUID(friend_id)) is not None  # untouched


def test_patch_history_updates_rating_date_notes(two_user_client):
    client = two_user_client(DEFAULT_USER_ID, "jaydee829@gmail.com")
    entry_id = client.get("/history").json()[0]["id"]
    resp = client.patch(f"/history/{entry_id}", json={"rating": 5, "date_completed": "2020-12-31", "notes": "loved it"})
    assert resp.status_code == 200
    assert resp.json()["rating"] == 5 and resp.json()["date_completed"] == "2020-12-31"
    row = next(h for h in client.get("/history").json() if h["id"] == entry_id)
    assert row["rating"] == 5 and row["date_completed"] == "2020-12-31" and row["notes"] == "loved it"


def test_patch_history_rejects_bad_input_and_other_users(two_user_client, db_url):
    from datetime import timedelta

    client = two_user_client(DEFAULT_USER_ID, "jaydee829@gmail.com")
    entry_id = client.get("/history").json()[0]["id"]
    assert client.patch(f"/history/{entry_id}", json={"rating": True}).status_code == 422
    assert client.patch(f"/history/{entry_id}", json={"rating": 9}).status_code == 422
    future = (date.today() + timedelta(days=3)).isoformat()
    assert client.patch(f"/history/{entry_id}", json={"date_completed": future}).status_code == 422
    assert client.patch(f"/history/{entry_id}", json={"date_completed": None}).status_code == 422  # NOT NULL
    manager = DatabaseManager(db_url)
    with manager.get_session() as session:
        friend_id = str(session.query(ReadingHistory).filter(ReadingHistory.user_id == FRIEND_ID).first().id)
    assert client.patch(f"/history/{friend_id}", json={"rating": 3}).status_code == 404
```

- [ ] **Step 2: Run — expect failure** (405/404 for the new verbs)

`docker run --rm -v "C:\dev\agentic_librarian:/app" -w /app --network agentic_librarian_default -e POSTGRES_HOST=db agentic_librarian-app:latest python -m pytest test/integration/test_api_history_db.py -q`

- [ ] **Step 3: Implement in `main.py`.**

(a) Ensure these imports exist at the top (add any missing): `from uuid import UUID`, `from datetime import date`, `from pydantic import BaseModel, field_validator`, and add `HTTPException` to the existing `from fastapi import ...` line.

(b) Refactor the `get_history` row dict into a module-level serializer (add `notes`); replace the inline `_genre_and_tropes` + loop with a call to it:

```python
def _history_item(h) -> dict:
    """Serialize one ReadingHistory row to the /history payload shape (shared by GET + PATCH)."""
    work = h.edition.work
    top = sorted(work.tropes, key=lambda wt: wt.relevance_score, reverse=True)[:3]
    return {
        "id": str(h.id),
        "title": work.title,
        "authors": [c.author.name for c in work.contributors if c.role == "Author"],
        "date_completed": h.date_completed.isoformat() if h.date_completed else None,
        "rating": h.user_rating,
        "format": h.edition.format,
        "notes": h.user_notes,
        "genre": work.genres[0] if work.genres else None,
        "tropes": [wt.trope.name for wt in top],
    }
```
In `get_history`, replace the return with: `return [_history_item(h) for h in history_entries]` (drop the now-unused inline helper/loop).

(c) Add the update model + two endpoints (place after `get_history`):

```python
class HistoryUpdate(BaseModel):
    date_completed: date | None = None
    rating: int | None = None
    notes: str | None = None

    @field_validator("rating", mode="before")
    @classmethod
    def _no_bool_rating(cls, v: object) -> object:
        if isinstance(v, bool):
            raise ValueError("rating must be an integer, not a boolean")
        return v

    @field_validator("rating")
    @classmethod
    def _rating_range(cls, v: int | None) -> int | None:
        if v is not None and not 1 <= v <= 5:
            raise ValueError("rating must be from 1 to 5")
        return v

    @field_validator("date_completed")
    @classmethod
    def _not_future(cls, v: date | None) -> date | None:
        if v is not None and v > date.today():
            raise ValueError("date_completed cannot be in the future")
        return v


@app.delete("/history/{entry_id}")
def delete_history(entry_id: UUID, user: AuthenticatedUser = Depends(get_current_user)):  # noqa: B008
    with db_manager.get_session() as session:
        row = (
            session.query(ReadingHistory)
            .filter(ReadingHistory.id == entry_id, ReadingHistory.user_id == user.id)  # only mine (ADR-048)
            .first()
        )
        if row is None:
            raise HTTPException(status_code=404, detail="history entry not found")
        session.delete(row)
        session.flush()
    return {"id": str(entry_id), "deleted": True}


@app.patch("/history/{entry_id}")
def update_history(
    entry_id: UUID,
    req: HistoryUpdate,
    user: AuthenticatedUser = Depends(get_current_user),  # noqa: B008
):
    fields = req.model_dump(exclude_unset=True)  # only what the client actually sent
    if "date_completed" in fields and fields["date_completed"] is None:
        raise HTTPException(status_code=422, detail="date_completed cannot be null")
    with db_manager.get_session() as session:
        row = (
            session.query(ReadingHistory)
            .filter(ReadingHistory.id == entry_id, ReadingHistory.user_id == user.id)
            .first()
        )
        if row is None:
            raise HTTPException(status_code=404, detail="history entry not found")
        if "date_completed" in fields:
            row.date_completed = fields["date_completed"]
        if "rating" in fields:
            row.user_rating = fields["rating"]
        if "notes" in fields:
            row.user_notes = fields["notes"]
        session.flush()
        return _history_item(row)
```

- [ ] **Step 4: Run — expect pass** (the 4 new tests + existing history tests). Command from Step 2.

- [ ] **Step 5: Commit**

```bash
git add src/agentic_librarian/api/main.py test/integration/test_api_history_db.py
git commit -m "feat(history): user-scoped DELETE + PATCH /history/{id}; shared serializer (+ notes)"
```

---

### Task 2: HistoryView — ⋮ menu + delete confirm (+ client.deleteHistory)

**Files:** Modify `frontend/src/api/client.ts`, `frontend/src/views/HistoryView.tsx`, `frontend/src/views/HistoryView.css`, `frontend/src/views/HistoryView.test.tsx`.

**Note:** `HistoryView` will use `useNavigate`, so its tests must render inside a router. Update ALL existing `render(<HistoryView />)` calls to a `MemoryRouter` wrapper.

- [ ] **Step 1: Update existing tests to a router wrapper + add new tests.** In `frontend/src/views/HistoryView.test.tsx`:
  - Add `import { MemoryRouter } from 'react-router'` and a helper `const renderView = () => render(<HistoryView />, { wrapper: MemoryRouter })`; replace the existing `render(<HistoryView />)` calls (in the 4 current tests) with `renderView()`.
  - Append:
```tsx
  it('opens the ⋮ menu with Edit and Delete', async () => {
    vi.mocked(client.getHistory).mockResolvedValueOnce([item('a0', 'Jhereg')])
    renderView()
    await screen.findByText('Jhereg')
    await userEvent.click(screen.getByRole('button', { name: /actions for Jhereg/i }))
    expect(screen.getByRole('button', { name: /^edit$/i })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /^delete$/i })).toBeInTheDocument()
  })

  it('confirms then deletes the row', async () => {
    vi.mocked(client.getHistory).mockResolvedValueOnce([item('a0', 'Jhereg')])
    vi.mocked(client.deleteHistory).mockResolvedValueOnce()
    renderView()
    await screen.findByText('Jhereg')
    await userEvent.click(screen.getByRole('button', { name: /actions for Jhereg/i }))
    await userEvent.click(screen.getByRole('button', { name: /^delete$/i }))
    // confirm dialog
    await userEvent.click(screen.getByRole('button', { name: /delete entry/i }))
    expect(client.deleteHistory).toHaveBeenCalledWith('a0')
    await waitFor(() => expect(screen.queryByText('Jhereg')).not.toBeInTheDocument())
  })

  it('cancel in the confirm dialog keeps the row', async () => {
    vi.mocked(client.getHistory).mockResolvedValueOnce([item('a0', 'Jhereg')])
    renderView()
    await screen.findByText('Jhereg')
    await userEvent.click(screen.getByRole('button', { name: /actions for Jhereg/i }))
    await userEvent.click(screen.getByRole('button', { name: /^delete$/i }))
    await userEvent.click(screen.getByRole('button', { name: /cancel/i }))
    expect(client.deleteHistory).not.toHaveBeenCalled()
    expect(screen.getByText('Jhereg')).toBeInTheDocument()
  })
```
  (`waitFor` is already imported in this file; `deleteHistory` is auto-mocked by `vi.mock('../api/client')`.)

- [ ] **Step 2: Run — expect new tests FAIL.** `cd C:\dev\agentic_librarian\frontend; npx vitest run src/views/HistoryView.test.tsx`

- [ ] **Step 3a: `client.ts`** — add `deleteHistory` and `HistoryItem.notes`:
```ts
export interface HistoryItem {
  id: string
  title: string
  authors: string[]
  date_completed: string | null
  rating: number | null
  format: string | null
  notes?: string | null
  genre?: string | null
  tropes?: string[]
}
```
```ts
export async function deleteHistory(id: string): Promise<void> {
  const res = await authedFetchRaw(`/history/${id}`, { method: 'DELETE' })
  if (!res.ok) throw new Error(`delete history → ${res.status}`)
}
```

- [ ] **Step 3b: Rewrite `frontend/src/views/HistoryView.tsx`** to add the ⋮ menu + confirm dialog:
```tsx
import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router'
import { deleteHistory, getHistory, type HistoryItem } from '../api/client'
import './HistoryView.css'

const PAGE_SIZE = 50

export default function HistoryView() {
  const navigate = useNavigate()
  const [items, setItems] = useState<HistoryItem[] | null>(null)
  const [hasMore, setHasMore] = useState(false)
  const [loadingMore, setLoadingMore] = useState(false)
  const [menuFor, setMenuFor] = useState<string | null>(null)
  const [confirm, setConfirm] = useState<HistoryItem | null>(null)
  const [deleting, setDeleting] = useState(false)

  useEffect(() => {
    void getHistory(PAGE_SIZE, 0).then((page) => {
      setItems(page)
      setHasMore(page.length === PAGE_SIZE)
    })
  }, [])

  async function loadMore() {
    if (items === null) return
    setLoadingMore(true)
    try {
      const page = await getHistory(PAGE_SIZE, items.length)
      setItems([...items, ...page])
      setHasMore(page.length === PAGE_SIZE)
    } finally {
      setLoadingMore(false)
    }
  }

  async function doDelete() {
    if (!confirm) return
    setDeleting(true)
    try {
      await deleteHistory(confirm.id)
      setItems((cur) => (cur ? cur.filter((h) => h.id !== confirm.id) : cur))
      setConfirm(null)
    } finally {
      setDeleting(false)
    }
  }

  if (items === null) return <p>Loading…</p>
  if (items.length === 0) return <p>Nothing here yet — finish a book and it'll show up.</p>

  return (
    <div>
      <h2>Reading history</h2>
      <ul className="history-list">
        {items.map((h) => (
          <li key={h.id} className="history-row">
            <div className="history-main">
              <span className="history-title">{h.title}</span>
              <span className="history-authors">{h.authors.join(', ')}</span>
            </div>
            <div className="history-meta">
              {h.rating != null && <span className="history-rating">{'★'.repeat(h.rating)}</span>}
              {h.format && <span className="history-format">{h.format}</span>}
              {h.date_completed && <span className="history-date">{h.date_completed}</span>}
            </div>
            <div className="history-tropes">
              {h.tropes && h.tropes.length > 0 ? (
                <>
                  {h.genre && <span className="trope-chip genre">{h.genre}</span>}
                  {h.tropes.map((t) => (
                    <span key={t} className="trope-chip">{t}</span>
                  ))}
                </>
              ) : (
                <span className="trope-chip enriching">Enriching…</span>
              )}
            </div>
            <div className="history-actions">
              <button
                className="kebab"
                aria-label={`Actions for ${h.title}`}
                aria-haspopup="menu"
                onClick={() => setMenuFor(menuFor === h.id ? null : h.id)}
              >
                ⋮
              </button>
              {menuFor === h.id && (
                <div className="row-menu" role="menu">
                  <button
                    onClick={() => {
                      setMenuFor(null)
                      navigate(`/history/${h.id}/edit`, { state: h })
                    }}
                  >
                    Edit
                  </button>
                  <button
                    onClick={() => {
                      setMenuFor(null)
                      setConfirm(h)
                    }}
                  >
                    Delete
                  </button>
                </div>
              )}
            </div>
          </li>
        ))}
      </ul>
      {hasMore && (
        <button className="history-load-more" onClick={() => void loadMore()} disabled={loadingMore}>
          {loadingMore ? 'Loading…' : 'Load more'}
        </button>
      )}
      {confirm && (
        <div className="confirm-backdrop" role="dialog" aria-modal="true" aria-label="Confirm delete">
          <div className="confirm-box">
            <p>
              Delete your read of “{confirm.title}”
              {confirm.date_completed ? ` finished ${confirm.date_completed}` : ''}? This can't be undone.
            </p>
            <div className="confirm-actions">
              <button onClick={() => setConfirm(null)} disabled={deleting}>Cancel</button>
              <button className="danger" onClick={() => void doDelete()} disabled={deleting}>
                {deleting ? 'Deleting…' : 'Delete entry'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
```

- [ ] **Step 3c: Append CSS** to `frontend/src/views/HistoryView.css`:
```css
.history-row { position: relative; }
.history-actions { position: absolute; top: 6px; right: 6px; }
.kebab { background: none; border: none; font-size: 18px; line-height: 1; padding: 2px 6px; cursor: pointer; color: #6b7280; }
.row-menu { position: absolute; right: 0; top: 100%; z-index: 5; background: #fff; border: 1px solid #d1d5db; border-radius: 8px; box-shadow: 0 4px 12px rgba(0,0,0,0.1); display: flex; flex-direction: column; min-width: 96px; }
.row-menu button { background: none; border: none; text-align: left; padding: 8px 12px; cursor: pointer; }
.row-menu button:hover { background: #f3f4f6; }
.confirm-backdrop { position: fixed; inset: 0; background: rgba(0,0,0,0.4); display: grid; place-items: center; z-index: 50; }
.confirm-box { background: #fff; border-radius: 12px; padding: 20px; max-width: 22rem; }
.confirm-actions { display: flex; justify-content: flex-end; gap: 8px; margin-top: 12px; }
.confirm-actions .danger { background: #c62828; color: #fff; border: none; border-radius: 6px; padding: 6px 12px; }
```

- [ ] **Step 4: Run — expect all HistoryView tests pass** (4 pre-existing now wrapped + 3 new). Command from Step 2.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/api/client.ts frontend/src/views/HistoryView.tsx frontend/src/views/HistoryView.css frontend/src/views/HistoryView.test.tsx
git commit -m "feat(history): per-row ⋮ menu + delete confirm dialog"
```

---

### Task 3: HistoryEditView + route + client.updateHistory

**Files:** Create `frontend/src/views/HistoryEditView.tsx`, `frontend/src/views/HistoryEditView.test.tsx`; Modify `frontend/src/api/client.ts`, `frontend/src/App.tsx`, `frontend/src/App.test.tsx`.

- [ ] **Step 1: Write the failing test** `frontend/src/views/HistoryEditView.test.tsx`:
```tsx
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { MemoryRouter, Route, Routes } from 'react-router'
import type { HistoryItem } from '../api/client'

vi.mock('../auth/firebase', () => ({ getIdToken: vi.fn().mockResolvedValue(null) }))
vi.mock('../api/client')
import * as client from '../api/client'
import HistoryEditView from './HistoryEditView'

const row: HistoryItem = {
  id: 'h1', title: 'Jhereg', authors: ['Steven Brust'], date_completed: '2019-03-14',
  rating: 4, format: 'ebook', notes: 'fun', genre: 'Fantasy', tropes: ['Antihero'],
}

function renderAt(state: HistoryItem | null) {
  return render(
    <MemoryRouter initialEntries={[{ pathname: '/history/h1/edit', state }]}>
      <Routes>
        <Route path="/history/:id/edit" element={<HistoryEditView />} />
        <Route path="/history" element={<div>history-list</div>} />
      </Routes>
    </MemoryRouter>,
  )
}

describe('HistoryEditView', () => {
  it('prefills from router state and saves edits', async () => {
    vi.mocked(client.updateHistory).mockResolvedValueOnce({ ...row, rating: 5 })
    renderAt(row)
    expect(screen.getByDisplayValue('2019-03-14')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: /save changes/i }))
    await waitFor(() => expect(client.updateHistory).toHaveBeenCalledWith('h1', expect.objectContaining({ date_completed: '2019-03-14' })))
    expect(await screen.findByText('history-list')).toBeInTheDocument()  // navigated back
  })

  it('redirects to history when opened with no router state', () => {
    renderAt(null)
    expect(screen.getByText('history-list')).toBeInTheDocument()
  })
})
```

- [ ] **Step 2: Run — expect failure** (cannot resolve `./HistoryEditView`). `cd C:\dev\agentic_librarian\frontend; npx vitest run src/views/HistoryEditView.test.tsx`

- [ ] **Step 3a: `client.ts`** — add `updateHistory`:
```ts
export interface HistoryUpdate {
  date_completed?: string | null
  rating?: number | null
  notes?: string | null
}

export async function updateHistory(id: string, body: HistoryUpdate): Promise<HistoryItem> {
  const res = await authedFetchRaw(`/history/${id}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!res.ok) throw new Error(`update history → ${res.status}`)
  return res.json()
}
```

- [ ] **Step 3b: Create `frontend/src/views/HistoryEditView.tsx`** (reuse `AddBookView.css`):
```tsx
import { useState, type FormEvent } from 'react'
import { Navigate, useLocation, useNavigate, useParams } from 'react-router'
import { updateHistory, type HistoryItem } from '../api/client'
import './AddBookView.css'

export default function HistoryEditView() {
  const { id } = useParams()
  const navigate = useNavigate()
  const row = (useLocation().state as HistoryItem | null) ?? null
  const [rating, setRating] = useState(row?.rating != null ? String(row.rating) : '')
  const [dateFinished, setDateFinished] = useState(row?.date_completed ?? '')
  const [notes, setNotes] = useState(row?.notes ?? '')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  if (!row || !id) return <Navigate to="/history" replace />

  async function onSubmit(e: FormEvent) {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      await updateHistory(id as string, {
        rating: rating ? Number(rating) : null,
        date_completed: dateFinished || null,
        notes: notes.trim() || null,
      })
      navigate('/history')
    } catch {
      setError("Couldn't save those changes — try again.")
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="addbook">
      <h2>Edit read</h2>
      <p className="edit-context">
        <strong>{row.title}</strong>
        <br />
        {row.authors.join(', ')}
        {row.format ? ` · ${row.format}` : ''}
      </p>
      <form onSubmit={onSubmit} className="addbook-form">
        <label>
          Rating
          <select value={rating} onChange={(e) => setRating(e.target.value)}>
            <option value="">—</option>
            {[1, 2, 3, 4, 5].map((n) => (
              <option key={n} value={n}>{'★'.repeat(n)}</option>
            ))}
          </select>
        </label>
        <label>
          Date finished
          <input type="date" value={dateFinished} onChange={(e) => setDateFinished(e.target.value)} required />
        </label>
        <label>
          Notes
          <textarea value={notes} onChange={(e) => setNotes(e.target.value)} rows={3} />
        </label>
        <div className="edit-actions">
          <button type="submit" disabled={busy}>Save changes</button>
          <button type="button" onClick={() => navigate('/history')} disabled={busy}>Cancel</button>
        </div>
      </form>
      {error && <p className="addbook-error">{error}</p>}
    </div>
  )
}
```
(Add minimal CSS for `.edit-context`/`.edit-actions` to `AddBookView.css` — e.g. `.edit-actions { display: flex; gap: 8px; }` — or skip if visually acceptable; keep it tiny.)

- [ ] **Step 3c: Register the route** in `frontend/src/App.tsx`: add the import `import HistoryEditView from './views/HistoryEditView'` and, inside the `<Route element={<AppShell />}>` block, `<Route path="history/:id/edit" element={<HistoryEditView />} />`.

- [ ] **Step 3d: Mock the new view** in `frontend/src/App.test.tsx`: add `vi.mock('./views/HistoryEditView', () => ({ default: () => <div>history-edit-view</div> }))` alongside the other view mocks.

- [ ] **Step 4: Run — expect pass.** Run the edit-view test AND App.test:
`cd C:\dev\agentic_librarian\frontend; npx vitest run src/views/HistoryEditView.test.tsx src/App.test.tsx`

- [ ] **Step 5: Commit**

```bash
git add frontend/src/api/client.ts frontend/src/views/HistoryEditView.tsx frontend/src/views/HistoryEditView.test.tsx frontend/src/views/AddBookView.css frontend/src/App.tsx frontend/src/App.test.tsx
git commit -m "feat(history): dedicated edit view (rating/date/notes) + route"
```

---

### Task 4: Full verification + finish

- [ ] **Step 1: Backend integration** — `docker run --rm -v "C:\dev\agentic_librarian:/app" -w /app --network agentic_librarian_default -e POSTGRES_HOST=db agentic_librarian-app:latest python -m pytest test/integration -m "not api_dependent" -q` → pass.
- [ ] **Step 2: Frontend** — `cd C:\dev\agentic_librarian\frontend; npx vitest run` ; `npm run build` ; `npm run lint` → all green.
- [ ] **Step 3: Backend lint parity** — keep added lines ≤120 cols; if a clean `python:3.11-slim` + `pip install ruff==0.15.16` is handy, run `ruff format`/`ruff check` on `main.py` + the test file; else rely on CI ruff-format.
- [ ] **Step 4: Finish** — use superpowers:finishing-a-development-branch (push + PR; Gemini review → squash-merge).

---

## Self-Review

**Spec coverage:** D1 delete+edit (Tasks 1–3); D2 dedicated edit view route (Task 3); D3 per-row ⋮ menu (Task 2); D4 router-state prefill + redirect-when-absent (Task 3 `<Navigate>`); D5 confirm dialog before delete (Task 2); D6 API+UI only, no MCP tools (Task 1 endpoints only). Backend tests cover scoping (cross-user 404), validation (bool/range/future/null date), and delete-isolation; frontend tests cover the menu, confirm+delete, cancel, edit prefill+save, and the no-state redirect.

**Placeholder scan:** none — full code/commands per step.

**Type consistency:** `HistoryItem` gains `notes?` (Task 2) matching the backend serializer's new `notes` key (Task 1); `updateHistory(id, HistoryUpdate)` body keys (`date_completed`/`rating`/`notes`) match the backend `HistoryUpdate` model and are applied via `exclude_unset`; the edit view passes the row through router state typed as `HistoryItem`; the PATCH response is `HistoryItem` (full serializer). `useNavigate` in `HistoryView` is why its tests move to a `MemoryRouter` wrapper (Task 2 Step 1).
