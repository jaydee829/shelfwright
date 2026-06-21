# Bulk Reading-History Import — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a user bulk-import a reading history from a Goodreads export or a generic CSV, via an auto-detecting column-mapping wizard, with a quota-safe per-row Cloud Tasks pipeline (de-dup → shallow → queue-deep) and a live progress UI.

**Architecture:** Two synchronous endpoints (`/import/preview`, `/import/commit`) parse/validate and persist an `ImportJob` + one `ImportRow` per source row, then enqueue one Cloud Task per importable row onto a dedicated import queue. An OIDC-gated worker (`/internal/import-row/{id}`) runs the per-row pipeline by reusing the existing `two_phase.enrich_fast` / `add_read_event` / `enqueue_enrichment`. Progress is derived from `import_rows` (no counters → redelivery-safe). The React wizard polls `GET /import/{job_id}`.

**Tech Stack:** Python 3 / FastAPI / SQLAlchemy / Alembic / Google Cloud Tasks (backend); React + TypeScript + Vitest (frontend); pytest with a `db_integration` marker (Postgres).

**Spec:** `docs/superpowers/specs/2026-06-18-bulk-import-design.md`

**Conventions used below:**
- Backend tests: `uv run pytest <path>::<name> -v`. DB tests carry `pytestmark = pytest.mark.db_integration` and auto-skip when Postgres is unreachable (`test/conftest.py`).
- Frontend tests: from `frontend/`, `npx vitest run <path>`.
- Commit after every green task (no push; `feat/bulk-import` branch is already checked out).
- New package is `imports` (not `import` — reserved word).

---

## File Structure

**Backend — create:**
- `src/agentic_librarian/imports/__init__.py` — empty package marker.
- `src/agentic_librarian/imports/parsing.py` — pure: `sniff_source`, `suggest_mapping`, `parse_rows`, `ParsedRow`.
- `src/agentic_librarian/imports/bucketing.py` — pure: `bucket()` routing.
- `src/agentic_librarian/imports/tasks.py` — `enqueue_import_row()` (Cloud Tasks).
- `src/agentic_librarian/imports/worker.py` — `process_import_row()` per-row pipeline.
- `src/agentic_librarian/api/imports.py` — router: preview / commit / get / retry.
- `alembic/versions/<rev>_bulk_import_tables.py` — migration.

**Backend — modify:**
- `src/agentic_librarian/db/models.py` — add `ImportJob`, `ImportRow`.
- `src/agentic_librarian/api/internal.py` — add `POST /internal/import-row/{row_id}`.
- `src/agentic_librarian/api/main.py` — `include_router(imports_router)`.

**Frontend — create:**
- `frontend/src/views/ImportView.tsx`, `.css`, `.test.tsx`.

**Frontend — modify:**
- `frontend/src/api/client.ts` — import API functions + types.
- `frontend/src/App.tsx` — `/import` route (+ mock in `App.test.tsx`).
- `frontend/src/views/HistoryView.tsx` — "Import history" link.

---

## Task 1: Data model — `ImportJob` and `ImportRow`

**Files:**
- Modify: `src/agentic_librarian/db/models.py`
- Test: `test/unit/test_db_models.py`

- [ ] **Step 1: Write the failing test**

Add to `test/unit/test_db_models.py`:

```python
def test_import_job_and_row_models_exist():
    from agentic_librarian.db.models import ImportJob, ImportRow

    assert ImportJob.__tablename__ == "import_jobs"
    assert ImportRow.__tablename__ == "import_rows"
    # ImportRow carries everything the worker needs without loading the job.
    cols = ImportRow.__table__.columns.keys()
    for c in ("import_job_id", "user_id", "raw_title", "raw_author", "raw_format",
              "raw_date", "date_completed", "rating", "notes", "destination",
              "shelf", "status", "outcome", "skip_reason", "work_id", "error_detail"):
        assert c in cols, c
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest test/unit/test_db_models.py::test_import_job_and_row_models_exist -v`
Expected: FAIL with `ImportError: cannot import name 'ImportJob'`.

- [ ] **Step 3: Write minimal implementation**

In `src/agentic_librarian/db/models.py`, add at the end (the file already imports `Date`, `DateTime`, `Integer`, `String`, `Text`, `ForeignKey`, `PG_UUID`, `uuid4`, `datetime`, `UTC`, `date`):

```python
class ImportJob(Base):
    """One bulk-import upload (Spec 2026-06-18). Progress is derived from import_rows,
    not stored here — so Cloud Tasks redelivery can never double-count."""

    __tablename__ = "import_jobs"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4, nullable=False)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    source: Mapped[str] = mapped_column(String, nullable=False)  # 'goodreads' | 'generic'
    original_filename: Mapped[str | None] = mapped_column(String, nullable=True)
    total_rows: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC), nullable=False)


class ImportRow(Base):
    """One parsed source row. The Cloud Task targets this id; status is the idempotency
    boundary (a redelivered row whose status is already 'done' is a no-op)."""

    __tablename__ = "import_rows"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4, nullable=False)
    import_job_id: Mapped[UUID] = mapped_column(ForeignKey("import_jobs.id"), nullable=False, index=True)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    raw_title: Mapped[str | None] = mapped_column(String, nullable=True)
    raw_author: Mapped[str | None] = mapped_column(String, nullable=True)
    raw_format: Mapped[str | None] = mapped_column(String, nullable=True)  # normalized vocab
    raw_date: Mapped[str | None] = mapped_column(String, nullable=True)  # original text, for the report
    date_completed: Mapped[date | None] = mapped_column(Date, nullable=True)  # parsed; set for history rows
    rating: Mapped[int | None] = mapped_column(Integer, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    destination: Mapped[str] = mapped_column(String, nullable=False)  # 'history' | 'suggestion' | 'skip'
    shelf: Mapped[str | None] = mapped_column(String, nullable=True)  # drives the suggestion context tag
    status: Mapped[str] = mapped_column(String, nullable=False, default="pending")
    outcome: Mapped[str | None] = mapped_column(String, nullable=True)  # linked|created|duplicate|not_found|error
    skip_reason: Mapped[str | None] = mapped_column(String, nullable=True)
    work_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    error_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC), nullable=False
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest test/unit/test_db_models.py::test_import_job_and_row_models_exist -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/agentic_librarian/db/models.py test/unit/test_db_models.py
git commit -m "feat(import): ImportJob + ImportRow models"
```

---

## Task 2: Alembic migration for the two tables

**Files:**
- Create: `alembic/versions/<rev>_bulk_import_tables.py` (filename generated by Alembic)
- Test: `test/integration/test_import_migration.py`

- [ ] **Step 1: Write the failing test**

Create `test/integration/test_import_migration.py`:

```python
"""The bulk-import tables exist in the migrated schema (Spec 2026-06-18)."""

import pytest
from sqlalchemy import create_engine, inspect

pytestmark = pytest.mark.db_integration


def test_import_tables_present(db_url):
    insp = inspect(create_engine(db_url))
    tables = set(insp.get_table_names())
    assert {"import_jobs", "import_rows"} <= tables
    row_cols = {c["name"] for c in insp.get_columns("import_rows")}
    assert {"status", "destination", "date_completed", "work_id"} <= row_cols
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest test/integration/test_import_migration.py -v`
Expected: FAIL (`assert {'import_jobs', 'import_rows'} <= tables`) — or SKIP if no DB. If skipped, you cannot verify locally; proceed and rely on CI, but still generate the migration.

- [ ] **Step 3: Generate and write the migration**

Generate an empty revision (auto-chains `down_revision` to the current head):

```bash
uv run alembic revision -m "bulk import tables"
```

Open the new file in `alembic/versions/` and replace `upgrade`/`downgrade` with:

```python
def upgrade() -> None:
    op.create_table(
        "import_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("original_filename", sa.String(), nullable=True),
        sa.Column("total_rows", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_import_jobs_user_id", "import_jobs", ["user_id"])
    op.create_table(
        "import_rows",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("import_job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("raw_title", sa.String(), nullable=True),
        sa.Column("raw_author", sa.String(), nullable=True),
        sa.Column("raw_format", sa.String(), nullable=True),
        sa.Column("raw_date", sa.String(), nullable=True),
        sa.Column("date_completed", sa.Date(), nullable=True),
        sa.Column("rating", sa.Integer(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("destination", sa.String(), nullable=False),
        sa.Column("shelf", sa.String(), nullable=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("outcome", sa.String(), nullable=True),
        sa.Column("skip_reason", sa.String(), nullable=True),
        sa.Column("work_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("error_detail", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["import_job_id"], ["import_jobs.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_import_rows_import_job_id", "import_rows", ["import_job_id"])
    op.create_index("ix_import_rows_user_id", "import_rows", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_import_rows_user_id", table_name="import_rows")
    op.drop_index("ix_import_rows_import_job_id", table_name="import_rows")
    op.drop_table("import_rows")
    op.drop_index("ix_import_jobs_user_id", table_name="import_jobs")
    op.drop_table("import_jobs")
```

Ensure the imports at the top match the multi-user migration: `import sqlalchemy as sa`, `from sqlalchemy.dialects import postgresql`, `from alembic import op`.

- [ ] **Step 4: Run test to verify it passes**

Recreate the test schema and run:

```bash
uv run alembic upgrade head
uv run pytest test/integration/test_import_migration.py -v
```

Expected: PASS (or SKIP without a DB). To force a clean rebuild if the session DB already exists, drop the `*_test` database first or rely on the session fixture which runs `upgrade head`.

- [ ] **Step 5: Commit**

```bash
git add alembic/versions/ test/integration/test_import_migration.py
git commit -m "feat(import): migration for import_jobs + import_rows"
```

---

## Task 3: `parsing.py` — source detection + mapping suggestion

**Files:**
- Create: `src/agentic_librarian/imports/__init__.py` (empty)
- Create: `src/agentic_librarian/imports/parsing.py`
- Test: `test/unit/test_import_parsing.py`

- [ ] **Step 1: Write the failing test**

Create `test/unit/test_import_parsing.py`:

```python
from agentic_librarian.imports import parsing

GOODREADS_HEADERS = [
    "Book Id", "Title", "Author", "My Rating", "Average Rating", "Binding",
    "Date Read", "Date Added", "Bookshelves", "Exclusive Shelf", "My Review",
]


def test_sniff_detects_goodreads():
    assert parsing.sniff_source(GOODREADS_HEADERS) == "goodreads"
    assert parsing.sniff_source(["title", "writer", "finished"]) == "generic"


def test_suggest_mapping_goodreads_is_the_known_map():
    m = parsing.suggest_mapping(GOODREADS_HEADERS, "goodreads")
    assert m["title"] == "Title"
    assert m["author"] == "Author"
    assert m["format"] == "Binding"
    assert m["date_completed"] == "Date Read"
    assert m["rating"] == "My Rating"
    assert m["notes"] == "My Review"
    assert m["shelf"] == "Exclusive Shelf"


def test_suggest_mapping_generic_fuzzy_matches_synonyms():
    m = parsing.suggest_mapping(["Book Title", "Writer", "Date Finished", "Stars"], "generic")
    assert m["title"] == "Book Title"
    assert m["author"] == "Writer"
    assert m["date_completed"] == "Date Finished"
    assert m["rating"] == "Stars"
    assert m["format"] is None  # no format-like column present
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest test/unit/test_import_parsing.py -v`
Expected: FAIL with `ModuleNotFoundError: agentic_librarian.imports`.

- [ ] **Step 3: Write minimal implementation**

Create `src/agentic_librarian/imports/__init__.py` (empty). Create `src/agentic_librarian/imports/parsing.py`:

```python
"""Pure CSV parsing/normalization for bulk import (Spec 2026-06-18). No I/O — the highest
test-value surface, where Goodreads/generic format variability lives."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

# Goodreads exports carry this stable header signature.
_GOODREADS_SIGNATURE = {"Book Id", "Title", "Author", "Exclusive Shelf"}

_GOODREADS_MAP = {
    "title": "Title",
    "author": "Author",
    "format": "Binding",
    "date_completed": "Date Read",
    "rating": "My Rating",
    "notes": "My Review",
    "shelf": "Exclusive Shelf",
}

# Field -> ordered synonyms (normalized, substring match) for generic CSVs.
_SYNONYMS = {
    "title": ["title", "book"],
    "author": ["author", "writer", "by"],
    "format": ["format", "binding", "edition type"],
    "date_completed": ["date read", "date finished", "finished", "date completed", "completed", "read date"],
    "rating": ["my rating", "rating", "stars", "score"],
    "notes": ["my review", "review", "notes", "comment"],
    "shelf": ["exclusive shelf", "shelf", "status"],
}

_BINDING_TO_FORMAT = {
    "kindle edition": "ebook", "ebook": "ebook", "kindle": "ebook",
    "paperback": "paperback", "mass market paperback": "paperback",
    "hardcover": "hardcover", "hardback": "hardcover",
    "audiobook": "audiobook", "audio cd": "audiobook", "audible audio": "audiobook", "audio": "audiobook",
}


@dataclass
class ParsedRow:
    raw_title: str
    raw_author: str
    raw_format: str          # normalized vocab; defaults to 'ebook'
    raw_date: str            # original text (may be '')
    date_completed: date | None  # parsed; None if blank/unparseable/future
    bad_date: bool           # raw_date non-empty but unparseable or future
    rating: int | None
    notes: str | None
    shelf: str               # lowercased exclusive shelf; '' when absent


def _norm(s: str) -> str:
    return " ".join((s or "").strip().lower().split())


def sniff_source(headers: list[str]) -> str:
    return "goodreads" if _GOODREADS_SIGNATURE <= set(headers) else "generic"


def suggest_mapping(headers: list[str], source: str) -> dict[str, str | None]:
    if source == "goodreads":
        present = set(headers)
        return {field: (col if col in present else None) for field, col in _GOODREADS_MAP.items()}
    norm_headers = [(_norm(h), h) for h in headers]
    mapping: dict[str, str | None] = {}
    for field, syns in _SYNONYMS.items():
        match = None
        for syn in syns:
            for nh, original in norm_headers:
                if nh == syn or syn in nh:
                    match = original
                    break
            if match:
                break
        mapping[field] = match
    return mapping
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest test/unit/test_import_parsing.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/agentic_librarian/imports/__init__.py src/agentic_librarian/imports/parsing.py test/unit/test_import_parsing.py
git commit -m "feat(import): source sniff + column-mapping suggestion"
```

---

## Task 4: `parsing.py` — `parse_rows` value normalization

**Files:**
- Modify: `src/agentic_librarian/imports/parsing.py`
- Test: `test/unit/test_import_parsing.py`

- [ ] **Step 1: Write the failing test**

Append to `test/unit/test_import_parsing.py`:

```python
def test_parse_rows_normalizes_format_rating_date_shelf():
    mapping = parsing.suggest_mapping(GOODREADS_HEADERS, "goodreads")
    rows = [
        {"Title": "Dune", "Author": "Frank Herbert", "Binding": "Kindle Edition",
         "Date Read": "2024/03/05", "My Rating": "5", "My Review": "great",
         "Exclusive Shelf": "read"},
        {"Title": "Unrated", "Author": "A B", "Binding": "Audiobook",
         "Date Read": "", "My Rating": "0", "My Review": "", "Exclusive Shelf": "to-read"},
    ]
    parsed = parsing.parse_rows(rows, mapping)

    assert parsed[0].raw_format == "ebook"          # Kindle Edition -> ebook
    assert parsed[0].rating == 5
    assert parsed[0].date_completed == date(2024, 3, 5)
    assert parsed[0].bad_date is False
    assert parsed[0].shelf == "read"

    assert parsed[1].raw_format == "audiobook"
    assert parsed[1].rating is None                 # 0 -> unrated
    assert parsed[1].date_completed is None
    assert parsed[1].bad_date is False              # blank date is not "bad"
    assert parsed[1].shelf == "to-read"


def test_parse_rows_flags_future_and_unparseable_dates_and_defaults_format():
    mapping = {"title": "t", "author": "a", "format": None,
               "date_completed": "d", "rating": None, "notes": None, "shelf": None}
    rows = [
        {"t": "Future Book", "a": "X", "d": "2999-01-01"},
        {"t": "Junk Date", "a": "Y", "d": "not a date"},
    ]
    parsed = parsing.parse_rows(rows, mapping)
    assert all(p.raw_format == "ebook" for p in parsed)  # unmapped format -> default
    assert all(p.date_completed is None and p.bad_date is True for p in parsed)
    assert all(p.shelf == "" for p in parsed)            # unmapped shelf -> ''
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest test/unit/test_import_parsing.py -k parse_rows -v`
Expected: FAIL with `AttributeError: module 'agentic_librarian.imports.parsing' has no attribute 'parse_rows'`.

- [ ] **Step 3: Write minimal implementation**

Append to `src/agentic_librarian/imports/parsing.py`:

```python
def _cell(row: dict, col: str | None) -> str:
    if not col:
        return ""
    return (row.get(col) or "").strip()


def _parse_date(text: str) -> tuple[date | None, bool]:
    """Return (date or None, bad_date). bad_date is True only when text is present but
    unusable (unparseable or in the future). A blank string is (None, False)."""
    if not text:
        return None, False
    for fmt in ("%Y/%m/%d", "%Y-%m-%d", "%m/%d/%Y"):
        try:
            d = datetime.strptime(text, fmt).date()
        except ValueError:
            continue
        if d > date.today():
            return None, True
        return d, False
    return None, True


def _parse_rating(text: str) -> int | None:
    try:
        n = int(text)
    except (TypeError, ValueError):
        return None
    return n if 1 <= n <= 5 else None  # Goodreads 0 = unrated; out-of-range dropped


def _normalize_format(text: str) -> str:
    return _BINDING_TO_FORMAT.get(_norm(text), "ebook")


def parse_rows(rows: list[dict], mapping: dict[str, str | None]) -> list[ParsedRow]:
    out: list[ParsedRow] = []
    for row in rows:
        raw_date = _cell(row, mapping.get("date_completed"))
        parsed_date, bad = _parse_date(raw_date)
        notes = _cell(row, mapping.get("notes")) or None
        out.append(
            ParsedRow(
                raw_title=_cell(row, mapping.get("title")),
                raw_author=_cell(row, mapping.get("author")),
                raw_format=_normalize_format(_cell(row, mapping.get("format"))),
                raw_date=raw_date,
                date_completed=parsed_date,
                bad_date=bad,
                rating=_parse_rating(_cell(row, mapping.get("rating"))),
                notes=notes,
                shelf=_norm(_cell(row, mapping.get("shelf"))),
            )
        )
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest test/unit/test_import_parsing.py -v`
Expected: PASS (all parsing tests).

- [ ] **Step 5: Commit**

```bash
git add src/agentic_librarian/imports/parsing.py test/unit/test_import_parsing.py
git commit -m "feat(import): parse_rows value normalization"
```

---

## Task 5: `bucketing.py` — shelf routing

**Files:**
- Create: `src/agentic_librarian/imports/bucketing.py`
- Test: `test/unit/test_import_bucketing.py`

- [ ] **Step 1: Write the failing test**

Create `test/unit/test_import_bucketing.py`:

```python
from datetime import date

from agentic_librarian.imports.bucketing import bucket
from agentic_librarian.imports.parsing import ParsedRow


def _row(shelf="read", d=date(2024, 1, 1), bad=False):
    return ParsedRow(raw_title="t", raw_author="a", raw_format="ebook", raw_date="x",
                     date_completed=d, bad_date=bad, rating=None, notes=None, shelf=shelf)


def test_read_with_date_goes_to_history():
    assert bucket(_row(), import_to_read=True, import_currently_reading=True) == ("history", None)


def test_generic_no_shelf_treated_as_read():
    assert bucket(_row(shelf=""), import_to_read=False, import_currently_reading=False) == ("history", None)


def test_read_without_date_is_skipped():
    assert bucket(_row(d=None), import_to_read=True, import_currently_reading=True) == ("skip", "no_completion_date")


def test_read_with_bad_date_is_skipped():
    assert bucket(_row(d=None, bad=True), import_to_read=True, import_currently_reading=True) == ("skip", "bad_date")


def test_to_read_routes_by_opt_in():
    assert bucket(_row(shelf="to-read", d=None), import_to_read=True, import_currently_reading=True) == ("suggestion", None)
    assert bucket(_row(shelf="to-read", d=None), import_to_read=False, import_currently_reading=True) == ("skip", "to_read_opt_out")


def test_currently_reading_routes_by_opt_in():
    assert bucket(_row(shelf="currently-reading", d=None), import_to_read=True, import_currently_reading=True) == ("suggestion", None)
    assert bucket(_row(shelf="currently-reading", d=None), import_to_read=True, import_currently_reading=False) == ("skip", "currently_reading_opt_out")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest test/unit/test_import_bucketing.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Write minimal implementation**

Create `src/agentic_librarian/imports/bucketing.py`:

```python
"""Pure shelf->destination routing for bulk import (Spec 2026-06-18, D4)."""

from __future__ import annotations

from agentic_librarian.imports.parsing import ParsedRow


def bucket(row: ParsedRow, *, import_to_read: bool, import_currently_reading: bool) -> tuple[str, str | None]:
    """Return (destination, skip_reason). destination is 'history' | 'suggestion' | 'skip';
    skip_reason is set only when destination == 'skip'."""
    shelf = row.shelf
    if shelf == "to-read":
        return ("suggestion", None) if import_to_read else ("skip", "to_read_opt_out")
    if shelf == "currently-reading":
        return ("suggestion", None) if import_currently_reading else ("skip", "currently_reading_opt_out")
    # 'read', a custom shelf, or no shelf (generic CSV) → a completed-read candidate.
    if row.date_completed is not None:
        return ("history", None)
    return ("skip", "bad_date" if row.bad_date else "no_completion_date")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest test/unit/test_import_bucketing.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/agentic_librarian/imports/bucketing.py test/unit/test_import_bucketing.py
git commit -m "feat(import): shelf routing/bucketing"
```

---

## Task 6: `tasks.py` — `enqueue_import_row`

**Files:**
- Create: `src/agentic_librarian/imports/tasks.py`
- Test: `test/unit/test_enqueue_import_row.py`

- [ ] **Step 1: Write the failing test**

Create `test/unit/test_enqueue_import_row.py`:

```python
from agentic_librarian.imports import tasks


class _FakeClient:
    def __init__(self):
        self.created = []

    def create_task(self, *, parent, task):
        self.created.append((parent, task))
        return object()


def _set_env(monkeypatch):
    monkeypatch.setenv("IMPORT_TASKS_QUEUE", "projects/p/locations/us-central1/queues/import")
    monkeypatch.setenv("ENRICH_TARGET_BASE_URL", "https://librarian.example.run.app")
    monkeypatch.setenv("ENRICH_INVOKER_SA", "queue-invoker@p.iam.gserviceaccount.com")


def test_enqueue_builds_oidc_task_targeting_the_import_route(monkeypatch):
    _set_env(monkeypatch)
    fake = _FakeClient()
    monkeypatch.setattr(tasks, "_client", lambda: fake)

    assert tasks.enqueue_import_row("11111111-1111-4111-8111-111111111111") is True

    parent, task = fake.created[0]
    assert parent == "projects/p/locations/us-central1/queues/import"
    http = task["http_request"]
    assert http["url"] == "https://librarian.example.run.app/internal/import-row/11111111-1111-4111-8111-111111111111"
    assert http["oidc_token"]["service_account_email"] == "queue-invoker@p.iam.gserviceaccount.com"


def test_enqueue_skips_when_queue_not_configured(monkeypatch):
    monkeypatch.delenv("IMPORT_TASKS_QUEUE", raising=False)
    called = {"n": 0}
    monkeypatch.setattr(tasks, "_client", lambda: called.__setitem__("n", called["n"] + 1))

    assert tasks.enqueue_import_row("abc") is False
    assert called["n"] == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest test/unit/test_enqueue_import_row.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Write minimal implementation**

Create `src/agentic_librarian/imports/tasks.py` (mirrors `enrichment/tasks.py`, separate queue env):

```python
"""Cloud Tasks enqueue for the per-row bulk-import worker (Spec 2026-06-18). One task per
importable row → POST /internal/import-row/{row_id} with the queue's OIDC token. Uses a
SEPARATE queue (IMPORT_TASKS_QUEUE) so an import burst can't starve interactive deep-enrich.
Reuses the enrich path's base-URL / SA / OIDC-audience env."""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)


def _client():
    from google.cloud import tasks_v2

    return tasks_v2.CloudTasksClient()


def enqueue_import_row(row_id: str) -> bool:
    """Enqueue the worker task for row_id. Returns False (logged no-op) when Cloud Tasks is
    unconfigured (local dev) — commit still succeeds; the row stays 'pending'."""
    queue = os.environ.get("IMPORT_TASKS_QUEUE")
    base = os.environ.get("ENRICH_TARGET_BASE_URL")
    sa = os.environ.get("ENRICH_INVOKER_SA")
    if not (queue and base and sa):
        logger.info("import-row enqueue skipped — Cloud Tasks not configured (row %s)", row_id)
        return False

    url = f"{base.rstrip('/')}/internal/import-row/{row_id}"
    audience = os.environ.get("ENRICH_OIDC_AUDIENCE") or url
    task = {
        "http_request": {
            "http_method": "POST",
            "url": url,
            "oidc_token": {"service_account_email": sa, "audience": audience},
        }
    }
    _client().create_task(parent=queue, task=task)
    logger.info("enqueued import-row worker for row %s", row_id)
    return True
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest test/unit/test_enqueue_import_row.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/agentic_librarian/imports/tasks.py test/unit/test_enqueue_import_row.py
git commit -m "feat(import): enqueue_import_row (dedicated queue)"
```

---

## Task 7: `worker.py` — the per-row pipeline

**Files:**
- Create: `src/agentic_librarian/imports/worker.py`
- Test: `test/integration/test_import_worker.py`

- [ ] **Step 1: Write the failing test**

Create `test/integration/test_import_worker.py`:

```python
"""Per-row import worker: de-dup, shallow miss, not_found, redelivery, suggestion routing,
re-import idempotency (Spec 2026-06-18)."""

from datetime import date
from uuid import uuid4

import pytest

from agentic_librarian.core.user_context import DEFAULT_USER_ID
from agentic_librarian.db.models import (
    Author, Edition, ImportJob, ImportRow, ReadingHistory, Suggestions, Work, WorkContributor,
)
from agentic_librarian.db.session import DatabaseManager
from agentic_librarian.enrichment import two_phase
from agentic_librarian.imports import worker

pytestmark = pytest.mark.db_integration


@pytest.fixture()
def wired(db_url, monkeypatch):
    manager = DatabaseManager(db_url)
    monkeypatch.setattr(worker, "db_manager", manager)
    two_phase.set_db_manager(manager)
    # Never enqueue real Cloud Tasks from the worker in tests.
    monkeypatch.setattr(worker, "enqueue_enrichment", lambda work_id: True)
    return manager


def _seed_work(manager, title="Dune", author="Frank Herbert"):
    with manager.get_session() as s:
        a = Author(name=author)
        w = Work(title=title, contributors=[WorkContributor(author=a, role="Author")])
        e = Edition(work=w, format="ebook")
        s.add_all([a, w, e])
        s.flush()
        return w.id


def _make_row(manager, **kw):
    with manager.get_session() as s:
        job = ImportJob(user_id=DEFAULT_USER_ID, source="goodreads", total_rows=1)
        s.add(job)
        s.flush()
        defaults = dict(
            import_job_id=job.id, user_id=DEFAULT_USER_ID, raw_title="Dune",
            raw_author="Frank Herbert", raw_format="ebook", raw_date="2024-01-01",
            date_completed=date(2024, 1, 1), destination="history", shelf="read", status="pending",
        )
        defaults.update(kw)
        row = ImportRow(**defaults)
        s.add(row)
        s.flush()
        return row.id


def test_dedup_links_existing_catalog_work_without_scouts(wired, monkeypatch):
    _seed_work(wired)
    # If a scout were called, this would blow up — proving de-dup short-circuits.
    monkeypatch.setattr(two_phase, "_scout_and_persist", lambda *a, **k: pytest.fail("scouts ran on a de-dup hit"))
    row_id = _make_row(wired)

    assert worker.process_import_row(row_id) == "done"
    with wired.get_session() as s:
        row = s.get(ImportRow, row_id)
        assert row.status == "done"
        assert row.outcome == "linked"
        assert s.query(ReadingHistory).filter_by(user_id=DEFAULT_USER_ID).count() == 1


def test_miss_creates_work_and_enqueues_deep(wired, monkeypatch):
    # Fake the fast scouts so a miss resolves to a created Work deterministically.
    monkeypatch.setattr(two_phase, "enrich_fast", lambda t, a, f="ebook": (_seed_work(wired, t, a), True))
    enq = {"n": 0}
    monkeypatch.setattr(worker, "enqueue_enrichment", lambda work_id: enq.__setitem__("n", enq["n"] + 1) or True)
    row_id = _make_row(wired, raw_title="New Title", raw_author="New Author")

    assert worker.process_import_row(row_id) == "done"
    with wired.get_session() as s:
        assert s.get(ImportRow, row_id).outcome == "created"
    assert enq["n"] == 1


def test_not_found_marks_failed_and_does_not_retry(wired, monkeypatch):
    monkeypatch.setattr(two_phase, "enrich_fast", lambda t, a, f="ebook": None)
    row_id = _make_row(wired, raw_title="Ghost", raw_author="Nobody")

    assert worker.process_import_row(row_id) == "not_found"
    with wired.get_session() as s:
        row = s.get(ImportRow, row_id)
        assert row.status == "failed"
        assert row.outcome == "not_found"


def test_redelivery_of_done_row_is_a_noop(wired):
    _seed_work(wired)
    row_id = _make_row(wired)
    worker.process_import_row(row_id)
    # Second delivery must not add a second history row.
    assert worker.process_import_row(row_id) == "done"
    with wired.get_session() as s:
        assert s.query(ReadingHistory).filter_by(user_id=DEFAULT_USER_ID).count() == 1


def test_suggestion_routing_writes_suggestion_with_context(wired):
    work_id = _seed_work(wired, "Wishlist Book", "W Author")  # de-dup hit resolves the work_id
    row_id = _make_row(
        wired, raw_title="Wishlist Book", raw_author="W Author", destination="suggestion",
        shelf="to-read", date_completed=None, raw_date="",
    )
    assert worker.process_import_row(row_id) == "done"
    with wired.get_session() as s:
        sug = s.query(Suggestions).filter_by(user_id=DEFAULT_USER_ID).one()
        assert sug.work_id == work_id
        assert sug.status == "Suggested"
        assert sug.context == "imported:to-read"


def test_reimport_does_not_duplicate(wired):
    _seed_work(wired)
    r1 = _make_row(wired)
    r2 = _make_row(wired)  # same book + date, a re-import
    worker.process_import_row(r1)
    worker.process_import_row(r2)
    with wired.get_session() as s:
        assert s.query(ReadingHistory).filter_by(user_id=DEFAULT_USER_ID).count() == 1
        assert s.get(ImportRow, r2).outcome == "duplicate"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest test/integration/test_import_worker.py -v`
Expected: FAIL with `ModuleNotFoundError: ...imports.worker` (or SKIP without a DB).

- [ ] **Step 3: Write minimal implementation**

Create `src/agentic_librarian/imports/worker.py`:

```python
"""Per-row bulk-import worker (Spec 2026-06-18). The ONLY place de-dup/shallow/route/
queue-deep happens. Keyed by import_row_id; status is the idempotency boundary.

Returns 'done' or 'not_found'. Raises LookupError when the row is gone (→ 404, non-retryable).
Any other exception propagates (→ 5xx → Cloud Tasks redelivery), with error_detail recorded
and the row left in 'processing' so the stalled-row retry can recover it."""

from __future__ import annotations

import logging
from uuid import UUID

from agentic_librarian.core.user_context import as_user
from agentic_librarian.db.models import ImportRow, Suggestions
from agentic_librarian.db.session import DatabaseManager
from agentic_librarian.enrichment import two_phase
from agentic_librarian.enrichment.tasks import enqueue_enrichment

logger = logging.getLogger(__name__)

db_manager = DatabaseManager()


def _upsert_suggestion(session, *, work_id: UUID, user_id: UUID, context: str) -> bool:
    """Get-or-create the user's wishlist suggestion. Returns True if created, False if it
    already existed (re-import safe — mirrors add_read_event's idempotency)."""
    existing = (
        session.query(Suggestions)
        .filter_by(work_id=work_id, user_id=user_id, status="Suggested")
        .first()
    )
    if existing:
        return False
    session.add(Suggestions(work_id=work_id, user_id=user_id, status="Suggested", context=context))
    session.flush()
    return True


def _finish(row_id: UUID, *, status: str, outcome: str, work_id: UUID | None = None) -> None:
    with db_manager.get_session() as session:
        row = session.get(ImportRow, row_id)
        if row is not None:
            row.status = status
            row.outcome = outcome
            if work_id is not None:
                row.work_id = work_id


def _record_error(row_id: UUID, detail: str) -> None:
    with db_manager.get_session() as session:
        row = session.get(ImportRow, row_id)
        if row is not None:
            row.error_detail = detail[:2000]  # stays in 'processing' for the stalled-row retry


def process_import_row(row_id: UUID) -> str:
    # 1. Load + idempotency guard + claim.
    with db_manager.get_session() as session:
        row = session.get(ImportRow, row_id)
        if row is None:
            raise LookupError("import row not found")
        if row.status == "done":
            return "done"
        row.status = "processing"
        data = {
            "title": row.raw_title or "", "author": row.raw_author or "",
            "fmt": row.raw_format or "ebook", "completed": row.date_completed,
            "rating": row.rating, "notes": row.notes, "destination": row.destination,
            "shelf": row.shelf or "", "user_id": row.user_id,
        }

    # 2. Resolve the work (de-dup, else shallow scouts) OUTSIDE our session — enrich_fast
    #    owns its own session/pool.
    try:
        fast = two_phase.enrich_fast(data["title"], data["author"], data["fmt"])
    except Exception as e:  # noqa: BLE001 - transient: record + re-raise so Cloud Tasks retries
        _record_error(row_id, f"{type(e).__name__}: {e}")
        raise
    if fast is None:
        _finish(row_id, status="failed", outcome="not_found")
        return "not_found"
    work_id, created = fast

    # 3. Write to the routed destination.
    try:
        if data["destination"] == "history":
            with as_user(data["user_id"]):
                event = two_phase.add_read_event(
                    work_id, completed=data["completed"], rating=data["rating"],
                    notes=data["notes"], fmt=data["fmt"],
                )
            outcome = "duplicate" if event["already_logged"] else ("created" if created else "linked")
        else:  # suggestion
            shelf = data["shelf"]
            context = f"imported:{shelf}" if shelf in ("to-read", "currently-reading") else "imported"
            with db_manager.get_session() as session:
                is_new = _upsert_suggestion(
                    session, work_id=work_id, user_id=data["user_id"], context=context
                )
            outcome = "created" if (is_new and created) else ("linked" if is_new else "duplicate")
    except Exception as e:  # noqa: BLE001 - transient: record + re-raise for retry
        _record_error(row_id, f"{type(e).__name__}: {e}")
        raise

    # 4. Queue the deep pass only for newly-created works (best-effort).
    if created:
        try:
            enqueue_enrichment(str(work_id))
        except Exception:  # noqa: BLE001 - deep pass can be retried later; never fail the row
            logger.exception("deep-enrichment enqueue failed for work %s", work_id)

    _finish(row_id, status="done", outcome=outcome, work_id=work_id)
    return "done"
```

Note for the test: `test_suggestion_routing_writes_suggestion_with_context` resolves via a de-dup hit (the seeded work), so `enrich_fast` runs for real against the test DB — no scout stub needed.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest test/integration/test_import_worker.py -v`
Expected: PASS (or SKIP without a DB).

- [ ] **Step 5: Commit**

```bash
git add src/agentic_librarian/imports/worker.py test/integration/test_import_worker.py
git commit -m "feat(import): per-row worker (de-dup/shallow/route/queue-deep)"
```

---

## Task 8: `/internal/import-row/{row_id}` endpoint

**Files:**
- Modify: `src/agentic_librarian/api/internal.py`
- Test: `test/unit/test_internal_import_row.py`

- [ ] **Step 1: Write the failing test**

Create `test/unit/test_internal_import_row.py`:

```python
from uuid import uuid4

from fastapi.testclient import TestClient

from agentic_librarian.api import internal as internal_mod
from agentic_librarian.api.main import app

client = TestClient(app)


def test_rejects_caller_without_bearer_token():
    r = client.post(f"/internal/import-row/{uuid4()}")
    assert r.status_code == 401


def test_invokes_worker_when_oidc_passes(monkeypatch):
    # Bypass the OIDC gate (its own dedicated tests cover verification).
    monkeypatch.setattr(internal_mod, "_require_queue_caller", lambda authorization: None)
    seen = {}
    monkeypatch.setattr(
        "agentic_librarian.imports.worker.process_import_row",
        lambda row_id: seen.setdefault("row_id", row_id) or "done",
    )
    rid = uuid4()
    r = client.post(f"/internal/import-row/{rid}", headers={"Authorization": "Bearer x"})
    assert r.status_code == 200
    assert r.json() == {"row_id": str(rid), "result": "done"}
    assert str(seen["row_id"]) == str(rid)


def test_missing_row_is_404(monkeypatch):
    monkeypatch.setattr(internal_mod, "_require_queue_caller", lambda authorization: None)

    def _raise(row_id):
        raise LookupError

    monkeypatch.setattr("agentic_librarian.imports.worker.process_import_row", _raise)
    r = client.post(f"/internal/import-row/{uuid4()}", headers={"Authorization": "Bearer x"})
    assert r.status_code == 404
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest test/unit/test_internal_import_row.py -v`
Expected: FAIL (404 route not found → assertion failures).

- [ ] **Step 3: Write minimal implementation**

In `src/agentic_librarian/api/internal.py`, append a new route (keep the existing `/internal/enrich`):

```python
@router.post("/internal/import-row/{row_id}")
def import_row(row_id: UUID, authorization: str | None = Header(None)):  # noqa: B008
    _require_queue_caller(authorization)
    from agentic_librarian.imports import worker

    try:
        result = worker.process_import_row(row_id)
    except LookupError as e:
        # Non-retryable: the row is gone. 404 stops Cloud Tasks from retrying.
        raise HTTPException(status_code=404, detail="import row not found") from e
    return {"row_id": str(row_id), "result": result}
```

(`UUID`, `Header`, `HTTPException` are already imported in this module.)

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest test/unit/test_internal_import_row.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/agentic_librarian/api/internal.py test/unit/test_internal_import_row.py
git commit -m "feat(import): /internal/import-row worker endpoint"
```

---

## Task 9: `POST /import/preview`

**Files:**
- Create: `src/agentic_librarian/api/imports.py`
- Test: `test/unit/test_api_import_preview.py`

- [ ] **Step 1: Write the failing test**

Create `test/unit/test_api_import_preview.py`:

```python
import io

import pytest
from fastapi.testclient import TestClient

from agentic_librarian.api.auth import AuthenticatedUser, get_current_user
from agentic_librarian.api.imports import router
from agentic_librarian.core.user_context import DEFAULT_USER_EMAIL, DEFAULT_USER_ID
from fastapi import FastAPI

app = FastAPI()
app.include_router(router)
app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(id=DEFAULT_USER_ID, email=DEFAULT_USER_EMAIL)
client = TestClient(app)

GOODREADS_CSV = (
    "Book Id,Title,Author,My Rating,Binding,Date Read,Exclusive Shelf,My Review\n"
    "1,Dune,Frank Herbert,5,Kindle Edition,2024/03/05,read,loved it\n"
    "2,Hyperion,Dan Simmons,0,Audiobook,,to-read,\n"
)


def _upload(csv_text):
    return client.post("/import/preview", files={"file": ("export.csv", io.BytesIO(csv_text.encode()), "text/csv")})


def test_preview_detects_goodreads_and_suggests_mapping():
    r = _upload(GOODREADS_CSV)
    assert r.status_code == 200
    body = r.json()
    assert body["source"] == "goodreads"
    assert body["suggested_mapping"]["title"] == "Title"
    assert body["counts"]["read_dated"] == 1
    assert body["counts"]["to_read"] == 1
    assert len(body["preview_rows"]) == 2


def test_preview_rejects_empty_file():
    r = _upload("")
    assert r.status_code == 422
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest test/unit/test_api_import_preview.py -v`
Expected: FAIL with `ModuleNotFoundError: ...api.imports`.

- [ ] **Step 3: Write minimal implementation**

Create `src/agentic_librarian/api/imports.py`:

```python
"""Bulk reading-history import API (Spec 2026-06-18). Stateless preview/commit (the client
re-uploads the small CSV); per-row Cloud Tasks do the work. Firebase-gated like books.py."""

from __future__ import annotations

import csv
import io
import json
import logging

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from agentic_librarian.api.auth import AuthenticatedUser, get_current_user
from agentic_librarian.imports import bucketing, parsing

logger = logging.getLogger(__name__)
router = APIRouter()

MAX_ROWS = 2000


def _read_csv(raw: bytes) -> tuple[list[str], list[dict]]:
    text = raw.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    headers = reader.fieldnames or []
    rows = [r for r in reader]
    if not headers or not rows:
        raise HTTPException(status_code=422, detail="The file has no data rows.")
    return list(headers), rows


def _counts(parsed: list[parsing.ParsedRow]) -> dict:
    c = {"read_dated": 0, "read_undated": 0, "to_read": 0, "currently_reading": 0, "total": len(parsed)}
    for p in parsed:
        if p.shelf == "to-read":
            c["to_read"] += 1
        elif p.shelf == "currently-reading":
            c["currently_reading"] += 1
        elif p.date_completed is not None:
            c["read_dated"] += 1
        else:
            c["read_undated"] += 1
    return c


def _preview_row(p: parsing.ParsedRow) -> dict:
    return {
        "title": p.raw_title, "author": p.raw_author, "format": p.raw_format,
        "date_completed": p.date_completed.isoformat() if p.date_completed else None,
        "rating": p.rating, "shelf": p.shelf,
    }


@router.post("/import/preview")
async def preview(
    file: UploadFile = File(...),  # noqa: B008
    mapping: str | None = Form(None),  # noqa: B008 - JSON override when the user edits the map
    user: AuthenticatedUser = Depends(get_current_user),  # noqa: B008
):
    headers, rows = _read_csv(await file.read())
    source = parsing.sniff_source(headers)
    suggested = parsing.suggest_mapping(headers, source)
    effective = json.loads(mapping) if mapping else suggested
    parsed = parsing.parse_rows(rows, effective)
    return {
        "source": source,
        "headers": headers,
        "suggested_mapping": suggested,
        "preview_rows": [_preview_row(p) for p in parsed[:5]],
        "counts": _counts(parsed),
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest test/unit/test_api_import_preview.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/agentic_librarian/api/imports.py test/unit/test_api_import_preview.py
git commit -m "feat(import): POST /import/preview"
```

---

## Task 10: `POST /import/commit`

**Files:**
- Modify: `src/agentic_librarian/api/imports.py`
- Test: `test/unit/test_api_import_commit.py`

- [ ] **Step 1: Write the failing test**

Create `test/unit/test_api_import_commit.py`:

```python
import io
import json

from fastapi import FastAPI
from fastapi.testclient import TestClient

from agentic_librarian.api import imports as imports_mod
from agentic_librarian.api.auth import AuthenticatedUser, get_current_user
from agentic_librarian.api.imports import router
from agentic_librarian.core.user_context import DEFAULT_USER_EMAIL, DEFAULT_USER_ID

app = FastAPI()
app.include_router(router)
app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(id=DEFAULT_USER_ID, email=DEFAULT_USER_EMAIL)
client = TestClient(app)

CSV = (
    "Book Id,Title,Author,My Rating,Binding,Date Read,Exclusive Shelf,My Review\n"
    "1,Dune,Frank Herbert,5,Kindle Edition,2024/03/05,read,loved it\n"          # history
    "2,Hyperion,Dan Simmons,0,Audiobook,,to-read,\n"                            # suggestion (opt-in)
    "3,Blank,No Date,0,Paperback,,read,\n"                                      # skip (no date)
)


class _Recorder:
    def __init__(self):
        self.jobs = []
        self.rows = []

    def add(self, obj):
        name = type(obj).__name__
        (self.jobs if name == "ImportJob" else self.rows).append(obj)

    # the rest of the Session protocol the endpoint uses:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def flush(self):
        for j in self.jobs:
            if getattr(j, "id", None) is None:
                from uuid import uuid4
                j.id = uuid4()
        for r in self.rows:
            if getattr(r, "id", None) is None:
                from uuid import uuid4
                r.id = uuid4()


def _commit(monkeypatch, *, to_read=True):
    rec = _Recorder()
    monkeypatch.setattr(imports_mod, "db_manager", _fake_manager(rec))
    enq = []
    monkeypatch.setattr(imports_mod, "enqueue_import_row", lambda row_id: enq.append(row_id) or True)
    r = client.post(
        "/import/commit",
        files={"file": ("export.csv", io.BytesIO(CSV.encode()), "text/csv")},
        data={"mapping": json.dumps(_GOODREADS_MAP), "import_to_read": str(to_read).lower(),
              "import_currently_reading": "true", "original_filename": "export.csv"},
    )
    return r, rec, enq


_GOODREADS_MAP = {
    "title": "Title", "author": "Author", "format": "Binding", "date_completed": "Date Read",
    "rating": "My Rating", "notes": "My Review", "shelf": "Exclusive Shelf",
}


def _fake_manager(rec):
    class _M:
        def get_session(self):
            return rec

    return _M()


def test_commit_writes_rows_and_enqueues_only_non_skip(monkeypatch):
    r, rec, enq = _commit(monkeypatch)
    assert r.status_code == 200
    job = rec.jobs[0]
    assert job.total_rows == 3
    dests = sorted(row.destination for row in rec.rows)
    assert dests == ["history", "skip", "suggestion"]
    # Exactly the two non-skip rows were enqueued.
    assert len(enq) == 2
    assert r.json()["import_job_id"] == str(job.id)


def test_commit_422_when_required_mapping_missing(monkeypatch):
    monkeypatch.setattr(imports_mod, "db_manager", _fake_manager(_Recorder()))
    bad = dict(_GOODREADS_MAP, date_completed=None)
    r = client.post(
        "/import/commit",
        files={"file": ("export.csv", io.BytesIO(CSV.encode()), "text/csv")},
        data={"mapping": json.dumps(bad), "import_to_read": "true", "import_currently_reading": "true"},
    )
    assert r.status_code == 422
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest test/unit/test_api_import_commit.py -v`
Expected: FAIL (no `/import/commit` route; `db_manager`/`enqueue_import_row` attrs missing).

- [ ] **Step 3: Write minimal implementation**

In `src/agentic_librarian/api/imports.py`, add the imports and the route. At the top, extend imports:

```python
from agentic_librarian.db.models import ImportJob, ImportRow
from agentic_librarian.db.session import DatabaseManager
from agentic_librarian.imports.tasks import enqueue_import_row

db_manager = DatabaseManager()


def set_db_manager(new_manager: DatabaseManager) -> None:
    """Override the module db_manager (tests / shared-pool lifespan) — mcp/server.py pattern."""
    global db_manager
    db_manager = new_manager


_REQUIRED_FIELDS = ("title", "author", "date_completed")
```

Then the route:

```python
@router.post("/import/commit")
async def commit(
    file: UploadFile = File(...),  # noqa: B008
    mapping: str = Form(...),  # noqa: B008
    import_to_read: bool = Form(False),  # noqa: B008
    import_currently_reading: bool = Form(False),  # noqa: B008
    original_filename: str | None = Form(None),  # noqa: B008
    user: AuthenticatedUser = Depends(get_current_user),  # noqa: B008
):
    parsed_mapping = json.loads(mapping)
    missing = [f for f in _REQUIRED_FIELDS if not parsed_mapping.get(f)]
    if missing:
        raise HTTPException(status_code=422, detail=f"Missing required column mapping: {', '.join(missing)}")

    headers, rows = _read_csv(await file.read())
    if len(rows) > MAX_ROWS:
        raise HTTPException(status_code=422, detail=f"File has {len(rows)} rows; the limit is {MAX_ROWS}.")

    source = parsing.sniff_source(headers)
    parsed = parsing.parse_rows(rows, parsed_mapping)

    enqueue_ids: list[str] = []
    with db_manager.get_session() as session:
        job = ImportJob(
            user_id=user.id, source=source, original_filename=original_filename, total_rows=len(parsed)
        )
        session.add(job)
        session.flush()
        for p in parsed:
            destination, skip_reason = bucketing.bucket(
                p, import_to_read=import_to_read, import_currently_reading=import_currently_reading
            )
            row = ImportRow(
                import_job_id=job.id, user_id=user.id,
                raw_title=p.raw_title, raw_author=p.raw_author, raw_format=p.raw_format,
                raw_date=p.raw_date, date_completed=p.date_completed if destination == "history" else None,
                rating=p.rating, notes=p.notes, destination=destination, shelf=p.shelf,
                status="skipped" if destination == "skip" else "pending",
                skip_reason=skip_reason,
            )
            session.add(row)
            session.flush()
            if destination != "skip":
                enqueue_ids.append(str(row.id))
        job_id = str(job.id)

    for rid in enqueue_ids:
        try:
            enqueue_import_row(rid)
        except Exception:  # noqa: BLE001 - a failed enqueue leaves the row 'pending' for retry
            logger.exception("import-row enqueue failed for row %s", rid)

    return {"import_job_id": job_id, "total_rows": len(parsed), "enqueued": len(enqueue_ids)}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest test/unit/test_api_import_commit.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/agentic_librarian/api/imports.py test/unit/test_api_import_commit.py
git commit -m "feat(import): POST /import/commit (write rows + enqueue)"
```

---

## Task 11: `GET /import/{job_id}` (derived progress) + `POST /import/{job_id}/retry`

**Files:**
- Modify: `src/agentic_librarian/api/imports.py`
- Test: `test/integration/test_api_import_status.py`

- [ ] **Step 1: Write the failing test**

Create `test/integration/test_api_import_status.py`:

```python
"""Derived progress + retry, scoped to the owner (Spec 2026-06-18, ADR-048)."""

from datetime import date

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agentic_librarian.api import imports as imports_mod
from agentic_librarian.api.auth import AuthenticatedUser, get_current_user
from agentic_librarian.api.imports import router
from agentic_librarian.core.user_context import DEFAULT_USER_EMAIL, DEFAULT_USER_ID
from agentic_librarian.db.models import ImportJob, ImportRow
from agentic_librarian.db.session import DatabaseManager

pytestmark = pytest.mark.db_integration


@pytest.fixture()
def client(db_url, monkeypatch):
    manager = DatabaseManager(db_url)
    monkeypatch.setattr(imports_mod, "db_manager", manager)
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(id=DEFAULT_USER_ID, email=DEFAULT_USER_EMAIL)
    return TestClient(app), manager


def _seed_job(manager, statuses):
    with manager.get_session() as s:
        job = ImportJob(user_id=DEFAULT_USER_ID, source="goodreads", total_rows=len(statuses))
        s.add(job)
        s.flush()
        for st in statuses:
            s.add(ImportRow(import_job_id=job.id, user_id=DEFAULT_USER_ID, destination="history",
                            status=st, outcome=("linked" if st == "done" else None),
                            date_completed=date(2024, 1, 1)))
        s.flush()
        return job.id


def test_progress_is_derived_from_rows(client):
    c, manager = client
    job_id = _seed_job(manager, ["done", "done", "failed", "pending"])
    body = c.get(f"/import/{job_id}").json()
    assert body["total_rows"] == 4
    assert body["counts"]["done"] == 2
    assert body["counts"]["failed"] == 1
    assert body["counts"]["pending"] == 1
    assert body["complete"] is False  # a pending row remains


def test_retry_re_enqueues_failed_rows(client, monkeypatch):
    c, manager = client
    job_id = _seed_job(manager, ["failed", "done"])
    enq = []
    monkeypatch.setattr(imports_mod, "enqueue_import_row", lambda rid: enq.append(rid) or True)
    r = c.post(f"/import/{job_id}/retry")
    assert r.status_code == 200
    assert len(enq) == 1  # only the failed row
    body = c.get(f"/import/{job_id}").json()
    assert body["counts"].get("failed", 0) == 0
    assert body["counts"]["pending"] == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest test/integration/test_api_import_status.py -v`
Expected: FAIL (routes missing) or SKIP without a DB.

- [ ] **Step 3: Write minimal implementation**

In `src/agentic_librarian/api/imports.py`, add imports and the two routes. `ImportJob`/`ImportRow` were already imported in Task 10 — do **not** re-import them. Add only:

```python
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import func

STALLED_AFTER = timedelta(minutes=15)
```

```python
def _load_owned_job(session, job_id, user_id):
    job = session.get(ImportJob, job_id)
    if job is None or job.user_id != user_id:
        raise HTTPException(status_code=404, detail="import job not found")
    return job


@router.get("/import/{job_id}")
def get_status(job_id: UUID, user: AuthenticatedUser = Depends(get_current_user)):  # noqa: B008
    with db_manager.get_session() as session:
        job = _load_owned_job(session, job_id, user.id)
        counts = dict(
            session.query(ImportRow.status, func.count())
            .filter(ImportRow.import_job_id == job_id)
            .group_by(ImportRow.status)
            .all()
        )
        outcomes = dict(
            session.query(ImportRow.outcome, func.count())
            .filter(ImportRow.import_job_id == job_id, ImportRow.outcome.isnot(None))
            .group_by(ImportRow.outcome)
            .all()
        )
        report = [
            {"title": r.raw_title, "author": r.raw_author, "status": r.status,
             "outcome": r.outcome, "skip_reason": r.skip_reason, "error": r.error_detail}
            for r in session.query(ImportRow)
            .filter(ImportRow.import_job_id == job_id, ImportRow.status.in_(("failed", "skipped")))
            .all()
        ]
        active = counts.get("pending", 0) + counts.get("processing", 0)
        return {
            "import_job_id": str(job_id),
            "source": job.source,
            "total_rows": job.total_rows,
            "counts": counts,
            "outcomes": outcomes,
            "complete": active == 0,
            "report": report,
        }


@router.post("/import/{job_id}/retry")
def retry(job_id: UUID, user: AuthenticatedUser = Depends(get_current_user)):  # noqa: B008
    cutoff = datetime.now(UTC) - STALLED_AFTER
    retry_ids: list[str] = []
    with db_manager.get_session() as session:
        _load_owned_job(session, job_id, user.id)
        rows = (
            session.query(ImportRow)
            .filter(
                ImportRow.import_job_id == job_id,
                (ImportRow.status == "failed")
                | ((ImportRow.status == "processing") & (ImportRow.updated_at < cutoff)),
            )
            .all()
        )
        for row in rows:
            row.status = "pending"
            row.error_detail = None
            retry_ids.append(str(row.id))

    for rid in retry_ids:
        try:
            enqueue_import_row(rid)
        except Exception:  # noqa: BLE001
            logger.exception("retry enqueue failed for row %s", rid)
    return {"retried": len(retry_ids)}
```

Note: `failed` here is the intentional "not retryable at the queue level, but the user explicitly asked to retry" path — re-queueing a `not_found` row will simply fail again unless the catalog has since changed, which is acceptable.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest test/integration/test_api_import_status.py -v`
Expected: PASS (or SKIP without a DB).

- [ ] **Step 5: Commit**

```bash
git add src/agentic_librarian/api/imports.py test/integration/test_api_import_status.py
git commit -m "feat(import): GET /import/{id} status + POST retry"
```

---

## Task 12: Wire the import router into the app

**Files:**
- Modify: `src/agentic_librarian/api/main.py`
- Test: `test/unit/test_api_import_routes_wired.py`

- [ ] **Step 1: Write the failing test**

Create `test/unit/test_api_import_routes_wired.py`:

```python
from agentic_librarian.api.main import app


def test_import_routes_are_registered():
    paths = {route.path for route in app.routes}
    assert "/import/preview" in paths
    assert "/import/commit" in paths
    assert "/import/{job_id}" in paths
    assert "/internal/import-row/{row_id}" in paths
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest test/unit/test_api_import_routes_wired.py -v`
Expected: FAIL (`/import/preview` not in paths).

- [ ] **Step 3: Write minimal implementation**

In `src/agentic_librarian/api/main.py`, add the import near the other router imports (line ~16):

```python
from agentic_librarian.api.imports import router as imports_router
```

And register it alongside the others (after line ~68):

```python
app.include_router(imports_router)
```

Then wire its pool into `lifespan` (so it uses the shared `DatabaseManager`, matching the other modules). Add an import `from agentic_librarian.api import imports as imports_api` at the top, and inside `lifespan` after `analysis.set_db_manager(shared)`:

```python
    imports_api.set_db_manager(shared)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest test/unit/test_api_import_routes_wired.py -v`
Expected: PASS.

Also run the broader API suite to confirm nothing regressed:
Run: `uv run pytest test/unit/test_api_import_preview.py test/unit/test_api_import_commit.py test/unit/test_internal_import_row.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/agentic_librarian/api/main.py test/unit/test_api_import_routes_wired.py
git commit -m "feat(import): wire import router + shared pool"
```

---

## Task 13: Frontend API client

**Files:**
- Modify: `frontend/src/api/client.ts`
- Test: `frontend/src/api/client.import.test.ts`

- [ ] **Step 1: Write the failing test**

Create `frontend/src/api/client.import.test.ts`:

```ts
import { afterEach, describe, expect, it, vi } from 'vitest'
import { commitImport, getImportJob, previewImport, retryImport } from './client'

vi.mock('../auth/firebase', () => ({ getIdToken: async () => 'tok' }))

afterEach(() => vi.restoreAllMocks())

function mockFetch(body: unknown, ok = true) {
  return vi.spyOn(globalThis, 'fetch').mockResolvedValue({
    ok,
    status: ok ? 200 : 422,
    json: async () => body,
  } as Response)
}

describe('import client', () => {
  it('previewImport posts the file as multipart', async () => {
    const f = mockFetch({ source: 'goodreads', counts: { total: 1 } })
    const file = new File(['Title\nDune'], 'export.csv', { type: 'text/csv' })
    const res = await previewImport(file)
    expect(res.source).toBe('goodreads')
    const [path, init] = f.mock.calls[0]
    expect(path).toBe('/import/preview')
    expect((init as RequestInit).method).toBe('POST')
    expect((init as RequestInit).body).toBeInstanceOf(FormData)
  })

  it('commitImport sends mapping + opt-ins', async () => {
    const f = mockFetch({ import_job_id: 'j1', total_rows: 3, enqueued: 2 })
    const file = new File(['x'], 'export.csv', { type: 'text/csv' })
    const res = await commitImport(file, { title: 'Title' }, { importToRead: true, importCurrentlyReading: false })
    expect(res.import_job_id).toBe('j1')
    const body = (f.mock.calls[0][1] as RequestInit).body as FormData
    expect(body.get('import_to_read')).toBe('true')
    expect(body.get('import_currently_reading')).toBe('false')
  })

  it('getImportJob fetches status', async () => {
    mockFetch({ import_job_id: 'j1', complete: true, counts: { done: 3 } })
    const res = await getImportJob('j1')
    expect(res.complete).toBe(true)
  })

  it('retryImport posts to the retry route', async () => {
    const f = mockFetch({ retried: 2 })
    await retryImport('j1')
    expect(f.mock.calls[0][0]).toBe('/import/j1/retry')
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run (from `frontend/`): `npx vitest run src/api/client.import.test.ts`
Expected: FAIL (`commitImport` etc. not exported).

- [ ] **Step 3: Write minimal implementation**

Append to `frontend/src/api/client.ts`:

```ts
export type ColumnMapping = Partial<Record<
  'title' | 'author' | 'format' | 'date_completed' | 'rating' | 'notes' | 'shelf',
  string | null
>>

export interface ImportPreview {
  source: 'goodreads' | 'generic'
  headers: string[]
  suggested_mapping: ColumnMapping
  preview_rows: Array<{
    title: string; author: string; format: string
    date_completed: string | null; rating: number | null; shelf: string
  }>
  counts: { read_dated: number; read_undated: number; to_read: number; currently_reading: number; total: number }
}

export interface ImportCommitResult {
  import_job_id: string
  total_rows: number
  enqueued: number
}

export interface ImportStatus {
  import_job_id: string
  source: string
  total_rows: number
  counts: Record<string, number>
  outcomes: Record<string, number>
  complete: boolean
  report: Array<{
    title: string | null; author: string | null; status: string
    outcome: string | null; skip_reason: string | null; error: string | null
  }>
}

export async function previewImport(file: File, mapping?: ColumnMapping): Promise<ImportPreview> {
  const form = new FormData()
  form.set('file', file)
  if (mapping) form.set('mapping', JSON.stringify(mapping))
  const res = await authedFetchRaw('/import/preview', { method: 'POST', body: form })
  if (!res.ok) throw new Error(`preview import → ${res.status}`)
  return res.json() as Promise<ImportPreview>
}

export async function commitImport(
  file: File,
  mapping: ColumnMapping,
  opts: { importToRead: boolean; importCurrentlyReading: boolean },
): Promise<ImportCommitResult> {
  const form = new FormData()
  form.set('file', file)
  form.set('mapping', JSON.stringify(mapping))
  form.set('import_to_read', String(opts.importToRead))
  form.set('import_currently_reading', String(opts.importCurrentlyReading))
  form.set('original_filename', file.name)
  const res = await authedFetchRaw('/import/commit', { method: 'POST', body: form })
  if (!res.ok) throw new Error(`commit import → ${res.status}`)
  return res.json() as Promise<ImportCommitResult>
}

export function getImportJob(jobId: string): Promise<ImportStatus> {
  return getJson<ImportStatus>(`/import/${jobId}`)
}

export async function retryImport(jobId: string): Promise<{ retried: number }> {
  const res = await authedFetchRaw(`/import/${jobId}/retry`, { method: 'POST' })
  if (!res.ok) throw new Error(`retry import → ${res.status}`)
  return res.json() as Promise<{ retried: number }>
}
```

- [ ] **Step 4: Run test to verify it passes**

Run (from `frontend/`): `npx vitest run src/api/client.import.test.ts`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/api/client.ts frontend/src/api/client.import.test.ts
git commit -m "feat(import): frontend API client functions"
```

---

## Task 14: Frontend import wizard view

**Files:**
- Create: `frontend/src/views/ImportView.tsx`, `frontend/src/views/ImportView.css`
- Test: `frontend/src/views/ImportView.test.tsx`

- [ ] **Step 1: Write the failing test**

Create `frontend/src/views/ImportView.test.tsx`:

```tsx
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import ImportView from './ImportView'
import * as client from '../api/client'

afterEach(() => vi.restoreAllMocks())

const PREVIEW: client.ImportPreview = {
  source: 'goodreads',
  headers: ['Title', 'Author'],
  suggested_mapping: { title: 'Title', author: 'Author', date_completed: 'Date Read' },
  preview_rows: [{ title: 'Dune', author: 'Frank Herbert', format: 'ebook', date_completed: '2024-03-05', rating: 5, shelf: 'read' }],
  counts: { read_dated: 1, read_undated: 0, to_read: 1, currently_reading: 0, total: 2 },
}

function uploadFile() {
  const input = screen.getByTestId('import-file') as HTMLInputElement
  const file = new File(['Title,Author\nDune,Frank Herbert'], 'export.csv', { type: 'text/csv' })
  fireEvent.change(input, { target: { files: [file] } })
}

describe('ImportView', () => {
  it('previews after upload and advances to mapping', async () => {
    vi.spyOn(client, 'previewImport').mockResolvedValue(PREVIEW)
    render(<ImportView />)
    uploadFile()
    await waitFor(() => expect(screen.getByText(/Detected: goodreads/i)).toBeInTheDocument())
    expect(screen.getByText(/1 read/i)).toBeInTheDocument()
  })

  it('commits and then polls status to completion', async () => {
    vi.spyOn(client, 'previewImport').mockResolvedValue(PREVIEW)
    vi.spyOn(client, 'commitImport').mockResolvedValue({ import_job_id: 'j1', total_rows: 2, enqueued: 2 })
    vi.spyOn(client, 'getImportJob').mockResolvedValue({
      import_job_id: 'j1', source: 'goodreads', total_rows: 2,
      counts: { done: 2 }, outcomes: { linked: 2 }, complete: true, report: [],
    })
    render(<ImportView />)
    uploadFile()
    await screen.findByText(/Detected: goodreads/i)
    fireEvent.click(screen.getByRole('button', { name: /continue/i }))     // map → review
    fireEvent.click(screen.getByRole('button', { name: /start import/i })) // review → progress
    await waitFor(() => expect(screen.getByText(/2 \/ 2/)).toBeInTheDocument())
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run (from `frontend/`): `npx vitest run src/views/ImportView.test.tsx`
Expected: FAIL (`ImportView` module missing).

- [ ] **Step 3: Write minimal implementation**

Create `frontend/src/views/ImportView.css`:

```css
.import { max-width: 720px; margin: 0 auto; padding: 1rem; }
.import-step { margin-top: 1rem; }
.import-counts { display: flex; gap: 1rem; flex-wrap: wrap; }
.import-progress-bar { height: 12px; background: var(--surface-2, #eee); border-radius: 6px; overflow: hidden; }
.import-progress-bar > span { display: block; height: 100%; background: var(--accent, #5b8def); }
.import-error { color: var(--danger, #c0392b); }
```

Create `frontend/src/views/ImportView.tsx`:

```tsx
import { useEffect, useRef, useState } from 'react'
import {
  commitImport, getImportJob, previewImport, retryImport,
  type ColumnMapping, type ImportPreview, type ImportStatus,
} from '../api/client'
import './ImportView.css'

type Step = 'upload' | 'map' | 'review' | 'progress'
const FIELDS: Array<keyof ColumnMapping> = ['title', 'author', 'format', 'date_completed', 'rating', 'notes', 'shelf']
const REQUIRED: Array<keyof ColumnMapping> = ['title', 'author', 'date_completed']

export default function ImportView() {
  const [step, setStep] = useState<Step>('upload')
  const [file, setFile] = useState<File | null>(null)
  const [preview, setPreview] = useState<ImportPreview | null>(null)
  const [mapping, setMapping] = useState<ColumnMapping>({})
  const [toRead, setToRead] = useState(true)
  const [currently, setCurrently] = useState(true)
  const [jobId, setJobId] = useState<string | null>(null)
  const [status, setStatus] = useState<ImportStatus | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  async function onFile(f: File) {
    setFile(f)
    setBusy(true)
    setError(null)
    try {
      const p = await previewImport(f)
      setPreview(p)
      setMapping(p.suggested_mapping)
      setStep('map')
    } catch {
      setError('Could not read that file. Make sure it is a CSV with a header row.')
    } finally {
      setBusy(false)
    }
  }

  const missing = REQUIRED.filter((f) => !mapping[f])

  async function onCommit() {
    if (!file) return
    setBusy(true)
    setError(null)
    try {
      const res = await commitImport(file, mapping, { importToRead: toRead, importCurrentlyReading: currently })
      setJobId(res.import_job_id)
      setStep('progress')
    } catch {
      setError('Import could not start. Please try again.')
    } finally {
      setBusy(false)
    }
  }

  // Poll status while on the progress step until complete.
  const timer = useRef<number | null>(null)
  useEffect(() => {
    if (step !== 'progress' || !jobId) return
    let active = true
    async function tick() {
      try {
        const s = await getImportJob(jobId!)
        if (!active) return
        setStatus(s)
        if (!s.complete) timer.current = window.setTimeout(tick, 2000)
      } catch {
        if (active) timer.current = window.setTimeout(tick, 4000)
      }
    }
    tick()
    return () => {
      active = false
      if (timer.current) window.clearTimeout(timer.current)
    }
  }, [step, jobId])

  function downloadReport() {
    if (!status) return
    const header = 'title,author,status,outcome,skip_reason,error\n'
    const body = status.report
      .map((r) => [r.title, r.author, r.status, r.outcome, r.skip_reason, r.error]
        .map((v) => `"${(v ?? '').toString().replace(/"/g, '""')}"`).join(','))
      .join('\n')
    const url = URL.createObjectURL(new Blob([header + body], { type: 'text/csv' }))
    const a = document.createElement('a')
    a.href = url
    a.download = 'import-report.csv'
    a.click()
    URL.revokeObjectURL(url)
  }

  const done = (status?.counts.done ?? 0) + (status?.counts.failed ?? 0) + (status?.counts.skipped ?? 0)

  return (
    <div className="import">
      <h2>Import reading history</h2>
      {error && <p className="import-error">{error}</p>}

      {step === 'upload' && (
        <div className="import-step">
          <p>Upload a CSV — a Goodreads export, or your own with title, author and date columns.</p>
          <input
            data-testid="import-file"
            type="file"
            accept=".csv,text/csv"
            onChange={(e) => e.target.files?.[0] && onFile(e.target.files[0])}
          />
        </div>
      )}

      {step === 'map' && preview && (
        <div className="import-step">
          <p>Detected: {preview.source}</p>
          <div className="import-counts">
            <span>{preview.counts.read_dated} read</span>
            <span>{preview.counts.to_read} to-read</span>
            <span>{preview.counts.currently_reading} currently-reading</span>
          </div>
          {FIELDS.map((field) => (
            <label key={field} style={{ display: 'block' }}>
              {field}
              <select
                value={mapping[field] ?? ''}
                onChange={(e) => setMapping({ ...mapping, [field]: e.target.value || null })}
              >
                <option value="">—</option>
                {preview.headers.map((h) => <option key={h} value={h}>{h}</option>)}
              </select>
            </label>
          ))}
          <button disabled={missing.length > 0 || busy} onClick={() => setStep('review')}>Continue</button>
          {missing.length > 0 && <p className="import-error">Map required columns: {missing.join(', ')}</p>}
        </div>
      )}

      {step === 'review' && preview && (
        <div className="import-step">
          <p>{preview.counts.read_dated} books will be added to your history.</p>
          <label>
            <input type="checkbox" checked={toRead} onChange={(e) => setToRead(e.target.checked)} />
            Import {preview.counts.to_read} to-read books as wishlist
          </label>
          <label>
            <input type="checkbox" checked={currently} onChange={(e) => setCurrently(e.target.checked)} />
            Import {preview.counts.currently_reading} currently-reading as wishlist
          </label>
          <button disabled={busy} onClick={onCommit}>Start import</button>
        </div>
      )}

      {step === 'progress' && (
        <div className="import-step">
          <div className="import-progress-bar">
            <span style={{ width: `${status && status.total_rows ? (done / status.total_rows) * 100 : 0}%` }} />
          </div>
          <p>{done} / {status?.total_rows ?? '…'}</p>
          {status && (
            <ul>
              <li>✓ {status.counts.done ?? 0} imported</li>
              <li>⚠ {status.counts.failed ?? 0} failed</li>
              <li>⏭ {status.counts.skipped ?? 0} skipped</li>
            </ul>
          )}
          {status?.complete && (
            <>
              <button onClick={downloadReport}>Download report</button>
              {(status.counts.failed ?? 0) > 0 && jobId && (
                <button onClick={() => retryImport(jobId)}>Retry failed</button>
              )}
            </>
          )}
        </div>
      )}
    </div>
  )
}
```

- [ ] **Step 4: Run test to verify it passes**

Run (from `frontend/`): `npx vitest run src/views/ImportView.test.tsx`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/views/ImportView.tsx frontend/src/views/ImportView.css frontend/src/views/ImportView.test.tsx
git commit -m "feat(import): import wizard view"
```

---

## Task 15: Route + navigation link

**Files:**
- Modify: `frontend/src/App.tsx` (route + the `App.test.tsx` mock)
- Modify: `frontend/src/views/HistoryView.tsx` (link)
- Test: existing `frontend/src/App.test.tsx`

- [ ] **Step 1: Inspect current routing**

Open `frontend/src/App.tsx` and find where the other routes (e.g. `/add`, `/history`) are declared with `react-router`, and how `App.test.tsx` mocks each view (per project memory, `App.test.tsx` must `vi.mock` every view or `getAuth()` throws).

- [ ] **Step 2: Add the mock + a failing route assertion**

In `frontend/src/App.test.tsx`, add a mock beside the others:

```tsx
vi.mock('./views/ImportView', () => ({ default: () => <div>ImportView</div> }))
```

If `App.test.tsx` has a test that asserts reachable routes, add `/import`; otherwise add:

```tsx
it('renders the import route', () => {
  window.history.pushState({}, '', '/import')
  render(<App />)
  expect(screen.getByText('ImportView')).toBeInTheDocument()
})
```

- [ ] **Step 3: Run test to verify it fails**

Run (from `frontend/`): `npx vitest run src/App.test.tsx`
Expected: FAIL (no `/import` route renders `ImportView`).

- [ ] **Step 4: Add the route + nav link**

In `frontend/src/App.tsx`, import and add the route alongside the existing ones:

```tsx
import ImportView from './views/ImportView'
// ...
<Route path="/import" element={<ImportView />} />
```

In `frontend/src/views/HistoryView.tsx`, add a link near the top of the rendered history (match the existing link/button style in that file):

```tsx
<Link to="/import">Import history</Link>
```

(ensure `Link` is imported from `react-router` if not already).

- [ ] **Step 5: Run tests to verify they pass**

Run (from `frontend/`): `npx vitest run src/App.test.tsx`
Expected: PASS.

Then the full frontend suite:
Run (from `frontend/`): `npx vitest run`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/App.tsx frontend/src/App.test.tsx frontend/src/views/HistoryView.tsx
git commit -m "feat(import): route + history nav link"
```

---

## Task 16: Full-suite verification

**Files:** none (verification only)

- [ ] **Step 1: Backend suite**

Run: `uv run pytest test/unit test/integration -q`
Expected: PASS (DB-marked tests run if Postgres is reachable; otherwise SKIP). Investigate any failure before proceeding.

- [ ] **Step 2: Frontend suite + lint/build**

Run (from `frontend/`):
```bash
npx vitest run
npm run build
```
Expected: tests PASS; build succeeds.

- [ ] **Step 3: Lint (backend)**

Run: `uv run ruff check src/agentic_librarian/imports src/agentic_librarian/api/imports.py`
Expected: clean (fix any import-order/unused warnings).

- [ ] **Step 4: Commit any lint fixes**

```bash
git add -A
git commit -m "chore(import): lint + suite green"
```

---

## Rollout Notes (post-merge, operator)

These are **not** code tasks — they are the prod wiring the spec calls out (no `.tf` in the repo; the enrich queue was made via `gcloud` in Stage 4):

1. Create the import Cloud Tasks queue with a conservative dispatch rate, e.g.:
   ```bash
   gcloud tasks queues create librarian-import \
     --location=us-central1 \
     --max-dispatches-per-second=2 \
     --max-concurrent-dispatches=4
   ```
2. Set `IMPORT_TASKS_QUEUE=projects/<p>/locations/us-central1/queues/librarian-import` on the Cloud Run service (reuse `ENRICH_TARGET_BASE_URL`, `ENRICH_INVOKER_SA`, `ENRICH_OIDC_AUDIENCE`).
3. Run the migration against prod: `alembic upgrade head` (via the existing migration step).
4. Smoke: import a small (~5-row) CSV; confirm history rows appear and the deep-enrich queue drains.
```
