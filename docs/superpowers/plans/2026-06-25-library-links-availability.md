# Library Links + Live Availability (cut #1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show every recommended book *where to get it* — a live Libby availability badge for the user's saved libraries plus free→local→retail links — degrading to links-only if the upstream source fails.

**Architecture:** One isolated backend service (`availability/` package) is the single consumer of OverDrive's unofficial public "Thunder" API. It reads through a DB cache (`availability_cache`, 4h TTL) and is consumed by two surfaces: the Recommendations cards (via `POST /availability`) and the chat agent (via a new `check_availability` MCP tool). Links are built purely from the user's saved libraries (`user_libraries`) and never depend on Thunder, so a Thunder outage degrades to links-only.

**Tech Stack:** Python 3 / FastAPI / SQLAlchemy ORM / Alembic / `requests` (sync HTTP) / pytest (`db_integration` marker) / `uv`; React + TypeScript + Vitest frontend.

**Spec:** `docs/superpowers/specs/2026-06-25-library-links-availability-design.md`

**Conventions to honor (from the repo):**
- Run pytest with `uv run pytest <path> -v`. DB tests use the `@pytest.mark.db_integration` marker (auto-skips locally without Postgres; runs in CI).
- Format Python before committing: `uvx ruff@0.15.16 format .` then `uvx ruff@0.15.16 check .`.
- Frontend tests: `cd frontend && npm test -- --run`. Lint: `cd frontend && npm run lint`.
- Commit messages end with: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`. Commit per task; do **not** push (PR is opened at the end by the operator workflow).
- Module DB pattern: each API/MCP module holds a module-level `db_manager = DatabaseManager()` and a `set_db_manager()` override for tests (see `api/recommendations.py`, `mcp/server.py`).
- User identity is **never** a function/tool parameter — read it via `get_current_user` (FastAPI) or `get_required_user_id()` (MCP), per `core/user_context.py` (ADR-048).

---

## File Structure

**Backend — new package `src/agentic_librarian/availability/`**
- `__init__.py` — exports `build_links`, `availability_for`, `search_libraries`.
- `links.py` — pure URL builders. No I/O.
- `overdrive.py` — the Thunder client. The ONLY module that calls `thunder.api.overdrive.com`. Defines `ThunderError`.
- `service.py` — title matcher + read-through cache orchestration + response assembly.

**Backend — API**
- `api/availability.py` — `POST /availability`.
- `api/libraries.py` — `GET /libraries/search`, `GET /me/libraries`, `PUT /me/libraries`.
- `api/main.py` — register both routers (MODIFY).

**Backend — model + migration + MCP**
- `db/models.py` — add `UserLibrary`, `AvailabilityCache` (MODIFY).
- `alembic/versions/<rev>_library_links_availability.py` — two tables.
- `mcp/server.py` — add `check_availability` tool (MODIFY).

**Frontend**
- `api/client.ts` — new calls + types (MODIFY).
- `components/BookLinks.tsx` (+ `.css`) — link row + badge.
- `views/SettingsView.tsx` (+ `.css`) — library picker.
- `App.tsx`, `components/Nav.tsx`/`.css` — route + nav entry (MODIFY).
- `views/RecommendationsView.tsx` — render `<BookLinks>` in the card (MODIFY).

**Tests:** `test/unit/test_availability_links.py`, `test/unit/test_availability_overdrive.py`, `test/unit/test_availability_service.py`, `test/integration/test_availability_api.py`, `test/integration/test_libraries_api.py`, `test/integration/test_check_availability_tool.py`; co-located `*.test.tsx` for the two components + `client` additions.

---

## Task 1: DB models + migration (`user_libraries`, `availability_cache`)

**Files:**
- Modify: `src/agentic_librarian/db/models.py` (append two classes after `UserCredential`, ~line 268)
- Create: `alembic/versions/a1b2c3d4e5f6_library_links_availability.py`
- Test: `test/integration/test_library_models.py`

- [ ] **Step 1: Write the failing test**

```python
# test/integration/test_library_models.py
import pytest
from datetime import datetime, UTC
from uuid import uuid4
from agentic_librarian.db.models import UserLibrary, AvailabilityCache, User

pytestmark = pytest.mark.db_integration


def test_user_library_roundtrip(db_session):
    user = User(id=uuid4(), email="t@example.com")
    db_session.add(user)
    db_session.flush()
    db_session.add(UserLibrary(
        user_id=user.id, provider="libby", library_slug="kcls",
        display_name="King County Library System", sort_order=0,
    ))
    db_session.flush()
    row = db_session.query(UserLibrary).filter_by(user_id=user.id).one()
    assert row.library_slug == "kcls"
    assert row.sort_order == 0


def test_availability_cache_roundtrip(db_session):
    db_session.add(AvailabilityCache(
        provider="libby", library_slug="kcls",
        norm_title="project hail mary", norm_author="andy weir",
        payload={"formats": [{"format": "Audiobook", "available": True}]},
        fetched_at=datetime.now(UTC),
    ))
    db_session.flush()
    row = db_session.query(AvailabilityCache).filter_by(library_slug="kcls").one()
    assert row.payload["formats"][0]["available"] is True
```

> **Note on `db_session`:** reuse the existing test fixture used by other `db_integration` tests (search `test/` for `def db_session` / `conftest.py`). If none exists in scope, mirror the session-construction used in `test/integration/test_contributor_dedup.py`.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest test/integration/test_library_models.py -v`
Expected: FAIL — `ImportError: cannot import name 'UserLibrary'`.

- [ ] **Step 3: Add the models**

Append to `src/agentic_librarian/db/models.py` (after `UserCredential`). Note `JSONB` needs an import — add `JSONB` to the existing `from sqlalchemy.dialects.postgresql import UUID as PG_UUID` line:

```python
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
```

```python
class UserLibrary(Base):
    """A library system the user holds a card at (public OverDrive slug — NOT a secret,
    so this is plain prefs, not the UserCredential/keyring). Ordered by sort_order = the
    user's priority. provider is 'libby' in cut #1 (Hoopla has no availability signal)."""

    __tablename__ = "user_libraries"

    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), primary_key=True)
    provider: Mapped[str] = mapped_column(String, primary_key=True, default="libby")
    library_slug: Mapped[str] = mapped_column(String, primary_key=True)
    display_name: Mapped[str] = mapped_column(String, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC), nullable=False)


class AvailabilityCache(Base):
    """Read-through cache for a (library, title, author) availability lookup. Keyed on
    NORMALIZED title+author so the recs consumer (has work_id) and the chat tool (has raw
    title/author) share rows. Freshness = now - fetched_at < TTL (default 4h)."""

    __tablename__ = "availability_cache"

    provider: Mapped[str] = mapped_column(String, primary_key=True)
    library_slug: Mapped[str] = mapped_column(String, primary_key=True)
    norm_title: Mapped[str] = mapped_column(String, primary_key=True)
    norm_author: Mapped[str] = mapped_column(String, primary_key=True)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
```

- [ ] **Step 4: Write the Alembic migration**

Find the current head: `uv run alembic heads` (expected `7b7b4d6ae6f6`, the bulk-import revision). Create `alembic/versions/a1b2c3d4e5f6_library_links_availability.py`:

```python
"""library links + availability

Revision ID: a1b2c3d4e5f6
Revises: 7b7b4d6ae6f6
Create Date: 2026-06-25 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "a1b2c3d4e5f6"
down_revision: str | None = "7b7b4d6ae6f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "user_libraries",
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("provider", sa.String(), nullable=False),
        sa.Column("library_slug", sa.String(), nullable=False),
        sa.Column("display_name", sa.String(), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("user_id", "provider", "library_slug"),
    )
    op.create_table(
        "availability_cache",
        sa.Column("provider", sa.String(), nullable=False),
        sa.Column("library_slug", sa.String(), nullable=False),
        sa.Column("norm_title", sa.String(), nullable=False),
        sa.Column("norm_author", sa.String(), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("fetched_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("provider", "library_slug", "norm_title", "norm_author"),
    )


def downgrade() -> None:
    op.drop_table("availability_cache")
    op.drop_table("user_libraries")
```

If `alembic heads` shows a different revision than `7b7b4d6ae6f6`, set `down_revision` to that value instead.

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest test/integration/test_library_models.py -v`
Expected: PASS (or SKIP locally if no Postgres — then rely on CI). Sanity-check the migration applies: `uv run alembic upgrade head` against a test DB if available.

- [ ] **Step 6: Format + commit**

```bash
uvx ruff@0.15.16 format . && uvx ruff@0.15.16 check .
git add src/agentic_librarian/db/models.py alembic/versions/a1b2c3d4e5f6_library_links_availability.py test/integration/test_library_models.py
git commit -m "$(printf 'feat(availability): add user_libraries + availability_cache tables\n\nCo-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>')"
```

---

## Task 2: Pure link builders (`availability/links.py`)

**Files:**
- Create: `src/agentic_librarian/availability/__init__.py`, `src/agentic_librarian/availability/links.py`
- Test: `test/unit/test_availability_links.py`

A "link" is `{"kind": str, "label": str, "url": str}`. Order: one Libby link per saved library (in sort order), then Hoopla, Bookshop.org, Amazon.

- [ ] **Step 1: Write the failing test**

```python
# test/unit/test_availability_links.py
from agentic_librarian.availability.links import build_links


def test_links_order_and_libby_per_library():
    libs = [
        {"slug": "kcls", "name": "King County LS"},
        {"slug": "spl", "name": "Seattle PL"},
    ]
    links = build_links("Project Hail Mary", "Andy Weir", libraries=libs)
    kinds = [link["kind"] for link in links]
    assert kinds == ["libby", "libby", "hoopla", "bookshop", "amazon"]
    assert links[0]["label"] == "King County LS on Libby"
    assert "kcls" in links[0]["url"]
    assert "spl" in links[1]["url"]


def test_links_with_no_libraries_still_has_retail_and_hoopla():
    links = build_links("Dune", "Frank Herbert", libraries=[])
    assert [link["kind"] for link in links] == ["hoopla", "bookshop", "amazon"]


def test_links_url_encode_special_characters():
    links = build_links("Cat & Mouse", "A. B", libraries=[])
    amazon = next(link for link in links if link["kind"] == "amazon")
    assert " " not in amazon["url"]
    assert "%26" in amazon["url"] or "+" in amazon["url"]  # '&' encoded
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest test/unit/test_availability_links.py -v`
Expected: FAIL — `ModuleNotFoundError: ... availability.links`.

- [ ] **Step 3: Implement the builders**

`src/agentic_librarian/availability/__init__.py`:

```python
from agentic_librarian.availability.links import build_links

__all__ = ["build_links"]
```

`src/agentic_librarian/availability/links.py`:

```python
"""Pure URL builders for 'where to get this book'. No I/O — never calls a network. The
Libby link is per saved library; the rest are single catalog/retail search links. Order
is free → local → retail: Libby, Hoopla, Bookshop.org, Amazon."""

from __future__ import annotations

from urllib.parse import quote_plus


def _retail_query(title: str, author: str) -> str:
    return quote_plus(f"{title} {author}".strip())


def _libby_url(slug: str, title: str) -> str:
    # Libby web-app search scoped to one library. NOTE: confirm the exact path segments
    # against a live libbyapp.com session in review; this is the documented format.
    return f"https://libbyapp.com/search/{slug}/search/query-{quote_plus(title)}/page-1"


def build_links(title: str, author: str, *, libraries: list[dict]) -> list[dict]:
    """libraries: [{"slug","name"}] in the user's priority order. Returns ordered link dicts
    {kind,label,url}."""
    links: list[dict] = []
    for lib in libraries:
        links.append({
            "kind": "libby",
            "label": f"{lib['name']} on Libby",
            "url": _libby_url(lib["slug"], title),
        })
    links.append({
        "kind": "hoopla",
        "label": "Search Hoopla",
        "url": f"https://www.hoopladigital.com/search?q={quote_plus(title)}",
    })
    links.append({
        "kind": "bookshop",
        "label": "Bookshop.org",
        "url": f"https://bookshop.org/search?keywords={_retail_query(title, author)}",
    })
    links.append({
        "kind": "amazon",
        "label": "Amazon",
        "url": f"https://www.amazon.com/s?k={_retail_query(title, author)}",
    })
    return links
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest test/unit/test_availability_links.py -v`
Expected: PASS.

- [ ] **Step 5: Format + commit**

```bash
uvx ruff@0.15.16 format . && uvx ruff@0.15.16 check .
git add src/agentic_librarian/availability/__init__.py src/agentic_librarian/availability/links.py test/unit/test_availability_links.py
git commit -m "$(printf 'feat(availability): pure link builders (Libby/Hoopla/Bookshop/Amazon)\n\nCo-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>')"
```

---

## Task 3: OverDrive Thunder client (`availability/overdrive.py`)

**Files:**
- Create: `src/agentic_librarian/availability/overdrive.py`
- Test: `test/unit/test_availability_overdrive.py`

The only module that calls `thunder.api.overdrive.com`. Two functions: `search_libraries(query)` (for the picker) and `fetch_media(slug, title)` (raw items for the matcher). All HTTP goes through a single `_http_get_json(url)` seam tests monkeypatch. Failures raise `ThunderError`.

- [ ] **Step 1: Write the failing test**

```python
# test/unit/test_availability_overdrive.py
import pytest
from agentic_librarian.availability import overdrive
from agentic_librarian.availability.overdrive import ThunderError


def test_search_libraries_maps_items(monkeypatch):
    monkeypatch.setattr(overdrive, "_http_get_json", lambda url: {
        "items": [{"preferredKey": "kcls", "name": "King County Library System"},
                  {"preferredKey": "spl", "name": "Seattle Public Library"}],
    })
    out = overdrive.search_libraries("seattle")
    assert out == [
        {"slug": "kcls", "name": "King County Library System"},
        {"slug": "spl", "name": "Seattle Public Library"},
    ]


def test_fetch_media_returns_items(monkeypatch):
    monkeypatch.setattr(overdrive, "_http_get_json", lambda url: {"items": [{"title": "Dune"}]})
    assert overdrive.fetch_media("kcls", "Dune") == [{"title": "Dune"}]


def test_http_failure_raises_thundererror(monkeypatch):
    def boom(url):
        raise RuntimeError("network down")
    monkeypatch.setattr(overdrive, "_http_get_json", boom)
    with pytest.raises(ThunderError):
        overdrive.search_libraries("x")
    with pytest.raises(ThunderError):
        overdrive.fetch_media("kcls", "Dune")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest test/unit/test_availability_overdrive.py -v`
Expected: FAIL — module/attr not found.

- [ ] **Step 3: Implement the client**

`src/agentic_librarian/availability/overdrive.py`:

```python
"""OverDrive 'Thunder' client — the SINGLE module touching OverDrive's unofficial public
API (the same endpoints libbyapp.com's frontend calls; no auth, x-client-id=dewey). It is
undocumented and NOT covered by OverDrive's developer agreement: isolated here so it can be
swapped for the official partner API later, and so every caller degrades on ThunderError."""

from __future__ import annotations

from urllib.parse import quote_plus

import requests

_THUNDER = "https://thunder.api.overdrive.com"
_CLIENT = "dewey"
_TIMEOUT = 8  # seconds — one slow library must not hang a request


class ThunderError(Exception):
    """Any failure talking to Thunder. Callers catch this and degrade to links-only."""


def _http_get_json(url: str) -> dict:
    """The one network seam (tests monkeypatch this)."""
    resp = requests.get(url, timeout=_TIMEOUT, headers={"Accept": "application/json"})
    resp.raise_for_status()
    return resp.json()


def search_libraries(query: str) -> list[dict]:
    """Public OverDrive directory search — powers the picker. Returns [{slug, name}]."""
    url = f"{_THUNDER}/v2/libraries?query={quote_plus(query)}&x-client-id={_CLIENT}"
    try:
        data = _http_get_json(url)
    except Exception as exc:  # noqa: BLE001 - normalize every failure to ThunderError
        raise ThunderError(str(exc)) from exc
    out = []
    for item in data.get("items", []):
        slug = item.get("preferredKey") or item.get("advantageKey")
        name = item.get("name")
        if slug and name:
            out.append({"slug": slug, "name": name})
    return out


def fetch_media(slug: str, title: str) -> list[dict]:
    """Per-library catalog search (ebook+audiobook) with availability inline. Returns the
    raw `items` list; matching/shaping is the service's job."""
    url = (
        f"{_THUNDER}/v2/libraries/{quote_plus(slug)}/media"
        f"?query={quote_plus(title)}&format=ebook-overdrive,audiobook-overdrive"
        f"&perPage=24&x-client-id={_CLIENT}"
    )
    try:
        data = _http_get_json(url)
    except Exception as exc:  # noqa: BLE001
        raise ThunderError(str(exc)) from exc
    return data.get("items", [])
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest test/unit/test_availability_overdrive.py -v`
Expected: PASS.

- [ ] **Step 5: Format + commit**

```bash
uvx ruff@0.15.16 format . && uvx ruff@0.15.16 check .
git add src/agentic_librarian/availability/overdrive.py test/unit/test_availability_overdrive.py
git commit -m "$(printf 'feat(availability): isolated OverDrive Thunder client\n\nCo-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>')"
```

---

## Task 4: Title matcher + availability service (`availability/service.py`)

**Files:**
- Create: `src/agentic_librarian/availability/service.py`
- Test: `test/unit/test_availability_service.py` (matcher + shaping, pure), `test/integration/test_availability_service_cache.py` (read-through, DB)

The matcher and per-format shaping are pure (unit-testable with fixtures). The cache read-through needs a DB session (integration test).

- [ ] **Step 1: Write the failing pure-logic test**

```python
# test/unit/test_availability_service.py
from agentic_librarian.availability.service import _normalize, _shape_formats

ITEMS = [
    {"title": "Project Hail Mary", "type": {"id": "ebook", "name": "eBook"},
     "isAvailable": False, "ownedCopies": 447, "availableCopies": 0,
     "holdsRatio": 6, "estimatedWaitDays": 83, "firstCreatorName": "Andy Weir"},
    {"title": "Project Hail Mary", "type": {"id": "audiobook", "name": "Audiobook"},
     "isAvailable": True, "ownedCopies": 20, "availableCopies": 2,
     "holdsRatio": 0, "estimatedWaitDays": 0, "firstCreatorName": "Andy Weir"},
    {"title": "Unrelated Book", "type": {"id": "ebook", "name": "eBook"},
     "isAvailable": True, "firstCreatorName": "Someone Else"},
]


def test_normalize():
    assert _normalize("  The   Martian ") == "the martian"


def test_shape_formats_matches_title_and_splits_by_format():
    formats = _shape_formats(ITEMS, "Project Hail Mary", "Andy Weir")
    by = {f["format"]: f for f in formats}
    assert set(by) == {"eBook", "Audiobook"}
    assert by["Audiobook"]["available"] is True
    assert by["Audiobook"]["copies_available"] == 2
    assert by["eBook"]["available"] is False
    assert by["eBook"]["wait_days"] == 83


def test_shape_formats_no_title_match_returns_empty():
    assert _shape_formats(ITEMS, "Some Other Title", "Nobody") == []


def test_shape_formats_author_mismatch_still_ok_when_title_unique():
    # Title-equality is the bar; author is a soft confirm. Wrong author but exact title → matched.
    formats = _shape_formats(ITEMS, "Project Hail Mary", "Wrong Author")
    assert {f["format"] for f in formats} == {"eBook", "Audiobook"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest test/unit/test_availability_service.py -v`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement matcher + shaping + cache read-through**

`src/agentic_librarian/availability/service.py`:

```python
"""Availability orchestration: normalize → read-through the availability_cache → on miss
fetch from Thunder → shape per-format → write through. Pure helpers (_normalize, _shape_formats,
_best_match) are unit-tested; availability_for() needs a Session (integration-tested).

Title-matching policy: a Thunder item matches only on normalized-title equality. Author is a
SOFT confirm (preferred when several items share a title) — we under-claim rather than show a
wrong 'available now'. Each format (ebook/audiobook) is matched independently."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from agentic_librarian.availability import overdrive
from agentic_librarian.availability.overdrive import ThunderError
from agentic_librarian.db.models import AvailabilityCache

_PROVIDER = "libby"
_FORMATS = (("ebook", "eBook"), ("audiobook", "Audiobook"))


def _ttl() -> timedelta:
    return timedelta(seconds=int(os.environ.get("AVAILABILITY_TTL_SECONDS", "14400")))  # 4h


def _normalize(s: str) -> str:
    return " ".join((s or "").strip().lower().split())


def _item_author(item: dict) -> str:
    if item.get("firstCreatorName"):
        return item["firstCreatorName"]
    creators = item.get("creators") or []
    return creators[0].get("name", "") if creators else ""


def _best_match(items: list[dict], title: str, author: str) -> dict | None:
    nt = _normalize(title)
    cands = [it for it in items if _normalize(it.get("title", "")) == nt]
    if not cands:
        return None
    if author:
        na = set(_normalize(author).split())
        for it in cands:
            if na & set(_normalize(_item_author(it)).split()):
                return it
    return cands[0]


def _shape_formats(items: list[dict], title: str, author: str) -> list[dict]:
    """One entry per format that has a confident title match."""
    out: list[dict] = []
    for fmt_id, fmt_label in _FORMATS:
        fmt_items = [it for it in items if (it.get("type") or {}).get("id") == fmt_id]
        match = _best_match(fmt_items, title, author)
        if match is None:
            continue
        out.append({
            "format": fmt_label,
            "available": bool(match.get("isAvailable")),
            "copies_owned": match.get("ownedCopies"),
            "copies_available": match.get("availableCopies"),
            "holds_ratio": match.get("holdsRatio"),
            "wait_days": match.get("estimatedWaitDays"),
        })
    return out


def availability_for(session: Session, library: dict, title: str, author: str) -> list[dict] | None:
    """Read-through cache for ONE (library, title, author). Returns the per-format list, or
    None if Thunder failed (caller degrades to links-only). A fresh cache row → zero upstream
    calls. Empty list (matched nothing) is a real, cacheable result."""
    nt, na = _normalize(title), _normalize(author)
    slug = library["slug"]
    row = session.get(AvailabilityCache, (_PROVIDER, slug, nt, na))
    if row is not None and (datetime.now(UTC) - row.fetched_at.replace(tzinfo=UTC)) < _ttl():
        return row.payload.get("formats", [])

    try:
        items = overdrive.fetch_media(slug, title)
    except ThunderError:
        return None  # degrade: no badge, links unaffected

    formats = _shape_formats(items, title, author)
    payload = {"formats": formats}
    if row is None:
        session.add(AvailabilityCache(
            provider=_PROVIDER, library_slug=slug, norm_title=nt, norm_author=na,
            payload=payload, fetched_at=datetime.now(UTC),
        ))
    else:
        row.payload = payload
        row.fetched_at = datetime.now(UTC)
    session.flush()
    return formats
```

- [ ] **Step 4: Run the pure test to verify it passes**

Run: `uv run pytest test/unit/test_availability_service.py -v`
Expected: PASS.

- [ ] **Step 5: Write the read-through cache integration test**

```python
# test/integration/test_availability_service_cache.py
import pytest
from agentic_librarian.availability import overdrive, service

pytestmark = pytest.mark.db_integration

_ITEMS = [{"title": "Dune", "type": {"id": "ebook", "name": "eBook"},
           "isAvailable": True, "firstCreatorName": "Frank Herbert"}]


def test_cache_miss_then_hit(db_session, monkeypatch):
    calls = {"n": 0}

    def fake_fetch(slug, title):
        calls["n"] += 1
        return _ITEMS

    monkeypatch.setattr(overdrive, "fetch_media", fake_fetch)
    lib = {"slug": "kcls", "name": "KCLS"}

    first = service.availability_for(db_session, lib, "Dune", "Frank Herbert")
    assert first[0]["format"] == "eBook"
    second = service.availability_for(db_session, lib, "Dune", "Frank Herbert")
    assert second[0]["available"] is True
    assert calls["n"] == 1  # second call served from cache


def test_thunder_error_degrades_to_none(db_session, monkeypatch):
    def boom(slug, title):
        raise overdrive.ThunderError("down")

    monkeypatch.setattr(overdrive, "fetch_media", boom)
    out = service.availability_for(db_session, {"slug": "kcls", "name": "KCLS"}, "X", "Y")
    assert out is None
```

- [ ] **Step 6: Run it (PASS or SKIP locally)**

Run: `uv run pytest test/integration/test_availability_service_cache.py -v`
Expected: PASS (runs in CI; SKIP locally without Postgres).

- [ ] **Step 7: Format + commit**

```bash
uvx ruff@0.15.16 format . && uvx ruff@0.15.16 check .
git add src/agentic_librarian/availability/service.py test/unit/test_availability_service.py test/integration/test_availability_service_cache.py
git commit -m "$(printf 'feat(availability): matcher + read-through cache service\n\nCo-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>')"
```

---

## Task 5: API endpoints (`api/availability.py`, `api/libraries.py`)

**Files:**
- Create: `src/agentic_librarian/api/availability.py`, `src/agentic_librarian/api/libraries.py`
- Modify: `src/agentic_librarian/api/main.py` (register routers)
- Test: `test/integration/test_availability_api.py`, `test/integration/test_libraries_api.py`

Follows `api/recommendations.py` exactly: `APIRouter`, module `db_manager`, `set_db_manager`, `get_current_user` dependency.

- [ ] **Step 1: Write the failing API tests**

```python
# test/integration/test_availability_api.py
import pytest
from uuid import uuid4
from fastapi.testclient import TestClient

pytestmark = pytest.mark.db_integration


def test_availability_always_200_with_links(client_as_user, seeded_work, monkeypatch):
    # seeded_work: a Work with title/author in the DB; client_as_user: TestClient authed as a user.
    from agentic_librarian.availability import service
    monkeypatch.setattr(service, "availability_for", lambda *a, **k: [])  # no badge, links only
    resp = client_as_user.post("/availability", json={"work_ids": [str(seeded_work.id)]})
    assert resp.status_code == 200
    body = resp.json()[str(seeded_work.id)]
    assert any(link["kind"] == "amazon" for link in body["links"])
    assert body["libby"] == []  # libraries saved but no match → empty


def test_availability_thunder_down_still_200(client_as_user, seeded_work, monkeypatch):
    from agentic_librarian.availability import service
    monkeypatch.setattr(service, "availability_for", lambda *a, **k: None)  # degrade
    resp = client_as_user.post("/availability", json={"work_ids": [str(seeded_work.id)]})
    assert resp.status_code == 200
```

```python
# test/integration/test_libraries_api.py
import pytest
pytestmark = pytest.mark.db_integration


def test_library_search_proxies_thunder(client_as_user, monkeypatch):
    from agentic_librarian.availability import overdrive
    monkeypatch.setattr(overdrive, "search_libraries",
                        lambda q: [{"slug": "kcls", "name": "KCLS"}])
    resp = client_as_user.get("/libraries/search?q=king")
    assert resp.status_code == 200
    assert resp.json() == [{"slug": "kcls", "name": "KCLS"}]


def test_my_libraries_put_then_get_roundtrip(client_as_user):
    payload = {"libraries": [
        {"slug": "kcls", "name": "KCLS"},
        {"slug": "spl", "name": "Seattle PL"},
    ]}
    put = client_as_user.put("/me/libraries", json=payload)
    assert put.status_code == 200
    got = client_as_user.get("/me/libraries").json()
    assert [lib["slug"] for lib in got["libraries"]] == ["kcls", "spl"]  # order preserved
```

> **Fixtures `client_as_user` / `seeded_work`:** reuse the existing authed-`TestClient` fixture used by `test/integration/test_recommendations_api.py` (it monkeypatches `_verify_token` and calls `set_db_manager` on each router). If a shared one exists in `conftest.py`, use it; otherwise mirror that test's setup and register the two new routers' `set_db_manager` the same way.

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest test/integration/test_availability_api.py test/integration/test_libraries_api.py -v`
Expected: FAIL — 404 (routes not registered).

- [ ] **Step 3: Implement `api/libraries.py`**

```python
"""Library-picker endpoints: search the public OverDrive directory, and read/replace the
user's saved libraries (ordered). No secrets — slugs are public (see UserLibrary)."""

from __future__ import annotations

from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import BaseModel

from agentic_librarian.api.auth import AuthenticatedUser, get_current_user
from agentic_librarian.availability import overdrive
from agentic_librarian.availability.overdrive import ThunderError
from agentic_librarian.db.models import UserLibrary
from agentic_librarian.db.session import DatabaseManager

router = APIRouter()
db_manager = DatabaseManager()


def set_db_manager(new_manager: DatabaseManager) -> None:
    global db_manager
    db_manager = new_manager


class LibraryIn(BaseModel):
    slug: str
    name: str


class LibrariesIn(BaseModel):
    libraries: list[LibraryIn]


@router.get("/libraries/search")
def search_libraries(q: str, user: AuthenticatedUser = Depends(get_current_user)):  # noqa: B008
    if not q.strip():
        return []
    try:
        return overdrive.search_libraries(q)
    except ThunderError:
        raise HTTPException(status_code=503, detail="library directory unavailable")


@router.get("/me/libraries")
def get_my_libraries(user: AuthenticatedUser = Depends(get_current_user)):  # noqa: B008
    with db_manager.get_session() as session:
        rows = (
            session.query(UserLibrary)
            .filter(UserLibrary.user_id == user.id, UserLibrary.provider == "libby")
            .order_by(UserLibrary.sort_order)
            .all()
        )
        return {"libraries": [{"slug": r.library_slug, "name": r.display_name} for r in rows]}


@router.put("/me/libraries")
def put_my_libraries(
    body: LibrariesIn = Body(...),  # noqa: B008
    user: AuthenticatedUser = Depends(get_current_user),  # noqa: B008
):
    with db_manager.get_session() as session:
        session.query(UserLibrary).filter(
            UserLibrary.user_id == user.id, UserLibrary.provider == "libby"
        ).delete()
        for i, lib in enumerate(body.libraries):
            session.add(UserLibrary(
                user_id=user.id, provider="libby", library_slug=lib.slug,
                display_name=lib.name, sort_order=i,
            ))
        session.flush()
    return {"libraries": [{"slug": lib.slug, "name": lib.name} for lib in body.libraries]}
```

- [ ] **Step 4: Implement `api/availability.py`**

```python
"""POST /availability — batch 'where to get it' + live Libby badge for the visible recs.
ALWAYS 200: links are built purely from the user's saved libraries (never depend on Thunder);
the per-library badge is best-effort (null/[] when Thunder is down or nothing matched)."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Body, Depends
from sqlalchemy.orm import joinedload

from agentic_librarian.api.auth import AuthenticatedUser, get_current_user
from agentic_librarian.availability import service
from agentic_librarian.availability.links import build_links
from agentic_librarian.db.models import UserLibrary, Work, WorkContributor
from agentic_librarian.db.session import DatabaseManager

router = APIRouter()
db_manager = DatabaseManager()

_MAX_WORKS = 50  # a recs page is small; cap to bound upstream work


def set_db_manager(new_manager: DatabaseManager) -> None:
    global db_manager
    db_manager = new_manager


def _authors(work: Work) -> list[str]:
    return [c.author.name for c in work.contributors if c.role == "Author"]


@router.post("/availability")
def get_availability(
    work_ids: list[str] = Body(..., embed=True),  # noqa: B008
    user: AuthenticatedUser = Depends(get_current_user),  # noqa: B008
):
    parsed: list[UUID] = []
    for wid in work_ids[:_MAX_WORKS]:
        try:
            parsed.append(UUID(str(wid)))
        except (ValueError, TypeError):
            continue

    result: dict[str, dict] = {}
    if not parsed:
        return result

    with db_manager.get_session() as session:
        libs = [
            {"slug": r.library_slug, "name": r.display_name}
            for r in session.query(UserLibrary)
            .filter(UserLibrary.user_id == user.id, UserLibrary.provider == "libby")
            .order_by(UserLibrary.sort_order)
            .all()
        ]
        works = (
            session.query(Work)
            .options(joinedload(Work.contributors).joinedload(WorkContributor.author))
            .filter(Work.id.in_(parsed))
            .all()
        )
        for work in works:
            authors = _authors(work)
            author = authors[0] if authors else ""
            libby: list[dict] = []
            for lib in libs:
                formats = service.availability_for(session, lib, work.title, author)
                if formats:  # non-empty match → show a badge for this library
                    libby.append({"library": lib["name"], "slug": lib["slug"], "formats": formats})
            result[str(work.id)] = {
                "links": build_links(work.title, author, libraries=libs),
                "libby": libby,
            }
    return result
```

- [ ] **Step 5: Register the routers in `api/main.py`**

Add imports near the other router imports (lines 17-20) and `include_router` calls (after line 72):

```python
from agentic_librarian.api.availability import router as availability_router
from agentic_librarian.api.libraries import router as libraries_router
```

```python
app.include_router(availability_router)
app.include_router(libraries_router)
```

If `main.py` wires `set_db_manager` for routers on startup (it calls `imports_api.set_db_manager(shared)` ~line 59), add the same for `availability` and `libraries` so they share the one `DatabaseManager`:

```python
from agentic_librarian.api import availability as availability_api
from agentic_librarian.api import libraries as libraries_api
# ... in the same startup block as imports_api.set_db_manager(shared):
availability_api.set_db_manager(shared)
libraries_api.set_db_manager(shared)
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest test/integration/test_availability_api.py test/integration/test_libraries_api.py -v`
Expected: PASS (CI; SKIP locally without Postgres).

- [ ] **Step 7: Format + commit**

```bash
uvx ruff@0.15.16 format . && uvx ruff@0.15.16 check .
git add src/agentic_librarian/api/availability.py src/agentic_librarian/api/libraries.py src/agentic_librarian/api/main.py test/integration/test_availability_api.py test/integration/test_libraries_api.py
git commit -m "$(printf 'feat(availability): /availability + /me/libraries + /libraries/search endpoints\n\nCo-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>')"
```

---

## Task 6: `check_availability` MCP tool (`mcp/server.py`)

**Files:**
- Modify: `src/agentic_librarian/mcp/server.py` (add one `@mcp.tool()`, ~after `check_reading_history`)
- Test: `test/integration/test_check_availability_tool.py`

Same shape as `check_reading_history(title, author)`: SEC-002 input validation, `get_required_user_id()`, share the availability service + cache. Never throws into the agent loop.

- [ ] **Step 1: Write the failing test**

```python
# test/integration/test_check_availability_tool.py
import pytest
from uuid import uuid4
from agentic_librarian.core.user_context import as_user
from agentic_librarian.db.models import User, UserLibrary
from agentic_librarian.mcp import server

pytestmark = pytest.mark.db_integration


def _user_with_library(session):
    user = User(id=uuid4(), email="t@example.com")
    session.add(user)
    session.add(UserLibrary(user_id=user.id, provider="libby", library_slug="kcls",
                            display_name="KCLS", sort_order=0))
    session.flush()
    return user


def test_check_availability_returns_links_and_badge(db_session, monkeypatch):
    from agentic_librarian.availability import service
    server.set_db_manager(_manager_for(db_session))  # mirror existing MCP tests' manager stub
    user = _user_with_library(db_session)
    monkeypatch.setattr(service, "availability_for",
                        lambda *a, **k: [{"format": "Audiobook", "available": True}])
    with as_user(user.id):
        out = server.check_availability("Project Hail Mary", "Andy Weir")
    assert out["libraries"][0]["formats"][0]["available"] is True
    assert any(link["kind"] == "amazon" for link in out["links"])


def test_check_availability_rejects_bad_input():
    assert "Error" in server.check_availability("", "Andy Weir")["note"]
```

> Reuse the manager-stub helper the other MCP tests use (search `test/` for how `mcp.server.set_db_manager` is fed a session). If they pass a real `DatabaseManager` pointed at the test DB, do the same here instead of `_manager_for`.

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest test/integration/test_check_availability_tool.py -v`
Expected: FAIL — `check_availability` not defined.

- [ ] **Step 3: Add the tool**

Insert into `src/agentic_librarian/mcp/server.py` (after `check_reading_history`, before `get_read_status`). Add the imports at the top of the file (alongside the existing model imports):

```python
from agentic_librarian.availability import service as availability_service
from agentic_librarian.availability.links import build_links
from agentic_librarian.db.models import UserLibrary
```

```python
@mcp.tool()
def check_availability(title: str, author: str) -> dict:
    """Check library + retail availability for a book and return where to get it. Use when
    recommending a title or when the user asks where/how to read it. Returns {title, author,
    libraries: [{library, slug, formats:[{format, available, copies_available, copies_owned,
    holds_ratio, wait_days}]}], links:[{kind,label,url}], note}. 'libraries' is the user's
    saved Libby systems with live availability; 'links' (Libby/Hoopla/Bookshop/Amazon) is
    always present. Narrate it naturally; never paste the raw dict."""
    if not _valid_name(title) or not _valid_name(author):
        return {"title": title, "author": author, "libraries": [], "links": [],
                "note": "Error: title and author must be non-empty strings."}
    user_id = get_required_user_id()
    libraries: list[dict] = []
    note = ""
    with db_manager.get_session() as session:
        libs = [
            {"slug": r.library_slug, "name": r.display_name}
            for r in session.query(UserLibrary)
            .filter(UserLibrary.user_id == user_id, UserLibrary.provider == "libby")
            .order_by(UserLibrary.sort_order)
            .all()
        ]
        if not libs:
            note = "No libraries saved — the reader can add theirs in Settings."
        for lib in libs:
            try:
                formats = availability_service.availability_for(session, lib, title, author)
            except Exception:  # noqa: BLE001 - never throw into the agent loop
                formats = None
            if formats:
                libraries.append({"library": lib["name"], "slug": lib["slug"], "formats": formats})
        links = build_links(title, author, libraries=libs)
    if libs and not libraries and not note:
        note = "Couldn't confirm live availability — offer the search links."
    return {"title": title, "author": author, "libraries": libraries, "links": links, "note": note}
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest test/integration/test_check_availability_tool.py -v`
Expected: PASS.

- [ ] **Step 5: Format + commit**

```bash
uvx ruff@0.15.16 format . && uvx ruff@0.15.16 check .
git add src/agentic_librarian/mcp/server.py test/integration/test_check_availability_tool.py
git commit -m "$(printf 'feat(availability): check_availability MCP tool for chat\n\nCo-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>')"
```

---

## Task 7: Frontend API client (`api/client.ts`)

**Files:**
- Modify: `frontend/src/api/client.ts` (append types + 4 functions)
- Test: `frontend/src/api/client.test.ts` (create if absent; otherwise append)

- [ ] **Step 1: Write the failing test**

```ts
// frontend/src/api/client.test.ts
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { getAvailability, searchLibraries } from './client'

vi.mock('../auth/firebase', () => ({ getIdToken: () => Promise.resolve('tok') }))

describe('availability client', () => {
  beforeEach(() => { vi.restoreAllMocks() })

  it('posts work_ids and returns the availability map', async () => {
    const map = { w1: { links: [{ kind: 'amazon', label: 'Amazon', url: 'u' }], libby: [] } }
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(
      { ok: true, json: () => Promise.resolve(map) } as Response))
    const out = await getAvailability(['w1'])
    expect(out.w1.links[0].kind).toBe('amazon')
  })

  it('searchLibraries hits the directory endpoint', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(
      { ok: true, json: () => Promise.resolve([{ slug: 'kcls', name: 'KCLS' }]) } as Response))
    const out = await searchLibraries('king')
    expect(out[0].slug).toBe('kcls')
  })
})
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd frontend && npm test -- --run src/api/client.test.ts`
Expected: FAIL — `getAvailability`/`searchLibraries` not exported.

- [ ] **Step 3: Append to `frontend/src/api/client.ts`**

```ts
export interface BookLink {
  kind: 'libby' | 'hoopla' | 'bookshop' | 'amazon'
  label: string
  url: string
}

export interface LibbyFormat {
  format: string
  available: boolean
  copies_owned: number | null
  copies_available: number | null
  holds_ratio: number | null
  wait_days: number | null
}

export interface LibbyAvailability {
  library: string
  slug: string
  formats: LibbyFormat[]
}

export interface BookAvailability {
  links: BookLink[]
  libby: LibbyAvailability[]
}

export interface SavedLibrary {
  slug: string
  name: string
}

export async function getAvailability(workIds: string[]): Promise<Record<string, BookAvailability>> {
  const res = await authedFetchRaw('/availability', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ work_ids: workIds }),
  })
  if (!res.ok) throw new Error(`availability → ${res.status}`)
  return res.json() as Promise<Record<string, BookAvailability>>
}

export function searchLibraries(q: string): Promise<SavedLibrary[]> {
  return getJson<SavedLibrary[]>(`/libraries/search?q=${encodeURIComponent(q)}`)
}

export async function getMyLibraries(): Promise<SavedLibrary[]> {
  const data = await getJson<{ libraries: SavedLibrary[] }>('/me/libraries')
  return data.libraries
}

export async function saveMyLibraries(libraries: SavedLibrary[]): Promise<void> {
  const res = await authedFetchRaw('/me/libraries', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ libraries }),
  })
  if (!res.ok) throw new Error(`save libraries → ${res.status}`)
}
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd frontend && npm test -- --run src/api/client.test.ts`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/api/client.ts frontend/src/api/client.test.ts
git commit -m "$(printf 'feat(availability): frontend client for availability + library prefs\n\nCo-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>')"
```

---

## Task 8: `BookLinks` component + Recommendations integration

**Files:**
- Create: `frontend/src/components/BookLinks.tsx`, `frontend/src/components/BookLinks.css`
- Modify: `frontend/src/views/RecommendationsView.tsx` (render `<BookLinks>` per card)
- Test: `frontend/src/components/BookLinks.test.tsx`

`BookLinks` takes the already-fetched availability (the view fetches once, in batch) so each card doesn't call the API. Props: `{ availability?: BookAvailability }`. Renders the link row always; renders the badge when `libby` has entries; renders nothing extra (links only) when `libby` is empty/undefined.

- [ ] **Step 1: Write the failing test**

```tsx
// frontend/src/components/BookLinks.test.tsx
import { render, screen } from '@testing-library/react'
import { describe, it, expect } from 'vitest'
import BookLinks from './BookLinks'

describe('BookLinks', () => {
  const links = [
    { kind: 'libby' as const, label: 'KCLS on Libby', url: 'https://libby/x' },
    { kind: 'amazon' as const, label: 'Amazon', url: 'https://amazon/x' },
  ]

  it('renders links always, even with no availability', () => {
    render(<BookLinks availability={{ links, libby: [] }} />)
    expect(screen.getByText('Amazon')).toBeInTheDocument()
    expect(screen.queryByText(/available now/i)).not.toBeInTheDocument()
  })

  it('renders an availability badge when libby data is present', () => {
    render(<BookLinks availability={{
      links,
      libby: [{ library: 'KCLS', slug: 'kcls', formats: [
        { format: 'Audiobook', available: true, copies_owned: 20, copies_available: 2, holds_ratio: 0, wait_days: 0 },
      ] }],
    }} />)
    expect(screen.getByText(/KCLS/)).toBeInTheDocument()
    expect(screen.getByText(/available now/i)).toBeInTheDocument()
  })

  it('renders nothing when availability is undefined (still loading)', () => {
    const { container } = render(<BookLinks availability={undefined} />)
    expect(container).toBeEmptyDOMElement()
  })
})
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd frontend && npm test -- --run src/components/BookLinks.test.tsx`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement `BookLinks.tsx`**

```tsx
import type { BookAvailability, LibbyFormat } from '../api/client'
import './BookLinks.css'

function formatLabel(f: LibbyFormat): string {
  if (f.available) return `${f.format} available now`
  if (f.wait_days && f.wait_days > 0) {
    const weeks = Math.round(f.wait_days / 7)
    return `${f.format} ~${weeks}wk wait`
  }
  return `${f.format} on hold`
}

export default function BookLinks({ availability }: { availability?: BookAvailability }) {
  if (!availability) return null
  const { links, libby } = availability
  return (
    <div className="book-links">
      {libby.length > 0 && (
        <ul className="book-links__avail">
          {libby.map((lib) => (
            <li key={lib.slug} className="book-links__lib">
              <span className="book-links__libname">{lib.library}</span>
              {lib.formats.map((f) => (
                <span key={f.format} className={`book-links__fmt${f.available ? ' is-available' : ''}`}>
                  {formatLabel(f)}
                </span>
              ))}
            </li>
          ))}
        </ul>
      )}
      <div className="book-links__row">
        {links.map((link) => (
          <a key={link.kind + link.url} className={`book-links__link kind-${link.kind}`}
             href={link.url} target="_blank" rel="noreferrer">
            {link.label}
          </a>
        ))}
      </div>
    </div>
  )
}
```

`BookLinks.css` (consume design tokens; keep minimal — follow existing `*.css` token usage like `RecommendationsView.css`):

```css
.book-links { margin-top: 0.5rem; display: flex; flex-direction: column; gap: 0.4rem; }
.book-links__avail { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 0.2rem; }
.book-links__lib { display: flex; flex-wrap: wrap; gap: 0.4rem; align-items: baseline; font-size: 0.85rem; }
.book-links__libname { font-weight: 600; }
.book-links__fmt { opacity: 0.8; }
.book-links__fmt.is-available { color: var(--color-success, #2e7d32); opacity: 1; font-weight: 600; }
.book-links__row { display: flex; flex-wrap: wrap; gap: 0.5rem; }
.book-links__link { font-size: 0.85rem; text-decoration: underline; }
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd frontend && npm test -- --run src/components/BookLinks.test.tsx`
Expected: PASS.

- [ ] **Step 5: Wire into `RecommendationsView.tsx`**

Add state + a batch fetch after recs load, and render `<BookLinks>` in each card. Concretely:

Add import:
```tsx
import BookLinks from '../components/BookLinks'
import { getAvailability, type BookAvailability } from '../api/client'
```

Add state (next to `recs`):
```tsx
const [avail, setAvail] = useState<Record<string, BookAvailability>>({})
```

After the recs are set in the existing `useEffect` (after `setRecs(data)`), fetch availability in one batch — failure is silent (links/badges simply don't appear):
```tsx
const workIds = data.map((r) => r.work_id)
if (workIds.length > 0) {
  void getAvailability(workIds).then(setAvail).catch(() => { /* links-only fallback: leave avail empty */ })
}
```

In the card JSX (after the `rec-actions` div, inside the `<article>`):
```tsx
<BookLinks availability={avail[r.work_id]} />
```

- [ ] **Step 6: Run the view test suite**

Run: `cd frontend && npm test -- --run src/views/RecommendationsView.test.tsx`
Expected: PASS. If the existing test asserts on fetch counts, stub `getAvailability` in it (mirror how it stubs `getRecommendations`).

- [ ] **Step 7: Lint + commit**

```bash
cd frontend && npm run lint && cd ..
git add frontend/src/components/BookLinks.tsx frontend/src/components/BookLinks.css frontend/src/components/BookLinks.test.tsx frontend/src/views/RecommendationsView.tsx
git commit -m "$(printf 'feat(availability): BookLinks component on recommendation cards\n\nCo-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>')"
```

---

## Task 9: Settings library-picker view + route + nav

**Files:**
- Create: `frontend/src/views/SettingsView.tsx`, `frontend/src/views/SettingsView.css`
- Modify: `frontend/src/App.tsx` (route), `frontend/src/components/Nav.tsx` (+ `.css` if needed) (nav entry)
- Test: `frontend/src/views/SettingsView.test.tsx`

Search (debounced) → add → reorder (up/down) → remove → save. Persists via `saveMyLibraries`.

- [ ] **Step 1: Write the failing test**

```tsx
// frontend/src/views/SettingsView.test.tsx
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import SettingsView from './SettingsView'
import * as client from '../api/client'

vi.mock('../api/client')

describe('SettingsView', () => {
  beforeEach(() => {
    vi.mocked(client.getMyLibraries).mockResolvedValue([{ slug: 'kcls', name: 'KCLS' }])
    vi.mocked(client.searchLibraries).mockResolvedValue([{ slug: 'spl', name: 'Seattle PL' }])
    vi.mocked(client.saveMyLibraries).mockResolvedValue(undefined)
  })

  it('loads and shows saved libraries', async () => {
    render(<SettingsView />)
    expect(await screen.findByText('KCLS')).toBeInTheDocument()
  })

  it('searches, adds, and saves a library', async () => {
    render(<SettingsView />)
    fireEvent.change(screen.getByPlaceholderText(/search/i), { target: { value: 'seattle' } })
    fireEvent.click(await screen.findByText(/add/i))
    fireEvent.click(screen.getByText(/save/i))
    await waitFor(() => expect(client.saveMyLibraries).toHaveBeenCalled())
    const saved = vi.mocked(client.saveMyLibraries).mock.calls[0][0]
    expect(saved.map((l) => l.slug)).toContain('spl')
  })
})
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd frontend && npm test -- --run src/views/SettingsView.test.tsx`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement `SettingsView.tsx`**

```tsx
import { useEffect, useState } from 'react'
import { getMyLibraries, searchLibraries, saveMyLibraries, type SavedLibrary } from '../api/client'
import './SettingsView.css'

export default function SettingsView() {
  const [saved, setSaved] = useState<SavedLibrary[]>([])
  const [query, setQuery] = useState('')
  const [results, setResults] = useState<SavedLibrary[]>([])
  const [status, setStatus] = useState<string>('')

  useEffect(() => { void getMyLibraries().then(setSaved) }, [])

  useEffect(() => {
    const q = query.trim()
    if (!q) { setResults([]); return }
    const t = setTimeout(() => { void searchLibraries(q).then(setResults).catch(() => setResults([])) }, 300)
    return () => clearTimeout(t)
  }, [query])

  function add(lib: SavedLibrary) {
    if (!saved.some((s) => s.slug === lib.slug)) setSaved([...saved, lib])
  }
  function remove(slug: string) { setSaved(saved.filter((s) => s.slug !== slug)) }
  function move(i: number, delta: number) {
    const j = i + delta
    if (j < 0 || j >= saved.length) return
    const next = [...saved]
    ;[next[i], next[j]] = [next[j], next[i]]
    setSaved(next)
  }
  async function save() {
    setStatus('Saving…')
    try { await saveMyLibraries(saved); setStatus('Saved') } catch { setStatus('Save failed') }
  }

  return (
    <div className="settings">
      <header className="view-head"><h2>Libraries</h2></header>
      <p className="settings__hint">Add the library systems you have a Libby card for. We’ll show live
        availability for these on your recommendations, in your priority order.</p>

      <ul className="settings__saved">
        {saved.map((lib, i) => (
          <li key={lib.slug} className="settings__saved-row">
            <span>{lib.name}</span>
            <span className="settings__controls">
              <button className="btn btn--ghost" onClick={() => move(i, -1)} aria-label="Move up">↑</button>
              <button className="btn btn--ghost" onClick={() => move(i, 1)} aria-label="Move down">↓</button>
              <button className="btn btn--ghost" onClick={() => remove(lib.slug)}>Remove</button>
            </span>
          </li>
        ))}
      </ul>

      <input className="settings__search" placeholder="Search for your library…"
             value={query} onChange={(e) => setQuery(e.target.value)} />
      <ul className="settings__results">
        {results.map((lib) => (
          <li key={lib.slug} className="settings__result-row">
            <span>{lib.name}</span>
            <button className="btn" onClick={() => add(lib)}>Add</button>
          </li>
        ))}
      </ul>

      <div className="settings__actions">
        <button className="btn" onClick={() => void save()}>Save</button>
        {status && <span className="settings__status">{status}</span>}
      </div>
    </div>
  )
}
```

`SettingsView.css` — minimal, token-consuming:

```css
.settings { display: flex; flex-direction: column; gap: 0.75rem; }
.settings__hint { opacity: 0.8; font-size: 0.9rem; }
.settings__saved, .settings__results { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 0.4rem; }
.settings__saved-row, .settings__result-row { display: flex; justify-content: space-between; align-items: center; gap: 0.5rem; }
.settings__controls { display: flex; gap: 0.3rem; }
.settings__search { padding: 0.5rem; }
.settings__actions { display: flex; gap: 0.5rem; align-items: center; }
.settings__status { opacity: 0.8; font-size: 0.9rem; }
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd frontend && npm test -- --run src/views/SettingsView.test.tsx`
Expected: PASS.

- [ ] **Step 5: Add the route + nav entry**

`App.tsx` — add the import and a route inside the `<AppShell>` route group:
```tsx
import SettingsView from './views/SettingsView'
```
```tsx
<Route path="settings" element={<SettingsView />} />
```

`components/Nav.tsx` — add a nav link to `/settings` labeled "Libraries" (mirror the existing entries; reuse a `LineIcon` glyph — pick an existing one such as the shelf/book glyph, or the gear if present). Follow the exact pattern of the other `<NavLink>`/`<Link>` items already in the file. If `Nav.test.tsx` asserts the set of nav items, update it to include the new entry.

- [ ] **Step 6: Run nav + full frontend suite**

Run: `cd frontend && npm test -- --run`
Expected: PASS (update `Nav.test.tsx` if it enumerates items).

- [ ] **Step 7: Lint + commit**

```bash
cd frontend && npm run lint && cd ..
git add frontend/src/views/SettingsView.tsx frontend/src/views/SettingsView.css frontend/src/views/SettingsView.test.tsx frontend/src/App.tsx frontend/src/components/Nav.tsx frontend/src/components/Nav.test.tsx
git commit -m "$(printf 'feat(availability): Settings library picker + nav entry\n\nCo-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>')"
```

---

## Task 10: Full-suite verification + manual smoke

**Files:** none (verification only)

- [ ] **Step 1: Backend suite**

Run: `uv run pytest test/unit/test_availability_links.py test/unit/test_availability_overdrive.py test/unit/test_availability_service.py -v` (unit, must pass locally) and `uv run pytest -m "not db_integration" -q` (no regressions). DB-integration tests run in CI.
Expected: PASS / no new failures.

- [ ] **Step 2: Format + lint gate**

Run: `uvx ruff@0.15.16 format . && uvx ruff@0.15.16 check .` then `cd frontend && npm run lint && npm test -- --run && cd ..`
Expected: clean; all frontend tests pass.

- [ ] **Step 3: Manual smoke against the live Thunder API (optional, no DB writes)**

Confirm the unofficial endpoints still behave as the spec assumes:
```bash
curl -s "https://thunder.api.overdrive.com/v2/libraries?query=seattle&x-client-id=dewey" | head -c 300
curl -s "https://thunder.api.overdrive.com/v2/libraries/kcls/media?query=project%20hail%20mary&format=ebook-overdrive,audiobook-overdrive&perPage=2&x-client-id=dewey" | head -c 400
```
Expected: JSON with `items[]`; media items carry `isAvailable`, `holdsRatio`, `estimatedWaitDays`. **Also confirm the Libby deep-link path** (`links.py` `_libby_url`) opens a real search in a browser; adjust the constant if the path segments differ.

- [ ] **Step 4: Hand off**

Use `superpowers:finishing-a-development-branch` to open the PR. The operator runs the migration (`alembic upgrade head`) on prod as part of deploy; no backfill needed (cache fills lazily).

---

## Self-Review (completed during planning)

**Spec coverage:** shared service (T2–T4) · availability_cache + 4h TTL (T1, T4) · user_libraries (T1) · picker endpoints + UI (T5, T9) · pure links independent of Thunder (T2, used in T5/T6) · `/availability` always-200 (T5) · `check_availability` MCP tool sharing the cache (T6) · recs cards lazy-batch refresh (T8) · Hoopla = search link only, no stored library (T2, no Hoopla in `user_libraries`) · isolated gray endpoint (T3). Out-of-scope items (checkout/holds, Hoopla badge, pre-warm, #56) intentionally absent. **No gaps.**

**Placeholder scan:** every code/test step has concrete code and exact commands. Two real-world verifications (Libby deep-link path, live Thunder shape) are explicit smoke steps in T10, not hidden TODOs.

**Type consistency:** `BookAvailability {links, libby}`, `LibbyAvailability {library, slug, formats}`, `LibbyFormat {format, available, copies_owned, copies_available, holds_ratio, wait_days}`, and link `{kind,label,url}` are identical across backend (`service._shape_formats`, `availability.py`, `links.py`, `check_availability`) and frontend (`client.ts`, `BookLinks`, `SettingsView`). `availability_for()` signature matches every call site (T5, T6).

## Open verification items (carried from the spec)

- **Libby deep-link path** — verified in T10 Step 3; adjust `links._libby_url` if needed.
- **TTL** — `AVAILABILITY_TTL_SECONDS` env, default 14400 (4h); back off if Thunder rate-limits.
- **Branch** — cut a fresh feature branch off updated `main` and carry the two spec commits + this plan before executing (the worktree is still on the merged `fix/fallback-prune-by-genre-membership`).
