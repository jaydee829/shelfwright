# Lift 2 Stage 2 — Frontend SPA Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the friends-&-family beta web app — a Vite + React (TypeScript) SPA over the conversational Librarian, with Google sign-in, a streaming Chat view, and History / Recommendations / Analysis views — plus the two new read-only backend endpoints (`/recommendations`, `/analysis`) those views need.

**Architecture:** A net-new `frontend/` Vite app (React 19, react-router 7, firebase 11, Vitest + React Testing Library) signs in with Firebase (Google), attaches the ID token as `Authorization: Bearer` on every call, and consumes the Stage-1 SSE `/chat` contract via `fetch()` + a streaming reader. In **dev** the Vite dev server proxies API paths to the running FastAPI (`localhost:8080`) — production same-origin serving and the multi-stage Docker build are **Stage 4**. Two new FastAPI router modules (`api/recommendations.py`, `api/analysis.py`) expose simple user-scoped reads over the existing `Suggestions` / reading-history data; they follow the established per-module `DatabaseManager` + `set_db_manager` pattern (pool consolidation is Stage 4).

**Tech Stack:** Backend — FastAPI, SQLAlchemy, pytest (existing harness). Frontend — Vite, React 19 + TypeScript, react-router 7, firebase 11 (Auth), Vitest + @testing-library/react + jsdom, ESLint.

---

## Scope (locked with the user)

**In this stage:** all net-new frontend foundation; app shell + responsive nav (icon rail desktop / bottom bar mobile); Firebase Google sign-in + auth-gate screens (signed-out / not-invited); SSE chat client + **Chat** view; **History** view (existing `GET /history`); **Recommendations** view (new `GET /recommendations` + `POST /recommendations/{id}/status` → Dismissed) ; **Analysis** view (new `GET /analysis`, four sub-views).

**Deferred and why:**
- **Add-a-book** view + `POST /books` → **Stage 3** (needs the two-phase enrichment / Cloud Tasks internal endpoint). The nav gains its **Add** item in Stage 3.
- Recommendations **"✓ I read this"** action routes through the add-book form, so in Stage 2 it renders **disabled** (with a "coming soon" title); **"Not for me" → Dismissed** is fully wired (a plain status POST).
- **Production static serving** (FastAPI `StaticFiles` + SPA catch-all) and the **multi-stage Docker build** → **Stage 4** (per spec §6). Stage 2 runs the SPA via `npm run dev` + Vite proxy.
- **Playwright e2e** → deferred to **Stage 4's live-verify** step: it needs real Firebase + a real running stack, so it cannot run in CI without auth secrets. Stage 2's frontend tests are Vitest + RTL with the backend mocked. (Spec §5 lists Playwright under Stage 2; this is a conscious move to keep Stage 2 CI-able without secrets.)
- `GET /analysis` is **one endpoint returning all four sub-views** rather than the spec's `/analysis/...` sub-paths — at beta data scale a single round trip is simpler for both ends; future sub-views (ratings-over-time, taste-profile) can be added as fields or new endpoints.

---

## File structure

**Backend (new):**
- `src/agentic_librarian/api/recommendations.py` — `APIRouter`: `GET /recommendations`, `POST /recommendations/{id}/status`. Own `db_manager` + `set_db_manager`.
- `src/agentic_librarian/api/analysis.py` — `APIRouter`: `GET /analysis` (snapshot / genres+moods / top tropes / authors+narrators). Own `db_manager` + `set_db_manager`.
- `src/agentic_librarian/api/main.py` — **modified**: `include_router` for both.
- `test/integration/test_recommendations_api.py`, `test/integration/test_analysis_api.py` — new.

**Frontend (all new, under `frontend/`):**
- `frontend/package.json`, `frontend/vite.config.ts`, `frontend/tsconfig*.json`, `frontend/index.html`, `frontend/.gitignore`, `frontend/.env.example`, `frontend/README.md`, `frontend/eslint.config.js`, `frontend/src/vite-env.d.ts`, `frontend/src/test/setup.ts`.
- `frontend/src/main.tsx`, `frontend/src/App.tsx`, `frontend/src/index.css`.
- `frontend/src/auth/firebase.ts` — Firebase init + auth helpers.
- `frontend/src/auth/AuthContext.tsx` — auth state (`loading` / `signedOut` / `notInvited` / `ready`) + provider/hook.
- `frontend/src/api/client.ts` — `authedFetch`, `probeAccess`, `getHistory`, `getRecommendations`, `setRecommendationStatus`, `getAnalysis`, `getCurrentConversation`, `newConversation`, `streamChat`, shared types.
- `frontend/src/components/AppShell.tsx`, `Nav.tsx`, `TopBar.tsx`, `SignIn.tsx`, `NotInvited.tsx`.
- `frontend/src/views/ChatView.tsx`, `HistoryView.tsx`, `RecommendationsView.tsx`, `AnalysisView.tsx`.
- `*.test.tsx` / `*.test.ts` colocated next to each unit under test.

**CI (new):** `.github/workflows/frontend.yml` — Node, `npm ci`, lint, typecheck/build, `vitest run`.

---

## Conventions for the implementer

- **Backend tests** run inside the project's Docker DB. On this Windows clone, run the fast suite via a throwaway container (the long-running app container mounts the *WSL* clone, so `docker exec` would test stale code):
  ```powershell
  docker run --rm -v C:\dev\agentic_librarian:/app -w /app --network agentic_librarian_default -e POSTGRES_HOST=db agentic_librarian-app:latest pytest test/integration/test_recommendations_api.py -v
  ```
  Backend tests use `pytestmark = pytest.mark.db_integration` and the `DatabaseManager(db_url)` + `monkeypatch.setattr(<module>, "db_manager", manager)` pattern (see `test/integration/test_chat_api.py`). The conftest builds the schema via `alembic upgrade head`, truncates between tests, and reseeds the default user.
- **Authoritative backend lint** is CI `lint.yml` (pinned ruff in an isolated env treats `agentic_librarian` as third-party; the image's newer ruff false-flags `I001`). Reproduce locally with a clean `python:3.11-slim` + `pip install ruff==0.4.4` if CI lint fails.
- **Frontend** commands run from `frontend/` **on the Windows host** (decided 2026-06-09): `npm run dev`, `npm run test`, `npm run lint`, `npm run build`. Node **24.14.0** is already installed on Windows (≥ Vite 7's 20.19/22 floor); the WSL devcontainer stays backend-only and is **not** touched this stage. The frontend edit/test loop is native to `C:\dev` (no dual-checkout git-pull needed, unlike the backend). Frontend code is **not** linted by ruff/pre-commit (it lives outside `src/`).
- **Identity** comes from `get_current_user` context, never a parameter (ADR-048). The new read endpoints filter by `user.id` directly, exactly like the existing `GET /history`.
- **Secrets:** never commit `frontend/.env.local` or any Firebase web config values. `.env.example` documents variable *names* only.

---

## Task 1: `GET /recommendations` endpoint

**Files:**
- Create: `src/agentic_librarian/api/recommendations.py`
- Modify: `src/agentic_librarian/api/main.py`
- Test: `test/integration/test_recommendations_api.py`

- [ ] **Step 1: Write the failing test**

Create `test/integration/test_recommendations_api.py`:

```python
from uuid import uuid4

import pytest
from agentic_librarian.api import auth
from agentic_librarian.api import main as api_main
from agentic_librarian.api import recommendations as rec_mod
from agentic_librarian.core.user_context import DEFAULT_USER_EMAIL, DEFAULT_USER_ID
from agentic_librarian.db.models import Author, Suggestions, User, Work, WorkContributor
from agentic_librarian.db.session import DatabaseManager
from fastapi.testclient import TestClient

pytestmark = pytest.mark.db_integration


@pytest.fixture
def client(db_url, monkeypatch):
    manager = DatabaseManager(db_url)
    monkeypatch.setattr(api_main, "db_manager", manager)
    monkeypatch.setattr(rec_mod, "db_manager", manager)
    monkeypatch.setitem(
        api_main.app.dependency_overrides,
        auth.get_current_user,
        lambda: auth.AuthenticatedUser(id=DEFAULT_USER_ID, email=DEFAULT_USER_EMAIL),
    )
    yield TestClient(api_main.app)


def _seed_suggestion(manager, *, user_id, title, author, status="Suggested", justification="because"):
    with manager.get_session() as s:
        work = Work(title=title)
        s.add(work)
        s.flush()
        a = Author(name=author)
        s.add(a)
        s.flush()
        s.add(WorkContributor(work_id=work.id, author_id=a.id, role="Author"))
        sug = Suggestions(work_id=work.id, user_id=user_id, status=status, justification=justification)
        s.add(sug)
        s.flush()
        return sug.id, work.id


def test_lists_only_active_suggestions_for_the_user(client, db_url):
    manager = DatabaseManager(db_url)
    sid, wid = _seed_suggestion(manager, user_id=DEFAULT_USER_ID, title="Dune", author="Herbert")
    _seed_suggestion(manager, user_id=DEFAULT_USER_ID, title="Old Pick", author="X", status="Dismissed")

    body = client.get("/recommendations").json()

    assert [r["title"] for r in body] == ["Dune"]  # Dismissed one excluded
    row = body[0]
    assert row["id"] == str(sid)
    assert row["work_id"] == str(wid)
    assert row["authors"] == ["Herbert"]
    assert row["justification"] == "because"
    assert row["status"] == "Suggested"


def test_does_not_leak_another_users_suggestions(client, db_url):
    manager = DatabaseManager(db_url)
    other_id = uuid4()
    with manager.get_session() as s:
        s.add(User(id=other_id, email="other@example.com"))
        s.flush()
    _seed_suggestion(manager, user_id=other_id, title="Secret", author="Nobody")

    body = client.get("/recommendations").json()

    assert body == []  # the default user sees none of the other user's suggestions
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest test/integration/test_recommendations_api.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'agentic_librarian.api.recommendations'`.

- [ ] **Step 3: Create the router module**

Create `src/agentic_librarian/api/recommendations.py`:

```python
"""Recommendations surface (Lift 2 Stage 2). Reads the user's active Suggestions
(Lift 1 table) and lets them dismiss one. The '✓ I read this' → Read transition runs
through the add-book flow (Stage 3), so this endpoint accepts only 'Dismissed' for now.
Identity comes from the auth context; rows are filtered by user.id (ADR-048)."""

from __future__ import annotations

from uuid import UUID

from agentic_librarian.api.auth import AuthenticatedUser, get_current_user
from agentic_librarian.db.models import Suggestions, Work, WorkContributor
from agentic_librarian.db.session import DatabaseManager
from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy.orm import joinedload, selectinload

router = APIRouter()
db_manager = DatabaseManager()

# Stage 2 allows only Dismissed; Stage 3 wires 'Read' via the add-book flow.
ALLOWED_STATUS_UPDATES = {"Dismissed"}


def set_db_manager(new_manager: DatabaseManager) -> None:
    """Override the module db_manager (for tests) — the mcp/server.py pattern."""
    global db_manager
    db_manager = new_manager


@router.get("/recommendations")
def get_recommendations(user: AuthenticatedUser = Depends(get_current_user)):  # noqa: B008
    with db_manager.get_session() as session:
        rows = (
            session.query(Suggestions)
            .filter(Suggestions.user_id == user.id, Suggestions.status == "Suggested")  # my active picks
            .options(
                joinedload(Suggestions.work),
                selectinload(Suggestions.work).selectinload(Work.contributors).joinedload(WorkContributor.author),
            )
            .order_by(Suggestions.suggested_at.desc())
            .all()
        )
        return [
            {
                "id": str(s.id),
                "work_id": str(s.work_id),
                "title": s.work.title,
                "authors": [c.author.name for c in s.work.contributors if c.role == "Author"],
                "justification": s.justification,
                "context": s.context,
                "suggested_at": s.suggested_at.isoformat() if s.suggested_at else None,
                "status": s.status,
            }
            for s in rows
        ]
```

- [ ] **Step 4: Wire the router into the app**

In `src/agentic_librarian/api/main.py`, add the import alongside the other `agentic_librarian.api` imports near the top:

```python
from agentic_librarian.api.recommendations import router as recommendations_router
```

Then, immediately after `db_manager = DatabaseManager()` and its NOTE comment (around line 23), register it:

```python
app.include_router(recommendations_router)
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `pytest test/integration/test_recommendations_api.py -v`
Expected: PASS (both tests).

- [ ] **Step 6: Commit**

```bash
git add src/agentic_librarian/api/recommendations.py src/agentic_librarian/api/main.py test/integration/test_recommendations_api.py
git commit -m "feat: GET /recommendations — user-scoped active suggestions"
```

---

## Task 2: `POST /recommendations/{id}/status` (Dismissed)

**Files:**
- Modify: `src/agentic_librarian/api/recommendations.py`
- Test: `test/integration/test_recommendations_api.py`

- [ ] **Step 1: Write the failing test**

Append to `test/integration/test_recommendations_api.py`:

```python
def test_dismiss_marks_status_and_removes_from_list(client, db_url):
    manager = DatabaseManager(db_url)
    sid, _ = _seed_suggestion(manager, user_id=DEFAULT_USER_ID, title="Meh", author="Z")

    resp = client.post(f"/recommendations/{sid}/status", json={"status": "Dismissed"})
    assert resp.status_code == 200
    assert resp.json() == {"id": str(sid), "status": "Dismissed"}

    assert client.get("/recommendations").json() == []  # no longer active


def test_rejects_unknown_status(client, db_url):
    manager = DatabaseManager(db_url)
    sid, _ = _seed_suggestion(manager, user_id=DEFAULT_USER_ID, title="Meh", author="Z")

    resp = client.post(f"/recommendations/{sid}/status", json={"status": "Banished"})
    assert resp.status_code == 422


def test_cannot_dismiss_another_users_suggestion(client, db_url):
    manager = DatabaseManager(db_url)
    other_id = uuid4()
    with manager.get_session() as s:
        s.add(User(id=other_id, email="other2@example.com"))
        s.flush()
    sid, _ = _seed_suggestion(manager, user_id=other_id, title="Secret", author="Nobody")

    resp = client.post(f"/recommendations/{sid}/status", json={"status": "Dismissed"})
    assert resp.status_code == 404  # scoping: not the caller's suggestion
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest test/integration/test_recommendations_api.py -k status -v`
Expected: FAIL with 404/405 (route not defined).

- [ ] **Step 3: Add the endpoint**

Append to `src/agentic_librarian/api/recommendations.py`:

```python
@router.post("/recommendations/{suggestion_id}/status")
def set_recommendation_status(
    suggestion_id: UUID,
    status: str = Body(..., embed=True),
    user: AuthenticatedUser = Depends(get_current_user),  # noqa: B008
):
    if status not in ALLOWED_STATUS_UPDATES:
        raise HTTPException(status_code=422, detail=f"status must be one of {sorted(ALLOWED_STATUS_UPDATES)}")
    with db_manager.get_session() as session:
        sug = (
            session.query(Suggestions)
            .filter(Suggestions.id == suggestion_id, Suggestions.user_id == user.id)  # scoping: only mine
            .first()
        )
        if sug is None:
            raise HTTPException(status_code=404, detail="suggestion not found")
        sug.status = status
        session.flush()
    return {"id": str(suggestion_id), "status": status}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `pytest test/integration/test_recommendations_api.py -v`
Expected: PASS (all five tests).

- [ ] **Step 5: Commit**

```bash
git add src/agentic_librarian/api/recommendations.py test/integration/test_recommendations_api.py
git commit -m "feat: POST /recommendations/{id}/status — dismiss, user-scoped"
```

---

## Task 3: `GET /analysis` endpoint

**Files:**
- Create: `src/agentic_librarian/api/analysis.py`
- Modify: `src/agentic_librarian/api/main.py`
- Test: `test/integration/test_analysis_api.py`

- [ ] **Step 1: Write the failing test**

Create `test/integration/test_analysis_api.py`:

```python
from datetime import date
from uuid import uuid4

import pytest
from agentic_librarian.api import analysis as analysis_mod
from agentic_librarian.api import auth
from agentic_librarian.api import main as api_main
from agentic_librarian.core.user_context import DEFAULT_USER_EMAIL, DEFAULT_USER_ID
from agentic_librarian.db.models import (
    Author,
    Edition,
    Narrator,
    ReadingHistory,
    Trope,
    User,
    Work,
    WorkContributor,
    WorkTrope,
)
from agentic_librarian.db.session import DatabaseManager
from fastapi.testclient import TestClient

pytestmark = pytest.mark.db_integration


@pytest.fixture
def client(db_url, monkeypatch):
    manager = DatabaseManager(db_url)
    monkeypatch.setattr(api_main, "db_manager", manager)
    monkeypatch.setattr(analysis_mod, "db_manager", manager)
    monkeypatch.setitem(
        api_main.app.dependency_overrides,
        auth.get_current_user,
        lambda: auth.AuthenticatedUser(id=DEFAULT_USER_ID, email=DEFAULT_USER_EMAIL),
    )
    yield TestClient(api_main.app)


def _seed_read(
    manager,
    *,
    user_id,
    title,
    author,
    genres,
    moods,
    tropes,
    narrator=None,
    fmt="audiobook",
    rating=4,
    completed=None,
):
    with manager.get_session() as s:
        work = Work(title=title, genres=genres, moods=moods)
        s.add(work)
        s.flush()
        a = Author(name=author)
        s.add(a)
        s.flush()
        s.add(WorkContributor(work_id=work.id, author_id=a.id, role="Author"))
        for tname in tropes:
            t = Trope(name=f"{tname}-{uuid4().hex[:6]}")  # unique() on tropes.name
            s.add(t)
            s.flush()
            s.add(WorkTrope(work_id=work.id, trope_id=t.id, relevance_score=1.0))
        edition = Edition(work_id=work.id, format=fmt)
        if narrator:
            n = Narrator(name=narrator)
            s.add(n)
            s.flush()
            edition.narrators.append(n)
        s.add(edition)
        s.flush()
        s.add(
            ReadingHistory(
                edition_id=edition.id,
                user_id=user_id,
                date_completed=completed or date.today(),
                user_rating=rating,
            )
        )
        s.flush()


def test_analysis_aggregates_the_users_reading(client, db_url):
    manager = DatabaseManager(db_url)
    _seed_read(
        manager,
        user_id=DEFAULT_USER_ID,
        title="Dune",
        author="Herbert",
        genres=["Sci-Fi"],
        moods=["epic"],
        tropes=["chosen-one"],
        narrator="Vance",
        rating=5,
    )
    _seed_read(
        manager,
        user_id=DEFAULT_USER_ID,
        title="Hyperion",
        author="Simmons",
        genres=["Sci-Fi"],
        moods=["dark"],
        tropes=["chosen-one"],
        narrator="Vance",
        rating=3,
    )

    body = client.get("/analysis").json()

    snap = body["snapshot"]
    assert snap["total_read"] == 2
    assert snap["average_rating"] == 4.0
    assert snap["distinct_authors"] == 2
    assert {f["name"]: f["count"] for f in snap["formats"]} == {"audiobook": 2}
    assert {g["name"]: g["count"] for g in body["genres"]} == {"Sci-Fi": 2}
    assert {m["name"] for m in body["moods"]} == {"epic", "dark"}
    # both works tagged a "chosen-one"-prefixed trope; names are unique so each counts once,
    # but the section still surfaces the user's tropes
    assert len(body["top_tropes"]) == 2
    assert {a["name"]: a["count"] for a in body["authors"]} == {"Herbert": 1, "Simmons": 1}
    assert {n["name"]: n["count"] for n in body["narrators"]} == {"Vance": 2}


def test_analysis_empty_for_user_with_no_reading(client):
    body = client.get("/analysis").json()
    assert body["snapshot"] == {
        "total_read": 0,
        "read_this_year": 0,
        "average_rating": None,
        "distinct_authors": 0,
        "formats": [],
    }
    assert body["genres"] == []
    assert body["top_tropes"] == []
    assert body["narrators"] == []


def test_analysis_excludes_other_users(client, db_url):
    manager = DatabaseManager(db_url)
    other_id = uuid4()
    with manager.get_session() as s:
        s.add(User(id=other_id, email="other3@example.com"))
        s.flush()
    _seed_read(
        manager,
        user_id=other_id,
        title="NotMine",
        author="Ghost",
        genres=["Horror"],
        moods=["creepy"],
        tropes=["haunting"],
    )

    body = client.get("/analysis").json()
    assert body["snapshot"]["total_read"] == 0  # other user's reading is invisible
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest test/integration/test_analysis_api.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'agentic_librarian.api.analysis'`.

- [ ] **Step 3: Create the router module**

Create `src/agentic_librarian/api/analysis.py`:

```python
"""Analysis surface (Lift 2 Stage 2). Four beta views over the user's reading history:
a reading snapshot, genre & mood mix, top tropes (the signature fingerprint), and
authors & narrators. One endpoint returns all four — at beta scale a single round trip
beats four. Aggregation is done in Python over the user's rows (small data); the
embedding-based trope fingerprint and ratings-over-time are future work. Identity comes
from the auth context; rows are filtered by user.id (ADR-048)."""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime

from agentic_librarian.api.auth import AuthenticatedUser, get_current_user
from agentic_librarian.db.models import Edition, ReadingHistory, Work, WorkContributor, WorkTrope
from agentic_librarian.db.session import DatabaseManager
from fastapi import APIRouter, Depends
from sqlalchemy.orm import joinedload, selectinload

router = APIRouter()
db_manager = DatabaseManager()

_TOP_N = 10


def set_db_manager(new_manager: DatabaseManager) -> None:
    """Override the module db_manager (for tests) — the mcp/server.py pattern."""
    global db_manager
    db_manager = new_manager


def _ranked(counter: Counter) -> list[dict]:
    return [{"name": name, "count": count} for name, count in counter.most_common(_TOP_N)]


@router.get("/analysis")
def get_analysis(user: AuthenticatedUser = Depends(get_current_user)):  # noqa: B008
    with db_manager.get_session() as session:
        rows = (
            session.query(ReadingHistory)
            .filter(ReadingHistory.user_id == user.id)  # my reading, not the commons (ADR-048)
            .options(
                joinedload(ReadingHistory.edition).joinedload(Edition.work),
                selectinload(ReadingHistory.edition).selectinload(Edition.narrators),
                selectinload(ReadingHistory.edition)
                .selectinload(Edition.work)
                .selectinload(Work.contributors)
                .joinedload(WorkContributor.author),
                selectinload(ReadingHistory.edition)
                .selectinload(Edition.work)
                .selectinload(Work.tropes)
                .joinedload(WorkTrope.trope),
            )
            .all()
        )

        this_year = datetime.now(UTC).year
        ratings = [r.user_rating for r in rows if r.user_rating is not None]
        formats: Counter = Counter()
        genres: Counter = Counter()
        moods: Counter = Counter()
        tropes: Counter = Counter()
        authors: Counter = Counter()
        narrators: Counter = Counter()
        author_names: set[str] = set()

        for r in rows:
            edition = r.edition
            work = edition.work
            if edition.format:
                formats[edition.format] += 1
            for g in work.genres or []:
                genres[g] += 1
            for m in work.moods or []:
                moods[m] += 1
            for wt in work.tropes:
                tropes[wt.trope.name] += 1
            for c in work.contributors:
                if c.role == "Author":
                    authors[c.author.name] += 1
                    author_names.add(c.author.name)
            for narrator in edition.narrators:
                narrators[narrator.name] += 1

        return {
            "snapshot": {
                "total_read": len(rows),
                "read_this_year": sum(1 for r in rows if r.date_completed and r.date_completed.year == this_year),
                "average_rating": round(sum(ratings) / len(ratings), 2) if ratings else None,
                "distinct_authors": len(author_names),
                "formats": _ranked(formats),
            },
            "genres": _ranked(genres),
            "moods": _ranked(moods),
            "top_tropes": _ranked(tropes),
            "authors": _ranked(authors),
            "narrators": _ranked(narrators),
        }
```

- [ ] **Step 4: Wire the router into the app**

In `src/agentic_librarian/api/main.py`, add the import:

```python
from agentic_librarian.api.analysis import router as analysis_router
```

and register it next to the recommendations router:

```python
app.include_router(analysis_router)
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `pytest test/integration/test_analysis_api.py -v`
Expected: PASS (all three tests).

- [ ] **Step 6: Run the full backend suite + lint**

Run: `pytest -m "not api_dependent and not slow and not live"`
Expected: PASS (the existing 339 + the new tests).
Then reproduce CI lint if unsure (see Conventions). Commit:

```bash
git add src/agentic_librarian/api/analysis.py src/agentic_librarian/api/main.py test/integration/test_analysis_api.py
git commit -m "feat: GET /analysis — reading snapshot, genres/moods, top tropes, authors/narrators"
```

---

## Task 4: Scaffold the `frontend/` Vite app + test toolchain

**Files:**
- Create: the whole `frontend/` tree (see below)

> The implementer should scaffold with the official template, then add deps and configs. Use the **latest** versions `npm` resolves (this plan was written against React 19, Vite 7+, react-router 7, firebase 11+) and record them in `package.json`.

- [ ] **Step 1: Scaffold and install**

```bash
cd C:\dev\agentic_librarian
npm create vite@latest frontend -- --template react-ts
cd frontend
npm install
npm install react-router firebase
npm install -D vitest @testing-library/react @testing-library/jest-dom @testing-library/user-event jsdom
```

- [ ] **Step 2: Configure Vite (proxy + Vitest)**

Overwrite `frontend/vite.config.ts`:

```typescript
/// <reference types="vitest/config" />
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// Dev only: proxy API paths to the running FastAPI backend so the SPA is same-origin
// in development. Production same-origin serving is Stage 4 (multi-stage Docker build).
const API_PATHS = ['/chat', '/conversations', '/history', '/works', '/recommendations', '/analysis', '/health']

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: Object.fromEntries(
      API_PATHS.map((p) => [p, { target: 'http://localhost:8080', changeOrigin: true }]),
    ),
  },
  test: {
    globals: true,
    environment: 'jsdom',
    setupFiles: './src/test/setup.ts',
    css: false,
  },
})
```

- [ ] **Step 3: Test setup, env typing, npm scripts**

Create `frontend/src/test/setup.ts`:

```typescript
import '@testing-library/jest-dom'
```

Overwrite `frontend/src/vite-env.d.ts`:

```typescript
/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_FIREBASE_API_KEY: string
  readonly VITE_FIREBASE_AUTH_DOMAIN: string
  readonly VITE_FIREBASE_PROJECT_ID: string
  readonly VITE_FIREBASE_APP_ID: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
```

In `frontend/package.json`, set the `scripts` block to:

```json
"scripts": {
  "dev": "vite",
  "build": "tsc -b && vite build",
  "lint": "eslint .",
  "preview": "vite preview",
  "test": "vitest run",
  "test:watch": "vitest"
}
```

- [ ] **Step 4: Env example, gitignore, README**

Create `frontend/.env.example`:

```
# Firebase web app config (Project settings → Your apps → Web app → SDK setup).
# Copy to .env.local (gitignored) and fill in real values. These are client-embeddable
# config, but per project convention we never commit them.
VITE_FIREBASE_API_KEY=
VITE_FIREBASE_AUTH_DOMAIN=
VITE_FIREBASE_PROJECT_ID=
VITE_FIREBASE_APP_ID=
```

Append to `frontend/.gitignore` (the scaffold creates this file; ensure these lines are present):

```
.env.local
.env*.local
```

Create `frontend/README.md`:

```markdown
# Librarian frontend (Lift 2 Stage 2)

Vite + React + TypeScript SPA over the conversational Librarian.

## Develop
1. Copy `.env.example` to `.env.local` and fill in the Firebase web config.
2. Run the backend API on `localhost:8080` (uvicorn).
3. `npm install` then `npm run dev` — Vite proxies API paths to the backend.

## Test
- `npm run test` — Vitest + React Testing Library (backend mocked).
- `npm run lint`, `npm run build` (typecheck).

Production serving (FastAPI static + multi-stage Docker) and Playwright e2e are Stage 4.
```

- [ ] **Step 5: Replace the scaffold demo with a minimal shell + smoke test**

Overwrite `frontend/src/App.tsx`:

```tsx
export default function App() {
  return <div>Librarian</div>
}
```

Overwrite `frontend/src/main.tsx`:

```tsx
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import App from './App.tsx'
import './index.css'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
```

Create `frontend/src/App.test.tsx`:

```tsx
import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import App from './App'

describe('App', () => {
  it('renders', () => {
    render(<App />)
    expect(screen.getByText('Librarian')).toBeInTheDocument()
  })
})
```

Replace `frontend/src/index.css` with a minimal reset (the scaffold's default is fine to keep, but trim it to):

```css
* { box-sizing: border-box; }
body { margin: 0; font-family: system-ui, -apple-system, sans-serif; color: #1a1a1a; }
button { font: inherit; cursor: pointer; }
```

Delete the scaffold leftovers that are now unused: `frontend/src/App.css`, `frontend/src/assets/react.svg`.

- [ ] **Step 6: Run the smoke test + build**

```bash
npm run test
npm run build
```
Expected: test passes; build (typecheck) succeeds.

- [ ] **Step 7: Commit**

```bash
cd C:\dev\agentic_librarian
git add frontend
git commit -m "chore(frontend): scaffold Vite + React + TS app with Vitest, proxy, env"
```

---

## Task 5: Firebase auth module + AuthContext

**Files:**
- Create: `frontend/src/auth/firebase.ts`, `frontend/src/auth/AuthContext.tsx`, `frontend/src/auth/AuthContext.test.tsx`

- [ ] **Step 1: Write the failing test**

Create `frontend/src/auth/AuthContext.test.tsx`:

```tsx
import { render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

// Hoisted handles so the firebase mock and the tests share the same callbacks.
const h = vi.hoisted(() => ({
  authCb: null as ((user: unknown) => void) | null,
}))

vi.mock('./firebase', () => ({
  onAuth: (cb: (user: unknown) => void) => {
    h.authCb = cb
    return () => {}
  },
  signInWithGoogle: vi.fn(),
  signOutUser: vi.fn(),
}))

vi.mock('../api/client', () => ({
  probeAccess: vi.fn(),
}))

import { probeAccess } from '../api/client'
import { AuthProvider, useAuth } from './AuthContext'

function Probe() {
  const { status, user } = useAuth()
  return <div>status:{status} user:{user ? user.email : 'none'}</div>
}

function renderProvider() {
  render(
    <AuthProvider>
      <Probe />
    </AuthProvider>,
  )
}

describe('AuthContext', () => {
  beforeEach(() => {
    h.authCb = null
    vi.mocked(probeAccess).mockReset()
  })
  afterEach(() => vi.clearAllMocks())

  it('starts in loading then resolves to signedOut when no user', async () => {
    renderProvider()
    expect(screen.getByText(/status:loading/)).toBeInTheDocument()
    h.authCb!(null)
    await waitFor(() => expect(screen.getByText(/status:signedOut/)).toBeInTheDocument())
  })

  it('probes the backend and becomes ready for an invited user', async () => {
    vi.mocked(probeAccess).mockResolvedValue('ready')
    renderProvider()
    h.authCb!({ email: 'friend@example.com' })
    await waitFor(() => expect(screen.getByText(/status:ready/)).toBeInTheDocument())
    expect(screen.getByText(/user:friend@example.com/)).toBeInTheDocument()
  })

  it('becomes notInvited when the backend rejects with 403', async () => {
    vi.mocked(probeAccess).mockResolvedValue('notInvited')
    renderProvider()
    h.authCb!({ email: 'stranger@example.com' })
    await waitFor(() => expect(screen.getByText(/status:notInvited/)).toBeInTheDocument())
  })
})
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `npm run test -- AuthContext`
Expected: FAIL — cannot resolve `./firebase` / `./AuthContext`.

- [ ] **Step 3: Implement the Firebase module**

Create `frontend/src/auth/firebase.ts`:

```typescript
import { initializeApp } from 'firebase/app'
import {
  GoogleAuthProvider,
  getAuth,
  onAuthStateChanged,
  signInWithPopup,
  signOut,
  type User,
} from 'firebase/auth'

const app = initializeApp({
  apiKey: import.meta.env.VITE_FIREBASE_API_KEY,
  authDomain: import.meta.env.VITE_FIREBASE_AUTH_DOMAIN,
  projectId: import.meta.env.VITE_FIREBASE_PROJECT_ID,
  appId: import.meta.env.VITE_FIREBASE_APP_ID,
})

export const auth = getAuth(app)

export function onAuth(callback: (user: User | null) => void): () => void {
  return onAuthStateChanged(auth, callback)
}

export function signInWithGoogle(): Promise<unknown> {
  return signInWithPopup(auth, new GoogleAuthProvider())
}

export function signOutUser(): Promise<void> {
  return signOut(auth)
}

/** The current user's Firebase ID token, or null when signed out. The SDK auto-refreshes. */
export async function getIdToken(): Promise<string | null> {
  return auth.currentUser ? auth.currentUser.getIdToken() : null
}
```

- [ ] **Step 4: Implement the AuthContext**

Create `frontend/src/auth/AuthContext.tsx`:

```tsx
import { createContext, useContext, useEffect, useState, type ReactNode } from 'react'
import { probeAccess } from '../api/client'
import { onAuth, signInWithGoogle, signOutUser } from './firebase'

export type AuthStatus = 'loading' | 'signedOut' | 'notInvited' | 'ready'

interface AuthUser {
  email: string | null
  displayName?: string | null
}

interface AuthValue {
  status: AuthStatus
  user: AuthUser | null
  signIn: () => Promise<unknown>
  signOut: () => Promise<void>
}

const AuthContext = createContext<AuthValue | null>(null)

export function AuthProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<AuthStatus>('loading')
  const [user, setUser] = useState<AuthUser | null>(null)

  useEffect(() => {
    return onAuth(async (fbUser) => {
      if (!fbUser) {
        setUser(null)
        setStatus('signedOut')
        return
      }
      setUser({ email: fbUser.email, displayName: fbUser.displayName })
      setStatus('loading')
      // Firebase verified the identity; the backend decides invited-or-not (403).
      const access = await probeAccess()
      setStatus(access === 'ready' ? 'ready' : 'notInvited')
    })
  }, [])

  return (
    <AuthContext.Provider value={{ status, user, signIn: signInWithGoogle, signOut: signOutUser }}>
      {children}
    </AuthContext.Provider>
  )
}

export function useAuth(): AuthValue {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth must be used within AuthProvider')
  return ctx
}
```

> Note: `firebase/auth`'s `User` type is structurally compatible with our test's `{ email }` stub. The test mocks `./firebase` wholesale, so no real Firebase init runs.

- [ ] **Step 5: Run the test to verify it passes**

Run: `npm run test -- AuthContext`
Expected: PASS (all three cases). (`probeAccess` lives in `../api/client`, created in Task 6; the test mocks it, so this passes now. The real module import is satisfied by the mock.)

> If the import of `../api/client` fails because the file does not exist yet, create a temporary stub `frontend/src/api/client.ts` containing only `export async function probeAccess(): Promise<'ready' | 'notInvited' | 'error'> { return 'error' }` — Task 6 replaces it fully. (The implementer may reorder Task 6 before Task 5; either order works.)

- [ ] **Step 6: Commit**

```bash
git add frontend/src/auth
git commit -m "feat(frontend): Firebase Google auth + AuthContext gate states"
```

---

## Task 6: API client — authedFetch, typed reads, and the SSE chat stream

**Files:**
- Create/replace: `frontend/src/api/client.ts`, `frontend/src/api/client.test.ts`

- [ ] **Step 1: Write the failing test**

Create `frontend/src/api/client.test.ts`:

```typescript
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../auth/firebase', () => ({
  getIdToken: vi.fn(),
}))

import { getIdToken } from '../auth/firebase'
import { probeAccess, streamChat } from './client'

function sseStream(chunks: string[]): Response {
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      const enc = new TextEncoder()
      for (const c of chunks) controller.enqueue(enc.encode(c))
      controller.close()
    },
  })
  return new Response(body, { status: 200, headers: { 'Content-Type': 'text/event-stream' } })
}

describe('api client', () => {
  beforeEach(() => {
    vi.mocked(getIdToken).mockResolvedValue('tok-123')
    vi.stubGlobal('fetch', vi.fn())
  })
  afterEach(() => vi.unstubAllGlobals())

  it('attaches the bearer token on requests', async () => {
    vi.mocked(fetch).mockResolvedValue(new Response('ok', { status: 200 }))
    await probeAccess()
    const [, init] = vi.mocked(fetch).mock.calls[0]
    expect((init!.headers as Record<string, string>).Authorization).toBe('Bearer tok-123')
  })

  it('probeAccess maps 200 → ready and 403 → notInvited', async () => {
    vi.mocked(fetch).mockResolvedValueOnce(new Response('{}', { status: 200 }))
    expect(await probeAccess()).toBe('ready')
    vi.mocked(fetch).mockResolvedValueOnce(new Response('no', { status: 403 }))
    expect(await probeAccess()).toBe('notInvited')
  })

  it('streamChat parses activity, text, done across chunk boundaries', async () => {
    // The "text" event is split across two network chunks to prove buffering.
    vi.mocked(fetch).mockResolvedValue(
      sseStream([
        'event: activity\ndata: {"kind":"search","detail":"Explorer is searching"}\n\n',
        'event: text\ndata: {"text":"Hello',
        ' there"}\n\nevent: done\ndata: {}\n\n',
      ]),
    )
    const activity: string[] = []
    let text = ''
    let errored = false
    await streamChat('hi', {
      onActivity: (_k, d) => activity.push(d),
      onText: (t) => (text += t),
      onError: () => (errored = true),
    })
    expect(activity).toEqual(['Explorer is searching'])
    expect(text).toBe('Hello there')
    expect(errored).toBe(false)
  })

  it('streamChat reports a single error event', async () => {
    vi.mocked(fetch).mockResolvedValue(sseStream(['event: error\ndata: {"detail":"boom"}\n\n']))
    let detail = ''
    await streamChat('hi', { onActivity: () => {}, onText: () => {}, onError: (d) => (detail = d) })
    expect(detail).toBe('boom')
  })
})
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `npm run test -- client`
Expected: FAIL — `streamChat` / `probeAccess` not exported (or stub returns wrong values).

- [ ] **Step 3: Implement the client**

Replace `frontend/src/api/client.ts` with:

```typescript
import { getIdToken } from '../auth/firebase'

export interface HistoryItem {
  id: string
  title: string
  authors: string[]
  date_completed: string | null
  rating: number | null
  format: string | null
}

export interface Recommendation {
  id: string
  work_id: string
  title: string
  authors: string[]
  justification: string | null
  context: string | null
  suggested_at: string | null
  status: string
}

export interface Ranked {
  name: string
  count: number
}

export interface Analysis {
  snapshot: {
    total_read: number
    read_this_year: number
    average_rating: number | null
    distinct_authors: number
    formats: Ranked[]
  }
  genres: Ranked[]
  moods: Ranked[]
  top_tropes: Ranked[]
  authors: Ranked[]
  narrators: Ranked[]
}

export interface ChatMessage {
  role: 'user' | 'assistant'
  content: string
}

export interface Conversation {
  id: string
  messages: ChatMessage[]
}

/** fetch with the Firebase ID token attached. Throws on a non-2xx response (callers that
 *  care about specific statuses — like probeAccess — use authedFetchRaw instead). */
async function authedFetchRaw(path: string, init: RequestInit = {}): Promise<Response> {
  const token = await getIdToken()
  const headers = new Headers(init.headers)
  if (token) headers.set('Authorization', `Bearer ${token}`)
  return fetch(path, { ...init, headers })
}

async function getJson<T>(path: string): Promise<T> {
  const res = await authedFetchRaw(path)
  if (!res.ok) throw new Error(`${path} → ${res.status}`)
  return res.json() as Promise<T>
}

/** One lightweight authed call to decide invited-or-not. 200 → ready, 403 → notInvited,
 *  anything else → error (treated as not-ready by the caller). */
export async function probeAccess(): Promise<'ready' | 'notInvited' | 'error'> {
  const res = await authedFetchRaw('/conversations/current')
  if (res.ok) return 'ready'
  if (res.status === 403) return 'notInvited'
  return 'error'
}

export function getHistory(): Promise<HistoryItem[]> {
  return getJson<HistoryItem[]>('/history')
}

export function getRecommendations(): Promise<Recommendation[]> {
  return getJson<Recommendation[]>('/recommendations')
}

export async function setRecommendationStatus(id: string, status: 'Dismissed'): Promise<void> {
  const res = await authedFetchRaw(`/recommendations/${id}/status`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ status }),
  })
  if (!res.ok) throw new Error(`dismiss → ${res.status}`)
}

export function getAnalysis(): Promise<Analysis> {
  return getJson<Analysis>('/analysis')
}

export function getCurrentConversation(): Promise<Conversation> {
  return getJson<Conversation>('/conversations/current')
}

export async function newConversation(): Promise<Conversation> {
  const res = await authedFetchRaw('/conversations', { method: 'POST' })
  if (!res.ok) throw new Error(`new conversation → ${res.status}`)
  return res.json() as Promise<Conversation>
}

export interface ChatHandlers {
  onActivity: (kind: string, detail: string) => void
  onText: (text: string) => void
  onError: (detail: string) => void
  signal?: AbortSignal
}

const GENERIC_CHAT_ERROR = 'The Librarian hit a problem. Please try again.'

/** POST a chat turn and consume the SSE stream (EventSource cannot POST or set headers,
 *  so we use fetch + a streaming reader). Parses event frames split on a blank line and
 *  buffers across network chunk boundaries. Events: activity, text, error, done. */
export async function streamChat(message: string, handlers: ChatHandlers): Promise<void> {
  let res: Response
  try {
    res = await authedFetchRaw('/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message }),
      signal: handlers.signal,
    })
  } catch {
    handlers.onError(GENERIC_CHAT_ERROR)
    return
  }
  if (!res.ok || !res.body) {
    handlers.onError(GENERIC_CHAT_ERROR)
    return
  }

  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  for (;;) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    let sep: number
    while ((sep = buffer.indexOf('\n\n')) !== -1) {
      const frame = buffer.slice(0, sep)
      buffer = buffer.slice(sep + 2)
      dispatchFrame(frame, handlers)
    }
  }
}

function dispatchFrame(frame: string, handlers: ChatHandlers): void {
  let event = 'message'
  let data = ''
  for (const line of frame.split('\n')) {
    if (line.startsWith('event:')) event = line.slice(6).trim()
    else if (line.startsWith('data:')) data += line.slice(5).trim()
  }
  let payload: Record<string, string> = {}
  if (data) {
    try {
      payload = JSON.parse(data)
    } catch {
      return // ignore an unparseable frame rather than crash the stream
    }
  }
  if (event === 'activity') handlers.onActivity(payload.kind ?? '', payload.detail ?? '')
  else if (event === 'text') handlers.onText(payload.text ?? '')
  else if (event === 'error') handlers.onError(payload.detail ?? GENERIC_CHAT_ERROR)
  // 'done' → stream end, no callback
}
```

> If a temporary `client.ts` stub was created in Task 5, this step replaces it entirely.

- [ ] **Step 4: Run the test to verify it passes**

Run: `npm run test -- client`
Expected: PASS (all four cases).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/api
git commit -m "feat(frontend): API client — authed fetch, typed reads, SSE chat stream"
```

---

## Task 7: Auth gate + app shell (nav, top bar, sign-in / not-invited screens, router)

**Files:**
- Create: `frontend/src/components/SignIn.tsx`, `NotInvited.tsx`, `TopBar.tsx`, `Nav.tsx`, `Nav.css`, `AppShell.tsx`, `AppShell.css`
- Replace: `frontend/src/App.tsx`, `frontend/src/App.test.tsx`

- [ ] **Step 1: Write the failing test**

Replace `frontend/src/App.test.tsx`:

```tsx
import { render, screen } from '@testing-library/react'
import type { ReactNode } from 'react'
import { describe, expect, it, vi } from 'vitest'
import type { AuthStatus } from './auth/AuthContext'

const state = vi.hoisted(() => ({ status: 'loading' as AuthStatus }))

vi.mock('./auth/AuthContext', () => ({
  AuthProvider: ({ children }: { children: ReactNode }) => <>{children}</>,
  useAuth: () => ({
    status: state.status,
    user: { email: 'friend@example.com', displayName: 'Friend' },
    signIn: vi.fn(),
    signOut: vi.fn(),
  }),
}))

// Views render nothing meaningful here; we only assert the gate + shell.
vi.mock('./views/ChatView', () => ({ default: () => <div>chat-view</div> }))
vi.mock('./views/HistoryView', () => ({ default: () => <div>history-view</div> }))
vi.mock('./views/RecommendationsView', () => ({ default: () => <div>recs-view</div> }))
vi.mock('./views/AnalysisView', () => ({ default: () => <div>analysis-view</div> }))

import App from './App'

describe('App gate', () => {
  it('shows the sign-in screen when signed out', () => {
    state.status = 'signedOut'
    render(<App />)
    expect(screen.getByRole('button', { name: /sign in with google/i })).toBeInTheDocument()
  })

  it('shows the not-invited screen for a verified stranger', () => {
    state.status = 'notInvited'
    render(<App />)
    expect(screen.getByText(/invite/i)).toBeInTheDocument()
  })

  it('renders the shell with the chat view when ready', () => {
    state.status = 'ready'
    render(<App />)
    expect(screen.getByText('chat-view')).toBeInTheDocument()
    expect(screen.getByRole('navigation')).toBeInTheDocument()
  })
})
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `npm run test -- App`
Expected: FAIL — the shell/screens do not exist.

- [ ] **Step 3: Implement the sign-in and not-invited screens**

Create `frontend/src/components/SignIn.tsx`:

```tsx
import { useAuth } from '../auth/AuthContext'

export default function SignIn() {
  const { signIn } = useAuth()
  return (
    <div style={{ display: 'grid', placeItems: 'center', minHeight: '100vh', textAlign: 'center', padding: 24 }}>
      <div>
        <h1>The Librarian</h1>
        <p>Your personal reading companion.</p>
        <button onClick={() => void signIn()} style={{ padding: '10px 20px', fontSize: 16 }}>
          Sign in with Google
        </button>
      </div>
    </div>
  )
}
```

Create `frontend/src/components/NotInvited.tsx`:

```tsx
import { useAuth } from '../auth/AuthContext'

export default function NotInvited() {
  const { user, signOut } = useAuth()
  return (
    <div style={{ display: 'grid', placeItems: 'center', minHeight: '100vh', textAlign: 'center', padding: 24 }}>
      <div>
        <h1>You're not on the list yet</h1>
        <p>
          You're signed in as <strong>{user?.email}</strong>, but this account hasn't been invited.
          Ask the operator for an invite, then sign in again.
        </p>
        <button onClick={() => void signOut()} style={{ padding: '8px 16px' }}>
          Sign out
        </button>
      </div>
    </div>
  )
}
```

- [ ] **Step 4: Implement the top bar and nav**

Create `frontend/src/components/TopBar.tsx`:

```tsx
import { useAuth } from '../auth/AuthContext'

export default function TopBar() {
  const { user, signOut } = useAuth()
  const initial = (user?.displayName || user?.email || '?').charAt(0).toUpperCase()
  return (
    <header className="topbar">
      <span className="topbar-title">The Librarian</span>
      <div className="topbar-right">
        <span className="avatar" title={user?.email ?? ''}>{initial}</span>
        <button onClick={() => void signOut()}>Sign out</button>
      </div>
    </header>
  )
}
```

Create `frontend/src/components/Nav.tsx`:

```tsx
import { NavLink } from 'react-router'
import './Nav.css'

const ITEMS = [
  { to: '/', label: 'Chat', icon: '💬', end: true },
  { to: '/history', label: 'History', icon: '📚', end: false },
  { to: '/recommendations', label: 'Picks', icon: '✨', end: false },
  { to: '/analysis', label: 'Analysis', icon: '📊', end: false },
]

export default function Nav() {
  return (
    <nav className="nav" aria-label="Primary">
      {ITEMS.map((item) => (
        <NavLink key={item.to} to={item.to} end={item.end} className="nav-item">
          <span className="nav-icon" aria-hidden>{item.icon}</span>
          <span className="nav-label">{item.label}</span>
        </NavLink>
      ))}
    </nav>
  )
}
```

Create `frontend/src/components/Nav.css` (responsive: left rail on desktop, bottom bar on mobile):

```css
.nav { display: flex; background: #f3f4f6; }
.nav-item {
  display: flex; flex-direction: column; align-items: center; justify-content: center;
  gap: 2px; padding: 10px; text-decoration: none; color: #4b5563; flex: 1;
}
.nav-item.active { color: #111827; font-weight: 600; background: #e5e7eb; }
.nav-icon { font-size: 20px; }
.nav-label { font-size: 12px; }

/* Mobile-first: fixed bottom bar. */
.nav { position: fixed; bottom: 0; left: 0; right: 0; border-top: 1px solid #d1d5db; }

/* Desktop: left icon rail. */
@media (min-width: 768px) {
  .nav {
    position: fixed; top: 56px; bottom: 0; left: 0; right: auto;
    flex-direction: column; width: 88px; border-top: none; border-right: 1px solid #d1d5db;
  }
  .nav-item { flex: 0; }
}
```

> `react-router`'s `NavLink` adds the `active` class automatically when its route matches.

- [ ] **Step 5: Implement the app shell and wire the router**

Create `frontend/src/components/AppShell.tsx`:

```tsx
import { Outlet } from 'react-router'
import './AppShell.css'
import Nav from './Nav'
import TopBar from './TopBar'

export default function AppShell() {
  return (
    <>
      <TopBar />
      <Nav />
      <main className="content">
        <Outlet />
      </main>
    </>
  )
}
```

Create `frontend/src/components/AppShell.css`:

```css
.topbar {
  position: fixed; top: 0; left: 0; right: 0; height: 56px; z-index: 10;
  display: flex; align-items: center; justify-content: space-between; padding: 0 16px;
  background: #111827; color: #f9fafb;
}
.topbar-title { font-weight: 600; }
.topbar-right { display: flex; align-items: center; gap: 12px; }
.avatar {
  display: grid; place-items: center; width: 32px; height: 32px; border-radius: 50%;
  background: #6366f1; color: #fff; font-weight: 600;
}
.topbar-right button { background: transparent; color: #f9fafb; border: 1px solid #4b5563; border-radius: 6px; padding: 4px 10px; }

/* Mobile-first: content sits below the top bar and above the bottom nav. */
.content { padding: 72px 16px 88px; max-width: 820px; margin: 0 auto; }

@media (min-width: 768px) {
  .content { padding: 72px 24px 24px; margin-left: 88px; }
}
```

Replace `frontend/src/App.tsx`:

```tsx
import { BrowserRouter, Route, Routes } from 'react-router'
import { AuthProvider, useAuth } from './auth/AuthContext'
import AppShell from './components/AppShell'
import NotInvited from './components/NotInvited'
import SignIn from './components/SignIn'
import AnalysisView from './views/AnalysisView'
import ChatView from './views/ChatView'
import HistoryView from './views/HistoryView'
import RecommendationsView from './views/RecommendationsView'

function Gate() {
  const { status } = useAuth()
  if (status === 'loading') return <div style={{ display: 'grid', placeItems: 'center', minHeight: '100vh' }}>Loading…</div>
  if (status === 'signedOut') return <SignIn />
  if (status === 'notInvited') return <NotInvited />
  return (
    <BrowserRouter>
      <Routes>
        <Route element={<AppShell />}>
          <Route index element={<ChatView />} />
          <Route path="history" element={<HistoryView />} />
          <Route path="recommendations" element={<RecommendationsView />} />
          <Route path="analysis" element={<AnalysisView />} />
        </Route>
      </Routes>
    </BrowserRouter>
  )
}

export default function App() {
  return (
    <AuthProvider>
      <Gate />
    </AuthProvider>
  )
}
```

> The four view modules are created in Tasks 8–11. To make this task compile and its test pass now, create minimal placeholder files for any not-yet-built view, e.g. `frontend/src/views/HistoryView.tsx` → `export default function HistoryView() { return <div>history</div> }`. Tasks 8–11 replace them. (The App test mocks all four views, so it passes regardless.)

- [ ] **Step 6: Run the test to verify it passes**

Run: `npm run test -- App`
Expected: PASS (all three gate cases).

- [ ] **Step 7: Commit**

```bash
git add frontend/src
git commit -m "feat(frontend): auth gate + responsive app shell, nav, router"
```

---

## Task 8: Chat view (SSE)

**Files:**
- Create/replace: `frontend/src/views/ChatView.tsx`, `frontend/src/views/ChatView.test.tsx`, `frontend/src/views/ChatView.css`

- [ ] **Step 1: Write the failing test**

Create `frontend/src/views/ChatView.test.tsx`:

```tsx
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { ChatHandlers } from '../api/client'

vi.mock('../api/client', () => ({
  getCurrentConversation: vi.fn(),
  newConversation: vi.fn(),
  streamChat: vi.fn(),
}))

import { getCurrentConversation, newConversation, streamChat } from '../api/client'
import ChatView from './ChatView'

describe('ChatView', () => {
  beforeEach(() => {
    vi.mocked(getCurrentConversation).mockResolvedValue({ id: 'c1', messages: [] })
    vi.mocked(newConversation).mockResolvedValue({ id: 'c2', messages: [] })
  })
  afterEach(() => vi.clearAllMocks())

  it('loads and shows prior messages on resume', async () => {
    vi.mocked(getCurrentConversation).mockResolvedValue({
      id: 'c1',
      messages: [
        { role: 'user', content: 'hi' },
        { role: 'assistant', content: 'hello friend' },
      ],
    })
    render(<ChatView />)
    expect(await screen.findByText('hello friend')).toBeInTheDocument()
  })

  it('sends a message and streams activity then reply', async () => {
    vi.mocked(streamChat).mockImplementation(async (_msg: string, h: ChatHandlers) => {
      h.onActivity('search', 'Explorer is searching')
      h.onText('Try Dune.')
    })
    render(<ChatView />)
    await screen.findByPlaceholderText(/ask the librarian/i)

    await userEvent.type(screen.getByPlaceholderText(/ask the librarian/i), 'recommend a book')
    await userEvent.click(screen.getByRole('button', { name: /send/i }))

    expect(await screen.findByText('recommend a book')).toBeInTheDocument()
    await waitFor(() => expect(screen.getByText('Try Dune.')).toBeInTheDocument())
    expect(vi.mocked(streamChat)).toHaveBeenCalledWith('recommend a book', expect.anything())
  })

  it('starts a new chat, clearing the thread', async () => {
    vi.mocked(getCurrentConversation).mockResolvedValue({
      id: 'c1',
      messages: [{ role: 'assistant', content: 'old thread' }],
    })
    render(<ChatView />)
    expect(await screen.findByText('old thread')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: /new chat/i }))
    await waitFor(() => expect(screen.queryByText('old thread')).not.toBeInTheDocument())
    expect(vi.mocked(newConversation)).toHaveBeenCalled()
  })
})
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `npm run test -- ChatView`
Expected: FAIL — placeholder ChatView lacks the input/streaming behavior.

- [ ] **Step 3: Implement the view**

Replace `frontend/src/views/ChatView.tsx`:

```tsx
import { useEffect, useRef, useState } from 'react'
import { getCurrentConversation, newConversation, streamChat, type ChatMessage } from '../api/client'
import './ChatView.css'

export default function ChatView() {
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [input, setInput] = useState('')
  const [activity, setActivity] = useState<string | null>(null)
  const [sending, setSending] = useState(false)
  const bottomRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    void getCurrentConversation().then((c) => setMessages(c.messages))
  }, [])

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, activity])

  async function send() {
    const text = input.trim()
    if (!text || sending) return
    setInput('')
    setSending(true)
    setActivity(null)
    setMessages((m) => [...m, { role: 'user', content: text }])
    let reply = ''
    await streamChat(text, {
      onActivity: (_kind, detail) => setActivity(detail),
      onText: (chunk) => {
        reply += chunk
      },
      onError: (detail) => {
        reply = reply || detail
      },
    })
    setActivity(null)
    setMessages((m) => [...m, { role: 'assistant', content: reply }])
    setSending(false)
  }

  async function startNew() {
    const c = await newConversation()
    setMessages(c.messages)
    setActivity(null)
  }

  return (
    <div className="chat">
      <div className="chat-toolbar">
        <button onClick={() => void startNew()} disabled={sending}>New chat</button>
      </div>
      <div className="chat-thread">
        {messages.map((m, i) => (
          <div key={i} className={`bubble ${m.role}`}>{m.content}</div>
        ))}
        {activity && <div className="activity-chip">{activity}…</div>}
        <div ref={bottomRef} />
      </div>
      <form
        className="chat-input"
        onSubmit={(e) => {
          e.preventDefault()
          void send()
        }}
      >
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="Ask the Librarian…"
          aria-label="Message"
        />
        <button type="submit" disabled={sending}>Send</button>
      </form>
    </div>
  )
}
```

Create `frontend/src/views/ChatView.css`:

```css
.chat { display: flex; flex-direction: column; gap: 12px; }
.chat-toolbar { display: flex; justify-content: flex-end; }
.chat-toolbar button { border: 1px solid #d1d5db; border-radius: 6px; padding: 6px 12px; background: #fff; }
.chat-thread { display: flex; flex-direction: column; gap: 8px; min-height: 50vh; }
.bubble { padding: 10px 14px; border-radius: 14px; max-width: 80%; white-space: pre-wrap; }
.bubble.user { align-self: flex-end; background: #6366f1; color: #fff; }
.bubble.assistant { align-self: flex-start; background: #f3f4f6; color: #111827; }
.activity-chip { align-self: flex-start; font-size: 13px; color: #6b7280; font-style: italic; }
.chat-input { display: flex; gap: 8px; position: sticky; bottom: 0; background: #fff; padding-top: 8px; }
.chat-input input { flex: 1; padding: 10px 12px; border: 1px solid #d1d5db; border-radius: 8px; }
.chat-input button { padding: 10px 16px; border: none; border-radius: 8px; background: #111827; color: #fff; }
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `npm run test -- ChatView`
Expected: PASS (all three cases).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/views/ChatView.tsx frontend/src/views/ChatView.test.tsx frontend/src/views/ChatView.css
git commit -m "feat(frontend): Chat view with SSE streaming + new/resume"
```

---

## Task 9: History view

**Files:**
- Create/replace: `frontend/src/views/HistoryView.tsx`, `frontend/src/views/HistoryView.test.tsx`, `frontend/src/views/HistoryView.css`

- [ ] **Step 1: Write the failing test**

Create `frontend/src/views/HistoryView.test.tsx`:

```tsx
import { render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

vi.mock('../api/client', () => ({ getHistory: vi.fn() }))

import { getHistory } from '../api/client'
import HistoryView from './HistoryView'

describe('HistoryView', () => {
  afterEach(() => vi.clearAllMocks())

  it('renders the reading log', async () => {
    vi.mocked(getHistory).mockResolvedValue([
      { id: 'h1', title: 'Dune', authors: ['Herbert'], date_completed: '2026-05-01', rating: 5, format: 'audiobook' },
    ])
    render(<HistoryView />)
    expect(await screen.findByText('Dune')).toBeInTheDocument()
    expect(screen.getByText(/Herbert/)).toBeInTheDocument()
  })

  it('shows an empty state when nothing has been read', async () => {
    vi.mocked(getHistory).mockResolvedValue([])
    render(<HistoryView />)
    expect(await screen.findByText(/nothing here yet/i)).toBeInTheDocument()
  })
})
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `npm run test -- HistoryView`
Expected: FAIL — placeholder lacks rendering.

- [ ] **Step 3: Implement the view**

Replace `frontend/src/views/HistoryView.tsx`:

```tsx
import { useEffect, useState } from 'react'
import { getHistory, type HistoryItem } from '../api/client'
import './HistoryView.css'

export default function HistoryView() {
  const [items, setItems] = useState<HistoryItem[] | null>(null)

  useEffect(() => {
    void getHistory().then(setItems)
  }, [])

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
          </li>
        ))}
      </ul>
    </div>
  )
}
```

Create `frontend/src/views/HistoryView.css`:

```css
.history-list { list-style: none; padding: 0; margin: 0; display: flex; flex-direction: column; gap: 8px; }
.history-row {
  display: flex; justify-content: space-between; align-items: center; gap: 12px;
  padding: 12px; border: 1px solid #e5e7eb; border-radius: 10px;
}
.history-main { display: flex; flex-direction: column; }
.history-title { font-weight: 600; }
.history-authors { color: #6b7280; font-size: 14px; }
.history-meta { display: flex; gap: 10px; align-items: center; font-size: 13px; color: #6b7280; }
.history-rating { color: #f59e0b; }
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `npm run test -- HistoryView`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/views/HistoryView.tsx frontend/src/views/HistoryView.test.tsx frontend/src/views/HistoryView.css
git commit -m "feat(frontend): History view"
```

---

## Task 10: Recommendations view

**Files:**
- Create/replace: `frontend/src/views/RecommendationsView.tsx`, `frontend/src/views/RecommendationsView.test.tsx`, `frontend/src/views/RecommendationsView.css`

- [ ] **Step 1: Write the failing test**

Create `frontend/src/views/RecommendationsView.test.tsx`:

```tsx
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../api/client', () => ({
  getRecommendations: vi.fn(),
  setRecommendationStatus: vi.fn(),
}))

import { getRecommendations, setRecommendationStatus } from '../api/client'
import RecommendationsView from './RecommendationsView'

const rec = {
  id: 'r1',
  work_id: 'w1',
  title: 'Project Hail Mary',
  authors: ['Weir'],
  justification: 'You loved The Martian',
  context: null,
  suggested_at: '2026-06-01T00:00:00',
  status: 'Suggested',
}

describe('RecommendationsView', () => {
  beforeEach(() => {
    vi.mocked(getRecommendations).mockResolvedValue([rec])
    vi.mocked(setRecommendationStatus).mockResolvedValue()
  })
  afterEach(() => vi.clearAllMocks())

  it('renders recommendation cards with the justification', async () => {
    render(<RecommendationsView />)
    expect(await screen.findByText('Project Hail Mary')).toBeInTheDocument()
    expect(screen.getByText(/You loved The Martian/)).toBeInTheDocument()
  })

  it('dismisses a recommendation and removes the card', async () => {
    render(<RecommendationsView />)
    await screen.findByText('Project Hail Mary')
    await userEvent.click(screen.getByRole('button', { name: /not for me/i }))
    expect(vi.mocked(setRecommendationStatus)).toHaveBeenCalledWith('r1', 'Dismissed')
    await waitFor(() => expect(screen.queryByText('Project Hail Mary')).not.toBeInTheDocument())
  })

  it('shows "I read this" as disabled (Stage 3)', async () => {
    render(<RecommendationsView />)
    await screen.findByText('Project Hail Mary')
    expect(screen.getByRole('button', { name: /i read this/i })).toBeDisabled()
  })

  it('shows an empty state when there are no picks', async () => {
    vi.mocked(getRecommendations).mockResolvedValue([])
    render(<RecommendationsView />)
    expect(await screen.findByText(/no recommendations/i)).toBeInTheDocument()
  })
})
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `npm run test -- RecommendationsView`
Expected: FAIL — placeholder lacks the cards/actions.

- [ ] **Step 3: Implement the view**

Replace `frontend/src/views/RecommendationsView.tsx`:

```tsx
import { useEffect, useState } from 'react'
import { getRecommendations, setRecommendationStatus, type Recommendation } from '../api/client'
import './RecommendationsView.css'

export default function RecommendationsView() {
  const [recs, setRecs] = useState<Recommendation[] | null>(null)
  const [busy, setBusy] = useState<string | null>(null)

  useEffect(() => {
    void getRecommendations().then(setRecs)
  }, [])

  async function dismiss(id: string) {
    setBusy(id)
    try {
      await setRecommendationStatus(id, 'Dismissed')
      setRecs((current) => (current ? current.filter((r) => r.id !== id) : current))
    } finally {
      setBusy(null)
    }
  }

  if (recs === null) return <p>Loading…</p>
  if (recs.length === 0) return <p>No recommendations right now — ask the Librarian in Chat for ideas.</p>

  return (
    <div>
      <h2>Recommendations</h2>
      <div className="rec-list">
        {recs.map((r) => (
          <article key={r.id} className="rec-card">
            <div className="rec-head">
              <span className="rec-title">{r.title}</span>
              <span className="rec-authors">{r.authors.join(', ')}</span>
            </div>
            {r.justification && <p className="rec-why">{r.justification}</p>}
            <div className="rec-actions">
              {/* "I read this" routes through the add-book form — wired in Stage 3. */}
              <button disabled title="Coming soon — adds the book to your history">✓ I read this</button>
              <button onClick={() => void dismiss(r.id)} disabled={busy === r.id}>Not for me</button>
            </div>
          </article>
        ))}
      </div>
    </div>
  )
}
```

Create `frontend/src/views/RecommendationsView.css`:

```css
.rec-list { display: flex; flex-direction: column; gap: 12px; }
.rec-card { padding: 14px; border: 1px solid #e5e7eb; border-radius: 12px; }
.rec-head { display: flex; flex-direction: column; }
.rec-title { font-weight: 600; }
.rec-authors { color: #6b7280; font-size: 14px; }
.rec-why { color: #374151; font-size: 14px; }
.rec-actions { display: flex; gap: 8px; }
.rec-actions button { border: 1px solid #d1d5db; border-radius: 8px; padding: 6px 12px; background: #fff; }
.rec-actions button:disabled { opacity: 0.5; cursor: not-allowed; }
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `npm run test -- RecommendationsView`
Expected: PASS (all four cases).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/views/RecommendationsView.tsx frontend/src/views/RecommendationsView.test.tsx frontend/src/views/RecommendationsView.css
git commit -m "feat(frontend): Recommendations view — dismiss wired, I-read-this deferred to Stage 3"
```

---

## Task 11: Analysis view

**Files:**
- Create/replace: `frontend/src/views/AnalysisView.tsx`, `frontend/src/views/AnalysisView.test.tsx`, `frontend/src/views/AnalysisView.css`

- [ ] **Step 1: Write the failing test**

Create `frontend/src/views/AnalysisView.test.tsx`:

```tsx
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { Analysis } from '../api/client'

vi.mock('../api/client', () => ({ getAnalysis: vi.fn() }))

import { getAnalysis } from '../api/client'
import AnalysisView from './AnalysisView'

const analysis: Analysis = {
  snapshot: {
    total_read: 12,
    read_this_year: 4,
    average_rating: 4.2,
    distinct_authors: 9,
    formats: [{ name: 'audiobook', count: 10 }, { name: 'ebook', count: 2 }],
  },
  genres: [{ name: 'Sci-Fi', count: 6 }],
  moods: [{ name: 'epic', count: 5 }],
  top_tropes: [{ name: 'chosen one', count: 3 }],
  authors: [{ name: 'Herbert', count: 2 }],
  narrators: [{ name: 'Vance', count: 4 }],
}

describe('AnalysisView', () => {
  beforeEach(() => vi.mocked(getAnalysis).mockResolvedValue(analysis))
  afterEach(() => vi.clearAllMocks())

  it('shows the snapshot numbers by default', async () => {
    render(<AnalysisView />)
    expect(await screen.findByText('12')).toBeInTheDocument() // total read
    expect(screen.getByText('4.2')).toBeInTheDocument() // average rating
  })

  it('switches to the Top tropes tab', async () => {
    render(<AnalysisView />)
    await screen.findByText('12')
    await userEvent.click(screen.getByRole('tab', { name: /top tropes/i }))
    await waitFor(() => expect(screen.getByText('chosen one')).toBeInTheDocument())
  })

  it('switches to the Authors & narrators tab', async () => {
    render(<AnalysisView />)
    await screen.findByText('12')
    await userEvent.click(screen.getByRole('tab', { name: /authors/i }))
    await waitFor(() => expect(screen.getByText('Vance')).toBeInTheDocument())
  })
})
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `npm run test -- AnalysisView`
Expected: FAIL — placeholder lacks tabs/content.

- [ ] **Step 3: Implement the view**

Replace `frontend/src/views/AnalysisView.tsx`:

```tsx
import { useEffect, useState } from 'react'
import { getAnalysis, type Analysis, type Ranked } from '../api/client'
import './AnalysisView.css'

type Tab = 'snapshot' | 'genres' | 'tropes' | 'people'

const TABS: { id: Tab; label: string }[] = [
  { id: 'snapshot', label: 'Snapshot' },
  { id: 'genres', label: 'Genre & mood' },
  { id: 'tropes', label: 'Top tropes' },
  { id: 'people', label: 'Authors & narrators' },
]

function RankedList({ title, items }: { title: string; items: Ranked[] }) {
  return (
    <div className="ranked">
      <h3>{title}</h3>
      {items.length === 0 ? (
        <p className="muted">No data yet.</p>
      ) : (
        <ul>
          {items.map((it) => (
            <li key={it.name}>
              <span>{it.name}</span>
              <span className="count">{it.count}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

export default function AnalysisView() {
  const [data, setData] = useState<Analysis | null>(null)
  const [tab, setTab] = useState<Tab>('snapshot')

  useEffect(() => {
    void getAnalysis().then(setData)
  }, [])

  if (data === null) return <p>Loading…</p>

  return (
    <div>
      <h2>Analysis</h2>
      <div className="tabs" role="tablist">
        {TABS.map((t) => (
          <button
            key={t.id}
            role="tab"
            aria-selected={tab === t.id}
            className={tab === t.id ? 'tab active' : 'tab'}
            onClick={() => setTab(t.id)}
          >
            {t.label}
          </button>
        ))}
      </div>

      {tab === 'snapshot' && (
        <div className="snapshot-grid">
          <div className="stat"><span className="stat-num">{data.snapshot.total_read}</span><span>books read</span></div>
          <div className="stat"><span className="stat-num">{data.snapshot.read_this_year}</span><span>this year</span></div>
          <div className="stat"><span className="stat-num">{data.snapshot.average_rating ?? '—'}</span><span>avg rating</span></div>
          <div className="stat"><span className="stat-num">{data.snapshot.distinct_authors}</span><span>authors</span></div>
          <RankedList title="Formats" items={data.snapshot.formats} />
        </div>
      )}
      {tab === 'genres' && (
        <div className="two-col">
          <RankedList title="Genres" items={data.genres} />
          <RankedList title="Moods" items={data.moods} />
        </div>
      )}
      {tab === 'tropes' && <RankedList title="Top tropes" items={data.top_tropes} />}
      {tab === 'people' && (
        <div className="two-col">
          <RankedList title="Authors" items={data.authors} />
          <RankedList title="Narrators" items={data.narrators} />
        </div>
      )}
    </div>
  )
}
```

Create `frontend/src/views/AnalysisView.css`:

```css
.tabs { display: flex; gap: 6px; flex-wrap: wrap; margin-bottom: 16px; }
.tab { border: 1px solid #d1d5db; background: #fff; border-radius: 999px; padding: 6px 14px; }
.tab.active { background: #111827; color: #fff; border-color: #111827; }
.snapshot-grid { display: grid; grid-template-columns: repeat(2, 1fr); gap: 12px; }
.stat { display: flex; flex-direction: column; padding: 16px; border: 1px solid #e5e7eb; border-radius: 12px; }
.stat-num { font-size: 28px; font-weight: 700; }
.two-col { display: grid; grid-template-columns: 1fr; gap: 16px; }
.ranked ul { list-style: none; padding: 0; margin: 0; }
.ranked li { display: flex; justify-content: space-between; padding: 6px 0; border-bottom: 1px solid #f3f4f6; }
.ranked .count { color: #6b7280; }
.muted { color: #9ca3af; }

@media (min-width: 768px) {
  .snapshot-grid { grid-template-columns: repeat(4, 1fr); }
  .two-col { grid-template-columns: 1fr 1fr; }
}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `npm run test -- AnalysisView`
Expected: PASS (all three cases).

- [ ] **Step 5: Run the whole frontend suite + lint + build**

```bash
npm run test
npm run lint
npm run build
```
Expected: all green (typecheck clean, no eslint errors).

- [ ] **Step 6: Commit**

```bash
git add frontend/src/views/AnalysisView.tsx frontend/src/views/AnalysisView.test.tsx frontend/src/views/AnalysisView.css
git commit -m "feat(frontend): Analysis view — snapshot, genre/mood, top tropes, authors/narrators"
```

---

## Task 12: Frontend CI workflow

**Files:**
- Create: `.github/workflows/frontend.yml`

- [ ] **Step 1: Write the workflow**

Create `.github/workflows/frontend.yml`:

```yaml
name: Frontend CI

on:
  push:
    branches: [ "**" ]
    paths:
      - "frontend/**"
      - ".github/workflows/frontend.yml"
  pull_request:
    branches: [ "**" ]
    paths:
      - "frontend/**"
      - ".github/workflows/frontend.yml"

jobs:
  frontend:
    runs-on: ubuntu-latest
    defaults:
      run:
        working-directory: frontend
    steps:
      - uses: actions/checkout@v4

      - name: Set up Node
        uses: actions/setup-node@v4
        with:
          node-version: '20'
          cache: 'npm'
          cache-dependency-path: frontend/package-lock.json

      - name: Install dependencies
        run: npm ci

      - name: Lint
        run: npm run lint

      - name: Typecheck + build
        run: npm run build

      - name: Unit tests
        run: npm run test
```

- [ ] **Step 2: Verify locally (the workflow mirrors local commands)**

Run from `frontend/`:
```bash
npm ci
npm run lint
npm run build
npm run test
```
Expected: all green. (`npm ci` requires `frontend/package-lock.json` to be committed — it was, in Task 4.)

- [ ] **Step 3: Commit**

```bash
cd C:\dev\agentic_librarian
git add .github/workflows/frontend.yml
git commit -m "ci: frontend lint, typecheck, and Vitest workflow"
```

---

## Self-review (completed during planning)

**Spec coverage (§2 Frontend / §3 Backend / §5 Testing):**
- App shell, top bar, responsive rail/bottom-nav, client-side routing → Task 7. ✅
- Five views: **Chat** (Task 8), **History** (Task 9), **Recommendations** (Task 10), **Analysis** (Task 11). **Add-a-book deferred to Stage 3** (documented in Scope). ✅
- Firebase Google sign-in; token attached on every call; 403 → invite screen, signed-out → sign-in, sign-out button → Tasks 5, 7, 6. ✅
- SSE client via `fetch()` + streaming reader, parsing activity/text/done(/error) → Task 6. ✅
- Responsiveness (mobile-first, rail→bottom-nav) → Nav.css / AppShell.css media queries (Task 7). ✅
- `GET /recommendations` + status POST → Tasks 1–2; `GET /analysis` (four views) → Task 3. ✅
- Backend tests (user-scoping, mutation-minded), frontend Vitest/RTL → throughout. ✅
- **Conscious deviations (documented in Scope):** `/analysis` is one endpoint, not `/analysis/...`; Playwright e2e deferred to Stage 4; production serving + multi-stage Docker + `/history` pagination + IAM gate + pool consolidation are **Stage 4** by the spec's own staging.

**Type consistency:** `Ranked`, `Analysis`, `Recommendation`, `HistoryItem`, `ChatMessage`, `Conversation`, `ChatHandlers` are defined once in `api/client.ts` and imported everywhere. `setRecommendationStatus(id, 'Dismissed')`, `streamChat(message, handlers)`, `probeAccess()` signatures match across client, tests, and views. Auth statuses `'loading' | 'signedOut' | 'notInvited' | 'ready'` are consistent across `AuthContext` and `App` gate.

**Placeholder scan:** no TBD/TODO; every code step shows complete code; every test shows complete assertions.

---

## Execution handoff

Plan complete. Recommended execution: **subagent-driven-development** (fresh subagent per task, two-stage review between tasks), same as Stage 1. Tasks 1–3 (backend) land in the existing pytest harness; Tasks 4–12 build the frontend. After all tasks, a final holistic review, then `finishing-a-development-branch` → push + PR through the Gemini + CI cycle.
