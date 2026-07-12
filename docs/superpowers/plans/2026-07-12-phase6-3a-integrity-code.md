# Phase 6.3 PR-C Integrity Code Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land the code-only integrity fixes (#96 #111 #98 #112 #110 #123) on branch `fix/phase6-3a-integrity-code` — no schema changes; PR-D (migration + gated prod ops) follows.

**Architecture:** persist.py's existing-work branch gains the contributor merge its narrator branch already has; the #69-corrected real-vs-fallback trope predicate becomes one shared helper used by the persist guard (and later PR-D's sweep); ScoutManager returns falsy when no scout contributed (reviving the dead not-found paths); update_reading_status becomes date-honest and dedup-guarded via two_phase.add_read_event; the availability cache gets a real Postgres upsert + piggybacked eviction; embedding calls are hoisted out of DB sessions by LRU-warming, letting the pool tighten to its 5+2 target.

**Tech Stack:** SQLAlchemy 2.0 (+ `sqlalchemy.dialects.postgresql.insert`), pytest (`filterwarnings` error promotion), google-genai LRU cache from #101.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-07-12-phase6-3-data-integrity-design.md` (PR-C sections). NO schema/model changes in this PR (PR-D owns migrations).
- The shared predicate implements EXACTLY the #69 semantics from `trope_backfill.plan_fallback_prune`: `clean_trope_name(name)` case-folded; fallback iff cleaned non-empty AND ⊆ case-folded genres∪moods; real iff cleaned non-empty and not fallback; junk (cleans to []) is neither.
- `ScoutManager.enrich` returns `{}` (falsy) iff `source_priority` is empty after the scout loop; `LLMTropeScout.search` returns `{}` when its trope list is empty (today's `{"tropes": []}` is truthy and would defeat the gate).
- `update_reading_status` keeps its public tool name and `str` return; new params are OPTIONAL (`date_completed: str | None = None, year: int | None = None`) — precedence date_completed > year (Jan 1 convention) > today-fallback, with the today-fallback flagged in the reply text.
- Pool value in `db/session.py` ends at exactly `max_overflow=2` (the #123 condition is met by this PR); update the comment, `test_pool_config.py`, key_facts.md, and the ADR-059 consequence line — mirror how the 5 value was documented.
- pytest gains `filterwarnings = ["error::sqlalchemy.exc.SAWarning"]` — if the suite surfaces OTHER latent SAWarnings, fix them in-place and report each.
- Tests: `.venv/Scripts/python -m pytest ...` from repo root; new unit tests DB-free/sqlite, no `db_integration` marker; db_integration suites are CI-gated (collect-check locally, say so). Lint/format every touched file: `uvx ruff check` + `uvx ruff format` (CI pre-commit enforces format).
- No `[skip ci]` in commit messages. Do not modify `frontend/**`, `alembic/**`, or `db/models.py`.

---

### Task 1: Shared real-vs-fallback trope predicate — #111

**Files:**
- Create: `src/agentic_librarian/etl/trope_predicate.py`
- Modify: `src/agentic_librarian/etl/trope_backfill.py:198-215` (plan_fallback_prune inner loop)
- Modify: `src/agentic_librarian/etl/persist.py:338-343` (has_real_trope guard)
- Test: `test/unit/test_trope_predicate.py` (new)

**Interfaces:**
- Produces: `is_fallback_trope_name(name: str, genres: list[str] | None, moods: list[str] | None) -> bool | None` — True = fallback, False = real, **None = junk** (cleans to nothing; callers treat separately). PR-D's `--requeue-unenriched` will consume this exact signature.

- [ ] **Step 1: Write the failing tests**

Create `test/unit/test_trope_predicate.py`:

```python
"""#111: ONE predicate for real-vs-fallback tropes (the #69-corrected semantics —
justification is NEVER consulted)."""

from agentic_librarian.etl.trope_predicate import is_fallback_trope_name


def test_genre_reencoded_trope_is_fallback():
    assert is_fallback_trope_name("Fantasy", ["Fantasy", "Romance"], ["Dark"]) is True


def test_mood_reencoded_trope_is_fallback_case_insensitive():
    assert is_fallback_trope_name("dark", ["Fantasy"], ["Dark"]) is True


def test_narrative_trope_is_real():
    assert is_fallback_trope_name("The Dark Night of the Soul", ["Fantasy"], ["Dark"]) is False


def test_junk_name_is_neither():
    # clean_trope_name("") -> [] — junk names are neither real nor fallback (None)
    assert is_fallback_trope_name("", ["Fantasy"], []) is None


def test_none_genre_mood_lists_tolerated():
    assert is_fallback_trope_name("Found Family", None, None) is False


def test_multi_slug_subset_is_fallback():
    # a slug that cleans to multiple names, ALL of which are genres/moods, is a fallback
    # (subset semantics — mirrors plan_fallback_prune's `cleaned_lower <= gm`)
    assert is_fallback_trope_name("Fantasy / Romance", ["Fantasy", "Romance"], []) is True
```

(If `clean_trope_name("Fantasy / Romance")` doesn't split on ` / ` in this codebase, adjust that last test's input to a slug form `clean_trope_name` genuinely splits — check `etl/tag_cleaning.py:99-128` — while keeping the multi-name-subset assertion.)

- [ ] **Step 2: Run to verify failure**

Run: `.venv/Scripts/python -m pytest test/unit/test_trope_predicate.py -v`
Expected: `ModuleNotFoundError: ... trope_predicate`.

- [ ] **Step 3: Implement `etl/trope_predicate.py`**

```python
"""The ONE real-vs-fallback trope predicate (GH #111, semantics from PR #69).

A genre/mood "fallback" trope is one whose CLEANED name is (a subset of) the work's own
cleaned genres+moods — the two-phase fast pass re-encoded a genre/mood as a trope. The
justification column is deliberately NEVER consulted: many real scout tropes carry NULL
justification (semantic-collapse "attractor" tropes), so it conflates real with fallback —
the exact mistake the #65 prune nearly made (bugs.md 2026-06-24, memory
verify-backfill-distinguisher). Shared by the persist-time guard, clean_catalog's prune,
and (PR-D) the enrichment-reconciliation sweep, so the definitions can't diverge again."""

from __future__ import annotations

from agentic_librarian.etl.tag_cleaning import clean_trope_name


def is_fallback_trope_name(name: str, genres: list[str] | None, moods: list[str] | None) -> bool | None:
    """True = fallback (re-encoded genre/mood); False = genuine narrative trope;
    None = junk (cleans to nothing — neither real nor fallback)."""
    gm = {s.lower() for s in (set(genres or []) | set(moods or []))}
    cleaned = clean_trope_name(name)
    if not cleaned:
        return None
    if {c.lower() for c in cleaned} <= gm:
        return True
    return False
```

- [ ] **Step 4: Adopt it in `plan_fallback_prune`** (trope_backfill.py:208-215) — replace the inline clean/subset logic:

```python
        for trope_id, name in w["links"]:
            verdict = is_fallback_trope_name(name, list(w["gm"]), [])  # gm is pre-folded; see below
            ...
```

CAREFUL: `plan_fallback_prune` pre-folds `gm` once per work; the helper folds internally. To avoid double-lowering subtleties, restructure minimally: keep building `gm` as today, and change the helper adoption to pass the ORIGINAL genres/moods per work instead of the pre-folded set — i.e. store `w["genres"]`/`w["moods"]` (raw lists) in `by_work` and call `is_fallback_trope_name(name, w["genres"], w["moods"])`. The `fallback and real` gating and FallbackPrune construction stay identical (`True` → fallback list, `False` → real += 1, `None` → skip). The function's behavior must be byte-equivalent for the existing CI test `test_fallback_prune.py` — that suite is the pin.

- [ ] **Step 5: Adopt it in persist's guard** (persist.py:338-343):

```python
            # GH #111: "has a real trope" = any linked trope whose cleaned name is NOT a
            # re-encoding of this work's genres/moods (the shared #69 predicate) — the old
            # `justification IS NOT NULL` heuristic misclassified real attractor tropes.
            linked = (
                session.query(Trope.name)
                .join(WorkTrope, WorkTrope.trope_id == Trope.id)
                .filter(WorkTrope.work_id == work.id)
                .all()
            )
            has_real_trope = any(
                is_fallback_trope_name(name, work.genres, work.moods) is False for (name,) in linked
            )
```

Add the imports (`from agentic_librarian.etl.trope_predicate import is_fallback_trope_name`; `Trope` is already imported — verify).

- [ ] **Step 6: Run tests**

Run: `.venv/Scripts/python -m pytest test/unit/test_trope_predicate.py test/unit -q` — all pass; `.venv/Scripts/python -m pytest test/integration/test_fallback_prune.py test/integration/test_persist_fallback_flag.py --collect-only -q` — collects (CI pins behavior).

- [ ] **Step 7: Lint, format, commit**

```bash
git add src/agentic_librarian/etl/trope_predicate.py src/agentic_librarian/etl/trope_backfill.py src/agentic_librarian/etl/persist.py test/unit/test_trope_predicate.py
git commit -m "fix(tropes): one shared real-vs-fallback predicate; persist guard drops the justification heuristic (#111)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 2: Contributor merge on existing works + SAWarning-as-error — #96

**Files:**
- Modify: `src/agentic_librarian/etl/persist.py:103-142,160-187`
- Modify: `pyproject.toml` (`[tool.pytest.ini_options]` gains filterwarnings)
- Test: `test/integration/test_two_phase_deep.py` (extend the existing repro test), `test/integration/test_persist_contributor_guard.py` (extend)

**Interfaces:** none new; `persist_enriched_work` signature unchanged.

- [ ] **Step 1: Write the failing tests** (db_integration — they FAIL IN CI today, which is the point; locally verify collection)

Extend `test/integration/test_two_phase_deep.py` — in `test_enrich_deep_updates_same_work_idempotently` (read it first), after the existing assertions add:

```python
    # GH #96: a co-author discovered by the deep pass must be LINKED on the existing work
    # (previously the WorkContributor dangled and SQLAlchemy 2.0 silently dropped it), and
    # no orphan Author rows may accumulate.
    with manager.get_session() as s:
        work = s.query(Work).filter_by(title="Dune").one()
        roles = {(c.author.name, c.role) for c in work.contributors}
        assert ("Kevin J. Anderson", "Author") in roles  # the co-author the fake deep scout returns
        orphans = (
            s.query(Author)
            .outerjoin(WorkContributor, WorkContributor.author_id == Author.id)
            .filter(WorkContributor.author_id.is_(None))
            .count()
        )
        assert orphans == 0
```

AND make the test's fake deep scout return a second contributor (`{"name": "Kevin J. Anderson", "role": "Author"}`) in its `contributors` list — read the test's existing fake/stub mechanism and extend it; adapt names to what the test actually seeds (the assertion intent is binding: newly discovered co-author linked, zero orphan authors).

Extend `test/integration/test_persist_contributor_guard.py` with a direct unit-of-work test:

```python
def test_existing_work_gains_new_contributor(db_url):
    """#96: re-persisting an existing work links newly discovered contributors."""
    manager = DatabaseManager(db_url)
    with manager.get_session() as s:
        row1 = {"Title": "CG Work", "Author_1": "Alice", "format": "ebook", "skip_enrichment": False,
                "date_completed": None, "genres": [], "moods": []}
        persist_enriched_work(s, row1, TropeManager(session=s), StyleManager(session=s))
        s.flush()
    with manager.get_session() as s:
        row2 = dict(row1)
        row2["contributors"] = [{"name": "Alice", "role": "Author"}, {"name": "Bob", "role": "Author"}]
        persist_enriched_work(s, row2, TropeManager(session=s), StyleManager(session=s))
        s.flush()
        work = s.query(Work).filter_by(title="CG Work").one()
        assert {(c.author.name, c.role) for c in work.contributors} == {("Alice", "Author"), ("Bob", "Author")}
```

(Match the file's existing fixture/manager conventions — read it first; TropeManager/StyleManager may need the api_key kwarg pattern used there.)

- [ ] **Step 2: Verify collection** (they run in CI): `--collect-only` on both files.

- [ ] **Step 3: Restructure `persist_enriched_work`**

Reorder so the Work lookup happens BEFORE contributor materialization, and the existing-work branch merges:

1. Move the Work lookup block (current lines 160-170, keeping `no_autoflush` — now unnecessary for dangling contributors but harmless for other pending state; keep it with an updated comment) ABOVE the contributor loop. It only needs `row["Title"]` and `row.get("Author_1") or row.get("Author")` — available before the loop.
2. Change the contributor loop to build `desired: list[tuple[Author, str]]` — the Author get-or-create + AuthorStyle handling stay exactly as they are (lines 107-141), but instead of `work_contributors_list.append(WorkContributor(author=author, role=role))` collect `desired.append((author, role))`. Author creation now happens ONLY for entries that will be linked (both branches below link every desired pair, so the orphan side effect dies with the dangling objects).
3. New-work branch: `work = Work(title=..., contributors=[WorkContributor(author=a, role=r) for a, r in desired], ...)` — unchanged semantics.
4. Existing-work branch (the #96 fix — mirrors the narrator merge at line 279):

```python
    elif not row.get("skip_enrichment"):
        work.original_publication_year = original_publication_year or work.original_publication_year
        work.description = description or work.description
        work.genres = genres or work.genres
        work.moods = moods or work.moods
        # GH #96: link newly discovered contributors (deep pass / re-import). Previously the
        # WorkContributor objects dangled off Author.contributions and SQLAlchemy 2.0's removed
        # backref-cascade silently never flushed them (SAWarning) — co-authors were lost and
        # their Author rows orphaned. Mirror the narrator merge below.
        existing_pairs = {(c.author_id, c.role) for c in work.contributors}
        for author, role in desired:
            if (author.id, role) not in existing_pairs:
                work.contributors.append(WorkContributor(author=author, role=role))
```

NOTE: `author.id` requires the flush at author creation (already present, line 124) — verify existing authors fetched from the DB also have ids (they do). `skip_enrichment` rows previously skipped ALL updates on existing works — preserve that: the merge lives inside the `elif not row.get("skip_enrichment")` branch.

- [ ] **Step 4: Promote SAWarning to error** — in `pyproject.toml` `[tool.pytest.ini_options]` add:

```toml
filterwarnings = [
    "error::sqlalchemy.exc.SAWarning",
]
```

Run the full local unit suite; if OTHER latent SAWarnings surface, fix each in place (report them). db_integration suites will enforce it in CI.

- [ ] **Step 5: Run tests**

`.venv/Scripts/python -m pytest test/unit -q` (green minus known 5 env failures) and collect-check the two integration files.

- [ ] **Step 6: Lint, format, commit**

```bash
git add src/agentic_librarian/etl/persist.py pyproject.toml test/integration/test_two_phase_deep.py test/integration/test_persist_contributor_guard.py
git commit -m "fix(persist): merge newly discovered contributors on existing works; SAWarning is now an error (#96)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 3: Garbage-title gate — #98

**Files:**
- Modify: `src/agentic_librarian/scouts/metadata_scout.py:556-563` (LLMTropeScout.search return), `:~593-616` (ScoutManager.enrich)
- Test: `test/unit/test_metadata_scout.py` (extend)

**Interfaces:** `ScoutManager.enrich` now returns `{}` when nothing contributed — callers already handle falsy (`_run_scouts`' `if not enriched: return None`; `api/books.py` 404; import `not_found`).

- [ ] **Step 1: Write the failing tests** — append to `test/unit/test_metadata_scout.py` (mirror its existing fake-scout fixtures):

```python
def test_enrich_returns_empty_when_no_scout_contributes():
    """#98: a title no scout can confirm must be falsy — revives the 404/not_found paths."""
    manager = ScoutManager()
    manager.register_scout(_FailingScout(), priority=1)   # raises
    manager.register_scout(_EmptyScout(), priority=2)     # returns {}
    assert manager.enrich(title="asdfjkl", author="Nobody") == {}


def test_enrich_truthy_when_a_scout_contributes():
    manager = ScoutManager()
    manager.register_scout(_GenreScout(), priority=1)     # returns {"genres": {"Fantasy"}}
    result = manager.enrich(title="Real Book", author="Real Author")
    assert result and result["source_priority"] == ["_GenreScout"]


def test_trope_scout_empty_tropes_is_falsy(monkeypatch):
    """An LLM that can't verify the book returns {'tropes': []} — that must NOT count as a
    contributing source (it was truthy before and defeated the gate)."""
    scout = LLMTropeScout.__new__(LLMTropeScout)
    monkeypatch.setattr(scout, "_llm", SimpleNamespace(generate=lambda *a, **k: '{"tropes": []}'))
    assert scout.search("Ghost Book", "Nobody") == {}


def test_trope_prompt_has_unknown_book_escape(monkeypatch):
    seen = {}

    def fake_generate(prompt, grounded=True):
        seen["prompt"] = prompt
        return '{"tropes": []}'

    scout = LLMTropeScout.__new__(LLMTropeScout)
    monkeypatch.setattr(scout, "_llm", SimpleNamespace(generate=fake_generate))
    scout.search("X", "Y")
    assert "cannot verify" in seen["prompt"].lower()
```

(Adapt fake-scout class names/registration API to the file's existing conventions — read it first; `register_scout` signature may differ. The four assertion intents are binding.)

- [ ] **Step 2: Verify failure** — the source_priority/empty-dict assertions fail against current behavior.

- [ ] **Step 3: Implement**

1. `LLMTropeScout.search`: prompt gains, after the CRITICAL line: `If you cannot verify that this book actually exists, return {"tropes": []}.` And the return becomes:

```python
        text = self._llm.generate(prompt, grounded=True)
        data = self._safe_extract_json(text, "Tropes", title) or {}
        # GH #98: an empty trope list means the model couldn't verify the book — return
        # falsy so ScoutManager doesn't count this scout as a contributing source.
        return data if data.get("tropes") else {}
```

2. `ScoutManager.enrich`: after the scout loop (before the genres/moods finalization/return — read the tail of the method), add:

```python
        # GH #98: merged_data is seeded from raw caller input, so it is ALWAYS truthy even
        # when every scout failed or returned nothing. If no scout contributed, return {} so
        # callers' not-found paths (books.py 404, import outcome, _run_scouts -> None) work.
        if not merged_data["source_priority"]:
            return {}
```

- [ ] **Step 4: Run** `.venv/Scripts/python -m pytest test/unit/test_metadata_scout.py test/unit -q` — green.

- [ ] **Step 5: Lint, format, commit**

```bash
git add src/agentic_librarian/scouts/metadata_scout.py test/unit/test_metadata_scout.py
git commit -m "fix(scouts): unverifiable titles return falsy — 404/not_found paths revive; trope prompt gains unknown-book escape (#98)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 4: update_reading_status correctness — #112

**Files:**
- Modify: `src/agentic_librarian/mcp/server.py:437-484` (update_reading_status)
- Modify: `src/agentic_librarian/agents/services.py` (FEEDBACK HANDLING instruction)
- Test: `test/unit/test_mcp_tools.py` (extend), `test/integration/test_mcp_tools.py` (extend)

**Interfaces:** tool signature becomes `update_reading_status(title, author, status, notes=None, date_completed: str | None = None, year: int | None = None) -> str` (additive — ADK schema regenerates via make_async_tool).

- [ ] **Step 1: Write the failing unit tests** — extend `test/unit/test_mcp_tools.py` (mirror its mocked-db_manager conventions):

```python
def test_update_reading_status_honors_year(...):
    # year=2019 -> date_completed == date(2019, 1, 1); reply does NOT contain "assumed"
def test_update_reading_status_rejects_bad_year(...):
    # year=1200 -> "Error: year must be between 1900 and <current year>"
def test_update_reading_status_flags_assumed_today(...):
    # no date/year -> reply contains "assumed" (the agent-visible flag)
def test_update_reading_status_rejects_future_date(...):
    # date_completed tomorrow -> Error (same rule as add_book_to_history)
```

(Write real bodies following the file's mock pattern; the four intents + exact date precedence are binding. Also extend the CI integration file with: calling it twice with the same resolved date logs ONE ReadingHistory row — the add_read_event dup guard.)

- [ ] **Step 2: Verify failure.**

- [ ] **Step 3: Rewrite the tool** (keep validation + status normalization + SEC-002 comments; replace the lookup/insert body):

```python
    # date resolution (GH #112): date_completed > year (Jan 1 convention) > today-fallback.
    assumed_today = False
    if date_completed is not None:
        try:
            completed = date.fromisoformat(str(date_completed))
        except ValueError:
            return f"Error: date_completed must be ISO YYYY-MM-DD; got {date_completed!r}."
        if completed > date.today():
            return f"Error: date_completed {completed.isoformat()} is in the future."
    elif year is not None:
        if isinstance(year, bool) or not isinstance(year, int) or not 1900 <= year <= date.today().year:
            return f"Error: year must be between 1900 and {date.today().year}; got {year!r}."
        completed = date(year, 1, 1)  # convention: unknown month/day -> Jan 1 (documented)
    else:
        completed = date.today()
        assumed_today = True

    user_id = get_required_user_id()
    try:
        with db_manager.get_session() as session:
            work = (
                session.query(Work)
                .join(WorkContributor)
                .join(Author)
                .filter(_normalized_col(Work.title) == _normalize(title))
                .filter(_normalized_col(Author.name) == _normalize(author))
                .first()
            )
            if not work:
                return f"Work '{title}' by {author} not found in database."
            work_id = work.id
        if canonical == "read":
            logged = two_phase.add_read_event(work_id, completed=completed, rating=None, notes=notes, fmt="Unknown")
            if logged["already_logged"]:
                return f"'{title}' is already logged as completed {completed.isoformat()}. No new entry written."
        note = " (completion date assumed today — ask the user when they read it if it matters)" if assumed_today else ""
        return f"Successfully updated status for '{title}' to {status}.{note}"
    except Exception as e:
        return f"Error updating status: {str(e)}"
```

Docstring update: document the params, the Jan-1 convention, and the assumed-today flag. NOTE: `two_phase` and `_normalize`/`_normalized_col` are already imported in this module post-#122 — verify; add_read_event creates the edition get-or-create (format "Unknown" edition may be created — same as the old placeholder behavior, acceptable). The old inline edition/ReadingHistory block is deleted.

- [ ] **Step 4: Librarian instruction** (services.py FEEDBACK HANDLING): change the first bullet to:

```
            - If user says "I read that", use 'update_reading_status' AND 'update_suggestion_status(Already Read)'.
              If they indicate it was a while ago ("years ago", "back in college"), ask roughly when —
              a year is enough — and pass it as 'year'; without a date the entry is logged as today,
              which wrongly blocks re-read suggestions for 2 years.
```

- [ ] **Step 5: Run** unit + collect-check integration; ruff both files.

- [ ] **Step 6: Commit**

```bash
git add src/agentic_librarian/mcp/server.py src/agentic_librarian/agents/services.py test/unit/test_mcp_tools.py test/integration/test_mcp_tools.py
git commit -m "fix(mcp): update_reading_status — normalized lookup, dup guard via add_read_event, honest dates (#112)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 5: Availability cache upsert + eviction — #110

**Files:**
- Modify: `src/agentic_librarian/availability/service.py` (`availability_for` write path; `batch_availability` phase 3; new `_upsert_cache_row`, `_evict_stale`)
- Test: `test/unit/test_availability_batch.py` + `test/unit/test_availability_service.py` (extend)

**Interfaces:** module-private helpers only.

- [ ] **Step 1: Failing tests** — extend `test/unit/test_availability_batch.py`:

```python
def test_write_back_uses_upsert(monkeypatch):
    # fake session captures session.execute(stmt) calls; assert the statement is a
    # postgresql Insert with on_conflict (stmt.__class__ / hasattr(stmt, "on_conflict_do_update")
    # — assert via str(stmt) containing "ON CONFLICT" after compile with postgresql dialect)
def test_eviction_runs_after_write_back(monkeypatch):
    # after a successful phase-3 write, a DELETE for fetched_at < cutoff is executed
```

(Write real bodies: compile the captured statement with `sqlalchemy.dialects.postgresql.dialect()` and assert `"ON CONFLICT (provider, library_slug, norm_title, norm_author) DO UPDATE"` appears; for eviction assert a delete statement referencing `fetched_at` executes. Binding intents: upsert targets the composite PK; eviction cutoff is 30 days; eviction only after successful write-back.)

- [ ] **Step 2: Verify failure.**

- [ ] **Step 3: Implement** in `availability/service.py`:

```python
from sqlalchemy.dialects.postgresql import insert as pg_insert

_EVICT_AFTER_DAYS = 30


def _upsert_cache_row(session, slug: str, title: str, author: str, formats: list, now) -> None:
    """Durable upsert on the composite PK (GH #110) — two concurrent writers can no longer
    IntegrityError; last write wins, which is fine for a cache."""
    nt, na = _normalize(title), _normalize(author)
    stmt = pg_insert(AvailabilityCache).values(
        provider=_PROVIDER, library_slug=slug, norm_title=nt, norm_author=na,
        payload={"formats": formats}, fetched_at=now,
    ).on_conflict_do_update(
        index_elements=["provider", "library_slug", "norm_title", "norm_author"],
        set_={"payload": {"formats": formats}, "fetched_at": now},
    )
    session.execute(stmt)


def _evict_stale(session, now) -> None:
    """Opportunistic eviction piggybacked on writes (GH #110) — rows are otherwise
    immortal. Unindexed seq scan is fine at this table's size; revisit if it grows."""
    from datetime import timedelta

    session.execute(
        AvailabilityCache.__table__.delete().where(
            AvailabilityCache.fetched_at < now - timedelta(days=_EVICT_AFTER_DAYS)
        )
    )
```

- `batch_availability` phase 3: replace the per-row add-or-update with `_upsert_cache_row(session, slug, title, author, formats, now)` per fetched item, then `_evict_stale(session, now)` at the end of the same session; the surrounding best-effort try/except stays.
- `availability_for`: replace its `if row is None: session.add(...) else: update` block with `_upsert_cache_row(...)` (the read-side `session.get` freshness check stays).
- NOTE sqlite: `pg_insert` compiles only on Postgres — these paths are exercised by unit tests via statement inspection (no execution) and by CI's Postgres. Ensure no local-unit test EXECUTES the statement against sqlite.

- [ ] **Step 4: Run** unit + collect-check `test_availability_service_cache.py`/`test_availability_api.py` (CI pins; they exercise the real upsert against Postgres).

- [ ] **Step 5: Commit**

```bash
git add src/agentic_librarian/availability/service.py test/unit/test_availability_batch.py test/unit/test_availability_service.py
git commit -m "fix(availability): ON CONFLICT upsert + 30-day piggybacked eviction (#110)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 6: Embeds out of sessions + pool 5+2 — #123

**Files:**
- Modify: `src/agentic_librarian/etl/persist.py` (new module-level `collect_embedding_texts(row)`)
- Modify: `src/agentic_librarian/enrichment/two_phase.py` (`_persist_row` warms the cache first)
- Modify: `src/agentic_librarian/mcp/server.py:88-120,186-240` (search tools embed before their sessions)
- Modify: `src/agentic_librarian/db/session.py` (+ `test/unit/test_pool_config.py`, `docs/project_notes/key_facts.md`, `docs/project_notes/decisions.md` ADR-059 line)
- Test: `test/unit/test_embed_warming.py` (new)

**Interfaces:**
- Produces: `collect_embedding_texts(row: dict) -> list[str]` in `etl/persist.py` — trope names from `enriched_tropes[].trope_name`, style strings via `_iter_style_items` over `author_style`/`work_style`/each `narrator_styles` value, plus cleaned genres∪moods fallback tags when `enriched_tropes` is empty and `row.get("write_fallback_tropes", True)`.

- [ ] **Step 1: Failing tests** — create `test/unit/test_embed_warming.py`:

```python
"""#123: embedding texts are collected from the scout row and warmed BEFORE any session."""

from agentic_librarian.etl.persist import collect_embedding_texts


def test_collects_trope_and_style_texts():
    row = {
        "enriched_tropes": [{"trope_name": "Found Family"}, {"trope_name": "Slow Burn"}],
        "author_style": {"pacing": "leisurely"},
        "work_style": {"tone": "wry"},
        "narrator_styles": {"Sam": {"accent": "Irish"}},
        "genres": ["Fantasy"], "moods": ["Cozy"],
    }
    texts = collect_embedding_texts(row)
    assert {"Found Family", "Slow Burn", "leisurely", "wry", "Irish"} <= set(texts)
    assert "Fantasy" not in texts  # real tropes present -> no fallback tags


def test_collects_fallback_tags_when_no_real_tropes():
    row = {"enriched_tropes": [], "genres": ["Fantasy"], "moods": ["Cozy"],
           "author_style": {}, "work_style": {}, "narrator_styles": {}}
    texts = collect_embedding_texts(row)
    assert set(texts) & {"Fantasy", "Cozy"}  # cleaned fallback tags included


def test_persist_row_warms_before_session(monkeypatch):
    from unittest.mock import MagicMock
    from agentic_librarian.enrichment import two_phase

    session_state = {"open": 0}
    warmed_during = []

    class FakeSession:
        def __enter__(self):
            session_state["open"] += 1
            return MagicMock()
        def __exit__(self, *a):
            session_state["open"] -= 1
            return False

    monkeypatch.setattr(two_phase, "db_manager", MagicMock(get_session=lambda: FakeSession()))
    def fake_embed(model, text):
        warmed_during.append(session_state["open"])
        return [0.0]
    monkeypatch.setattr(two_phase, "get_cached_embedding", fake_embed)
    monkeypatch.setattr(two_phase, "persist_enriched_work", lambda *a, **k: MagicMock())
    # drive via the public seam the branch actually exposes — read _persist_row's final
    # call shape and adapt: the binding assertion is warmed calls all happen with 0 sessions open
    ...
    assert warmed_during and all(n == 0 for n in warmed_during)
```

(Complete the drive mechanics after reading `_persist_row`'s current shape — the warm loop must run before `enrich_fast`/`enrich_deep`'s write session opens, which likely means warming in `enrich_fast`/`enrich_deep` between `_run_scouts` and the write `with`, NOT inside `_persist_row` which receives an open session. Place the warm call there; keep `collect_embedding_texts` as the single source of texts. The test then monkeypatches at the two_phase level and asserts `open == 0` during warming.)

- [ ] **Step 2: Verify failure.**

- [ ] **Step 3: Implement**

1. `etl/persist.py` — `collect_embedding_texts(row)` reusing `_iter_style_items`, `_nan_to_list`, `clean_genres`/`clean_moods`/`clean_trope_name` exactly as the persist path does (fallback tags = the cleaned names `clean_trope_name(tag)` yields over genres∪moods).
2. `two_phase.py` — in `enrich_fast` and `enrich_deep`, after `row = _run_scouts(...)` and BEFORE the write session:

```python
    # GH #123: warm the embedding LRU with every text the persist will standardize, so no
    # network embed happens inside the write session (the pool's 5+2 sizing depends on it).
    for text in collect_embedding_texts(row):
        try:
            get_cached_embedding(EMBED_MODEL, text)
        except Exception:  # noqa: BLE001 - warming is best-effort; _safe_standardize degrades in-session
            logger.warning("embed warm failed for %r — persist will retry in-session", text)
```

with `EMBED_MODEL = "gemini-embedding-001"` (import/define once — match the managers' constant; consider exposing it from scouts/utils to avoid a third copy) and imports for `collect_embedding_texts`/`get_cached_embedding`/logging.
3. `mcp/server.py` search tools: hoist the target-embedding loops above the `with db_manager.get_session(...)` in `search_internal_database` and `get_unacted_suggestions`: compute `embeddings = [get_cached_embedding(EMBED_MODEL, t) for t in target_tropes]` (and styles) before the session; inside the session, managers are constructed as today for `find_similar_*` but `_get_embedding` is no longer called pre-warmed... simpler and explicit: pass the precomputed vectors to the existing downstream code (read the two tools; the embeddings feed pgvector queries — keep variable names, just move the computation up; on cache hit the in-session path costs nothing, so ALTERNATIVELY warm-only: call get_cached_embedding above the session and leave the in-session code untouched — CHOOSE the warm-only form for minimal diff, mirroring two_phase).
4. `db/session.py`: `max_overflow` 5 → 2; comment's headroom paragraph replaced with: "5+2 per engine × max-instances=2 = 14 vs db-f1-micro's ~25. Safe since #94 (no scout/LLM/Thunder calls in sessions) and #123 (embeds warmed into the LRU before write sessions; search tools warm before reading)." Update `test_pool_config.py` (== 2), key_facts.md pool sentence ("5+2 per engine … 2 × 7 = 14 …; #123 landed"), ADR-059 consequences line (overflow now 2; #123 resolved).

- [ ] **Step 4: Run** `test/unit/test_embed_warming.py test/unit/test_pool_config.py test/unit -q` — green; collect-check `test_two_phase_fast.py`/`test_two_phase_deep.py`/`test_mcp_tools.py` integration.

- [ ] **Step 5: Commit**

```bash
git add -A src test docs/project_notes
git commit -m "perf(embeddings): warm the LRU outside DB sessions; pool tightens to 5+2 (#123)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## Out of scope (PR-D, separate plan)

#95 migration + get_or_create helpers + advisory lock + log_suggestion dedup; #97 deep_enriched_at + 503 + --requeue-unenriched; #108 timestamptz; #109 indexes; the gated dedup backfill. PR-D branches from main after PR-C merges; its prod sequence is in the spec.
