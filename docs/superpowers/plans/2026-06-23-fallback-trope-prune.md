# Fallback-Trope Prune & Two-Phase Persist Fix — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to
> implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop two-phase imports double-layering genre/mood fallback tropes (Shape B: the fast pass
opts out of fallbacks; the deep pass is the single authoritative trope write) and provide a one-time
prune of the existing pollution.

**Architecture:** Part A — a `write_fallback_tropes` flag through `persist_enriched_work` +
`two_phase._scout_and_persist`, with a `has_real` guard. Part B — `plan/apply_fallback_prune` in
`etl/trope_backfill.py` + a `--prune-fallbacks` mode on `scripts/clean_catalog.py`. No schema change.

**Tech Stack:** Python 3, SQLAlchemy ORM, pytest (`db_integration` → CI Postgres), uv.

**Spec:** `docs/superpowers/specs/2026-06-23-fallback-trope-prune-design.md`

**Conventions:** TDD per task. `uv run pytest <path> -v`. Before each commit: `uv run ruff check`
**and** `uvx ruff@0.15.16 format`. `db_integration` tests skip locally (no Postgres), run on CI;
reuse the `db_url` fixture + `DatabaseManager(db_url)` + `with manager.get_session() as session:`
pattern from `test/integration/test_persist_tag_cleaning.py`.

---

## Task 1: Persist Shape-B fallback guard (`persist.py`)

**Files:** Modify `src/agentic_librarian/etl/persist.py`; Test `test/integration/test_persist_fallback_flag.py`

- [ ] **Step 1: Write the failing test** `test/integration/test_persist_fallback_flag.py`:
```python
import pytest

from agentic_librarian.db.models import Trope, WorkTrope
from agentic_librarian.db.session import DatabaseManager
from agentic_librarian.etl.persist import persist_enriched_work

pytestmark = pytest.mark.db_integration


class _PassthroughTrope:
    """standardize_trope returns an exact-name Trope (no embedding) so names are assertable."""

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


def _row(**over):
    r = {
        "Title": "FB Flag Test",
        "Author_1": "A. Author",
        "format": "ebook",
        "genres": ["fantasy"],
        "moods": ["dark"],
    }
    r.update(over)
    return r


def _trope_names(session, work):
    return {
        session.get(Trope, wt.trope_id).name
        for wt in session.query(WorkTrope).filter_by(work_id=work.id).all()
    }


def test_fast_pass_writes_no_fallback_tropes(db_url):
    manager = DatabaseManager(db_url)
    with manager.get_session() as session:
        tm = _PassthroughTrope(session)
        work = persist_enriched_work(session, _row(write_fallback_tropes=False), tm, tm)
        session.flush()
        assert session.query(WorkTrope).filter_by(work_id=work.id).count() == 0  # no fallback tropes
        assert work.genres  # genres still written (still displayed)


def test_default_writes_fallback_when_no_real_tropes(db_url):
    manager = DatabaseManager(db_url)
    with manager.get_session() as session:
        tm = _PassthroughTrope(session)
        work = persist_enriched_work(session, _row(Title="FB Default"), tm, tm)  # flag defaults True
        session.flush()
        assert "Fantasy" in _trope_names(session, work)  # fallback stopgap preserved


def test_fallback_skipped_when_work_already_has_real_trope(db_url):
    manager = DatabaseManager(db_url)
    with manager.get_session() as session:
        tm = _PassthroughTrope(session)
        work = persist_enriched_work(
            session,
            _row(Title="FB HasReal", enriched_tropes=[{"trope_name": "Chosen One", "justification": "x"}]),
            tm,
            tm,
        )
        session.flush()
        # a later fallback-style persist (no enriched_tropes) for the SAME work adds NO fallback layer
        persist_enriched_work(session, _row(Title="FB HasReal"), tm, tm)
        session.flush()
        assert _trope_names(session, work) == {"Chosen One"}
```

- [ ] **Step 2: Run** `uv run pytest test/integration/test_persist_fallback_flag.py -v` — SKIP locally / FAIL on CI.

- [ ] **Step 3: Implement.** In `src/agentic_librarian/etl/persist.py`, replace the fallback `else`
block (currently starting `# Fallback to simple tags if no enriched tropes found …`) with:
```python
        else:
            # Fallback genre/mood tropes are a stopgap ONLY for a work with no real (scout) trope, and
            # only when the caller wants them — the two-phase fast pass opts out (write_fallback_tropes
            # =False) because its deep pass supplies the real tropes (Spec #65, 2026-06-23). Cleaned the
            # same way as genres/moods so a fallback can never write a UUID-tailed / unsplit slug.
            has_real_trope = (
                session.query(WorkTrope)
                .filter(WorkTrope.work_id == work.id, WorkTrope.justification.isnot(None))
                .first()
                is not None
            )
            if row.get("write_fallback_tropes", True) and not has_real_trope:
                for tag in all_fallback_tags:
                    for name in clean_trope_name(tag):
                        standardized_trope = _safe_standardize(
                            trope_manager.standardize_trope, name, label=f"trope {name!r}"
                        )
                        if standardized_trope is None:
                            continue
                        existing_link = (
                            session.query(WorkTrope)
                            .filter_by(work_id=work.id, trope_id=standardized_trope.id)
                            .first()
                        )
                        if not existing_link:
                            session.add(WorkTrope(work=work, trope=standardized_trope))
```
Leave the `if enriched_tropes:` (real) branch unchanged.

- [ ] **Step 4: Run** `uv run pytest test/integration/test_persist_fallback_flag.py test/integration/test_persist_trope_guard.py -v` (CI). Expect pass / no regression.

- [ ] **Step 5: Format + commit:**
```bash
uv run ruff check src/agentic_librarian/etl/persist.py test/integration/test_persist_fallback_flag.py
uvx ruff@0.15.16 format src/agentic_librarian/etl/persist.py test/integration/test_persist_fallback_flag.py
git add src/agentic_librarian/etl/persist.py test/integration/test_persist_fallback_flag.py
git commit -m "feat(persist): fast pass opts out of fallback tropes (write_fallback_tropes flag)"
```

---

## Task 2: Two-phase fast pass opts out (`two_phase.py`)

**Files:** Modify `src/agentic_librarian/enrichment/two_phase.py`; Test `test/unit/test_two_phase_fallback_flag.py`

- [ ] **Step 1: Write the failing test** `test/unit/test_two_phase_fallback_flag.py`:
```python
from uuid import uuid4

from agentic_librarian.enrichment import two_phase


def test_scout_and_persist_forwards_fallback_flag(monkeypatch):
    captured = {}

    def fake_persist(session, row, tm, sm):
        captured.update(row)
        return object()

    monkeypatch.setattr(two_phase, "persist_enriched_work", fake_persist)
    monkeypatch.setattr(two_phase, "TropeManager", lambda session: None)
    monkeypatch.setattr(two_phase, "StyleManager", lambda session: None)
    mgr = type("M", (), {"enrich": lambda self, **k: {"genres": ["x"], "moods": []}})()

    two_phase._scout_and_persist(None, mgr, title="T", author="A", fmt="ebook", write_fallback_tropes=False)
    assert captured["write_fallback_tropes"] is False


def test_enrich_fast_opts_out_of_fallback_tropes(monkeypatch):
    seen = {}

    def fake_sap(session, manager, *, title, author, fmt, write_fallback_tropes=True):
        seen["wft"] = write_fallback_tropes
        return type("W", (), {"id": uuid4()})()

    class _Sess:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def query(self, *a, **k):
            return self

        def join(self, *a, **k):
            return self

        def filter(self, *a, **k):
            return self

        def first(self):
            return None  # no existing work -> proceed to scout

        def flush(self):
            pass

    monkeypatch.setattr(two_phase, "_scout_and_persist", fake_sap)
    monkeypatch.setattr(two_phase, "create_fast_scout_manager", lambda: None)
    two_phase.set_db_manager(type("M", (), {"get_session": lambda s: _Sess()})())

    two_phase.enrich_fast("Some Title", "Some Author", "ebook")
    assert seen["wft"] is False
```

- [ ] **Step 2: Run** `uv run pytest test/unit/test_two_phase_fallback_flag.py -v` — FAIL (param/flag not present).

- [ ] **Step 3: Implement.** In `src/agentic_librarian/enrichment/two_phase.py`:

(a) Add the `write_fallback_tropes` param to `_scout_and_persist` and pass it into the row:
```python
def _scout_and_persist(
    session, manager, *, title: str, author: str, fmt: str, write_fallback_tropes: bool = True
) -> Work | None:
```
and in the `row = { ... }` dict add (alongside `"skip_enrichment": False,`):
```python
        "write_fallback_tropes": write_fallback_tropes,
```

(b) In `enrich_fast`, pass `write_fallback_tropes=False` on the fast-tier call:
```python
        work = _scout_and_persist(
            session,
            create_fast_scout_manager(),
            title=title,
            author=author,
            fmt=fmt,
            write_fallback_tropes=False,
        )
```
Leave `enrich_deep`'s `_scout_and_persist` call as-is (default `True` → real tropes, or the genre/mood
fallback only if the deep scout returns none).

- [ ] **Step 4: Run** `uv run pytest test/unit/test_two_phase_fallback_flag.py -v` — PASS.

- [ ] **Step 5: Format + commit:**
```bash
uv run ruff check src/agentic_librarian/enrichment/two_phase.py test/unit/test_two_phase_fallback_flag.py
uvx ruff@0.15.16 format src/agentic_librarian/enrichment/two_phase.py test/unit/test_two_phase_fallback_flag.py
git add src/agentic_librarian/enrichment/two_phase.py test/unit/test_two_phase_fallback_flag.py
git commit -m "feat(two-phase): fast pass passes write_fallback_tropes=False"
```

---

## Task 3: Fallback-prune backfill logic (`trope_backfill.py`)

**Files:** Modify `src/agentic_librarian/etl/trope_backfill.py`; Test `test/integration/test_fallback_prune.py`

- [ ] **Step 1: Write the failing test** `test/integration/test_fallback_prune.py`:
```python
import pytest

from agentic_librarian.db.models import Trope, Work, WorkTrope
from agentic_librarian.db.session import DatabaseManager
from agentic_librarian.etl import trope_backfill as tb

pytestmark = pytest.mark.db_integration


def _link(session, work, name, justification):
    t = Trope(name=name)
    session.add(t)
    session.flush()
    session.add(WorkTrope(work_id=work.id, trope_id=t.id, justification=justification))


def test_prune_removes_fallbacks_only_on_works_with_real_tropes(db_url):
    manager = DatabaseManager(db_url)
    with manager.get_session() as session:
        polluted = Work(title="Polluted")
        clean_only = Work(title="Fallback Only")
        session.add_all([polluted, clean_only])
        session.flush()
        _link(session, polluted, "Chosen One", "scout says so")  # real
        _link(session, polluted, "science-fiction-fantasy", None)  # fallback
        _link(session, polluted, "tense", None)  # fallback
        _link(session, clean_only, "literary-fiction", None)  # fallback, but NO real -> keep
        session.flush()

        deleted = tb.apply_fallback_prune(session)
        session.flush()

        assert deleted == 2
        pol = {session.get(Trope, wt.trope_id).name for wt in session.query(WorkTrope).filter_by(work_id=polluted.id).all()}
        assert pol == {"Chosen One"}  # only the real trope remains
        co = session.query(WorkTrope).filter_by(work_id=clean_only.id).count()
        assert co == 1  # fallback-only work untouched (stopgap preserved)

        assert tb.apply_fallback_prune(session) == 0  # idempotent
```

- [ ] **Step 2: Run** `uv run pytest test/integration/test_fallback_prune.py -v` — SKIP locally / FAIL on CI.

- [ ] **Step 3: Implement.** In `src/agentic_librarian/etl/trope_backfill.py`:

(a) Add `Work` to the models import:
```python
from agentic_librarian.db.models import Trope, Work, WorkTrope
```
(b) Append:
```python
@dataclass
class FallbackPrune:
    work_id: UUID
    title: str
    deleted: list[str]  # fallback trope names removed from this work
    real_kept: int


def plan_fallback_prune(session: Session) -> list[FallbackPrune]:
    """Works that carry BOTH a real (justification IS NOT NULL) and a fallback (justification IS NULL)
    trope — preview the fallback links that would be deleted. Read-only."""
    real_work_ids = {
        wid for (wid,) in session.query(WorkTrope.work_id).filter(WorkTrope.justification.isnot(None)).distinct()
    }
    if not real_work_ids:
        return []
    by_work: dict[UUID, list[WorkTrope]] = {}
    for wt in (
        session.query(WorkTrope)
        .filter(WorkTrope.justification.is_(None), WorkTrope.work_id.in_(real_work_ids))
        .all()
    ):
        by_work.setdefault(wt.work_id, []).append(wt)
    out: list[FallbackPrune] = []
    for wid, links in by_work.items():
        work = session.get(Work, wid)
        names = [session.get(Trope, wt.trope_id).name for wt in links]
        real_kept = (
            session.query(WorkTrope)
            .filter(WorkTrope.work_id == wid, WorkTrope.justification.isnot(None))
            .count()
        )
        out.append(FallbackPrune(wid, work.title if work else str(wid), names, real_kept))
    return out


def apply_fallback_prune(session: Session, changes: list[FallbackPrune] | None = None) -> int:
    """Delete the fallback (NULL-justification) links on each planned work. Link deletion only — no
    Trope rows, no embeddings. Idempotent (a second run finds no work with both layers)."""
    if changes is None:
        changes = plan_fallback_prune(session)
    n = 0
    for c in changes:
        for wt in (
            session.query(WorkTrope)
            .filter(WorkTrope.work_id == c.work_id, WorkTrope.justification.is_(None))
            .all()
        ):
            session.delete(wt)
            n += 1
    session.flush()
    return n


def fallback_prune_inventory(session: Session) -> tuple[int, int]:
    """(polluted works, total fallback links that would be pruned)."""
    plan = plan_fallback_prune(session)
    return len(plan), sum(len(c.deleted) for c in plan)
```
(`dataclass`, `UUID`, `Session`, `WorkTrope` are already imported in this module.)

- [ ] **Step 4: Run** `uv run pytest test/integration/test_fallback_prune.py test/integration/test_trope_backfill.py -v` (CI). Expect pass / no regression.

- [ ] **Step 5: Format + commit:**
```bash
uv run ruff check src/agentic_librarian/etl/trope_backfill.py test/integration/test_fallback_prune.py
uvx ruff@0.15.16 format src/agentic_librarian/etl/trope_backfill.py test/integration/test_fallback_prune.py
git add src/agentic_librarian/etl/trope_backfill.py test/integration/test_fallback_prune.py
git commit -m "feat(tropes): fallback-prune backfill (delete NULL tropes on works with real tropes)"
```

---

## Task 4: `--prune-fallbacks` CLI mode (`clean_catalog.py`)

**Files:** Modify `scripts/clean_catalog.py`; Test `test/unit/test_clean_catalog_prune_cli.py`

- [ ] **Step 1: Write the failing test** `test/unit/test_clean_catalog_prune_cli.py`:
```python
import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "clean_catalog", Path(__file__).resolve().parents[2] / "scripts" / "clean_catalog.py"
)
clean_catalog = importlib.util.module_from_spec(spec)
spec.loader.exec_module(clean_catalog)


class _Sess:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def query(self, *a, **k):
        return self

    def filter(self, *a, **k):
        return self

    def filter_by(self, *a, **k):
        return self

    def distinct(self):
        return self

    def all(self):
        return []

    def count(self):
        return 0

    def __iter__(self):
        return iter([])


def test_prune_fallbacks_refused_without_yes(monkeypatch, capsys):
    monkeypatch.setattr(clean_catalog, "resolve_database_url", lambda: "postgresql://u:p@prod-host/db")
    monkeypatch.setattr(
        clean_catalog, "DatabaseManager", lambda url: type("M", (), {"get_session": lambda s: _Sess()})()
    )
    rc = clean_catalog.main(["--prune-fallbacks", "--apply"])  # no --yes
    assert rc == 2
    assert "REFUSING --apply without --yes" in capsys.readouterr().out
```

- [ ] **Step 2: Run** `uv run pytest test/unit/test_clean_catalog_prune_cli.py -v` — FAIL (`--prune-fallbacks` unknown / branch missing).

- [ ] **Step 3: Implement.** In `scripts/clean_catalog.py`:

(a) Add the arg (next to the other `add_argument` calls):
```python
    ap.add_argument("--prune-fallbacks", action="store_true")
```
(b) In the `if args.inventory:` block, after the dirty-trope print and before `return 0`, add:
```python
            pw, pl = trope_backfill.fallback_prune_inventory(session)
            print(f"\n=== fallback-trope POLLUTION ===\n  {pw} works with real+fallback layers, {pl} fallback links prunable")
```
(c) Add a new branch (place it before the `if args.tropes:` branch so the prune runs first by convention):
```python
        if args.prune_fallbacks:
            changes = trope_backfill.plan_fallback_prune(session)
            total = sum(len(c.deleted) for c in changes)
            print(f"\n{len(changes)} works would have {total} fallback tropes pruned.")
            for c in changes[:80]:
                print(f"  [{c.title[:40]:40}] -{len(c.deleted)} fallback (keep {c.real_kept} real): {c.deleted}")
            early = _refuse(args, url, safe)
            if early is not None:
                return early
            print(f"\napplied: pruned {trope_backfill.apply_fallback_prune(session, changes)} fallback links.")
            return 0
```

- [ ] **Step 4: Run** `uv run pytest test/unit/test_clean_catalog_prune_cli.py test/unit/test_clean_catalog_cli.py -v` — PASS (new + existing).

- [ ] **Step 5: Format + commit:**
```bash
uv run ruff check scripts/clean_catalog.py test/unit/test_clean_catalog_prune_cli.py
uvx ruff@0.15.16 format scripts/clean_catalog.py test/unit/test_clean_catalog_prune_cli.py
git add scripts/clean_catalog.py test/unit/test_clean_catalog_prune_cli.py
git commit -m "feat(cli): clean_catalog --prune-fallbacks mode"
```

---

## Final verification (after all tasks)

- [ ] `uv run pytest test/unit -q` — all unit tests green.
- [ ] `uv run ruff check src/agentic_librarian/etl src/agentic_librarian/enrichment scripts/clean_catalog.py` — clean.
- [ ] Dispatch a final whole-branch reviewer (db_integration runs on CI Postgres).
- [ ] superpowers:finishing-a-development-branch → push + open PR (Gemini review).

## Rollout (operator, after merge + deploy)

Proxy up (`-e PYTHONPATH=/app/src`), against live prod:
1. `python scripts/clean_catalog.py --inventory` — confirm the pollution count.
2. `python scripts/clean_catalog.py --prune-fallbacks --dry-run` → `--prune-fallbacks --apply --yes`.
3. *then* `python scripts/clean_catalog.py --tropes --dry-run` → `--tropes --apply --yes` (the deferred
   trope-name cleaning, now on a smaller, un-muddied set).

Both convergent / retry-safe.
