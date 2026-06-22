# Genre / Mood Tag Cleaning — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Clean `Work.genres` / `Work.moods` — strip UUID/junk, collapse case/spelling variants to a BISAC-formatted canonical, split true combos, drop non-genres — at every write path, plus a one-off backfill of the live DB.

**Architecture:** Two pure modules — `etl/tag_maps.py` (curated `ALIAS_MAP`/`COMBO_MAP`/`DENYLIST`/`CONDITIONAL_DROP` + mood maps, seeded from examples) and `etl/tag_cleaning.py` (`clean_genres`/`clean_moods`) — hooked into `etl/persist.py` so all writes are cleaned. An operator script `scripts/clean_tags.py` (`--inventory`/`--dry-run`/`--apply`) re-cleans existing rows against **live prod**, guarded against backups/stale DBs. No schema change, no embeddings.

**Tech Stack:** Python 3 / SQLAlchemy / pytest (`db_integration` marker for DB tests). Run tests with `uv run pytest …`; format before pushing with `uvx ruff@0.15.16 format .` (CI runs the `ruff-format` pre-commit hook — `ruff check` alone misses it).

**Spec:** `docs/superpowers/specs/2026-06-22-genre-mood-tag-cleaning-design.md`

---

## File Structure

- **Create** `src/agentic_librarian/etl/tag_maps.py` — curated lookup data (dicts/sets), keys pre-normalized (lowercase, spaces).
- **Create** `src/agentic_librarian/etl/tag_cleaning.py` — pure `clean_genres`/`clean_moods` + helpers.
- **Modify** `src/agentic_librarian/etl/persist.py` (~L140-141) — wrap genres/moods coercion in the cleaners.
- **Create** `src/agentic_librarian/etl/tag_backfill.py` — testable backfill logic (`plan_changes`/`apply_changes`/`inventory`/`is_prod_url`).
- **Create** `scripts/clean_tags.py` — thin standalone CLI (inventory/dry-run/apply), run directly.
- **Test** `test/unit/test_tag_cleaning.py`, `test/unit/test_tag_backfill.py`, `test/integration/test_persist_tag_cleaning.py`, `test/integration/test_tag_backfill_db.py`.

**Normalization contract (used everywhere):** a tag is "normalized" by stripping a trailing UUID, replacing `-`/`_` with spaces, lowercasing, and collapsing whitespace. All map **keys** are normalized form; map **values** (RHS) are the canonical display spelling (BISAC where one exists).

---

## Task 1: `tag_maps.py` — curated seed maps

**Files:**
- Create: `src/agentic_librarian/etl/tag_maps.py`
- Test: `test/unit/test_tag_cleaning.py`

- [ ] **Step 1: Write the failing test**

Create `test/unit/test_tag_cleaning.py`:

```python
from agentic_librarian.etl import tag_maps


def test_seed_maps_have_expected_entries():
    assert tag_maps.ALIAS_MAP["sci fi"] == "Science Fiction"
    assert tag_maps.ALIAS_MAP["action adventure"] == "Action & Adventure"
    assert tag_maps.ALIAS_MAP["business economics"] == "Business & Economics"
    assert tag_maps.COMBO_MAP["science fiction fantasy"] == ["Science Fiction", "Fantasy"]
    assert "audiobook" in tag_maps.DENYLIST
    assert "general" in tag_maps.DENYLIST
    assert "Fiction" in tag_maps.CONDITIONAL_DROP
    # mood maps exist
    assert isinstance(tag_maps.MOOD_ALIAS_MAP, dict)
    assert isinstance(tag_maps.MOOD_DENYLIST, set)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest test/unit/test_tag_cleaning.py::test_seed_maps_have_expected_entries -v`
Expected: FAIL with `ModuleNotFoundError: …etl.tag_maps`.

- [ ] **Step 3: Write minimal implementation**

Create `src/agentic_librarian/etl/tag_maps.py`:

```python
"""Curated lookup tables for genre/mood cleaning (Spec 2026-06-22). Keys are NORMALIZED
(lowercase, hyphens/underscores -> spaces, whitespace-collapsed); values are the canonical
display spelling, following BISAC where one exists. Seeded from the operator's known examples;
expand from the `scripts/clean_tags.py --inventory` output during rollout."""

from __future__ import annotations

# normalized variant -> canonical (BISAC-formatted) spelling
ALIAS_MAP: dict[str, str] = {
    "fiction": "Fiction",
    "nonfiction": "Nonfiction",
    "non fiction": "Nonfiction",
    "sci fi": "Science Fiction",
    "scifi": "Science Fiction",
    "sf": "Science Fiction",
    "science fiction": "Science Fiction",
    "action adventure": "Action & Adventure",
    "business economics": "Business & Economics",
    "business & economics": "Business & Economics",
    "epic": "Epic",
    "fantasy": "Fantasy",
}

# normalized true-combo slug -> list of canonical genres (split)
COMBO_MAP: dict[str, list[str]] = {
    "science fiction fantasy": ["Science Fiction", "Fantasy"],
}

# normalized tags dropped always (non-genres: formats, fillers)
DENYLIST: set[str] = {
    "audiobook", "audio", "audio cd", "audible audio",
    "ebook", "e book", "kindle edition", "paperback", "hardcover", "mass market paperback",
    "general", "books", "miscellaneous", "uncategorized", "other",
}

# canonical genres dropped iff other genres remain in the list
CONDITIONAL_DROP: set[str] = {"Fiction"}

# moods: permissive QC — collapse only, drop only clear junk
MOOD_ALIAS_MAP: dict[str, str] = {
    "lighthearted": "Lighthearted",
    "light hearted": "Lighthearted",
}
MOOD_DENYLIST: set[str] = {"audiobook", "ebook", "general", "n a", "na"}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest test/unit/test_tag_cleaning.py::test_seed_maps_have_expected_entries -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/agentic_librarian/etl/tag_maps.py test/unit/test_tag_cleaning.py
git commit -m "feat(tags): seed genre/mood cleaning maps"
```

---

## Task 2: `tag_cleaning.py` — normalization helpers

**Files:**
- Create: `src/agentic_librarian/etl/tag_cleaning.py`
- Test: `test/unit/test_tag_cleaning.py`

- [ ] **Step 1: Write the failing test**

Append to `test/unit/test_tag_cleaning.py`:

```python
from agentic_librarian.etl import tag_cleaning as tc


def test_strip_uuid_and_normalize():
    assert tc._strip_uuid("science-fiction-fantasy-4c14c349-8d52-4893-aaf0-34f7e33bf275") == "science-fiction-fantasy"
    assert tc._strip_uuid("epic") == "epic"
    assert tc._normalize("Science-Fiction") == "science fiction"
    assert tc._normalize("  Business & Economics ") == "business & economics"


def test_bisac_reduce_takes_deepest_non_filler():
    assert tc._bisac_reduce("Fiction / Science Fiction / General") == "Science Fiction"
    assert tc._bisac_reduce("Fantasy") == "Fantasy"


def test_titlecase():
    assert tc._titlecase("science fiction") == "Science Fiction"
    assert tc._titlecase("business & economics") == "Business & Economics"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest test/unit/test_tag_cleaning.py -k "strip_uuid or bisac or titlecase" -v`
Expected: FAIL with `ModuleNotFoundError: …etl.tag_cleaning`.

- [ ] **Step 3: Write minimal implementation**

Create `src/agentic_librarian/etl/tag_cleaning.py`:

```python
"""Pure QC/cleaning for Work.genres / Work.moods (Spec 2026-06-22). No I/O; deterministic;
parametrised by the curated maps in tag_maps.py."""

from __future__ import annotations

import re

from agentic_librarian.etl import tag_maps

_UUID_RE = re.compile(r"-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE)
_HAS_DIGIT_RE = re.compile(r"\d")
_BISAC_FILLER = {"general", "fiction", "nonfiction", "non fiction", "books", "miscellaneous"}


def _strip_uuid(tag: str) -> str:
    return _UUID_RE.sub("", tag or "")


def _normalize(tag: str) -> str:
    s = (tag or "").replace("-", " ").replace("_", " ")
    return " ".join(s.lower().split())


def _bisac_reduce(tag: str) -> str:
    """BISAC path 'A / B / C' -> the deepest non-filler segment; otherwise the tag unchanged."""
    if "/" not in tag:
        return tag
    segments = [seg.strip() for seg in tag.split("/") if seg.strip()]
    for seg in reversed(segments):
        if _normalize(seg) not in _BISAC_FILLER:
            return seg
    return segments[-1] if segments else ""


def _titlecase(norm: str) -> str:
    return " ".join(w.capitalize() for w in norm.split())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest test/unit/test_tag_cleaning.py -k "strip_uuid or bisac or titlecase" -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/agentic_librarian/etl/tag_cleaning.py test/unit/test_tag_cleaning.py
git commit -m "feat(tags): normalization helpers (uuid strip, normalize, bisac, titlecase)"
```

---

## Task 3: `clean_genres` — the full pipeline

**Files:**
- Modify: `src/agentic_librarian/etl/tag_cleaning.py`
- Test: `test/unit/test_tag_cleaning.py`

- [ ] **Step 1: Write the failing test**

Append to `test/unit/test_tag_cleaning.py` (these are the spec's real-string acceptance cases):

```python
import pytest

UUID = "4c14c349-8d52-4893-aaf0-34f7e33bf275"


@pytest.mark.parametrize("raw,expected", [
    ([f"science-fiction-fantasy-{UUID}"], ["Science Fiction", "Fantasy"]),
    ([f"audiobook-{UUID}"], []),
    ([f"epic-{UUID}"], ["Epic"]),
    ([f"action-adventure-{UUID}"], ["Action & Adventure"]),
    (["fiction", "Fiction", "Fantasy"], ["Fantasy"]),
    (["fiction"], ["Fiction"]),
    (["business-economics", "Business & Economics"], ["Business & Economics"]),
    (["sci-fi", "scifi", "Science-Fiction"], ["Science Fiction"]),
    ([f"general-{UUID}", "Fiction / Science Fiction / General"], ["Science Fiction"]),
    ([], []),
    (None, []),
])
def test_clean_genres(raw, expected):
    assert tc.clean_genres(raw) == expected


def test_clean_genres_is_idempotent():
    msgs = [f"science-fiction-fantasy-{UUID}", "fiction", "Fantasy", f"audiobook-{UUID}"]
    once = tc.clean_genres(msgs)
    assert tc.clean_genres(once) == once
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest test/unit/test_tag_cleaning.py -k clean_genres -v`
Expected: FAIL with `AttributeError: …has no attribute 'clean_genres'`.

- [ ] **Step 3: Write minimal implementation**

Append to `src/agentic_librarian/etl/tag_cleaning.py`:

```python
def _clean_one(tag: str, *, alias: dict, combo: dict, denylist: set) -> list[str]:
    n = _normalize(_bisac_reduce(_strip_uuid(tag)))
    if not n:
        return []
    if n in combo:
        return list(combo[n])          # already canonical
    if n in alias:
        return [alias[n]]
    if n in denylist or _HAS_DIGIT_RE.search(n) or len(n) <= 1:
        return []
    return [_titlecase(n)]              # unknown-but-valid: keep, cleaned


def _dedup(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for it in items:
        k = it.lower()
        if k not in seen:
            seen.add(k)
            out.append(it)
    return out


def clean_genres(raw: list[str] | None) -> list[str]:
    out: list[str] = []
    for tag in raw or []:
        out.extend(_clean_one(tag, alias=tag_maps.ALIAS_MAP, combo=tag_maps.COMBO_MAP, denylist=tag_maps.DENYLIST))
    result = _dedup(out)
    if len(result) > 1:  # drop over-broad umbrellas only when more specific genres remain
        pruned = [g for g in result if g not in tag_maps.CONDITIONAL_DROP]
        result = pruned or result
    return result
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest test/unit/test_tag_cleaning.py -k clean_genres -v`
Expected: PASS (all parametrized cases + idempotency).

- [ ] **Step 5: Commit**

```bash
git add src/agentic_librarian/etl/tag_cleaning.py test/unit/test_tag_cleaning.py
git commit -m "feat(tags): clean_genres pipeline (alias/combo/deny/conditional-drop)"
```

---

## Task 4: `clean_moods`

**Files:**
- Modify: `src/agentic_librarian/etl/tag_cleaning.py`
- Test: `test/unit/test_tag_cleaning.py`

- [ ] **Step 1: Write the failing test**

Append to `test/unit/test_tag_cleaning.py`:

```python
@pytest.mark.parametrize("raw,expected", [
    ([f"dark-{UUID}", "Dark"], ["Dark"]),                 # uuid strip + case dedup
    ([f"audiobook-{UUID}"], []),                          # junk dropped
    (["lighthearted", "Light-Hearted"], ["Lighthearted"]),  # alias collapse
    (["Mysterious", "reflective"], ["Mysterious", "Reflective"]),  # unknown kept, title-cased
    (["general"], []),
    (None, []),
])
def test_clean_moods(raw, expected):
    assert tc.clean_moods(raw) == expected


def test_clean_moods_no_combo_split():
    # moods never split on the genre COMBO_MAP
    assert tc.clean_moods(["science fiction fantasy"]) == ["Science Fiction Fantasy"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest test/unit/test_tag_cleaning.py -k clean_moods -v`
Expected: FAIL (`clean_moods` not defined).

- [ ] **Step 3: Write minimal implementation**

Append to `src/agentic_librarian/etl/tag_cleaning.py`:

```python
def clean_moods(raw: list[str] | None) -> list[str]:
    out: list[str] = []
    for tag in raw or []:
        out.extend(_clean_one(tag, alias=tag_maps.MOOD_ALIAS_MAP, combo={}, denylist=tag_maps.MOOD_DENYLIST))
    return _dedup(out)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest test/unit/test_tag_cleaning.py -v`
Expected: PASS (whole file).

- [ ] **Step 5: Commit**

```bash
git add src/agentic_librarian/etl/tag_cleaning.py test/unit/test_tag_cleaning.py
git commit -m "feat(tags): clean_moods (permissive QC, no combo-split)"
```

---

## Task 5: Hook the cleaners into `persist.py`

**Files:**
- Modify: `src/agentic_librarian/etl/persist.py`
- Test: `test/integration/test_persist_tag_cleaning.py`

- [ ] **Step 1: Write the failing test**

Create `test/integration/test_persist_tag_cleaning.py`:

```python
"""persist_enriched_work stores cleaned genres/moods (Spec 2026-06-22)."""

import pytest

from agentic_librarian.db.models import Work
from agentic_librarian.db.session import DatabaseManager
from agentic_librarian.etl.persist import persist_enriched_work

pytestmark = pytest.mark.db_integration

UUID = "4c14c349-8d52-4893-aaf0-34f7e33bf275"


class _NullManager:
    """Stand-in for Trope/Style managers — never actually called for a skip_enrichment row,
    but persist takes them as args."""

    def standardize_trope(self, *a, **k):
        return None

    def standardize_style(self, *a, **k):
        return None


def test_persist_cleans_genres_and_moods(db_url):
    manager = DatabaseManager(db_url)
    row = {
        "Title": "Tag Cleaning Test",
        "Author_1": "T. Author",
        "genres": [f"science-fiction-fantasy-{UUID}", f"audiobook-{UUID}", "Fiction"],
        "moods": [f"dark-{UUID}", "Dark"],
        "skip_enrichment": True,  # new work: genres set at creation, no trope/embedding work
    }
    with manager.get_session() as session:
        persist_enriched_work(session, row, _NullManager(), _NullManager())
        session.flush()
        work = session.query(Work).filter_by(title="Tag Cleaning Test").one()
        assert work.genres == ["Science Fiction", "Fantasy"]  # combo split, audiobook dropped, Fiction dropped (others present)
        assert work.moods == ["Dark"]
```

- [ ] **Step 2: Run test to verify it fails (or skips)**

Run: `uv run pytest test/integration/test_persist_tag_cleaning.py -v`
Expected: FAIL (genres stored raw) if a DB is present; SKIP if no Postgres. Either confirms wiring.

- [ ] **Step 3: Write minimal implementation**

In `src/agentic_librarian/etl/persist.py`, add the import near the other `agentic_librarian.etl`/scout imports at the top:

```python
from agentic_librarian.etl.tag_cleaning import clean_genres, clean_moods
```

Then change the two coercion lines (currently `genres = _nan_to_list(row.get("genres"))` / `moods = _nan_to_list(row.get("moods"))`) to:

```python
    genres = clean_genres(_nan_to_list(row.get("genres")))
    moods = clean_moods(_nan_to_list(row.get("moods")))
```

Do not change anything else (the rest of the function consumes `genres`/`moods` unchanged, including the fallback-trope set at `all_fallback_tags = set(genres) | set(moods)` — which now benefits from clean tags).

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest test/integration/test_persist_tag_cleaning.py -v`
Expected: PASS (or SKIP without a DB). Also run the existing persist tests to confirm no regression:
Run: `uv run pytest test/integration/test_persist.py test/integration/test_persist_enriched.py -v`
Expected: PASS or SKIP.

- [ ] **Step 5: Commit**

```bash
git add src/agentic_librarian/etl/persist.py test/integration/test_persist_tag_cleaning.py
git commit -m "feat(tags): clean genres/moods at persist (covers all write paths)"
```

---

## Task 6: Backfill logic `etl/tag_backfill.py` + thin CLI `scripts/clean_tags.py`

**Files:**
- Create: `src/agentic_librarian/etl/tag_backfill.py` (testable logic)
- Create: `scripts/clean_tags.py` (thin standalone CLI, run directly)
- Test: `test/unit/test_tag_backfill.py`, `test/integration/test_tag_backfill_db.py`

> **Why the logic lives in `src/`:** pytest only puts `src` on the path (`pythonpath = ["src"]` in
> `pyproject.toml`), so `from scripts import …` does NOT resolve in tests. The testable logic lives in
> `agentic_librarian.etl.tag_backfill`; `scripts/clean_tags.py` is a thin wrapper run directly
> (`python scripts/clean_tags.py …`), never imported — so no `scripts/__init__.py` is needed.

- [ ] **Step 1: Write the failing tests**

Create `test/unit/test_tag_backfill.py` (pure guard logic — no DB):

```python
import pytest

from agentic_librarian.etl import tag_backfill


@pytest.mark.parametrize("url,ok", [
    ("postgresql://u:p@10.1.2.3:5432/agentic_librarian", True),
    ("postgresql://u:p@host.docker.internal:5433/agentic_librarian", True),
    ("postgresql://u:p@localhost:5432/agentic_librarian", False),
    ("postgresql://u:p@127.0.0.1:5432/agentic_librarian", False),
    ("sqlite:///data/backups/snapshot.db", False),
    ("postgresql://u:p@/agentic_librarian?host=/cloudsql/proj:reg:inst", True),
])
def test_is_prod_url(url, ok):
    assert tag_backfill.is_prod_url(url) is ok
```

Create `test/integration/test_tag_backfill_db.py`:

```python
"""plan_changes / apply_changes over real rows (Spec 2026-06-22)."""

import pytest

from agentic_librarian.db.models import Author, Edition, Work, WorkContributor
from agentic_librarian.db.session import DatabaseManager
from agentic_librarian.etl import tag_backfill

pytestmark = pytest.mark.db_integration

UUID = "4c14c349-8d52-4893-aaf0-34f7e33bf275"


def _seed(manager, genres, moods):
    with manager.get_session() as s:
        a = Author(name="B. Author")
        w = Work(title="Backfill Test", contributors=[WorkContributor(author=a, role="Author")],
                 genres=genres, moods=moods)
        s.add_all([a, w, Edition(work=w, format="ebook")])
        s.flush()
        return w.id


def test_plan_and_apply(db_url):
    manager = DatabaseManager(db_url)
    wid = _seed(manager, [f"science-fiction-fantasy-{UUID}", f"audiobook-{UUID}"], [f"dark-{UUID}", "Dark"])

    with manager.get_session() as session:
        mine = [c for c in tag_backfill.plan_changes(session) if c.work_id == wid]
        assert len(mine) == 1
        assert mine[0].genres_after == ["Science Fiction", "Fantasy"]
        assert mine[0].moods_after == ["Dark"]

    with manager.get_session() as session:
        tag_backfill.apply_changes(session)

    with manager.get_session() as session:
        w = session.get(Work, wid)
        assert w.genres == ["Science Fiction", "Fantasy"]
        assert w.moods == ["Dark"]
        # idempotent: a second plan over already-clean data finds no change for this work
        assert all(c.work_id != wid for c in tag_backfill.plan_changes(session))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest test/unit/test_tag_backfill.py test/integration/test_tag_backfill_db.py -v`
Expected: FAIL with `ModuleNotFoundError: …etl.tag_backfill` (guard test fails; DB test fails or skips without a DB).

- [ ] **Step 3: Write the implementation**

Create `src/agentic_librarian/etl/tag_backfill.py`:

```python
"""Backfill logic for genre/mood cleaning (Spec 2026-06-22): session in, lists out, no CLI/I-O of
its own. The thin operator CLI is scripts/clean_tags.py."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from uuid import UUID

from agentic_librarian.db.models import Work
from agentic_librarian.etl.tag_cleaning import clean_genres, clean_moods


@dataclass
class Change:
    work_id: UUID
    title: str
    genres_before: list[str]
    genres_after: list[str]
    moods_before: list[str]
    moods_after: list[str]


def plan_changes(session) -> list[Change]:
    """Works whose cleaned genres/moods differ from what's stored."""
    out: list[Change] = []
    for w in session.query(Work).all():
        gb, mb = list(w.genres or []), list(w.moods or [])
        ga, ma = clean_genres(gb), clean_moods(mb)
        if ga != gb or ma != mb:
            out.append(Change(w.id, w.title, gb, ga, mb, ma))
    return out


def apply_changes(session) -> int:
    n = 0
    for c in plan_changes(session):
        w = session.get(Work, c.work_id)
        w.genres, w.moods = c.genres_after, c.moods_after
        n += 1
    return n


def inventory(session) -> tuple[Counter, Counter]:
    genres: Counter = Counter()
    moods: Counter = Counter()
    for w in session.query(Work).all():
        genres.update(w.genres or [])
        moods.update(w.moods or [])
    return genres, moods


def is_prod_url(url: str) -> bool:
    """True only for a real remote/proxy Postgres — NOT sqlite, backups, or a local dev DB."""
    u = (url or "").lower()
    if u.startswith("sqlite") or "/backups/" in u or "data/backups" in u:
        return False
    if "@localhost" in u or "@127.0.0.1" in u:
        return False
    return u.startswith("postgresql")
```

Create `scripts/clean_tags.py` (thin CLI — run directly, never imported by tests):

```python
"""Operator backfill CLI for genre/mood cleaning (Spec 2026-06-22).

  python scripts/clean_tags.py --inventory      # read-only; distinct values + frequency
  python scripts/clean_tags.py --dry-run        # read-only; show changes, NO writes
  python scripts/clean_tags.py --apply --yes    # write cleaned values (idempotent)

Run against LIVE prod via the app container + Cloud SQL proxy (docs/runbooks/bulk-import-rollout.md §3).
Refuses --apply against a sqlite/backup/localhost DB."""

from __future__ import annotations

import argparse
import sys

from agentic_librarian.db.models import Work
from agentic_librarian.db.session import DatabaseManager, resolve_database_url
from agentic_librarian.etl import tag_backfill


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Clean Work.genres / Work.moods.")
    ap.add_argument("--inventory", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--yes", action="store_true", help="required confirmation for --apply")
    args = ap.parse_args(argv)

    url = resolve_database_url()
    safe = url.split("@")[-1] if "@" in url else url  # never print credentials
    manager = DatabaseManager(url)

    with manager.get_session() as session:
        print(f"DB target: …@{safe}")
        print(f"recency probe: works={session.query(Work).count()}  (confirm CURRENT prod, not a backup)")

        if args.inventory:
            genres, moods = tag_backfill.inventory(session)
            print("\n=== distinct GENRES (count | value) ===")
            for val, c in genres.most_common():
                print(f"{c:5d}  {val!r}")
            print("\n=== distinct MOODS (count | value) ===")
            for val, c in moods.most_common():
                print(f"{c:5d}  {val!r}")
            return 0

        changes = tag_backfill.plan_changes(session)
        print(f"\n{len(changes)} works would change.")
        for c in changes[:50]:
            if c.genres_before != c.genres_after:
                print(f"  [{c.title[:40]:40}] genres {c.genres_before} -> {c.genres_after}")
            if c.moods_before != c.moods_after:
                print(f"  [{c.title[:40]:40}] moods  {c.moods_before} -> {c.moods_after}")

        if args.dry_run or not args.apply:
            print("\n(dry-run: no writes)")
            return 0
        if not args.yes:
            print("\nREFUSING --apply without --yes.")
            return 2
        if not tag_backfill.is_prod_url(url):
            print(f"\nREFUSING --apply: '{safe}' is not a live prod DB (sqlite/backup/localhost).")
            return 2

        print(f"\napplied: {tag_backfill.apply_changes(session)} works updated.")
        return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest test/unit/test_tag_backfill.py -v`
Expected: PASS (6 cases).
Run: `uv run pytest test/integration/test_tag_backfill_db.py -v`
Expected: PASS (or SKIP without a DB).

- [ ] **Step 5: Commit**

```bash
git add src/agentic_librarian/etl/tag_backfill.py scripts/clean_tags.py test/unit/test_tag_backfill.py test/integration/test_tag_backfill_db.py
git commit -m "feat(tags): backfill logic + CLI with live-DB guard"
```

---

## Task 7: Verification

**Files:** none (verification only)

- [ ] **Step 1: Lint (CI parity)**

Run: `uvx ruff@0.15.16 format src/agentic_librarian/etl/tag_cleaning.py src/agentic_librarian/etl/tag_maps.py src/agentic_librarian/etl/tag_backfill.py src/agentic_librarian/etl/persist.py scripts/clean_tags.py test/unit/test_tag_cleaning.py test/unit/test_tag_backfill.py test/integration/test_persist_tag_cleaning.py test/integration/test_tag_backfill_db.py`
Then: `uv run ruff check src/agentic_librarian/etl scripts/clean_tags.py test/unit/test_tag_cleaning.py test/unit/test_tag_backfill.py`
Expected: format makes only intended changes; check is clean. (CI runs the `ruff-format` pre-commit hook — never push without running format first.)

- [ ] **Step 2: Full backend suite**

Run: `uv run pytest test/unit test/integration -q`
Expected: the new tag tests PASS; `db_integration` tests SKIP if no Postgres; the only failures are the 5 pre-existing environmental ones (live API/DB). Any other failure is a regression.

- [ ] **Step 3: Commit any format fixes**

```bash
git add -A
git commit -m "chore(tags): ruff format"
```

---

## Rollout (operator, post-merge)

1. Deploy ships the persist hook → all **new** enrichment writes are cleaned automatically (no env/migration).
2. Inventory the live DB (app container + Cloud SQL proxy, per `docs/runbooks/bulk-import-rollout.md` §3):
   `… agentic_librarian-app:latest python scripts/clean_tags.py --inventory`
3. Expand `tag_maps.py` from the inventory output (review the new aliases/combos/denials), reconcile canonical genre names against design-work's `GenreIcon` set (coordination board), re-run unit tests.
4. `python scripts/clean_tags.py --dry-run` → eyeball the diff + the recency probe (confirm it's CURRENT prod, not a `data/backups` snapshot).
5. `python scripts/clean_tags.py --apply --yes` → backfill. Idempotent; re-runnable.
