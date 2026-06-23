# Author/Narrator Dedup & Trope Cleanup — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to
> implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove duplicate `Author`/`Narrator` rows and clean dirty `Trope.name` values with the
genre/mood pipeline, with persist-side guards so neither recurs — the safe, non-destructive subset
of the work-representation problem (see spec).

**Architecture:** Two new `etl/` backfill modules (session-in/summary-out, siblings of
`etl/tag_backfill.py`) + one new `clean_trope_name` in `etl/tag_cleaning.py` + three `persist.py`
guards + one operator CLI `scripts/clean_catalog.py`. No schema/migration changes.

**Tech Stack:** Python 3, SQLAlchemy ORM, pytest (`db_integration` marker → CI Postgres), uv.

**Spec:** `docs/superpowers/specs/2026-06-23-author-trope-cleanup-design.md`

**Conventions:** TDD per task. Run unit tests `uv run pytest <path> -v`. Run
`uv run ruff check <files>` **and** `uvx ruff@0.15.16 format <files>` before each commit (CI
pre-commit gate). `db_integration` tests skip locally without Postgres; they run on CI. Reuse the
`db_url` fixture + `DatabaseManager(db_url)` + `with manager.get_session() as session:` pattern from
`test/integration/test_persist_tag_cleaning.py`.

---

## Task 1: Normalization helpers (`etl/contributor_dedup.py` foundation)

**Files:**
- Create: `src/agentic_librarian/etl/contributor_dedup.py`
- Test: `test/unit/test_contributor_dedup.py`

- [ ] **Step 1: Write the failing test**

```python
# test/unit/test_contributor_dedup.py
from dataclasses import dataclass

from agentic_librarian.etl import contributor_dedup as cd


@dataclass
class _Row:  # stand-in for Author/Narrator (has .name and .id)
    name: str
    id: str


def test_norm_name_collapses_case_and_whitespace():
    assert cd.norm_name("  Casualfarmer ") == cd.norm_name("casualfarmer") == "casualfarmer"
    assert cd.norm_name("Ann  Leckie") == "ann leckie"
    assert cd.norm_name(None) == ""


def test_norm_name_keeps_distinct_names_distinct():
    assert cd.norm_name("J. Smith") != cd.norm_name("John Smith")


def test_pick_survivor_prefers_cased_then_lowest_id():
    rows = [_Row("casualfarmer", "b"), _Row("Casualfarmer", "c"), _Row("casualfarmer", "a")]
    assert cd._pick_survivor(rows).name == "Casualfarmer"  # has uppercase wins
    rows_lower = [_Row("casualfarmer", "b"), _Row("casualfarmer", "a")]
    assert cd._pick_survivor(rows_lower).id == "a"  # tie -> lowest id


def test_dup_groups_only_returns_groups_over_one():
    rows = [_Row("A", "1"), _Row("a", "2"), _Row("B", "3")]
    groups = cd._dup_groups(rows)
    assert len(groups) == 1 and {r.id for r in groups[0]} == {"1", "2"}
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest test/unit/test_contributor_dedup.py -v`
Expected: FAIL (module / names not defined).

- [ ] **Step 3: Implement the foundation**

```python
# src/agentic_librarian/etl/contributor_dedup.py
"""Dedup Author/Narrator rows that differ only by case/whitespace, folding all links onto one
survivor (Spec 2026-06-23). Session in, summary out; the CLI is scripts/clean_catalog.py. Sibling
of etl/tag_backfill.py for the contributor tables."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from agentic_librarian.db.models import (
    Author,
    AuthorStyle,
    Narrator,
    NarratorStyle,
    WorkContributor,
    edition_narrators,
)


def norm_name(name: str | None) -> str:
    """Whitespace-collapsed, case-folded key. Two rows are 'the same' iff these match."""
    return " ".join((name or "").split()).casefold()


def _pick_survivor(rows: list):
    """Best-cased name wins (any uppercase > all-lowercase); deterministic id tiebreak."""
    return sorted(rows, key=lambda r: (0 if any(c.isupper() for c in r.name) else 1, str(r.id)))[0]


def _dup_groups(rows: list) -> list[list]:
    groups: dict[str, list] = defaultdict(list)
    for r in rows:
        groups[norm_name(r.name)].append(r)
    return [g for g in groups.values() if len(g) > 1]


@dataclass
class ContributorChange:
    kind: str          # "author" | "narrator"
    survivor: str
    merged: list[str]  # loser display names folded into the survivor
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest test/unit/test_contributor_dedup.py -v`
Expected: PASS.

- [ ] **Step 5: Format + commit**

```bash
uv run ruff check src/agentic_librarian/etl/contributor_dedup.py test/unit/test_contributor_dedup.py
uvx ruff@0.15.16 format src/agentic_librarian/etl/contributor_dedup.py test/unit/test_contributor_dedup.py
git add src/agentic_librarian/etl/contributor_dedup.py test/unit/test_contributor_dedup.py
git commit -m "feat(dedup): contributor normalization helpers"
```

---

## Task 2: Author merge + plan/apply/inventory

**Files:**
- Modify: `src/agentic_librarian/etl/contributor_dedup.py`
- Test: `test/integration/test_contributor_dedup.py` (db_integration)

- [ ] **Step 1: Write the failing test**

```python
# test/integration/test_contributor_dedup.py
import pytest

from agentic_librarian.db.models import Author, Work, WorkContributor
from agentic_librarian.db.session import DatabaseManager
from agentic_librarian.etl import contributor_dedup as cd

pytestmark = pytest.mark.db_integration


def test_apply_merges_dup_authors_preserving_distinct_roles(db_url):
    manager = DatabaseManager(db_url)
    with manager.get_session() as session:
        a1 = Author(name="Casualfarmer")
        a2 = Author(name="Casualfarmer ")  # trailing space -> dup
        w = Work(title="Beware of Chicken")
        session.add_all([a1, a2, w])
        session.flush()
        # same person, both as Author (true dup) -> collapses to one
        session.add(WorkContributor(work_id=w.id, author_id=a1.id, role="Author"))
        session.add(WorkContributor(work_id=w.id, author_id=a2.id, role="Author"))
        # same person as Editor (distinct role) -> preserved
        session.add(WorkContributor(work_id=w.id, author_id=a2.id, role="Editor"))
        session.flush()

        cd.apply_contributor_changes(session)
        session.flush()

        assert session.query(Author).count() == 1
        survivor = session.query(Author).one()
        assert survivor.name == "Casualfarmer"  # best-cased survived
        roles = sorted(c.role for c in session.query(WorkContributor).filter_by(work_id=w.id).all())
        assert roles == ["Author", "Editor"]  # one Author (dedup) + Editor (preserved)
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest test/integration/test_contributor_dedup.py -v`
Expected: FAIL (`apply_contributor_changes` not defined) — or SKIP locally without Postgres. If it
skips, reason about correctness from the code; CI runs it.

- [ ] **Step 3: Implement**

Append to `src/agentic_librarian/etl/contributor_dedup.py`:

```python
def _merge_authors(session: Session) -> list[ContributorChange]:
    changes: list[ContributorChange] = []
    for group in _dup_groups(session.query(Author).all()):
        survivor = _pick_survivor(group)
        losers = [a for a in group if a.id != survivor.id]
        for loser in losers:
            # work_contributors: re-point unless (work, survivor, role) already exists (true dup)
            for wc in session.query(WorkContributor).filter_by(author_id=loser.id).all():
                target = (
                    session.query(WorkContributor)
                    .filter_by(work_id=wc.work_id, author_id=survivor.id, role=wc.role)
                    .first()
                )
                session.delete(wc)
                if target is None:
                    session.add(WorkContributor(work_id=wc.work_id, author_id=survivor.id, role=wc.role))
            # author_styles: re-point unless (survivor, style, attr) already exists
            for st in session.query(AuthorStyle).filter_by(author_id=loser.id).all():
                target = (
                    session.query(AuthorStyle)
                    .filter_by(author_id=survivor.id, style_id=st.style_id, attribute_type=st.attribute_type)
                    .first()
                )
                session.delete(st)
                if target is None:
                    session.add(
                        AuthorStyle(author_id=survivor.id, style_id=st.style_id, attribute_type=st.attribute_type)
                    )
            session.flush()  # land re-points before deleting the loser row (no dangling FK)
            session.delete(loser)
        session.flush()
        changes.append(ContributorChange("author", survivor.name, [a.name for a in losers]))
    return changes


def plan_contributor_changes(session: Session) -> list[ContributorChange]:
    """Read-only preview of the merges apply would perform (authors + narrators)."""
    out: list[ContributorChange] = []
    for kind, rows in (("author", session.query(Author).all()), ("narrator", session.query(Narrator).all())):
        for group in _dup_groups(rows):
            survivor = _pick_survivor(group)
            out.append(ContributorChange(kind, survivor.name, [r.name for r in group if r.id != survivor.id]))
    return out


def apply_contributor_changes(session: Session) -> list[ContributorChange]:
    """Merge author then narrator dup-groups (narrators added in Task 3). Returns what was merged."""
    return _merge_authors(session)


def contributor_inventory(session: Session) -> dict:
    """Read-only: the duplicate groups for authors and narrators."""
    return {
        "authors": [[r.name for r in g] for g in _dup_groups(session.query(Author).all())],
        "narrators": [[r.name for r in g] for g in _dup_groups(session.query(Narrator).all())],
    }
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest test/integration/test_contributor_dedup.py -v` (CI runs with Postgres).
Expected: PASS on CI.

- [ ] **Step 5: Format + commit**

```bash
uvx ruff@0.15.16 format src/agentic_librarian/etl/contributor_dedup.py test/integration/test_contributor_dedup.py
git add src/agentic_librarian/etl/contributor_dedup.py test/integration/test_contributor_dedup.py
git commit -m "feat(dedup): merge duplicate Author rows, role-preserving"
```

---

## Task 3: Narrator merge

**Files:**
- Modify: `src/agentic_librarian/etl/contributor_dedup.py`
- Test: `test/integration/test_contributor_dedup.py`

- [ ] **Step 1: Add the failing test**

```python
def test_apply_merges_dup_narrators(db_url):
    from agentic_librarian.db.models import Edition, Narrator, Work
    manager = DatabaseManager(db_url)
    with manager.get_session() as session:
        n1 = Narrator(name="Travis Baldree")
        n2 = Narrator(name="travis baldree")  # case dup
        w = Work(title="Narr Test")
        session.add_all([n1, n2, w])
        session.flush()
        e = Edition(work_id=w.id, format="audiobook", narrators=[n1, n2])
        session.add(e)
        session.flush()

        cd.apply_contributor_changes(session)
        session.flush()

        assert session.query(Narrator).count() == 1
        survivor = session.query(Narrator).one()
        assert survivor.name == "Travis Baldree"
        session.refresh(e)
        assert [n.id for n in e.narrators] == [survivor.id]  # link folded, no dup
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest test/integration/test_contributor_dedup.py -v`
Expected: FAIL on CI (two narrators remain — `_merge_narrators` not wired).

- [ ] **Step 3: Implement**

Add `_merge_narrators` and call it from `apply_contributor_changes`:

```python
def _merge_narrators(session: Session) -> list[ContributorChange]:
    changes: list[ContributorChange] = []
    for group in _dup_groups(session.query(Narrator).all()):
        survivor = _pick_survivor(group)
        losers = [n for n in group if n.id != survivor.id]
        for loser in losers:
            # edition_narrators is a Core association table -> operate via Core statements
            edition_ids = (
                session.execute(
                    select(edition_narrators.c.edition_id).where(edition_narrators.c.narrator_id == loser.id)
                )
                .scalars()
                .all()
            )
            for eid in edition_ids:
                exists = session.execute(
                    select(edition_narrators.c.edition_id).where(
                        edition_narrators.c.edition_id == eid,
                        edition_narrators.c.narrator_id == survivor.id,
                    )
                ).first()
                session.execute(
                    delete(edition_narrators).where(
                        edition_narrators.c.edition_id == eid,
                        edition_narrators.c.narrator_id == loser.id,
                    )
                )
                if not exists:
                    session.execute(edition_narrators.insert().values(edition_id=eid, narrator_id=survivor.id))
            for st in session.query(NarratorStyle).filter_by(narrator_id=loser.id).all():
                target = (
                    session.query(NarratorStyle)
                    .filter_by(narrator_id=survivor.id, style_id=st.style_id, attribute_type=st.attribute_type)
                    .first()
                )
                session.delete(st)
                if target is None:
                    session.add(
                        NarratorStyle(
                            narrator_id=survivor.id, style_id=st.style_id, attribute_type=st.attribute_type
                        )
                    )
            session.flush()
            session.delete(loser)
        session.flush()
        changes.append(ContributorChange("narrator", survivor.name, [n.name for n in losers]))
    return changes
```

Change `apply_contributor_changes` to:

```python
def apply_contributor_changes(session: Session) -> list[ContributorChange]:
    """Merge author then narrator dup-groups. Returns what was merged."""
    return _merge_authors(session) + _merge_narrators(session)
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest test/integration/test_contributor_dedup.py -v` (CI).
Expected: PASS (both author and narrator tests).

- [ ] **Step 5: Format + commit**

```bash
uvx ruff@0.15.16 format src/agentic_librarian/etl/contributor_dedup.py test/integration/test_contributor_dedup.py
git add src/agentic_librarian/etl/contributor_dedup.py test/integration/test_contributor_dedup.py
git commit -m "feat(dedup): merge duplicate Narrator rows"
```

---

## Task 4: Persist guards (contributors + narrators)

**Files:**
- Modify: `src/agentic_librarian/etl/persist.py`
- Test: `test/integration/test_persist_contributor_guard.py`

- [ ] **Step 1: Write the failing test**

```python
# test/integration/test_persist_contributor_guard.py
import pytest

from agentic_librarian.db.models import Work, WorkContributor
from agentic_librarian.db.session import DatabaseManager
from agentic_librarian.etl.persist import persist_enriched_work

pytestmark = pytest.mark.db_integration


class _NullManager:
    def standardize_trope(self, *a, **k):
        return None

    def standardize_style(self, *a, **k):
        return None


def test_persist_dedups_same_author_twice(db_url):
    manager = DatabaseManager(db_url)
    row = {
        "Title": "Guard Test",
        "format": "ebook",
        "contributors": [
            {"name": "Casualfarmer", "role": "Author"},
            {"name": "Casualfarmer ", "role": "Author"},  # whitespace dup -> one row
            {"name": "Casualfarmer", "role": "Editor"},   # distinct role -> kept
        ],
        "skip_enrichment": True,
    }
    with manager.get_session() as session:
        work = persist_enriched_work(session, row, _NullManager(), _NullManager())
        session.flush()
        roles = sorted(c.role for c in session.query(WorkContributor).filter_by(work_id=work.id).all())
        assert roles == ["Author", "Editor"]
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest test/integration/test_persist_contributor_guard.py -v`
Expected: FAIL on CI (would create two `Author`-role contributors).

- [ ] **Step 3: Implement**

In `src/agentic_librarian/etl/persist.py`:

Add import near the other `etl` imports (top of file):
```python
from agentic_librarian.etl.contributor_dedup import norm_name
```

Replace the contributor loop header (currently `for c_data in raw_contributors:` at ~L105) so it
dedups by `(norm_name, role)`:
```python
    seen_contributors: set[tuple[str, str]] = set()
    for c_data in raw_contributors:
        name = c_data["name"].strip()
        # A whitespace-only role is truthy and a non-string role would persist as-is; both
        # must fall back to "Author" (PR #30 review). Valid roles keep their stripped value.
        role = c_data.get("role")
        role = role.strip() if isinstance(role, str) and role.strip() else "Author"
        key = (norm_name(name), role)
        if key in seen_contributors:  # guard: never write the same author+role twice
            continue
        seen_contributors.add(key)
        author = session.query(Author).filter(Author.name == name).first()
        ...  # rest of the loop body unchanged
```

For narrators, dedup `narrator_names` by `norm_name` just before the lookup loop (~L211):
```python
    seen_narr: set[str] = set()
    deduped_names = []
    for n_name in narrator_names:
        k = norm_name(n_name)
        if k not in seen_narr:
            seen_narr.add(k)
            deduped_names.append(n_name)
    narrator_names = deduped_names
    for n_name in narrator_names:
        ...  # unchanged
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest test/integration/test_persist_contributor_guard.py -v` (CI).
Run the existing persist test too: `uv run pytest test/integration/test_persist_tag_cleaning.py -v`.
Expected: PASS / no regression.

- [ ] **Step 5: Format + commit**

```bash
uvx ruff@0.15.16 format src/agentic_librarian/etl/persist.py test/integration/test_persist_contributor_guard.py
git add src/agentic_librarian/etl/persist.py test/integration/test_persist_contributor_guard.py
git commit -m "feat(persist): dedup contributors+narrators by normalized name on write"
```

---

## Task 5: `clean_trope_name` (`etl/tag_cleaning.py`)

**Files:**
- Modify: `src/agentic_librarian/etl/tag_cleaning.py`
- Test: `test/unit/test_tag_cleaning.py` (append)

- [ ] **Step 1: Write the failing test**

```python
# append to test/unit/test_tag_cleaning.py  (tc, pytest, UUID already imported)
@pytest.mark.parametrize("raw,expected", [
    ([f"science-fiction-fantasy-{UUID}"], ["Science Fiction", "Fantasy"]),  # combo split
    ([f"audiobook-{UUID}"], []),                                            # denylist drop
    (["enemies-to-lovers"], ["Enemies To Lovers"]),                         # genuine trope: titlecase
    (["chosen-one"], ["Chosen One"]),
    ([f"fast-paced-{UUID}"], ["Fast Paced"]),                              # mood-slug canonicalizes
    (["1735855214708"], []),                                               # numeric junk
])
def test_clean_trope_name(raw, expected):
    assert tc.clean_trope_name(raw[0]) == expected


def test_clean_trope_name_is_idempotent():
    for raw in ["science-fiction-fantasy", "enemies-to-lovers", "Chosen One"]:
        once = tc.clean_trope_name(raw)
        assert once == [x for v in once for x in tc.clean_trope_name(v)]
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest test/unit/test_tag_cleaning.py -k trope -v`
Expected: FAIL (`clean_trope_name` not defined).

- [ ] **Step 3: Implement**

Append to `src/agentic_librarian/etl/tag_cleaning.py`:
```python
def clean_trope_name(name: str) -> list[str]:
    """Clean one Trope.name with the UNION of the genre + mood maps: UUID-strip + combo-split +
    canonicalize + denylist drop + title-case. A genuine narrative trope (no map hit) just gets
    UUID-stripped + title-cased (e.g. 'enemies-to-lovers' -> 'Enemies To Lovers'). Returns 0..N
    canonical names, de-duped, order-preserving."""
    alias = {**tag_maps.ALIAS_MAP, **tag_maps.MOOD_ALIAS_MAP}
    denylist = tag_maps.DENYLIST | tag_maps.MOOD_DENYLIST
    return _dedup(_clean_one(name, alias=alias, combo=tag_maps.COMBO_MAP, denylist=denylist))
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest test/unit/test_tag_cleaning.py -v`
Expected: PASS (new + all prior cases).

- [ ] **Step 5: Format + commit**

```bash
uvx ruff@0.15.16 format src/agentic_librarian/etl/tag_cleaning.py test/unit/test_tag_cleaning.py
git add src/agentic_librarian/etl/tag_cleaning.py test/unit/test_tag_cleaning.py
git commit -m "feat(tags): clean_trope_name (genre+mood pipeline for trope names)"
```

---

## Task 6: Trope backfill (`etl/trope_backfill.py`)

**Files:**
- Create: `src/agentic_librarian/etl/trope_backfill.py`
- Test: `test/integration/test_trope_backfill.py` (db_integration)

- [ ] **Step 1: Write the failing test**

```python
# test/integration/test_trope_backfill.py
import pytest

from agentic_librarian.db.models import Trope, Work, WorkTrope
from agentic_librarian.db.session import DatabaseManager
from agentic_librarian.etl import trope_backfill as tb

pytestmark = pytest.mark.db_integration

UUID = "4c14c349-8d52-4893-aaf0-34f7e33bf275"


def test_apply_splits_dirty_trope_and_migrates_links(db_url):
    manager = DatabaseManager(db_url)
    with manager.get_session() as session:
        dirty = Trope(name=f"science-fiction-fantasy-{UUID}")
        w = Work(title="Trope Split Test")
        session.add_all([dirty, w])
        session.flush()
        session.add(WorkTrope(work_id=w.id, trope_id=dirty.id, relevance_score=0.9))
        session.flush()

        tb.apply_trope_changes(session, trope_manager=None, changes=None)  # None tm -> null embedding
        session.flush()

        names = {t.name for t in session.query(Trope).all()}
        assert "Science Fiction" in names and "Fantasy" in names
        assert f"science-fiction-fantasy-{UUID}" not in names  # dirty row gone
        linked = {
            session.get(Trope, wt.trope_id).name
            for wt in session.query(WorkTrope).filter_by(work_id=w.id).all()
        }
        assert linked == {"Science Fiction", "Fantasy"}  # link split, preserved


def test_apply_is_idempotent(db_url):
    manager = DatabaseManager(db_url)
    with manager.get_session() as session:
        t = Trope(name=f"audiobook-{UUID}")  # pure junk -> dropped
        w = Work(title="Junk Trope Test")
        session.add_all([t, w])
        session.flush()
        session.add(WorkTrope(work_id=w.id, trope_id=t.id))
        session.flush()

        tb.apply_trope_changes(session, trope_manager=None)
        session.flush()
        assert session.query(WorkTrope).filter_by(work_id=w.id).count() == 0
        # second run is a no-op
        assert tb.apply_trope_changes(session, trope_manager=None) == 0
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest test/integration/test_trope_backfill.py -v`
Expected: FAIL on CI (module not defined).

- [ ] **Step 3: Implement**

```python
# src/agentic_librarian/etl/trope_backfill.py
"""Backfill logic for trope-name cleaning (Spec 2026-06-23): clean Trope.name with the genre/mood
pipeline, migrating work_tropes links and re-embedding materially-changed names. Session in,
summary out; the CLI is scripts/clean_catalog.py. Sibling of etl/tag_backfill.py."""

from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass, field
from uuid import UUID

from sqlalchemy.orm import Session

from agentic_librarian.db.models import Trope, WorkTrope
from agentic_librarian.etl.tag_cleaning import _normalize, clean_trope_name

logger = logging.getLogger(__name__)


@dataclass
class TropeChange:
    trope_id: UUID
    name_before: str
    names_after: list[str]  # [] dropped, [x] rename, [x, y, …] split/canonicalised
    works_affected: int
    new_names: list[str] = field(default_factory=list)  # canonicals with no Trope row yet (need embedding)


def _is_cosmetic(before: str, after: list[str]) -> bool:
    """Single result whose only change is case/whitespace/hyphen formatting — embedding unchanged."""
    return len(after) == 1 and _normalize(after[0]) == _normalize(before)


def _safe_embed(trope_manager, name: str):
    if trope_manager is None:
        return None
    try:
        return trope_manager._get_embedding(name)
    except Exception:  # noqa: BLE001 - embedding failure degrades to a null-vector row, never aborts
        logger.warning("embedding failed for trope %r; creating with null vector", name, exc_info=True)
        return None


def plan_trope_changes(session: Session) -> list[TropeChange]:
    existing = {t.name for t in session.query(Trope).all()}
    out: list[TropeChange] = []
    for t in session.query(Trope).all():
        cleaned = clean_trope_name(t.name)
        if cleaned == [t.name]:
            continue
        works = session.query(WorkTrope).filter_by(trope_id=t.id).count()
        new = [] if _is_cosmetic(t.name, cleaned) else [c for c in cleaned if c not in existing]
        out.append(TropeChange(t.id, t.name, cleaned, works, new))
    return out


def embedding_call_estimate(session: Session) -> int:
    """Distinct brand-new canonical names a --apply would embed."""
    names: set[str] = set()
    for c in plan_trope_changes(session):
        names.update(c.new_names)
    return len(names)


def _move_links(session: Session, src: Trope, dst: Trope) -> None:
    """Re-point every work_tropes(src) onto dst, folding score/justification on PK collision."""
    for wt in session.query(WorkTrope).filter_by(trope_id=src.id).all():
        target = session.query(WorkTrope).filter_by(work_id=wt.work_id, trope_id=dst.id).first()
        if target is not None:
            target.relevance_score = max(target.relevance_score, wt.relevance_score)
            target.justification = target.justification or wt.justification
            session.delete(wt)
        else:
            session.add(
                WorkTrope(
                    work_id=wt.work_id,
                    trope_id=dst.id,
                    relevance_score=wt.relevance_score,
                    justification=wt.justification,
                )
            )
            session.delete(wt)
    session.flush()


def _delete_trope(session: Session, t: Trope) -> None:
    session.query(WorkTrope).filter_by(trope_id=t.id).delete()
    session.flush()
    session.delete(t)
    session.flush()


def _get_or_create_trope(session: Session, trope_manager, name: str) -> Trope:
    t = session.query(Trope).filter_by(name=name).first()
    if t is not None:
        return t
    t = Trope(name=name, embedding=_safe_embed(trope_manager, name))
    session.add(t)
    session.flush()
    return t


def apply_trope_changes(session: Session, trope_manager=None, changes: list[TropeChange] | None = None) -> int:
    """Apply (or compute) the trope changes. trope_manager supplies embeddings for brand-new
    canonical names; pass None to create them with a null vector (re-embed later)."""
    if changes is None:
        changes = plan_trope_changes(session)
    n = 0
    for c in changes:
        src = session.get(Trope, c.trope_id)
        if src is None:  # already cleaned/deleted
            continue
        if not c.names_after:  # pure junk
            _delete_trope(session, src)
            n += 1
            continue
        if _is_cosmetic(c.name_before, c.names_after):
            new = c.names_after[0]
            clash = session.query(Trope).filter(Trope.name == new, Trope.id != src.id).first()
            if clash is not None:
                _move_links(session, src, clash)
                _delete_trope(session, src)
            else:
                src.name = new  # keep embedding
            n += 1
            continue
        for name in c.names_after:  # material: split/canonicalise
            dst = _get_or_create_trope(session, trope_manager, name)
            if dst.id == src.id:
                continue
            _move_links(session, src, dst)
        _delete_trope(session, src)
        n += 1
    return n


def trope_inventory(session: Session) -> tuple[Counter, list]:
    counts: Counter = Counter()
    dirty: list = []
    for t in session.query(Trope).all():
        wc = session.query(WorkTrope).filter_by(trope_id=t.id).count()
        counts[t.name] = wc
        cleaned = clean_trope_name(t.name)
        if cleaned != [t.name]:
            dirty.append((t.name, cleaned, wc))
    return counts, dirty
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest test/integration/test_trope_backfill.py -v` (CI).
Expected: PASS.

- [ ] **Step 5: Format + commit**

```bash
uvx ruff@0.15.16 format src/agentic_librarian/etl/trope_backfill.py test/integration/test_trope_backfill.py
git add src/agentic_librarian/etl/trope_backfill.py test/integration/test_trope_backfill.py
git commit -m "feat(tropes): backfill cleaning that migrates work_tropes links + merges dups"
```

---

## Task 7: Persist fallback-trope guard

**Files:**
- Modify: `src/agentic_librarian/etl/persist.py`
- Test: `test/integration/test_persist_trope_guard.py`

- [ ] **Step 1: Write the failing test**

```python
# test/integration/test_persist_trope_guard.py
import pytest

from agentic_librarian.db.models import Trope, WorkTrope
from agentic_librarian.db.session import DatabaseManager
from agentic_librarian.etl.persist import persist_enriched_work

pytestmark = pytest.mark.db_integration

UUID = "4c14c349-8d52-4893-aaf0-34f7e33bf275"


class _PassthroughTrope:
    """standardize_trope returns an exact-name Trope (no embedding) so we can assert names."""

    def __init__(self, session):
        self.session = session

    def standardize_trope(self, name, *a, **k):
        t = self.session.query(Trope).filter_by(name=name).first()
        if t is None:
            t = Trope(name=name)
            self.session.add(t)
            self.session.flush()
        return t

    def standardize_style(self, *a, **k):
        return None


def test_fallback_tropes_are_cleaned(db_url):
    manager = DatabaseManager(db_url)
    row = {
        "Title": "Fallback Trope Test",
        "Author_1": "T. Author",
        "format": "ebook",
        "genres": [f"science-fiction-fantasy-{UUID}"],
        "moods": [],
        # no enriched_tropes -> fallback path fires; skip_enrichment must be falsy
    }
    with manager.get_session() as session:
        tm = _PassthroughTrope(session)
        work = persist_enriched_work(session, row, tm, tm)
        session.flush()
        names = {
            session.get(Trope, wt.trope_id).name
            for wt in session.query(WorkTrope).filter_by(work_id=work.id).all()
        }
        assert names == {"Science Fiction", "Fantasy"}  # cleaned + split, NOT the raw slug
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest test/integration/test_persist_trope_guard.py -v`
Expected: FAIL on CI (raw `science-fiction-fantasy-{UUID}` written as a single trope).

- [ ] **Step 3: Implement**

In `src/agentic_librarian/etl/persist.py`:

Add to the `etl.tag_cleaning` import:
```python
from agentic_librarian.etl.tag_cleaning import clean_genres, clean_moods, clean_trope_name
```

In the fallback-tag loop (~L313-323), clean each tag into 0..N canonical names before standardizing:
```python
        else:
            # Fallback to simple tags if no enriched tropes found — cleaned the same way as
            # genres/moods so the fallback can never write a UUID-tailed / unsplit slug (Spec 2026-06-23).
            for tag in all_fallback_tags:
                for name in clean_trope_name(tag):
                    standardized_trope = _safe_standardize(
                        trope_manager.standardize_trope, name, label=f"trope {name!r}"
                    )
                    if standardized_trope is None:
                        continue
                    existing_link = (
                        session.query(WorkTrope).filter_by(work_id=work.id, trope_id=standardized_trope.id).first()
                    )
                    if not existing_link:
                        session.add(WorkTrope(work=work, trope=standardized_trope))
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest test/integration/test_persist_trope_guard.py test/integration/test_persist_tag_cleaning.py -v`
Expected: PASS / no regression.

- [ ] **Step 5: Format + commit**

```bash
uvx ruff@0.15.16 format src/agentic_librarian/etl/persist.py test/integration/test_persist_trope_guard.py
git add src/agentic_librarian/etl/persist.py test/integration/test_persist_trope_guard.py
git commit -m "feat(persist): clean fallback-trope tags before standardize_trope"
```

---

## Task 8: Operator CLI (`scripts/clean_catalog.py`)

**Files:**
- Create: `scripts/clean_catalog.py`
- Test: `test/unit/test_clean_catalog_cli.py`

- [ ] **Step 1: Write the failing test**

```python
# test/unit/test_clean_catalog_cli.py
import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "clean_catalog", Path(__file__).resolve().parents[2] / "scripts" / "clean_catalog.py"
)
clean_catalog = importlib.util.module_from_spec(spec)
spec.loader.exec_module(clean_catalog)


def test_apply_refused_without_yes(monkeypatch, capsys):
    monkeypatch.setattr(clean_catalog, "resolve_database_url", lambda: "postgresql://u:p@prod-host/db")

    class _Sess:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def query(self, *a, **k): return self
        def count(self): return 1

    monkeypatch.setattr(clean_catalog, "DatabaseManager", lambda url: type("M", (), {"get_session": lambda s: _Sess()})())
    rc = clean_catalog.main(["--contributors", "--apply"])  # no --yes
    assert rc == 2
    assert "REFUSING --apply without --yes" in capsys.readouterr().out
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest test/unit/test_clean_catalog_cli.py -v`
Expected: FAIL (script not found).

- [ ] **Step 3: Implement**

```python
# scripts/clean_catalog.py
"""Operator CLI for catalog cleanup (Spec 2026-06-23): contributor dedup + trope-name cleaning.

  python scripts/clean_catalog.py --inventory
  python scripts/clean_catalog.py --contributors --dry-run
  python scripts/clean_catalog.py --contributors --apply --yes
  python scripts/clean_catalog.py --tropes --dry-run
  python scripts/clean_catalog.py --tropes --apply --yes

Run against LIVE prod via the app container + Cloud SQL proxy. Refuses --apply on sqlite/backup/localhost."""

from __future__ import annotations

import argparse
import sys

from agentic_librarian.db.models import Trope, Work
from agentic_librarian.db.session import DatabaseManager, resolve_database_url
from agentic_librarian.etl import contributor_dedup, trope_backfill
from agentic_librarian.etl.tag_backfill import is_prod_url


def _refuse(args, url, safe) -> int | None:
    if args.dry_run or not args.apply:
        print("\n(dry-run: no writes)")
        return 0
    if not args.yes:
        print("\nREFUSING --apply without --yes.")
        return 2
    if not is_prod_url(url):
        print(f"\nREFUSING --apply: '{safe}' is not a live prod DB (sqlite/backup/localhost).")
        return 2
    return None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Dedup contributors / clean trope names.")
    ap.add_argument("--inventory", action="store_true")
    ap.add_argument("--contributors", action="store_true")
    ap.add_argument("--tropes", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--yes", action="store_true", help="required confirmation for --apply")
    args = ap.parse_args(argv)

    url = resolve_database_url()
    safe = url.split("@")[-1] if "@" in url else url  # never print credentials
    manager = DatabaseManager(url)

    with manager.get_session() as session:
        print(f"DB target: …@{safe}")
        print(f"recency probe: works={session.query(Work).count()} tropes={session.query(Trope).count()}")

        if args.inventory:
            inv = contributor_dedup.contributor_inventory(session)
            print(f"\n=== duplicate AUTHOR groups ({len(inv['authors'])}) ===")
            for g in inv["authors"]:
                print(f"  {g}")
            print(f"\n=== duplicate NARRATOR groups ({len(inv['narrators'])}) ===")
            for g in inv["narrators"]:
                print(f"  {g}")
            _counts, dirty = trope_backfill.trope_inventory(session)
            print(f"\n=== dirty TROPES ({len(dirty)}) ===")
            for before, after, wc in dirty:
                print(f"  {wc:4d}  {before!r} -> {after}")
            print(f"\nembedding calls a --tropes --apply would make: {trope_backfill.embedding_call_estimate(session)}")
            return 0

        if args.contributors:
            changes = contributor_dedup.plan_contributor_changes(session)
            print(f"\n{len(changes)} contributor groups would merge.")
            for c in changes[:80]:
                print(f"  [{c.kind}] keep {c.survivor!r}  merge {c.merged}")
            early = _refuse(args, url, safe)
            if early is not None:
                return early
            applied = contributor_dedup.apply_contributor_changes(session)
            print(f"\napplied: merged {len(applied)} groups.")
            return 0

        if args.tropes:
            changes = trope_backfill.plan_trope_changes(session)
            calls = trope_backfill.embedding_call_estimate(session)
            print(f"\n{len(changes)} trope rows would change ({calls} embedding calls).")
            for c in changes[:80]:
                print(f"  {c.works_affected:4d}  {c.name_before!r} -> {c.names_after}")
            early = _refuse(args, url, safe)
            if early is not None:
                return early
            from agentic_librarian.scouts.trope_manager import TropeManager

            tm = TropeManager(session)
            print(f"\napplied: {trope_backfill.apply_trope_changes(session, tm, changes)} trope rows cleaned.")
            return 0

        print("Nothing to do. Pass --inventory, --contributors, or --tropes.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest test/unit/test_clean_catalog_cli.py -v`
Expected: PASS.

- [ ] **Step 5: Format + commit**

```bash
uvx ruff@0.15.16 format scripts/clean_catalog.py test/unit/test_clean_catalog_cli.py
git add scripts/clean_catalog.py test/unit/test_clean_catalog_cli.py
git commit -m "feat(cli): clean_catalog.py — contributor dedup + trope cleaning operator CLI"
```

---

## Final verification (after all tasks)

- [ ] `uv run pytest test/unit -v` — all unit tests green.
- [ ] `uv run ruff check src/agentic_librarian/etl scripts/clean_catalog.py` — clean.
- [ ] `uvx ruff@0.15.16 format --check .` (trust `git diff --stat` on Windows/CRLF).
- [ ] Dispatch a final whole-branch reviewer (db_integration tests run on CI Postgres).
- [ ] superpowers:finishing-a-development-branch → push + open PR (Gemini review).

## Rollout (operator, after merge + deploy)

Proxy up (with `-e PYTHONPATH=/app/src`), then against live prod:
1. `python scripts/clean_catalog.py --inventory` — sanity + embedding-call estimate.
2. `python scripts/clean_catalog.py --contributors --dry-run` → `--contributors --apply --yes`.
3. `python scripts/clean_catalog.py --tropes --dry-run` (review embedding count) → `--tropes --apply --yes`.

Both convergent — a retry after a transient blip is safe.
