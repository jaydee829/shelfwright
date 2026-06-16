# Enrichment Visibility + Tropes in History — Implementation Plan (C1/C2)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show each History row's genre + top tropes once its deep enrichment lands (and an "Enriching…" state until then), and set the expectation on the add screen that enrichment runs in the background.

**Architecture:** Derive enrichment status from trope presence (no DB migration). Backend adds `genre` + top-3 `tropes` to the `/history` payload; the frontend renders chips or an "Enriching…" indicator; `AddBookView` shows a static expectation message after a successful add.

**Tech Stack:** FastAPI + SQLAlchemy (backend), React 19 + TypeScript + Vitest (frontend).

**Spec:** `docs/superpowers/specs/2026-06-16-enrichment-visibility-design.md`

---

## Test commands

Run via the **PowerShell tool**.
- **Backend integration** (needs the compose DB):
  `docker run --rm -v "C:\dev\agentic_librarian:/app" -w /app --network agentic_librarian_default -e POSTGRES_HOST=db agentic_librarian-app:latest python -m pytest <path> -q`
- **Frontend** (Windows host, from `C:\dev\agentic_librarian\frontend`): `cd C:\dev\agentic_librarian\frontend; npx vitest run <path>` ; `npm run build` ; `npm run lint`

vitest-4: use `mockResolvedValueOnce` for per-test returns. `App.test.tsx` already mocks both views (no new route added).

## File Structure

- `src/agentic_librarian/api/main.py` — `/history` eager-load + payload (`genre`, `tropes`).
- `docs/project_notes/issues.md` — append DEBT-035.
- `frontend/src/api/client.ts` — `HistoryItem` gains `genre?`, `tropes?`.
- `frontend/src/views/HistoryView.tsx` (+ `.css`) — genre/trope chips + "Enriching…".
- `frontend/src/views/AddBookView.tsx` — success expectation message.
- Tests: `test/integration/test_api_history_db.py` (extend), `frontend/src/views/HistoryView.test.tsx`, `frontend/src/views/AddBookView.test.tsx`.

---

### Task 1: `/history` payload — genre + top-3 tropes (+ DEBT-035)

**Files:** Modify `src/agentic_librarian/api/main.py`, `docs/project_notes/issues.md`; Test `test/integration/test_api_history_db.py`.

`main.py` already imports `selectinload`, `joinedload`, `WorkTrope`, `Work`, `Edition`, `ReadingHistory`, `WorkContributor` — no new imports needed (`wt.trope.name` is reached via the relationship).

- [ ] **Step 1: Write the failing integration tests** — APPEND to `test/integration/test_api_history_db.py` (it already has `pytestmark = pytest.mark.db_integration`, the `two_user_client` fixture, `DEFAULT_USER_ID`, `AuthorModel`, `Edition`, `ReadingHistory`, `Work`, `WorkContributor`, `DatabaseManager`, `date`):

```python
def test_history_includes_genre_and_top_three_tropes(two_user_client, db_url):
    from agentic_librarian.db.models import Trope, WorkTrope

    manager = DatabaseManager(db_url)
    with manager.get_session() as session:
        author = AuthorModel(name="Trope Author")
        work = Work(
            title="Tropey Book",
            genres=["Fantasy", "Adventure"],
            contributors=[WorkContributor(author=author, role="Author")],
        )
        edition = Edition(work=work, format="ebook")
        session.add_all([author, work, edition])
        session.flush()
        # 4 tropes at varying relevance; only the top 3 by score surface, score-desc.
        for name, score in [("Heist", 0.90), ("Found Family", 0.95), ("Antihero", 0.99), ("Low Score", 0.10)]:
            trope = Trope(name=name)
            session.add(trope)
            session.flush()
            session.add(WorkTrope(work_id=work.id, trope_id=trope.id, relevance_score=score))
        session.add(ReadingHistory(edition_id=edition.id, user_id=DEFAULT_USER_ID, date_completed=date(2026, 1, 2)))
        session.flush()

    rows = two_user_client(DEFAULT_USER_ID, "jaydee829@gmail.com").get("/history").json()
    row = next(r for r in rows if r["title"] == "Tropey Book")
    assert row["genre"] == "Fantasy"
    assert row["tropes"] == ["Antihero", "Found Family", "Heist"]  # top 3, score desc; "Low Score" dropped


def test_history_no_tropes_returns_empty_list(two_user_client):
    # The fixture's "Shared Book" has no tropes/genres -> drives the "Enriching…" UI state.
    rows = two_user_client(DEFAULT_USER_ID, "jaydee829@gmail.com").get("/history").json()
    shared = next(r for r in rows if r["title"] == "Shared Book")
    assert shared["tropes"] == []
    assert shared["genre"] is None
```

- [ ] **Step 2: Run — expect failure** (`KeyError: 'genre'`)

`docker run --rm -v "C:\dev\agentic_librarian:/app" -w /app --network agentic_librarian_default -e POSTGRES_HOST=db agentic_librarian-app:latest python -m pytest test/integration/test_api_history_db.py -q`

- [ ] **Step 3: Implement.** In `get_history` (`main.py`), extend the `.options(...)` with a second
chain that shares the `edition→work` join strategy and diverges only at the trope collection
(`selectinload` so the to-many doesn't cartesian-multiply rows):

```python
            .options(
                joinedload(ReadingHistory.edition)
                .joinedload(Edition.work)
                .joinedload(Work.contributors)
                .joinedload(WorkContributor.author),
                joinedload(ReadingHistory.edition)
                .joinedload(Edition.work)
                .selectinload(Work.tropes)
                .joinedload(WorkTrope.trope),
            )
```

Then in the return list comprehension, compute the top tropes + genre per row and add the two keys:

```python
        def _genre_and_tropes(work):
            top = sorted(work.tropes, key=lambda wt: wt.relevance_score, reverse=True)[:3]
            return (work.genres[0] if work.genres else None, [wt.trope.name for wt in top])

        result = []
        for h in history_entries:
            genre, tropes = _genre_and_tropes(h.edition.work)
            result.append(
                {
                    "id": str(h.id),
                    "title": h.edition.work.title,
                    "authors": [c.author.name for c in h.edition.work.contributors if c.role == "Author"],
                    "date_completed": h.date_completed.isoformat() if h.date_completed else None,
                    "rating": h.user_rating,
                    "format": h.edition.format,
                    "genre": genre,
                    "tropes": tropes,
                }
            )
        return result
```
(Replace the existing `return [ ... ]` comprehension with this loop form.)

- [ ] **Step 4: Append DEBT-035** to `docs/project_notes/issues.md` (under the `## Log` section, newest first — place it above the most recent dated entry):

```markdown
### 2026-06-16 - DEBT-035: Detect stuck/failed background enrichment
- **Status**: Open (deferred from C1/C2 enrichment-visibility by decision)
- **Description**: Enrichment status is DERIVED from trope presence (no enriched_at/status column), so a deep pass that failed, found no tropes, or whose Cloud Task never fired is indistinguishable from one still in flight — History shows "Enriching…" indefinitely.
- **URL**: spec `docs/superpowers/specs/2026-06-16-enrichment-visibility-design.md` (D5)
- **Notes**: Future fix needs a creation/enqueue timestamp (Work has no created_at) + a timeout sweep or explicit status to flag long-pending works as failed/retryable; pairs with a retry action alongside D1b (history edit/delete).
```

- [ ] **Step 5: Run — expect pass** (both new tests + the existing history tests). Command from Step 2.

- [ ] **Step 6: Commit**

```bash
git add src/agentic_librarian/api/main.py test/integration/test_api_history_db.py docs/project_notes/issues.md
git commit -m "feat(history): genre + top-3 tropes in /history payload (derive-only status); log DEBT-035"
```

---

### Task 2: HistoryView — genre/trope chips + "Enriching…"

**Files:** Modify `frontend/src/api/client.ts`, `frontend/src/views/HistoryView.tsx`, `frontend/src/views/HistoryView.css`, `frontend/src/views/HistoryView.test.tsx`.

- [ ] **Step 1: Add failing tests** — APPEND to `frontend/src/views/HistoryView.test.tsx` (it mocks `../api/client` and `../auth/firebase`; `client`, `render`, `screen`, `userEvent`, `vi` already imported):

```tsx
  it('renders genre + trope chips when a row has tropes', async () => {
    vi.mocked(client.getHistory).mockResolvedValueOnce([
      {
        id: 'x', title: 'Tropey', authors: ['A'], date_completed: '2024-01-01', rating: 4, format: 'ebook',
        genre: 'Fantasy', tropes: ['Found Family', 'Antihero', 'Heist'],
      },
    ])
    render(<HistoryView />)
    expect(await screen.findByText('Tropey')).toBeInTheDocument()
    expect(screen.getByText('Fantasy')).toBeInTheDocument()
    expect(screen.getByText('Found Family')).toBeInTheDocument()
    expect(screen.queryByText(/Enriching/)).not.toBeInTheDocument()
  })

  it('renders an Enriching… chip when a row has no tropes', async () => {
    vi.mocked(client.getHistory).mockResolvedValueOnce([
      {
        id: 'y', title: 'Fresh', authors: ['B'], date_completed: '2024-01-01', rating: null, format: 'ebook',
        genre: null, tropes: [],
      },
    ])
    render(<HistoryView />)
    expect(await screen.findByText('Fresh')).toBeInTheDocument()
    expect(screen.getByText(/Enriching/)).toBeInTheDocument()
  })
```

- [ ] **Step 2: Run — expect the 2 new tests FAIL** (no chips/Enriching rendered; possibly TS error on `genre`/`tropes`).

`cd C:\dev\agentic_librarian\frontend; npx vitest run src/views/HistoryView.test.tsx`

- [ ] **Step 3a: Extend `HistoryItem`** in `frontend/src/api/client.ts`:

```ts
export interface HistoryItem {
  id: string
  title: string
  authors: string[]
  date_completed: string | null
  rating: number | null
  format: string | null
  genre?: string | null
  tropes?: string[]
}
```

- [ ] **Step 3b: Render the trope line** in `frontend/src/views/HistoryView.tsx` — add a block inside the `<li>`, after the `history-meta` div:

```tsx
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
```

- [ ] **Step 3c: Chip styles** — append to `frontend/src/views/HistoryView.css`:

```css
.history-tropes { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 4px; }
.trope-chip { font-size: 12px; padding: 2px 8px; border-radius: 10px; background: #eef2ff; color: #3730a3; }
.trope-chip.genre { background: #e0e7ff; font-weight: 600; }
.trope-chip.enriching { background: transparent; color: #6b7280; font-style: italic; padding-left: 0; }
```

- [ ] **Step 4: Run — expect pass** (new + the 2 pre-existing pagination tests; the pagination `item()` helper omits `genre`/`tropes`, so those rows render "Enriching…", which doesn't affect the title assertions).

`cd C:\dev\agentic_librarian\frontend; npx vitest run src/views/HistoryView.test.tsx`

- [ ] **Step 5: Commit**

```bash
git add frontend/src/api/client.ts frontend/src/views/HistoryView.tsx frontend/src/views/HistoryView.css frontend/src/views/HistoryView.test.tsx
git commit -m "feat(history): genre + trope chips, Enriching… state per row"
```

---

### Task 3: AddBookView — background-enrichment expectation message

**Files:** Modify `frontend/src/views/AddBookView.tsx`, `frontend/src/views/AddBookView.test.tsx`.

- [ ] **Step 1: Read `AddBookView.test.tsx`** to find the existing success-path assertion (it asserts the old `Added "<title>" to your history.` copy). Update that assertion to match the new copy AND add an explicit check for the enrichment message. The success test should assert text matching `/Enriching in the background/i`.

Concretely, change the existing success assertion to:
```tsx
    expect(await screen.findByText(/Enriching in the background/i)).toBeInTheDocument()
```
(Keep the rest of that test — the mocked `addBook` resolving, the form fill, the submit — as-is.)

- [ ] **Step 2: Run — expect failure** (old copy gone / new copy not present yet).

`cd C:\dev\agentic_librarian\frontend; npx vitest run src/views/AddBookView.test.tsx`

- [ ] **Step 3: Update the success message** in `frontend/src/views/AddBookView.tsx` — replace:
```tsx
      setDone(`Added "${result.title}" to your history.`)
```
with:
```tsx
      setDone(
        `Added "${result.title}"! Enriching in the background (~a minute) — its tropes will appear in your History.`,
      )
```

- [ ] **Step 4: Run — expect pass.** Command from Step 2.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/views/AddBookView.tsx frontend/src/views/AddBookView.test.tsx
git commit -m "feat(add-book): set background-enrichment expectation on success"
```

---

### Task 4: Full verification + finish

- [ ] **Step 1: Backend integration suite** — `docker run --rm -v "C:\dev\agentic_librarian:/app" -w /app --network agentic_librarian_default -e POSTGRES_HOST=db agentic_librarian-app:latest python -m pytest test/integration -m "not api_dependent" -q` → pass.
- [ ] **Step 2: Frontend** — `cd C:\dev\agentic_librarian\frontend; npx vitest run` ; `npm run build` ; `npm run lint` → all green.
- [ ] **Step 3: Lint parity (backend)** — keep added lines ≤120 cols; if a clean `python:3.11-slim` + `pip install ruff==0.15.16` is available, run `ruff format`/`ruff check` on `src/agentic_librarian/api/main.py` and the test file (ruff isn't in the runtime image); else rely on CI ruff-format.
- [ ] **Step 4: Finish** — use superpowers:finishing-a-development-branch (push + PR; Gemini review → squash-merge).

---

## Self-Review

**Spec coverage:** D1 derive-from-tropes (no migration) → `/history` returns `tropes`; empty list when none (Task 1) → frontend "Enriching…" (Task 2). D2 add-flow expectation message (Task 3). D3 genre + top-3 tropes by `relevance_score`, "Enriching…" when none, genre not shown alone (Task 2 conditional). D4 no polling (nothing added). D5 DEBT-035 logged (Task 1 Step 4). Tests: backend top-3 ordering + empty-list; frontend chips vs Enriching; add-book message.

**Placeholder scan:** none — full code/commands per step. The success copy is finalized in Task 3.

**Type consistency:** `HistoryItem.genre?: string | null` / `tropes?: string[]` (client) match the `/history` keys `genre` (str|None) / `tropes` (list[str]) from Task 1, and the `HistoryView` render guards on `h.tropes?.length`. The eager-load shares the `edition→work` joinedload prefix in both option chains (avoids the mixed-strategy conflict), diverging to `selectinload` only for the trope to-many.
