# History Format Edit Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let users change a history entry's format via `PATCH /history/{id}`, repointing the row to the right sibling `Edition` and asynchronously filling that edition's metadata (ISBN, page/audio counts; narrators + narrator styles for audiobooks) via a new targeted Cloud Tasks pass.

**Architecture:** `format` lives on `Edition` (unique `(work_id, format)`, `NULLS NOT DISTINCT`), so a format change is a repoint to a get-or-created sibling edition of the same Work — never a mutation of the shared edition. A new `complete_edition(work_id, fmt)` pass in `two_phase.py` (short read session → scouts with NO session held, the #94 rule → fresh write session) runs the fast API scouts plus, for audiobooks, the audiobook LLM scouts and per-narrator style scouting — never `LLMTropeScout` or author/work-style scouting. Persistence reuses the "Edition & Narrators" section of `persist_enriched_work`, extracted into a shared helper.

**Tech Stack:** FastAPI + SQLAlchemy 2 (Postgres-only models — never run them on sqlite), Cloud Tasks (OIDC-gated internal endpoints), React + vitest frontend, pytest.

**Spec:** `docs/superpowers/specs/2026-07-18-history-format-edit-design.md`

## Global Constraints

- Tests: `.venv/Scripts/python -m pytest ...` from repo root (Windows host venv). Full **unit** suite before each commit.
- `db_integration`-marked tests are **deselected locally** (no Postgres) — CI is their gate. They must still be written to pass in CI on the first run.
- Suite policy: `filterwarnings = ["error::sqlalchemy.exc.SAWarning"]` — never downgrade; if an SAWarning appears you have found a bug.
- Case-driven tests are parametrized (`pytest.mark.parametrize` / `test.each`), each case atomic and named — never loops inside one test body.
- Lint AND format before every commit: `uvx ruff check <files>` **and** `uvx ruff format <files>`.
- No `[skip ci]` anywhere in commit messages.
- Format vocab (exact strings, lowercase): `ebook`, `audiobook`, `paperback`, `hardcover`.
- Commit messages end with the Co-Authored-By / Claude-Session trailer per session convention.
- Work on a feature branch (e.g. `feat/history-format-edit`), not `main`; squash-merge PR at the end per repo convention.

---

### Task 1: Extract `merge_edition_and_narrators` from `persist_enriched_work` (pure refactor)

**Files:**
- Modify: `src/agentic_librarian/etl/persist.py` (the "# 3. Edition & Narrators" section, ~L264–353)

**Interfaces:**
- Produces: `merge_edition_and_narrators(session, *, work_id, fmt, isbn_13=None, page_count=None, audio_minutes=None, publication_date=None, narrator_names=None, narrator_styles=None, style_manager, apply_metadata=True) -> Edition` in `agentic_librarian.etl.persist`. Task 3 imports and calls it.
- Behavior contract (must be preserved EXACTLY — this is a lift-and-call refactor, not a rewrite):
  - Case-insensitive narrator reuse; `insert_or_requery` #95 backstops for Narrator and Edition; narrator-name dedup via `norm_name`; NaN/non-list coercion of `narrator_names`/`narrator_styles`.
  - New edition: created with all metadata + narrators via `insert_or_requery`, then `session.flush()` (id must be populated for the caller's ReadingHistory block).
  - Existing edition: metadata merge (`isbn_13 = isbn_13 or edition.isbn_13`, same for `page_count`, `audio_minutes`; `publication_date` is NOT updated on existing editions) gated by `apply_metadata`; narrator merge (`set` union) NOT gated.
  - Narrator styles standardized via `style_manager.standardize_style` through the existing `_safe_standardize`/`_iter_style_items` helpers, with the existing-link dedup query.

- [ ] **Step 1: Write the helper and re-wire the call site**

In `src/agentic_librarian/etl/persist.py`, add a new module-level function ABOVE `persist_enriched_work` (move the existing GH #95 comments with the code they explain):

```python
def merge_edition_and_narrators(
    session,
    *,
    work_id,
    fmt,
    isbn_13=None,
    page_count=None,
    audio_minutes=None,
    publication_date=None,
    narrator_names=None,
    narrator_styles=None,
    style_manager,
    apply_metadata=True,
):
    """Resolve narrators (+ narrator styles) and get-or-create/merge the (work_id, fmt)
    Edition. Extracted from persist_enriched_work (history-format-edit spec) so the
    format-completion pass (two_phase.complete_edition) shares the exact same merge
    semantics. apply_metadata=False (persist's skip_enrichment rows) still merges
    narrators, mirroring the original gating. Returns the Edition, flushed when newly
    created so its id is populated for the caller."""
    edition = session.query(Edition).filter_by(work_id=work_id, format=fmt).first()

    # A row may carry narrator_names/styles as NaN (float) — pandas fills the column with
    # NaN for rows that lack it. Coerce non-list/dict to empty so this never crashes.
    if not isinstance(narrator_names, list):
        narrator_names = []
    # Keep only non-empty strings: a malformed/NaN element would crash norm_name/.lower().
    narrator_names = [n for n in narrator_names if isinstance(n, str) and n.strip()]
    if not isinstance(narrator_styles, dict):
        narrator_styles = {}

    seen_narr: set[str] = set()
    deduped_names = []
    for n_name in narrator_names:
        k = norm_name(n_name)
        if k not in seen_narr:
            seen_narr.add(k)
            deduped_names.append(n_name)
    narrator_names = deduped_names

    narrator_objs = []
    for n_name in narrator_names:
        # Case-insensitive lookup so a casing variant reuses the existing Narrator row.
        narrator = session.query(Narrator).filter(func.lower(Narrator.name) == n_name.lower()).first()
        if not narrator:
            # GH #95: uq_narrators_name_lower — same case-insensitive requery pattern as Author.
            narrator, _created = insert_or_requery(
                session,
                Narrator(name=n_name),
                lambda n_name=n_name: (
                    session.query(Narrator).filter(func.lower(Narrator.name) == n_name.lower()).first()
                ),
            )

        n_style_data = narrator_styles.get(n_name, {})
        if n_style_data:
            for attr_type, style_name in _iter_style_items(n_style_data, f"Narrator '{n_name}'"):
                standard_style = _safe_standardize(
                    style_manager.standardize_style, style_name, category="Narrator", label=f"style {style_name!r}"
                )
                if standard_style is None:
                    continue
                existing_link = (
                    session.query(NarratorStyle)
                    .filter_by(narrator_id=narrator.id, style_id=standard_style.id, attribute_type=attr_type)
                    .first()
                )
                if not existing_link:
                    session.add(NarratorStyle(narrator=narrator, style=standard_style, attribute_type=attr_type))

        narrator_objs.append(narrator)

    if not edition:
        # GH #95: uq_editions_work_format backstops the SELECT-then-INSERT race above; a
        # concurrent persist for the same (work_id, format) recovers via requery instead of
        # a 500. narrators/other creation-only fields only apply on the winning insert.
        # work_id= (not work=) so the not-yet-added Edition never lands in work.editions via
        # the back_populates backref before session.add — that dangling membership is exactly
        # what trips "Object of type <Edition> not in session" as an SAWarning-promoted error.
        edition, _created = insert_or_requery(
            session,
            Edition(
                work_id=work_id,
                isbn_13=isbn_13,
                format=fmt,
                page_count=page_count,
                audio_minutes=audio_minutes,
                publication_date=publication_date,
                narrators=narrator_objs,
            ),
            lambda: session.query(Edition).filter_by(work_id=work_id, format=fmt).first(),
        )
        session.flush()  # Ensure edition.id is populated for the caller's ReadingHistory check
    else:
        if apply_metadata:
            # Update existing edition if new metadata found (publication_date intentionally
            # not updated on existing editions — original behavior preserved).
            edition.isbn_13 = isbn_13 or edition.isbn_13
            edition.page_count = page_count or edition.page_count
            edition.audio_minutes = audio_minutes or edition.audio_minutes
        if narrator_objs:
            edition.narrators = list(set(edition.narrators) | set(narrator_objs))

    return edition
```

Then replace the entire `# 3. Edition & Narrators` block inside `persist_enriched_work` (from the `edition = session.query(Edition)...` line through the `edition.narrators = list(set(...))` line inclusive) with:

```python
    # 3. Edition & Narrators (shared with two_phase.complete_edition — history-format-edit)
    edition = merge_edition_and_narrators(
        session,
        work_id=work.id,
        fmt=row.get("format"),
        isbn_13=isbn_13,
        page_count=page_count,
        audio_minutes=audio_minutes,
        publication_date=publication_date,
        narrator_names=row.get("narrator_names"),
        narrator_styles=row.get("narrator_styles"),
        style_manager=style_manager,
        apply_metadata=not row.get("skip_enrichment"),
    )
```

(Note the one intentional unification: the original block read `row["format"]` in the SELECT but `row.get("format")` at creation; the helper uses one `fmt` value for both. Every caller populates `"format"`, so behavior is unchanged.)

- [ ] **Step 2: Run the persist-touching unit tests**

Run: `.venv/Scripts/python -m pytest test/unit/test_persist_styles.py test/unit/test_two_phase_fallback_flag.py test/unit/test_two_phase_redirect.py test/unit/test_two_phase_sessions.py -v`
Expected: ALL PASS (pure refactor — any failure means the extraction changed behavior; fix the extraction, not the tests).

- [ ] **Step 3: Run the full unit suite**

Run: `.venv/Scripts/python -m pytest test/unit -v`
Expected: PASS (known env-dependent exceptions: live-network, `db` hostname, optional `claude_agent_sdk` — name them explicitly if they appear).

- [ ] **Step 4: Lint, format, commit**

```powershell
uvx ruff check src/agentic_librarian/etl/persist.py; uvx ruff format src/agentic_librarian/etl/persist.py
git add src/agentic_librarian/etl/persist.py
git commit -m "refactor(etl): extract merge_edition_and_narrators from persist_enriched_work"
```
(The `db_integration` persist suites — `test_persist_enriched.py`, `test_persist_styles`' DB cousins etc. — execute FIRST in CI and are the real gate for this refactor.)

---

### Task 2: `create_completion_scout_manager` factory

**Files:**
- Modify: `src/agentic_librarian/orchestration/definitions.py`
- Test: `test/unit/test_scout_factories.py`

**Interfaces:**
- Produces: `create_completion_scout_manager() -> ScoutManager` registering EXACTLY `HardcoverScout(priority=1)`, `GoogleBooksScout(2)`, `AudiobookScout(3)`, `DirectKnowledgeScout(4)`. No `StyleScout`, no `LLMTropeScout`. (Audiobook/DirectKnowledge self-skip inside `ScoutManager.enrich` when `"audiobook" not in format.lower()` — so this one manager serves every format.) Task 3 consumes it.

- [ ] **Step 1: Write the failing test**

Append to `test/unit/test_scout_factories.py` (match the file's existing import style):

```python
def test_completion_manager_composition():
    """Format-completion pass (history-format-edit spec): fast API scouts + audiobook
    scouts ONLY — never LLMTropeScout (paid trope pass) or StyleScout (author/work
    styles); narrator styles are scouted directly by two_phase.complete_edition."""
    from agentic_librarian.orchestration.definitions import create_completion_scout_manager
    from agentic_librarian.scouts.metadata_scout import (
        AudiobookScout,
        DirectKnowledgeScout,
        GoogleBooksScout,
        HardcoverScout,
    )

    manager = create_completion_scout_manager()
    assert [type(s) for s, _priority in manager.scouts] == [
        HardcoverScout,
        GoogleBooksScout,
        AudiobookScout,
        DirectKnowledgeScout,
    ]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python -m pytest test/unit/test_scout_factories.py::test_completion_manager_composition -v`
Expected: FAIL with `ImportError: cannot import name 'create_completion_scout_manager'`

- [ ] **Step 3: Implement the factory**

In `src/agentic_librarian/orchestration/definitions.py`, after `create_deep_scout_manager`:

```python
def create_completion_scout_manager() -> ScoutManager:
    """Format-completion pass (history-format-edit): the fast API scouts fetch the new
    format's edition metadata (ISBN, pages/audio minutes, publication date); the audiobook
    scouts (which self-skip on non-audiobook formats) add narrators. Deliberately NO
    LLMTropeScout and NO StyleScout — tropes and author/work styles belong to the Work,
    which a format change does not touch; narrator styles are scouted directly by
    two_phase.complete_edition."""
    manager = ScoutManager()
    manager.register_scout(HardcoverScout(), priority=1)
    manager.register_scout(GoogleBooksScout(), priority=2)
    manager.register_scout(AudiobookScout(), priority=3)
    manager.register_scout(DirectKnowledgeScout(), priority=4)
    return manager
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python -m pytest test/unit/test_scout_factories.py -v`
Expected: PASS (whole file — the existing factory tests must stay green).

- [ ] **Step 5: Lint, format, commit**

```powershell
uvx ruff check src/agentic_librarian/orchestration/definitions.py test/unit/test_scout_factories.py; uvx ruff format src/agentic_librarian/orchestration/definitions.py test/unit/test_scout_factories.py
git add src/agentic_librarian/orchestration/definitions.py test/unit/test_scout_factories.py
git commit -m "feat(scouts): completion scout manager for the format-completion pass"
```

---

### Task 3: `complete_edition` pass in `two_phase.py`

**Files:**
- Modify: `src/agentic_librarian/enrichment/two_phase.py`
- Test: `test/unit/test_edition_completion.py` (new)

**Interfaces:**
- Consumes: `merge_edition_and_narrators` (Task 1), `create_completion_scout_manager` (Task 2), existing `StyleScout.scout_narrator_style(name) -> dict`, `get_cached_embedding(EMBED_MODEL, text)`.
- Produces: `complete_edition(work_id: UUID, fmt: str) -> str` returning `"missing" | "empty" | "done"`. Task 5's endpoint consumes it. Statuses: `"missing"` = work/author/edition gone (non-retryable); `"empty"` = no scout contributed (final, 200); `"done"` = merged.

- [ ] **Step 1: Write the failing tests**

Create `test/unit/test_edition_completion.py`:

```python
"""complete_edition (history-format-edit): targeted format-completion pass.

Session discipline (#94), status contract, and scout composition — narrator styles are
scouted ONLY for audiobook formats, and only via scout_narrator_style (never
StyleScout.search, which would re-scout author/work styles)."""

from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from agentic_librarian.enrichment import two_phase


class _FakeSession:
    """Counting session double (the house #94 pattern, test_two_phase_sessions.py)."""

    def __init__(self, state, work, edition):
        self._state = state
        self._work = work
        self._edition = edition

    def __enter__(self):
        self._state["open"] += 1
        m = MagicMock()
        m.get.return_value = self._work
        m.query.return_value.filter_by.return_value.first.return_value = self._edition
        return m

    def __exit__(self, *a):
        self._state["open"] -= 1
        return False


def _work_double(title="T", author="A"):
    work = MagicMock()
    work.title = title
    contrib = MagicMock(role="Author")
    contrib.author.name = author
    work.contributors = [contrib]
    return work


def _wire(monkeypatch, *, work, edition, enriched, state=None):
    state = state if state is not None else {"open": 0}
    fake_manager = MagicMock()
    fake_manager.get_session = lambda: _FakeSession(state, work, edition)
    monkeypatch.setattr(two_phase, "db_manager", fake_manager)
    scout_mgr = MagicMock()
    scout_mgr.enrich.return_value = enriched
    monkeypatch.setattr(two_phase, "create_completion_scout_manager", lambda: scout_mgr)
    return state, scout_mgr


def test_scouts_run_outside_any_session(monkeypatch):
    state, scout_mgr = _wire(monkeypatch, work=_work_double(), edition=MagicMock(), enriched={})
    seen = {}
    scout_mgr.enrich.side_effect = lambda **kw: seen.setdefault("open_during_scout", state["open"]) or {}
    assert two_phase.complete_edition(uuid4(), "ebook") == "empty"
    assert seen["open_during_scout"] == 0  # THE #94 assertion


@pytest.mark.parametrize(
    ("work", "edition"),
    [
        pytest.param(None, MagicMock(), id="work_gone"),
        pytest.param(_work_double(), None, id="edition_gone"),
        pytest.param(MagicMock(title="T", contributors=[]), MagicMock(), id="no_author"),
    ],
)
def test_missing_paths(monkeypatch, work, edition):
    _wire(monkeypatch, work=work, edition=edition, enriched={})
    assert two_phase.complete_edition(uuid4(), "ebook") == "missing"


def test_empty_scouts_is_final(monkeypatch):
    _wire(monkeypatch, work=_work_double(), edition=MagicMock(), enriched={})
    assert two_phase.complete_edition(uuid4(), "audiobook") == "empty"


def test_done_merges_scouted_values_for_audiobook(monkeypatch):
    enriched = {
        "isbn_13": "9780000000000",
        "page_count": 300,
        "audio_minutes": 600,
        "publication_date": "2020-01-01",
        "narrator_names": ["Ray Porter"],
        "source_priority": ["Hardcover"],
    }
    _wire(monkeypatch, work=_work_double(), edition=MagicMock(), enriched=enriched)
    monkeypatch.setattr(two_phase, "get_cached_embedding", lambda *a, **k: [0.0])
    style_scout = MagicMock()
    style_scout.scout_narrator_style.return_value = {"pacing": "brisk"}
    monkeypatch.setattr(two_phase, "StyleScout", lambda: style_scout)
    merged = {}

    def fake_merge(session, **kwargs):
        merged.update(kwargs)
        return MagicMock()

    monkeypatch.setattr(two_phase, "merge_edition_and_narrators", fake_merge)

    assert two_phase.complete_edition(uuid4(), "audiobook") == "done"
    style_scout.scout_narrator_style.assert_called_once_with("Ray Porter")
    assert merged["fmt"] == "audiobook"
    assert merged["isbn_13"] == "9780000000000"
    assert merged["audio_minutes"] == 600
    assert merged["narrator_names"] == ["Ray Porter"]
    assert merged["narrator_styles"] == {"Ray Porter": {"pacing": "brisk"}}


def test_non_audiobook_never_scouts_narrator_styles(monkeypatch):
    enriched = {"isbn_13": "9780000000001", "narrator_names": [], "source_priority": ["Hardcover"]}
    _wire(monkeypatch, work=_work_double(), edition=MagicMock(), enriched=enriched)
    monkeypatch.setattr(two_phase, "merge_edition_and_narrators", lambda session, **kw: MagicMock())
    with patch.object(two_phase, "StyleScout") as style_cls:
        assert two_phase.complete_edition(uuid4(), "paperback") == "done"
    style_cls.assert_not_called()


def test_style_scout_failure_degrades_to_no_styles(monkeypatch):
    enriched = {"narrator_names": ["Ray Porter"], "source_priority": ["Audible"]}
    _wire(monkeypatch, work=_work_double(), edition=MagicMock(), enriched=enriched)
    monkeypatch.setattr(two_phase, "get_cached_embedding", lambda *a, **k: [0.0])
    broken = MagicMock()
    broken.scout_narrator_style.side_effect = RuntimeError("LLM down")
    monkeypatch.setattr(two_phase, "StyleScout", lambda: broken)
    merged = {}
    monkeypatch.setattr(
        two_phase, "merge_edition_and_narrators", lambda session, **kw: merged.update(kw) or MagicMock()
    )
    assert two_phase.complete_edition(uuid4(), "audiobook") == "done"
    assert merged["narrator_styles"] == {}  # narrators still merge; styles degrade


def test_work_deleted_mid_pass_returns_missing(monkeypatch):
    """The write session re-checks existence (same honesty rule as enrich_deep)."""
    enriched = {"isbn_13": "9780000000002", "narrator_names": [], "source_priority": ["Hardcover"]}
    state = {"open": 0, "calls": 0}

    work = _work_double()

    class _VanishingSession(_FakeSession):
        def __enter__(self):
            self._state["open"] += 1
            self._state["calls"] += 1
            m = MagicMock()
            # First (read) session sees the work; second (write) session finds it gone.
            m.get.return_value = work if self._state["calls"] == 1 else None
            m.query.return_value.filter_by.return_value.first.return_value = MagicMock()
            return m

    fake_manager = MagicMock()
    fake_manager.get_session = lambda: _VanishingSession(state, work, MagicMock())
    monkeypatch.setattr(two_phase, "db_manager", fake_manager)
    scout_mgr = MagicMock()
    scout_mgr.enrich.return_value = enriched
    monkeypatch.setattr(two_phase, "create_completion_scout_manager", lambda: scout_mgr)
    assert two_phase.complete_edition(uuid4(), "ebook") == "missing"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python -m pytest test/unit/test_edition_completion.py -v`
Expected: FAIL with `AttributeError: module ... has no attribute 'complete_edition'` (and missing imports).

- [ ] **Step 3: Implement `complete_edition`**

In `src/agentic_librarian/enrichment/two_phase.py`:

Add imports (extend existing import lines where the module already imports from that source):

```python
from agentic_librarian.etl.persist import collect_embedding_texts, merge_edition_and_narrators, persist_enriched_work
from agentic_librarian.orchestration.definitions import (
    create_completion_scout_manager,
    create_deep_scout_manager,
    create_fast_scout_manager,
)
from agentic_librarian.scouts.metadata_scout import StyleScout
```

Add after `add_read_event`:

```python
def complete_edition(work_id: UUID, fmt: str) -> str:
    """Format-completion pass (history-format-edit spec): fill the (work_id, fmt)
    edition's metadata after a history-entry format change. Fast API scouts always
    (ISBN, pages/audio minutes, publication date); audiobook scouts + per-narrator
    style scouting only when fmt is an audiobook. Deliberately NEVER LLMTropeScout or
    author/work-style scouting — the Work is unchanged; this pass must not touch
    deep_enriched_at or the deep pass's retry gating.

    Same session discipline as the other passes here (#94): short read session, scouts
    with NO session held, fresh write session. Idempotent (all merges) — Cloud Tasks
    redelivery is safe.

    Returns:
      "missing" — work, its Author link, or the (work_id, fmt) edition no longer exists
                  (also when the work vanished while scouts ran). Non-retryable.
      "empty"   — no scout contributed anything. Final state: the entry itself is already
                  saved, and unlike the deep pass there is no requeue-sweep backstop
                  economics to protect — do not retry-loop.
      "done"    — scouted values merged onto the edition."""
    fmt = (fmt or "")[:50]

    with db_manager.get_session() as session:
        work = session.get(Work, work_id)
        if work is None:
            return "missing"
        author = next((c.author.name for c in work.contributors if c.role == "Author"), None)
        if author is None:
            return "missing"
        title = work.title  # scalars captured before close (detached-instance rule)
        edition = session.query(Edition).filter_by(work_id=work_id, format=fmt).first()
        if edition is None:
            return "missing"

    enriched = create_completion_scout_manager().enrich(title=title, author=author, format=fmt)
    if not enriched:
        return "empty"

    narrator_names = [n for n in (enriched.get("narrator_names") or []) if isinstance(n, str) and n.strip()]
    narrator_styles: dict[str, dict] = {}
    if "audiobook" in fmt.lower() and narrator_names:
        style_scout = StyleScout()
        for n_name in narrator_names:
            try:
                narrator_styles[n_name] = style_scout.scout_narrator_style(n_name)
            except Exception:  # noqa: BLE001 - style scouting is additive; narrators still merge without it
                logger.warning("narrator style scout failed for %r — merging narrator without styles", n_name)

    # GH #123: warm the embedding LRU for every style string the persist will standardize,
    # so no network embed happens inside the write session.
    for style_map in narrator_styles.values():
        for style_text in style_map.values():
            try:
                get_cached_embedding(EMBED_MODEL, style_text)
            except Exception:  # noqa: BLE001 - warming is best-effort; _safe_standardize degrades in-session
                logger.warning("embed warm failed for %r — persist will retry in-session", style_text)

    with db_manager.get_session() as session:
        if session.get(Work, work_id) is None:
            # Deleted while the scouts ran with no session held — same honesty rule as
            # enrich_deep's empty path.
            return "missing"
        merge_edition_and_narrators(
            session,
            work_id=work_id,
            fmt=fmt,
            isbn_13=enriched.get("isbn_13"),
            page_count=enriched.get("page_count"),
            audio_minutes=enriched.get("audio_minutes"),
            publication_date=enriched.get("publication_date"),
            narrator_names=narrator_names,
            narrator_styles=narrator_styles,
            style_manager=StyleManager(session=session),
        )
        session.flush()
    return "done"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python -m pytest test/unit/test_edition_completion.py test/unit/test_two_phase_sessions.py -v`
Expected: PASS.

- [ ] **Step 5: Lint, format, commit**

```powershell
uvx ruff check src/agentic_librarian/enrichment/two_phase.py test/unit/test_edition_completion.py; uvx ruff format src/agentic_librarian/enrichment/two_phase.py test/unit/test_edition_completion.py
git add src/agentic_librarian/enrichment/two_phase.py test/unit/test_edition_completion.py
git commit -m "feat(enrichment): complete_edition targeted format-completion pass"
```

---

### Task 4: `enqueue_edition_completion` Cloud Tasks helper

**Files:**
- Modify: `src/agentic_librarian/enrichment/tasks.py`
- Test: `test/unit/test_enqueue_enrichment.py`

**Interfaces:**
- Produces: `enqueue_edition_completion(work_id: str, fmt: str) -> bool` — True if enqueued, False when Cloud Tasks is unconfigured (local dev). Task 6 consumes it. Task URL: `POST {base}/internal/complete-edition/{work_id}?format={fmt}`; OIDC audience defaults to the URL WITHOUT the query string (`ENRICH_OIDC_AUDIENCE` overrides, as in prod).

- [ ] **Step 1: Write the failing tests**

Append to `test/unit/test_enqueue_enrichment.py` (reuses that file's `_FakeClient` and `_set_env`):

```python
def test_enqueue_edition_completion_targets_the_completion_route(monkeypatch):
    _set_env(monkeypatch)
    fake = _FakeClient()
    monkeypatch.setattr(tasks, "_client", lambda: fake)

    assert tasks.enqueue_edition_completion("11111111-1111-4111-8111-111111111111", "audiobook") is True

    parent, task = fake.created[0]
    assert parent == "projects/p/locations/us-central1/queues/enrich"
    http = task["http_request"]
    assert http["url"] == (
        "https://librarian.example.run.app/internal/complete-edition/"
        "11111111-1111-4111-8111-111111111111?format=audiobook"
    )
    assert http["oidc_token"]["service_account_email"] == "queue-invoker@p.iam.gserviceaccount.com"
    # Audience must NOT include the query string (a per-task audience would break a fixed
    # receiver-side ENRICH_OIDC_AUDIENCE check).
    assert http["oidc_token"]["audience"] == (
        "https://librarian.example.run.app/internal/complete-edition/11111111-1111-4111-8111-111111111111"
    )


def test_enqueue_edition_completion_skips_when_not_configured(monkeypatch):
    monkeypatch.delenv("CLOUD_TASKS_QUEUE", raising=False)
    called = {"n": 0}
    monkeypatch.setattr(tasks, "_client", lambda: called.__setitem__("n", called["n"] + 1))

    assert tasks.enqueue_edition_completion("abc", "audiobook") is False
    assert called["n"] == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python -m pytest test/unit/test_enqueue_enrichment.py -v`
Expected: the two new tests FAIL with `AttributeError: ... 'enqueue_edition_completion'`; existing tests PASS.

- [ ] **Step 3: Implement the helper**

In `src/agentic_librarian/enrichment/tasks.py`, add `from urllib.parse import quote` to the imports, then after `enqueue_enrichment`:

```python
def enqueue_edition_completion(work_id: str, fmt: str) -> bool:
    """Enqueue the format-completion pass for (work_id, fmt) — history-format-edit spec.
    Returns True if enqueued, False if Cloud Tasks is not configured (local dev); the
    caller (PATCH /history) treats False/raised as non-fatal — the edit is already saved."""
    queue = os.environ.get("CLOUD_TASKS_QUEUE")
    base = os.environ.get("ENRICH_TARGET_BASE_URL")
    sa = os.environ.get("ENRICH_INVOKER_SA")
    if not (queue and base and sa):
        logger.info("edition-completion enqueue skipped — Cloud Tasks not configured (work %s)", work_id)
        return False

    path_url = f"{base.rstrip('/')}/internal/complete-edition/{work_id}"
    url = f"{path_url}?format={quote(fmt)}"
    # Audience deliberately excludes the query string: the receiver verifies against a
    # single fixed ENRICH_OIDC_AUDIENCE, so a per-format audience would never match.
    audience = os.environ.get("ENRICH_OIDC_AUDIENCE") or path_url
    task = {
        "http_request": {
            "http_method": "POST",
            "url": url,
            "oidc_token": {"service_account_email": sa, "audience": audience},
        }
    }
    _client().create_task(parent=queue, task=task)
    logger.info("enqueued edition completion for work %s format %s", work_id, fmt)
    return True
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python -m pytest test/unit/test_enqueue_enrichment.py test/unit/test_tasks_client_cache.py -v`
Expected: PASS.

- [ ] **Step 5: Lint, format, commit**

```powershell
uvx ruff check src/agentic_librarian/enrichment/tasks.py test/unit/test_enqueue_enrichment.py; uvx ruff format src/agentic_librarian/enrichment/tasks.py test/unit/test_enqueue_enrichment.py
git add src/agentic_librarian/enrichment/tasks.py test/unit/test_enqueue_enrichment.py
git commit -m "feat(enrichment): enqueue helper for the edition-completion pass"
```

---

### Task 5: internal endpoint `POST /internal/complete-edition/{work_id}`

**Files:**
- Modify: `src/agentic_librarian/api/internal.py`
- Test: `test/integration/test_internal_complete_edition_api.py` (new)

**Interfaces:**
- Consumes: `two_phase.complete_edition(work_id, fmt) -> "missing" | "empty" | "done"` (Task 3); existing `_require_queue_caller`.
- Produces: `POST /internal/complete-edition/{work_id}?format=<fmt>` — 401/403 per OIDC gate, 404 on `"missing"`, 422 on missing `format` query param, else 200 `{"work_id", "format", "status"}`. This is the URL Task 4 enqueues.

- [ ] **Step 1: Write the failing tests**

Create `test/integration/test_internal_complete_edition_api.py`:

```python
"""OIDC gate + status mapping for the edition-completion internal endpoint.

Mirrors test_internal_enrich_api.py: db_integration because the FastAPI app import
chain needs real settings, but complete_edition itself is monkeypatched — the pass's
own behavior is covered by test/unit/test_edition_completion.py."""

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from agentic_librarian.api import internal as internal_mod
from agentic_librarian.api import main as api_main

pytestmark = pytest.mark.db_integration

VALID_AUD = "https://librarian.example.run.app/internal/enrich/x"
QUEUE_SA = "queue-invoker@p.iam.gserviceaccount.com"


@pytest.fixture
def client(db_url, monkeypatch):
    monkeypatch.setenv("ENRICH_INVOKER_SA", QUEUE_SA)
    monkeypatch.setenv("ENRICH_OIDC_AUDIENCE", VALID_AUD)
    yield TestClient(api_main.app)


def _as_queue(monkeypatch):
    monkeypatch.setattr(
        internal_mod, "_verify_oidc", lambda token, audience: {"email": QUEUE_SA, "email_verified": True}
    )


def test_valid_queue_token_runs_completion(client, monkeypatch):
    _as_queue(monkeypatch)
    called = {}

    def fake_complete(wid, fmt):
        called["args"] = (wid, fmt)
        return "done"

    monkeypatch.setattr(internal_mod.two_phase, "complete_edition", fake_complete)
    wid = uuid4()
    resp = client.post(f"/internal/complete-edition/{wid}?format=audiobook", headers={"Authorization": "Bearer ok"})
    assert resp.status_code == 200
    assert resp.json() == {"work_id": str(wid), "format": "audiobook", "status": "done"}
    assert called["args"] == (wid, "audiobook")


def test_missing_work_is_404_non_retryable(client, monkeypatch):
    _as_queue(monkeypatch)
    monkeypatch.setattr(internal_mod.two_phase, "complete_edition", lambda wid, fmt: "missing")
    resp = client.post(f"/internal/complete-edition/{uuid4()}?format=ebook", headers={"Authorization": "Bearer ok"})
    assert resp.status_code == 404


def test_empty_scouts_is_200_final(client, monkeypatch):
    _as_queue(monkeypatch)
    monkeypatch.setattr(internal_mod.two_phase, "complete_edition", lambda wid, fmt: "empty")
    resp = client.post(f"/internal/complete-edition/{uuid4()}?format=ebook", headers={"Authorization": "Bearer ok"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "empty"


def test_missing_token_is_401(client):
    assert client.post(f"/internal/complete-edition/{uuid4()}?format=ebook").status_code == 401


def test_wrong_service_account_is_403(client, monkeypatch):
    monkeypatch.setattr(
        internal_mod, "_verify_oidc", lambda token, audience: {"email": "attacker@evil.com", "email_verified": True}
    )
    resp = client.post(f"/internal/complete-edition/{uuid4()}?format=ebook", headers={"Authorization": "Bearer x"})
    assert resp.status_code == 403


def test_missing_format_param_is_422(client, monkeypatch):
    _as_queue(monkeypatch)
    resp = client.post(f"/internal/complete-edition/{uuid4()}", headers={"Authorization": "Bearer ok"})
    assert resp.status_code == 422
```

- [ ] **Step 2: Verify collection (local run deselects db_integration)**

Run: `.venv/Scripts/python -m pytest test/integration/test_internal_complete_edition_api.py --collect-only -q`
Expected: 6 tests collected, no import errors. (Execution is CI's job — say so honestly in the task report; with a local compose db, `POSTGRES_HOST=localhost` runs them for real.)

- [ ] **Step 3: Implement the endpoint**

In `src/agentic_librarian/api/internal.py`, add `Query` to the fastapi import, then after the `enrich` route:

```python
@router.post("/internal/complete-edition/{work_id}")
def complete_edition(
    work_id: UUID,
    format: str = Query(..., max_length=50),  # noqa: B008
    authorization: str | None = Header(None),  # noqa: B008
):
    """Format-completion pass target (history-format-edit spec). Same OIDC gate as
    /internal/enrich. 'missing' → 404 (non-retryable: work/edition gone); 'empty' and
    'done' → 200 (final — no retry economics here, the history edit is already saved);
    an unexpected exception propagates → 500 → normal Cloud Tasks retry."""
    _require_queue_caller(authorization)
    result = two_phase.complete_edition(work_id, format)
    if result == "missing":
        raise HTTPException(status_code=404, detail="work or edition not found")
    return {"work_id": str(work_id), "format": format, "status": result}
```

- [ ] **Step 4: Re-verify collection + run the existing internal-route unit guard**

Run: `.venv/Scripts/python -m pytest test/integration/test_internal_complete_edition_api.py --collect-only -q; .venv/Scripts/python -m pytest test/unit -k "internal" -v`
Expected: collection clean; unit `internal` tests PASS.

- [ ] **Step 5: Lint, format, commit**

```powershell
uvx ruff check src/agentic_librarian/api/internal.py test/integration/test_internal_complete_edition_api.py; uvx ruff format src/agentic_librarian/api/internal.py test/integration/test_internal_complete_edition_api.py
git add src/agentic_librarian/api/internal.py test/integration/test_internal_complete_edition_api.py
git commit -m "feat(api): internal complete-edition endpoint for format changes"
```

---

### Task 6: `PATCH /history/{id}` format support

**Files:**
- Modify: `src/agentic_librarian/api/main.py` (`HistoryUpdate` model + `update_history` handler)
- Test: `test/unit/test_api_history.py` (validation cases), `test/integration/test_api_history_db.py` (behavior)

**Interfaces:**
- Consumes: `enqueue_edition_completion(work_id: str, fmt: str) -> bool` (Task 4), existing `get_or_create`, `_history_item`, `_history_options`.
- Produces: `PATCH /history/{id}` accepts `format` (vocab `ebook|audiobook|paperback|hardcover`, case-normalized); 409 on collision; response = `_history_item` payload + `"enrichment_enqueued": bool`. Task 7's frontend consumes the 409 detail string and the `format` field.

- [ ] **Step 1: Write the failing unit validation tests**

Append to `test/unit/test_api_history.py`:

```python
def _patch_with_mock_row(json_body):
    """PATCH against a minimal mocked row (validation-focused unit seam)."""
    with patch("agentic_librarian.api.main.db_manager") as mock_db:
        mock_session = MagicMock()
        mock_db.get_session.return_value.__enter__.return_value = mock_session
        _history_chain(mock_session, [])
        return client.patch("/history/00000000-0000-4000-8000-00000000abcd", json=json_body)


@pytest.mark.parametrize(
    ("body", "expected_status"),
    [
        pytest.param({"format": "vinyl"}, 422, id="unknown_format_rejected"),
        pytest.param({"format": ""}, 422, id="empty_format_rejected"),
        pytest.param({"format": None}, 422, id="null_format_rejected"),
        pytest.param({"format": 7}, 422, id="non_string_format_rejected"),
    ],
)
def test_patch_history_format_vocab_validation(body, expected_status):
    assert _patch_with_mock_row(body).status_code == expected_status


@pytest.mark.parametrize(
    "raw", [pytest.param("Audiobook", id="capitalized"), pytest.param("  audiobook  ", id="padded")]
)
def test_patch_history_format_is_case_and_space_normalized(raw):
    """Accepted spellings normalize to the canonical lowercase vocab before any DB work —
    prove it via the pydantic model directly (the endpoint seam is mocked in this file)."""
    from agentic_librarian.api.main import HistoryUpdate

    assert HistoryUpdate(format=raw).format == "audiobook"
```

- [ ] **Step 2: Run to verify failures**

Run: `.venv/Scripts/python -m pytest test/unit/test_api_history.py -v`
Expected: new tests FAIL (`format` currently ignored → 404-or-500 path instead of 422; `HistoryUpdate` has no `format`). Existing tests PASS.

- [ ] **Step 3: Write the failing db_integration tests**

Append to `test/integration/test_api_history_db.py`:

```python
def _db(db_url):
    return DatabaseManager(db_url)


def _my_entry(client):
    return next(h for h in client.get("/history").json() if h["title"] == "Shared Book")


def test_patch_format_creates_sibling_edition_and_repoints(two_user_client, db_url):
    client = two_user_client(DEFAULT_USER_ID, "jaydee829@gmail.com")
    entry = _my_entry(client)
    resp = client.patch(f"/history/{entry['id']}", json={"format": "audiobook"})
    assert resp.status_code == 200
    assert resp.json()["format"] == "audiobook"
    assert resp.json()["enrichment_enqueued"] is False  # no Cloud Tasks in tests
    with _db(db_url).get_session() as s:
        row = s.get(ReadingHistory, UUID(entry["id"]))
        work_id = row.edition.work_id
        formats = {e.format for e in s.query(Edition).filter(Edition.work_id == work_id)}
        assert row.edition.format == "audiobook"
        assert formats == {"ebook", "audiobook"}  # old edition intact (shared catalog object)
        # Assertion completeness (#96 lesson): untouched fields survive; the friend's row
        # on the ORIGINAL edition is untouched.
        assert row.date_completed == date(2021, 1, 1)
        friend = s.query(ReadingHistory).filter(ReadingHistory.user_id == FRIEND_ID).one()
        assert friend.edition.format == "ebook"


def test_patch_format_reuses_existing_sibling_edition(two_user_client, db_url):
    with _db(db_url).get_session() as s:
        work_id = s.query(Edition).filter(Edition.format == "ebook").first().work_id
        s.add(Edition(work_id=work_id, format="audiobook", isbn_13="9781111111111"))
        s.flush()
    client = two_user_client(DEFAULT_USER_ID, "jaydee829@gmail.com")
    entry = _my_entry(client)
    assert client.patch(f"/history/{entry['id']}", json={"format": "audiobook"}).status_code == 200
    with _db(db_url).get_session() as s:
        assert s.query(Edition).filter(Edition.work_id == work_id).count() == 2  # reused, not duplicated
        row = s.get(ReadingHistory, UUID(entry["id"]))
        assert row.edition.isbn_13 == "9781111111111"


def test_patch_same_format_is_a_noop(two_user_client, db_url):
    client = two_user_client(DEFAULT_USER_ID, "jaydee829@gmail.com")
    entry = _my_entry(client)
    assert client.patch(f"/history/{entry['id']}", json={"format": "ebook"}).status_code == 200
    with _db(db_url).get_session() as s:
        work_id = s.get(ReadingHistory, UUID(entry["id"])).edition.work_id
        assert s.query(Edition).filter(Edition.work_id == work_id).count() == 1  # nothing minted


def test_patch_format_collision_is_409_and_rolls_back(two_user_client, db_url):
    with _db(db_url).get_session() as s:
        ebook = s.query(Edition).filter(Edition.format == "ebook").first()
        audio = Edition(work_id=ebook.work_id, format="audiobook")
        s.add(audio)
        s.flush()
        # Same user, same work, SAME date — but as an audiobook (an import-duplicate shape).
        s.add(ReadingHistory(edition_id=audio.id, user_id=DEFAULT_USER_ID, date_completed=date(2021, 1, 1)))
        s.flush()
    client = two_user_client(DEFAULT_USER_ID, "jaydee829@gmail.com")
    entry = _my_entry(client)
    resp = client.patch(f"/history/{entry['id']}", json={"format": "audiobook", "notes": "should not stick"})
    assert resp.status_code == 409
    assert "already logged" in resp.json()["detail"]
    with _db(db_url).get_session() as s:
        row = s.get(ReadingHistory, UUID(entry["id"]))
        assert row.edition.format == "ebook"  # repoint rolled back
        assert row.user_notes != "should not stick"  # the whole PATCH rolled back, not just format


def test_patch_date_collision_is_409_not_500(two_user_client, db_url):
    """Pre-existing hole the format work closes: date-only edits could always trip
    uq_reading_history_user_edition_date; they must now 409 cleanly."""
    with _db(db_url).get_session() as s:
        edition_id = s.query(Edition).filter(Edition.format == "ebook").first().id
        s.add(ReadingHistory(edition_id=edition_id, user_id=DEFAULT_USER_ID, date_completed=date(2020, 6, 6)))
        s.flush()
    client = two_user_client(DEFAULT_USER_ID, "jaydee829@gmail.com")
    entry = _my_entry(client)  # the 2021-01-01 read
    assert client.patch(f"/history/{entry['id']}", json={"date_completed": "2020-06-06"}).status_code == 409


def test_patch_format_enqueues_completion_for_new_audiobook(two_user_client, monkeypatch):
    calls = []
    monkeypatch.setattr(api_main, "enqueue_edition_completion", lambda wid, fmt: calls.append((wid, fmt)) or True)
    client = two_user_client(DEFAULT_USER_ID, "jaydee829@gmail.com")
    entry = _my_entry(client)
    resp = client.patch(f"/history/{entry['id']}", json={"format": "audiobook"})
    assert resp.status_code == 200
    assert resp.json()["enrichment_enqueued"] is True
    assert len(calls) == 1 and calls[0][1] == "audiobook"


def test_patch_format_enqueue_failure_never_fails_the_edit(two_user_client, monkeypatch):
    def _boom(wid, fmt):
        raise RuntimeError("tasks down")

    monkeypatch.setattr(api_main, "enqueue_edition_completion", _boom)
    client = two_user_client(DEFAULT_USER_ID, "jaydee829@gmail.com")
    entry = _my_entry(client)
    resp = client.patch(f"/history/{entry['id']}", json={"format": "audiobook"})
    assert resp.status_code == 200
    assert resp.json()["format"] == "audiobook"  # the edit stuck
    assert resp.json()["enrichment_enqueued"] is False


def test_patch_format_skips_enqueue_when_edition_complete(two_user_client, db_url, monkeypatch):
    from agentic_librarian.db.models import Narrator

    with _db(db_url).get_session() as s:
        work_id = s.query(Edition).filter(Edition.format == "ebook").first().work_id
        done = Edition(work_id=work_id, format="audiobook", isbn_13="9782222222222")
        done.narrators = [Narrator(name="Ray Porter")]
        s.add(done)
        s.flush()
    calls = []
    monkeypatch.setattr(api_main, "enqueue_edition_completion", lambda wid, fmt: calls.append(1) or True)
    client = two_user_client(DEFAULT_USER_ID, "jaydee829@gmail.com")
    entry = _my_entry(client)
    resp = client.patch(f"/history/{entry['id']}", json={"format": "audiobook"})
    assert resp.status_code == 200
    assert resp.json()["enrichment_enqueued"] is False
    assert calls == []  # already complete — no paid pass


def test_patch_notes_only_never_enqueues(two_user_client, monkeypatch):
    calls = []
    monkeypatch.setattr(api_main, "enqueue_edition_completion", lambda wid, fmt: calls.append(1) or True)
    client = two_user_client(DEFAULT_USER_ID, "jaydee829@gmail.com")
    entry = _my_entry(client)
    resp = client.patch(f"/history/{entry['id']}", json={"notes": "just notes"})
    assert resp.status_code == 200
    assert resp.json()["enrichment_enqueued"] is False
    assert calls == []
```

Note for the implementer: these tests reference `Edition` and `date` — both already imported at the top of this file. `api_main` is imported as `from agentic_librarian.api import main as api_main`.

- [ ] **Step 4: Verify collection**

Run: `.venv/Scripts/python -m pytest test/integration/test_api_history_db.py --collect-only -q`
Expected: all tests (old + 9 new) collected cleanly. Execution is CI's gate (or `POSTGRES_HOST=localhost` with the compose db up).

- [ ] **Step 5: Implement the endpoint changes**

In `src/agentic_librarian/api/main.py`:

Imports — extend the existing blocks (add only what's missing):

```python
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError

from agentic_librarian.db.get_or_create import get_or_create
from agentic_librarian.enrichment.tasks import enqueue_edition_completion
```

`HistoryUpdate` — add the field and validator (keep the existing validators):

```python
HISTORY_FORMATS = {"ebook", "audiobook", "paperback", "hardcover"}


class HistoryUpdate(BaseModel):
    date_completed: date | None = None
    rating: int | None = None
    notes: str | None = None
    format: str | None = None

    @field_validator("format")
    @classmethod
    def _format_vocab(cls, v: str | None) -> str | None:
        if v is None:
            return v
        normalized = v.strip().lower()
        if normalized not in HISTORY_FORMATS:
            raise ValueError("format must be one of: ebook, audiobook, paperback, hardcover")
        return normalized
```

Replace the body of `update_history`:

```python
@app.patch("/history/{entry_id}")
def update_history(
    entry_id: UUID,
    req: HistoryUpdate,
    user: AuthenticatedUser = Depends(get_current_user),  # noqa: B008
):
    fields = req.model_dump(exclude_unset=True)  # only what the client actually sent
    if "date_completed" in fields and fields["date_completed"] is None:
        raise HTTPException(status_code=422, detail="date_completed cannot be null")
    if "format" in fields and fields["format"] is None:
        raise HTTPException(status_code=422, detail="format cannot be null")

    needs_completion = False
    work_id_str = fmt_str = ""
    with db_manager.get_session() as session:
        row = (
            session.query(ReadingHistory)
            .filter(ReadingHistory.id == entry_id, ReadingHistory.user_id == user.id)
            .options(*_history_options())
            .first()
        )
        if row is None:
            raise HTTPException(status_code=404, detail="history entry not found")
        # Scalar edits first, so the collision check below runs against the FINAL date.
        if "date_completed" in fields:
            row.date_completed = fields["date_completed"]
        if "rating" in fields:
            row.user_rating = fields["rating"]
        if "notes" in fields:
            row.user_notes = fields["notes"]

        target_edition = row.edition
        new_fmt = fields.get("format")
        if new_fmt is not None and new_fmt != (row.edition.format or "").strip().lower():
            # Format change = repoint to the sibling (work_id, format) edition — editions are
            # shared catalog objects, so the old one is never mutated or deleted. Reuse a
            # casing variant if one exists (uq_editions_work_format is case-sensitive);
            # get_or_create + the unique index backstop the concurrent-create race (#95).
            target_edition = (
                session.query(Edition)
                .filter(Edition.work_id == row.edition.work_id, func.lower(Edition.format) == new_fmt)
                .first()
            )
            if target_edition is None:
                target_edition, _created = get_or_create(
                    session, Edition, work_id=row.edition.work_id, format=new_fmt
                )

        collision_detail = (
            f"You already logged this book as {target_edition.format or 'the same format'} "
            f"on {row.date_completed.isoformat()}."
        )
        if target_edition.id != row.edition_id or "date_completed" in fields:
            # uq_reading_history_user_edition_date pre-check; the index itself backstops the
            # millisecond race below and maps to the same 409.
            dup = (
                session.query(ReadingHistory)
                .filter(
                    ReadingHistory.user_id == user.id,
                    ReadingHistory.edition_id == target_edition.id,
                    ReadingHistory.date_completed == row.date_completed,
                    ReadingHistory.id != row.id,
                )
                .first()
            )
            if dup is not None:
                # Raising inside the session context rolls back EVERY field edit above —
                # a 409 must leave the row exactly as it was.
                raise HTTPException(status_code=409, detail=collision_detail)

        if target_edition.id != row.edition_id:
            row.edition = target_edition
            # Decide the async completion enqueue while the session is open (narrators is a
            # lazy relationship): missing ISBN, or an audiobook with no narrators yet.
            needs_completion = target_edition.isbn_13 is None or (
                "audiobook" in (target_edition.format or "").lower() and not target_edition.narrators
            )
            work_id_str = str(target_edition.work_id)
            fmt_str = target_edition.format or ""
        try:
            session.flush()
        except IntegrityError as e:
            raise HTTPException(status_code=409, detail=collision_detail) from e
        payload = _history_item(row)

    # After commit: best-effort enqueue in the POST /books style — a Cloud Tasks failure
    # must never fail the edit (the completion sweep-of-one can be retriggered by any
    # later format edit; the entry itself is saved).
    enqueued = False
    if needs_completion:
        try:
            enqueued = enqueue_edition_completion(work_id_str, fmt_str)
        except Exception:  # noqa: BLE001 - enqueue is best-effort
            logger.exception("edition-completion enqueue failed for work %s", work_id_str)
    payload["enrichment_enqueued"] = enqueued
    return payload
```

(If `main.py` has no module `logger`, add `logger = logging.getLogger(__name__)` near the top alongside the existing imports.)

- [ ] **Step 6: Run unit tests to verify they pass**

Run: `.venv/Scripts/python -m pytest test/unit/test_api_history.py test/unit/test_api_requires_auth.py -v`
Expected: PASS, including the new validation cases.

- [ ] **Step 7: Full unit suite + collection check of the integration file**

Run: `.venv/Scripts/python -m pytest test/unit -v; .venv/Scripts/python -m pytest test/integration/test_api_history_db.py --collect-only -q`
Expected: unit suite PASS; collection clean.

- [ ] **Step 8: Lint, format, commit**

```powershell
uvx ruff check src/agentic_librarian/api/main.py test/unit/test_api_history.py test/integration/test_api_history_db.py; uvx ruff format src/agentic_librarian/api/main.py test/unit/test_api_history.py test/integration/test_api_history_db.py
git add src/agentic_librarian/api/main.py test/unit/test_api_history.py test/integration/test_api_history_db.py
git commit -m "feat(api): history format edit — edition repoint, 409 collisions, completion enqueue"
```

---

### Task 7: Frontend — format select + 409 messaging

**Files:**
- Modify: `frontend/src/api/client.ts` (`HistoryItem`, `HistoryUpdate`, `updateHistory`, new `ApiError`)
- Modify: `frontend/src/views/HistoryEditView.tsx`
- Test: `frontend/src/views/HistoryEditView.test.tsx`

**Interfaces:**
- Consumes: `PATCH /history/{id}` from Task 6 (accepts `format`; 409 `{detail: string}`; response includes `enrichment_enqueued`).
- Produces: `ApiError extends Error { status: number; detail: string }` exported from `client.ts`; `HistoryUpdate.format?: string`; `HistoryItem.enrichment_enqueued?: boolean`.

- [ ] **Step 1: Write the failing tests**

In `frontend/src/views/HistoryEditView.test.tsx`, add to the existing `describe` (the `row` fixture and `renderAt` helper already exist; `ApiError` import comes from the real module — add `import { ApiError } from '../api/client'` and mock it through with `vi.mock('../api/client', async (importOriginal) => ({ ...(await importOriginal<typeof import('../api/client')>()), updateHistory: vi.fn(), deleteHistory: vi.fn(), getHistory: vi.fn() }))` replacing the bare `vi.mock('../api/client')` so the real `ApiError` class survives mocking):

```tsx
  it('renders the format select prefilled with the current format', () => {
    renderAt(row)
    expect(screen.getByLabelText(/format/i)).toHaveValue('ebook')
  })

  it('sends format only when the user changes it', async () => {
    vi.mocked(client.updateHistory).mockResolvedValueOnce({ ...row, format: 'audiobook' })
    renderAt(row)
    await userEvent.selectOptions(screen.getByLabelText(/format/i), 'audiobook')
    await userEvent.click(screen.getByRole('button', { name: /save changes/i }))
    await waitFor(() =>
      expect(client.updateHistory).toHaveBeenCalledWith('h1', expect.objectContaining({ format: 'audiobook' })),
    )
  })

  it('omits format from the payload when unchanged', async () => {
    vi.mocked(client.updateHistory).mockResolvedValueOnce({ ...row })
    renderAt(row)
    await userEvent.click(screen.getByRole('button', { name: /save changes/i }))
    await waitFor(() =>
      expect(client.updateHistory).toHaveBeenCalledWith(
        'h1',
        expect.not.objectContaining({ format: expect.anything() }),
      ),
    )
  })

  it('shows the server message on a 409 collision', async () => {
    // ...Once variant (vitest pitfall memory): a persistent mockRejectedValue leaks
    // an unhandled rejection into later tests.
    vi.mocked(client.updateHistory).mockRejectedValueOnce(
      new ApiError(409, 'You already logged this book as audiobook on 2019-03-14.'),
    )
    renderAt(row)
    await userEvent.selectOptions(screen.getByLabelText(/format/i), 'audiobook')
    await userEvent.click(screen.getByRole('button', { name: /save changes/i }))
    expect(
      await screen.findByText('You already logged this book as audiobook on 2019-03-14.'),
    ).toBeInTheDocument()
  })

  it('falls back to the generic message on non-409 failures', async () => {
    vi.mocked(client.updateHistory).mockRejectedValueOnce(new Error('network'))
    renderAt(row)
    await userEvent.click(screen.getByRole('button', { name: /save changes/i }))
    expect(await screen.findByText(/couldn't save those changes/i)).toBeInTheDocument()
  })
```

- [ ] **Step 2: Run to verify failures**

Run (from `frontend/`): `npx vitest run src/views/HistoryEditView.test.tsx`
Expected: new tests FAIL (no format select; `ApiError` not exported). The two pre-existing tests PASS.

- [ ] **Step 3: Implement `client.ts` changes**

In `frontend/src/api/client.ts`:

Add to `HistoryItem`: `enrichment_enqueued?: boolean`.
Add to `HistoryUpdate`: `format?: string`.
Add the error class (near the top, after imports):

```ts
export class ApiError extends Error {
  constructor(
    public status: number,
    public detail: string,
  ) {
    super(detail)
    this.name = 'ApiError'
  }
}
```

Replace `updateHistory`'s failure path so the server's `detail` survives:

```ts
export async function updateHistory(id: string, body: HistoryUpdate): Promise<HistoryItem> {
  const res = await authedFetchRaw(`/history/${id}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!res.ok) {
    let detail = `update history → ${res.status}`
    try {
      const parsed = await res.json()
      if (typeof parsed?.detail === 'string') detail = parsed.detail
    } catch {
      // non-JSON error body — keep the generic detail
    }
    throw new ApiError(res.status, detail)
  }
  return res.json()
}
```

(Keep the rest of the function exactly as it is today — only the `!res.ok` branch changes.)

- [ ] **Step 4: Implement the view changes**

In `frontend/src/views/HistoryEditView.tsx`:

Add state after the existing `useState` lines:

```tsx
  const [format, setFormat] = useState(row?.format ?? '')
```

Import `ApiError` alongside `updateHistory`. In `onSubmit`, build the body conditionally and branch the catch:

```tsx
  async function onSubmit(e: FormEvent) {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      const body: Parameters<typeof updateHistory>[1] = {
        rating: rating ? Number(rating) : null,
        date_completed: dateFinished || null,
        notes: notes.trim() || null,
      }
      if (format && format !== row!.format) body.format = format
      await updateHistory(id as string, body)
      navigate('/history')
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) setError(err.detail)
      else setError("Couldn't save those changes — try again.")
    } finally {
      setBusy(false)
    }
  }
```

Add the select to the form, between the context paragraph's closing and the Rating label (mirrors `AddBookView`'s options; the empty option only renders for a null incoming format):

```tsx
        <label>
          Format
          <select value={format} onChange={(e) => setFormat(e.target.value)}>
            {!row.format && <option value="">—</option>}
            <option value="ebook">ebook</option>
            <option value="audiobook">audiobook</option>
            <option value="paperback">paperback</option>
            <option value="hardcover">hardcover</option>
          </select>
        </label>
```

Also remove the now-stale ` · {row.format}` from the context paragraph ONLY if it visually duplicates the select — otherwise leave it (implementer's call; default: leave it).

- [ ] **Step 5: Run the frontend tests**

Run (from `frontend/`): `npx vitest run src/views/HistoryEditView.test.tsx`
Expected: ALL PASS (2 pre-existing + 5 new).

Then the full frontend suite: `npx vitest run`
Expected: PASS (App.test.tsx mocks every view — no change needed there since no new module was created).

- [ ] **Step 6: Commit**

```powershell
git add frontend/src/api/client.ts frontend/src/views/HistoryEditView.tsx frontend/src/views/HistoryEditView.test.tsx
git commit -m "feat(frontend): format select on history edit with 409 collision messaging"
```

---

### Task 8: Final verification, docs, PR

**Files:**
- Modify: `docs/project_notes/issues.md` (work-log entry)

- [ ] **Step 1: Full unit suite + frontend suite**

Run: `.venv/Scripts/python -m pytest test/unit -v` and (from `frontend/`) `npx vitest run`
Expected: PASS. Name any env-dependent failures explicitly (live-network, `db` hostname, optional `claude_agent_sdk`) — verify with `git stash` that they pre-date this branch if any appear.

- [ ] **Step 2: Ruff over the whole touched set**

```powershell
uvx ruff check src/agentic_librarian test; uvx ruff format src/agentic_librarian test
```
Expected: clean (format may rewrite — re-run unit tests if it does, then amend nothing: make a follow-up commit).

- [ ] **Step 3: Log the work**

Append to `docs/project_notes/issues.md` under the current section:

```markdown
### 2026-07-18 - History format edit (spec 2026-07-18)
- **Status**: PR open
- **Description**: PATCH /history accepts format (vocab-validated); repoints to sibling
  (work_id, format) Edition; 409 on uq_reading_history collisions (incl. the pre-existing
  date-edit 500 hole); async /internal/complete-edition pass fills ISBN/pages/audio +
  narrators/styles for audiobooks — never the paid trope/style deep pass.
- **Notes**: merge_edition_and_narrators extracted from persist_enriched_work (shared).
```

- [ ] **Step 4: Commit docs, push branch, open PR**

```powershell
git add docs/project_notes/issues.md
git commit -m "docs(project-notes): log history-format-edit work"
git push -u origin feat/history-format-edit
gh pr create --title "feat: history format edit with targeted edition-completion enrichment" --body "..."
```
PR body: summarize the spec decisions (repoint not mutate; 409 collisions; targeted completion pass vs full deep pass; date-edit 409 fix), link the spec file, state honestly which suites executed locally vs are CI-gated (db_integration), and end with the standard generated-with footer. Gemini reviews each PR — respond per `pr-workflow-conventions` (TDD red→green fixes, reply with commit hash). **The first CI run is a merge gate: db_integration executes there for the first time.**

---

## Self-Review (completed)

- **Spec coverage:** §1 API → Task 6; §2 completion pass → Tasks 1–5; §3 frontend → Task 7; §4 testing → embedded per task + Task 8; "Out of scope" respected (no title/author editing, no history-UI narrator display, no backfill sweep).
- **Type consistency:** `complete_edition(work_id: UUID, fmt: str) -> "missing"|"empty"|"done"` consistent across Tasks 3/5; `enqueue_edition_completion(work_id: str, fmt: str) -> bool` consistent across Tasks 4/6; `merge_edition_and_narrators` kwargs consistent across Tasks 1/3; `ApiError(status, detail)` consistent across Task 7 files.
- **Known judgment calls documented inline:** `row["format"]`→`fmt` unification (Task 1), audience-without-query (Task 4), case-insensitive edition reuse (Task 6), leave-the-context-line (Task 7).
