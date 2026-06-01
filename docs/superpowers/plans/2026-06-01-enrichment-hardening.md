# Enrichment Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make web-discovered books actually get their structured metadata — fix Hardcover (contributes nothing), the style-dict persist crash, and the silent Google Books 429 storm; nudge the Critic to commit to a one-shot answer.

**Architecture:** Four independent changes to the enrichment path. Hardcover's exact-match editions query is replaced with a 2-step fuzzy `search`→`books`-by-id lookup. StyleScout output is normalized to `{attr: str}` and `persist_enriched_work` guards against non-string style values. `GoogleBooksScout` warns once when unauthenticated. The shared Critic prompt gains a one-shot-commitment line.

**Tech Stack:** Python 3.11, SQLAlchemy, Hardcover GraphQL API, Google Books REST, pytest. Tests run in the dev container: `docker exec agentic_librarian_app sh -lc 'cd /app && python -m pytest ...'`. Commit with `SKIP=pytest git commit` (the pytest pre-commit hook is skipped; ruff/ruff-format run — if ruff-format reformats, re-`git add` and re-commit). Lint is authoritative via pre-commit, NOT bare `ruff check`.

---

## File Structure

- `src/agentic_librarian/scouts/metadata_scout.py` — `HardcoverScout` rewrite (Task 2), `GoogleBooksScout` warning (Task 3), `StyleScout` normalization + `_flatten_style_map` helper (Task 1a).
- `src/agentic_librarian/etl/persist.py` — `_iter_style_items` guard helper + use it in the 3 style loops (Task 1b).
- `src/agentic_librarian/agents/prompts.py` — `CRITIC_INSTRUCTION` one-shot line (Task 4).
- `.env.example` — `GOOGLE_BOOKS_API_KEY` recommended note (Task 3).
- `test/unit/test_metadata_scout.py` — Hardcover, Google Books, StyleScout normalization tests.
- `test/unit/test_persist_styles.py` (new) — `_iter_style_items` guard unit test.
- `test/integration/test_persist_enriched.py` (new) — db_integration regression: dict-valued style doesn't crash.
- `test/unit/test_prompts.py` (new) — Critic one-shot assertion.
- `docs/project_notes/decisions.md`, `docs/project_notes/issues.md` — ADR-043 + resolve REC-021/022, REC-016 #3 (Task 5).

---

## Task 1: Style-value guard + StyleScout normalization (REC-021)

**Files:**
- Modify: `src/agentic_librarian/scouts/metadata_scout.py` (StyleScout + new module helper)
- Modify: `src/agentic_librarian/etl/persist.py`
- Test: `test/unit/test_metadata_scout.py`, `test/unit/test_persist_styles.py` (new), `test/integration/test_persist_enriched.py` (new)

### 1a. StyleScout output normalization

- [ ] **Step 1: Write the failing test** in `test/unit/test_metadata_scout.py`

```python
def test_flatten_style_map_hoists_nested_and_drops_nonstrings():
    from agentic_librarian.scouts.metadata_scout import _flatten_style_map

    raw = {
        "perspective": "1st person",
        "pacing": "",  # empty -> dropped
        "differences": {"prose_density": "denser", "tone": "darker"},  # nested -> hoisted
        "junk": ["a", "b"],  # list -> dropped
    }
    assert _flatten_style_map(raw) == {
        "perspective": "1st person",
        "prose_density": "denser",
        "tone": "darker",
    }
    assert _flatten_style_map("not a dict") == {}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker exec agentic_librarian_app sh -lc 'cd /app && python -m pytest test/unit/test_metadata_scout.py::test_flatten_style_map_hoists_nested_and_drops_nonstrings -v'`
Expected: FAIL with `ImportError: cannot import name '_flatten_style_map'`.

- [ ] **Step 3: Implement** — add this module-level function to `metadata_scout.py` (place it just above `class StyleScout`):

```python
def _flatten_style_map(data: dict) -> dict[str, str]:
    """Coerce a scouted style dict to {attribute: non-empty-string}. The work-style prompt asks the
    model to also list attributes that DIFFER from the author baseline, so a value can come back as a
    nested dict (e.g. {"differences": {"pacing": "..."}}). Hoist one level of nested string values to
    the top level and drop anything that is not a non-empty string (REC-021)."""
    out: dict[str, str] = {}
    if not isinstance(data, dict):
        return out
    for key, val in data.items():
        if isinstance(val, str) and val.strip():
            out[key] = val.strip()
        elif isinstance(val, dict):
            for sub_key, sub_val in val.items():
                if isinstance(sub_val, str) and sub_val.strip():
                    out.setdefault(sub_key, sub_val.strip())
    return out
```

- [ ] **Step 4: Apply it in the three StyleScout scout methods.** In `scout_work_style`, `scout_author_style`, and `scout_narrator_style`, wrap the return value. Change each method's final `return self._safe_extract_json(...) or {}` to:

```python
        return _flatten_style_map(self._safe_extract_json(self._extract_text(response), "Work Style", title))
```

(Use the existing label args per method: `"Work Style", title` / `"Author Style", name` / the narrator method's existing args. `_flatten_style_map(None)` returns `{}`, so the `or {}` is no longer needed.)

- [ ] **Step 5: Run test to verify it passes**

Run: `docker exec agentic_librarian_app sh -lc 'cd /app && python -m pytest test/unit/test_metadata_scout.py::test_flatten_style_map_hoists_nested_and_drops_nonstrings -v'`
Expected: PASS.

### 1b. persist_enriched_work guard (safety net)

- [ ] **Step 6: Write the failing unit test** in new file `test/unit/test_persist_styles.py`

```python
from agentic_librarian.etl.persist import _iter_style_items


def test_iter_style_items_keeps_strings_skips_nonstrings(capsys):
    data = {"perspective": "1st person", "blank": "", "bad": {"nested": "x"}, "missing": None}
    assert list(_iter_style_items(data, "Work 'X'")) == [("perspective", "1st person")]
    assert "skipping non-string style 'bad'" in capsys.readouterr().out


def test_iter_style_items_handles_none():
    assert list(_iter_style_items(None, "Work 'X'")) == []
```

- [ ] **Step 7: Run to verify it fails**

Run: `docker exec agentic_librarian_app sh -lc 'cd /app && python -m pytest test/unit/test_persist_styles.py -v'`
Expected: FAIL with `ImportError: cannot import name '_iter_style_items'`.

- [ ] **Step 8: Implement** — add this helper near the top of `src/agentic_librarian/etl/persist.py` (after the imports, before `persist_enriched_work`):

```python
def _iter_style_items(style_data: dict | None, owner_label: str):
    """Yield (attr_type, style_name) for valid non-empty string values only. A malformed scout
    response can nest a value as a dict/list; passing that to standardize_style would make it a
    Style.name and raise 'can't adapt type dict'. Skip + warn instead so persistence degrades
    gracefully (REC-021)."""
    for attr_type, style_name in (style_data or {}).items():
        if isinstance(style_name, str) and style_name.strip():
            yield attr_type, style_name
        elif style_name:
            print(f"Warning: skipping non-string style '{attr_type}'={type(style_name).__name__} for {owner_label}")
```

- [ ] **Step 9: Use the helper in all three style loops** in `persist_enriched_work`. Replace each of the three existing loops:

Author styles (was `for attr_type, style_name in author_style_data.items(): if not style_name: continue`):
```python
        if role == "Author" and author_style_data:
            for attr_type, style_name in _iter_style_items(author_style_data, f"Author '{name}'"):
                standard_style = style_manager.standardize_style(style_name, category="Author")
                existing_link = (
                    session.query(AuthorStyle)
                    .filter_by(author_id=author.id, style_id=standard_style.id, attribute_type=attr_type)
                    .first()
                )
                if not existing_link:
                    session.add(AuthorStyle(author=author, style=standard_style, attribute_type=attr_type))
```

Work styles (was `for attr_type, style_name in work_style_data.items(): if not style_name: continue`):
```python
    work_style_data = row.get("work_style", {})
    if work_style_data:
        for attr_type, style_name in _iter_style_items(work_style_data, f"Work '{row.get('Title')}'"):
            standard_style = style_manager.standardize_style(style_name, category="Work")
            existing_link = (
                session.query(WorkStyle)
                .filter_by(work_id=work.id, style_id=standard_style.id, attribute_type=attr_type)
                .first()
            )
            if not existing_link:
                session.add(WorkStyle(work=work, style=standard_style, attribute_type=attr_type))
```

Narrator styles (was `for attr_type, style_name in n_style_data.items(): if not style_name: continue`):
```python
        if n_style_data:
            for attr_type, style_name in _iter_style_items(n_style_data, f"Narrator '{n_name}'"):
                standard_style = style_manager.standardize_style(style_name, category="Narrator")
                existing_link = (
                    session.query(NarratorStyle)
                    .filter_by(narrator_id=narrator.id, style_id=standard_style.id, attribute_type=attr_type)
                    .first()
                )
                if not existing_link:
                    session.add(NarratorStyle(narrator=narrator, style=standard_style, attribute_type=attr_type))
```

- [ ] **Step 10: Run unit test to verify it passes**

Run: `docker exec agentic_librarian_app sh -lc 'cd /app && python -m pytest test/unit/test_persist_styles.py -v'`
Expected: PASS.

- [ ] **Step 11: Add a db_integration regression test** in new file `test/integration/test_persist_enriched.py`

```python
import pytest
from agentic_librarian.db.session import DatabaseManager
from agentic_librarian.etl.persist import persist_enriched_work
from agentic_librarian.scouts.style_manager import StyleManager
from agentic_librarian.scouts.trope_manager import TropeManager


@pytest.mark.db_integration
def test_persist_tolerates_dict_style_value(db_url, monkeypatch):
    # Regression (REC-021): a work_style attribute whose value is a dict must not crash persistence.
    monkeypatch.setenv("GOOGLE_SEARCH_API_KEY", "dummy-key-for-construction")
    dbm = DatabaseManager(db_url)
    with dbm.get_session() as session:
        tm = TropeManager(session=session)
        sm = StyleManager(session=session)
        # Avoid live embedding calls: standardize_style for the one valid attr returns a stub Style.
        from agentic_librarian.db.models import Style

        monkeypatch.setattr(
            sm, "standardize_style", lambda raw, category, threshold=0.85: Style(name=raw, category=category)
        )
        row = {
            "Title": "Dict Style Book",
            "Author_1": "Some Author",
            "format": "ebook",
            "skip_enrichment": False,
            "date_completed": None,
            "contributors": [{"name": "Some Author", "role": "Author"}],
            "work_style": {"perspective": "1st person", "differences": {"pacing": "fast"}},
        }
        work = persist_enriched_work(session, row, tm, sm)
        session.flush()
        assert work is not None and work.title == "Dict Style Book"
```

- [ ] **Step 12: Run the db_integration test**

Run: `docker exec agentic_librarian_app sh -lc 'cd /app && python -m pytest test/integration/test_persist_enriched.py -o addopts="" -m db_integration -v'`
Expected: PASS (the dict-valued `differences` is skipped by the guard; `perspective` persists).

- [ ] **Step 13: Commit**

```bash
git add src/agentic_librarian/scouts/metadata_scout.py src/agentic_librarian/etl/persist.py test/unit/test_metadata_scout.py test/unit/test_persist_styles.py test/integration/test_persist_enriched.py
SKIP=pytest git commit -m "fix: normalize StyleScout output + guard non-string style values (REC-021)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 2: Hardcover 2-step fuzzy lookup (REC-022)

**Files:**
- Modify: `src/agentic_librarian/scouts/metadata_scout.py` (`HardcoverScout.search` + helpers)
- Test: `test/unit/test_metadata_scout.py`

**Background:** the current `search` filters editions with `_eq` on title + format + US country, which almost never all match. Hardcover's `search(query, query_type:"Book")` returns ranked book hits; a `books(where:{id:{_eq}})` query then returns `description`/`pages`/`contributions`/`cached_tags`/`editions`. Search by **title only** (adding the author can surface companion "workbook" entries); select the hit whose `author_names` matches the author and that has the most `users_read_count`, excluding companion titles.

- [ ] **Step 1: Write the failing hit-selection unit test** in `test/unit/test_metadata_scout.py`

```python
def test_hardcover_select_hit_prefers_author_match_and_reads():
    import agentic_librarian.scouts.metadata_scout as md_scout

    scout = md_scout.HardcoverScout(api_key="key")
    hits = [
        {"document": {"id": "999", "title": "Workbook on The Spanish Love Deception", "author_names": ["Elena Armas"], "users_read_count": 50}},
        {"document": {"id": "1", "title": "The Spanish Love Deception", "author_names": ["Elena Armas"], "users_read_count": 578}},
        {"document": {"id": "2", "title": "The Spanish Love Deception", "author_names": [], "users_read_count": 1}},
    ]
    assert scout._select_hit(hits, "Elena Armas") == 1  # canonical (companion excluded, max reads, author match)
    assert scout._select_hit([], "Elena Armas") is None
```

- [ ] **Step 2: Run to verify it fails**

Run: `docker exec agentic_librarian_app sh -lc 'cd /app && python -m pytest test/unit/test_metadata_scout.py::test_hardcover_select_hit_prefers_author_match_and_reads -v'`
Expected: FAIL with `AttributeError: 'HardcoverScout' object has no attribute '_select_hit'`.

- [ ] **Step 3: Write the failing full-parse test** in `test/unit/test_metadata_scout.py`

```python
def test_hardcover_search_two_step_parses_canonical_book(monkeypatch):
    import agentic_librarian.scouts.metadata_scout as md_scout

    scout = md_scout.HardcoverScout(api_key="key")
    search_resp = {"data": {"search": {"results": {"hits": [
        {"document": {"id": "999", "title": "Workbook on The Spanish Love Deception", "author_names": ["Elena Armas"], "users_read_count": 50}},
        {"document": {"id": "1", "title": "The Spanish Love Deception", "author_names": ["Elena Armas"], "users_read_count": 578}},
    ]}}}}
    books_resp = {"data": {"books": [{
        "title": "The Spanish Love Deception",
        "description": "A rom-com.",
        "pages": 488,
        "release_date": "2021-02-23",
        "contributions": [{"author": {"name": "Elena Armas"}, "author_role": {"name": "Author"}}],
        "moods": [{"tagSlug": "funny"}],
        "genres": [{"tagSlug": "romance"}],
        "editions": [
            {"isbn_13": "9781", "edition_format": "Hardcover", "pages": 500, "audio_seconds": None, "release_date": "2022-01-01", "country": {"name": "United Kingdom"}},
            {"isbn_13": "9782", "edition_format": "Paperback", "pages": 488, "audio_seconds": None, "release_date": "2021-02-23", "country": {"name": "United States of America"}},
        ],
    }]}}
    responses = iter([search_resp, books_resp])
    captured = {}

    def fake_make_request(method, url, **kwargs):
        captured.setdefault("ids", []).append(kwargs.get("json", {}).get("variables"))
        return next(responses)

    monkeypatch.setattr(scout, "_make_request", fake_make_request)
    md = scout.search("The Spanish Love Deception", "Elena Armas", format="Paperback")

    assert md["title"] == "The Spanish Love Deception"
    assert md["description"] == "A rom-com."
    assert md["page_count"] == 488  # the US Paperback edition, not the UK Hardcover
    assert md["isbn_13"] == "9782"
    assert [c["name"] for c in md["contributors"]] == ["Elena Armas"]
    assert md["genres"] == {"romance"} and md["moods"] == {"funny"}
    # second call queried the canonical book id (1), not the workbook (999)
    assert captured["ids"][1] == {"id": 1}


def test_hardcover_search_returns_empty_when_no_hits(monkeypatch):
    import agentic_librarian.scouts.metadata_scout as md_scout

    scout = md_scout.HardcoverScout(api_key="key")
    monkeypatch.setattr(scout, "_make_request", lambda *a, **k: {"data": {"search": {"results": {"hits": []}}}})
    assert scout.search("Nonexistent", "Nobody") == {}
```

- [ ] **Step 4: Run to verify both fail**

Run: `docker exec agentic_librarian_app sh -lc 'cd /app && python -m pytest test/unit/test_metadata_scout.py -k hardcover -v'`
Expected: the two new tests FAIL.

- [ ] **Step 5: Replace `HardcoverScout.search` and add helpers.** Replace the entire body of `HardcoverScout.search` (keep the class, `__init__`) with the following methods:

```python
    def search(self, title: str, author: str, **kwargs) -> dict:
        format_val = kwargs.get("format", "Paperback")
        if not self.api_key:
            return {}
        book_id = self._find_book_id(title, author)
        if book_id is None:
            return {}
        book = self._fetch_book(book_id)
        if not book:
            return {}
        return self._book_to_metadata(book, format_val)

    def _graphql(self, query: str, variables: dict) -> dict:
        headers = {"Content-Type": "application/json", "Authorization": f"Bearer {self.api_key}"}
        url = "https://api.hardcover.app/v1/graphql"
        data = self._make_request("POST", url, headers=headers, json={"query": query, "variables": variables})
        return data.get("data") or {}

    def _find_book_id(self, title: str, author: str) -> int | None:
        # Search by TITLE only (adding the author can rank companion "workbook" entries first).
        query = 'query($q: String!) { search(query: $q, query_type: "Book", per_page: 5) { results } }'
        data = self._graphql(query, {"q": title})
        hits = (((data.get("search") or {}).get("results") or {}).get("hits")) or []
        return self._select_hit(hits, author)

    @staticmethod
    def _select_hit(hits: list, author: str) -> int | None:
        def norm(s: str) -> str:
            return " ".join((s or "").lower().split())

        companions = ("workbook", "summary", "analysis", "study guide", "conversation starters")
        a = norm(author)
        docs = [h.get("document", {}) for h in hits]

        def is_companion(d: dict) -> bool:
            return any(c in (d.get("title") or "").lower() for c in companions)

        def author_matches(d: dict) -> bool:
            return any(a and (a in norm(n) or norm(n) in a) for n in (d.get("author_names") or []))

        def reads(d: dict) -> int:
            return d.get("users_read_count") or 0

        candidates = [d for d in docs if not is_companion(d) and author_matches(d)]
        if not candidates:
            candidates = [d for d in docs if not is_companion(d)] or docs
        if not candidates:
            return None
        best = max(candidates, key=reads)
        try:
            return int(best.get("id"))
        except (TypeError, ValueError):
            return None

    def _fetch_book(self, book_id: int) -> dict | None:
        query = """
            query GetBook($id: Int!) {
                books(where: {id: {_eq: $id}}) {
                    title
                    description
                    pages
                    release_date
                    contributions { author { name } author_role { name } }
                    moods: cached_tags(path: "Mood")
                    genres: cached_tags(path: "Genre")
                    editions {
                        isbn_13
                        edition_format
                        pages
                        audio_seconds
                        release_date
                        country { name }
                    }
                }
            }
        """
        books = (self._graphql(query, {"id": book_id}).get("books")) or []
        return books[0] if books else None

    def _book_to_metadata(self, book: dict, format_val: str) -> dict:
        editions = book.get("editions") or []

        def fmt_match(e: dict) -> bool:
            return (e.get("edition_format") or "").lower() == format_val.lower()

        def is_us(e: dict) -> bool:
            return ((e.get("country") or {}).get("name")) == "United States of America"

        selected = (
            next((e for e in editions if fmt_match(e) and is_us(e)), None)
            or next((e for e in editions if fmt_match(e)), None)
            or (editions[0] if editions else {})
        )

        audio_seconds = selected.get("audio_seconds")
        contributors = []
        for c in book.get("contributions", []):
            name = (c.get("author") or {}).get("name")
            role = (c.get("author_role") or {}).get("name") or "Author"
            if name:
                contributors.append({"name": name, "role": role})

        edition_release = selected.get("release_date")
        return {
            "title": book.get("title"),
            "contributors": contributors,
            "edition_format": selected.get("edition_format"),
            "page_count": selected.get("pages") or book.get("pages"),
            "publication_date": edition_release,
            "original_publication_date": book.get("release_date") or edition_release,
            "isbn_13": selected.get("isbn_13"),
            "moods": {m.get("tagSlug") for m in (book.get("moods") or []) if m.get("tagSlug")},
            "genres": {g.get("tagSlug") for g in (book.get("genres") or []) if g.get("tagSlug")},
            "description": book.get("description", ""),
            "audio_minutes": audio_seconds // 60 if audio_seconds else None,
        }
```

- [ ] **Step 6: Run the Hardcover tests to verify they pass**

Run: `docker exec agentic_librarian_app sh -lc 'cd /app && python -m pytest test/unit/test_metadata_scout.py -k hardcover -v'`
Expected: the two new tests PASS.

**Then delete the obsolete test.** The existing parametrized `test_hardcover_scout_search` (and its `mock_data,expected_pages` parametrize block) asserts the OLD single-editions-query contract — it monkeypatches `_make_request` to return one editions payload and will now fail (the new `search` calls `_make_request` twice and expects a `{"data": {...}}` shape). Delete that test function and its `@pytest.mark.parametrize` decorator; the new tests in Steps 1 and 3 replace its coverage. Re-run `-k hardcover` and confirm only the new tests remain and pass.

- [ ] **Step 7: Add an `api_dependent` live test** in `test/unit/test_metadata_scout.py`

```python
import pytest


@pytest.mark.api_dependent
def test_hardcover_live_returns_metadata_for_known_title():
    import agentic_librarian.scouts.metadata_scout as md_scout

    md = md_scout.HardcoverScout().search("The Spanish Love Deception", "Elena Armas", format="ebook")
    assert md, "Hardcover returned nothing for a known title"
    assert md.get("description")
    assert md.get("page_count")
```

- [ ] **Step 8: (Optional, manual) run the live test** — only when ready to spend a Hardcover call:

Run: `docker exec agentic_librarian_app sh -lc 'cd /app && python -m pytest test/unit/test_metadata_scout.py::test_hardcover_live_returns_metadata_for_known_title -o addopts="" -m api_dependent -v'`
Expected: PASS (non-empty description + page_count). Skip in CI (api_dependent is deselected).

- [ ] **Step 9: Commit**

```bash
git add src/agentic_librarian/scouts/metadata_scout.py test/unit/test_metadata_scout.py
SKIP=pytest git commit -m "fix: Hardcover 2-step fuzzy search->books lookup (REC-022)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 3: Google Books unauthenticated warning + .env.example

**Files:**
- Modify: `src/agentic_librarian/scouts/metadata_scout.py` (`GoogleBooksScout.search`)
- Modify: `.env.example`
- Test: `test/unit/test_metadata_scout.py`

- [ ] **Step 1: Write the failing test** in `test/unit/test_metadata_scout.py`

```python
def test_google_books_warns_once_without_key(capsys, monkeypatch):
    import agentic_librarian.scouts.metadata_scout as md_scout

    monkeypatch.delenv("GOOGLE_BOOKS_API_KEY", raising=False)
    md_scout._gbooks_nokey_warned = False
    scout = md_scout.GoogleBooksScout()
    monkeypatch.setattr(scout, "_make_request", lambda *a, **k: {})
    scout.search("T", "A")
    scout.search("T2", "A2")
    out = capsys.readouterr().out
    assert out.count("no GOOGLE_BOOKS_API_KEY") == 1
```

- [ ] **Step 2: Run to verify it fails**

Run: `docker exec agentic_librarian_app sh -lc 'cd /app && python -m pytest test/unit/test_metadata_scout.py::test_google_books_warns_once_without_key -v'`
Expected: FAIL (no warning printed; `count == 0`).

- [ ] **Step 3: Implement.** Add a module-level flag near the top of `metadata_scout.py` (after the imports block, with the other module state):

```python
_gbooks_nokey_warned = False
```

Then at the very start of `GoogleBooksScout.search`, before building the request:

```python
    def search(self, title: str, author: str, **kwargs) -> dict:
        global _gbooks_nokey_warned
        if not self.api_key and not _gbooks_nokey_warned:
            print(
                "Warning: GoogleBooksScout has no GOOGLE_BOOKS_API_KEY — using the very low "
                "unauthenticated quota; expect 429s on enrichment bursts. Get a free key: "
                "https://developers.google.com/books/docs/v1/using#APIKey"
            )
            _gbooks_nokey_warned = True
        base_url = "https://www.googleapis.com/books/v1/volumes"
        # ... rest unchanged ...
```

- [ ] **Step 4: Run test to verify it passes**

Run: `docker exec agentic_librarian_app sh -lc 'cd /app && python -m pytest test/unit/test_metadata_scout.py::test_google_books_warns_once_without_key -v'`
Expected: PASS.

- [ ] **Step 5: Update `.env.example`.** Replace the existing Google Books line:

```
# Optional: raises Google Books rate limits.
GOOGLE_BOOKS_API_KEY=
```

with:

```
# RECOMMENDED: without it, Google Books calls are unauthenticated and share a tiny per-IP quota,
# so the per-discovery enrichment burst will 429 (discoveries then miss Google Books metadata).
# Free key: https://developers.google.com/books/docs/v1/using#APIKey
GOOGLE_BOOKS_API_KEY=
```

- [ ] **Step 6: Commit**

```bash
git add src/agentic_librarian/scouts/metadata_scout.py .env.example
SKIP=pytest git commit -m "feat: warn once when Google Books is unauthenticated; recommend the key

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 4: Critic one-shot commitment nudge (REC-016 #3)

**Files:**
- Modify: `src/agentic_librarian/agents/prompts.py`
- Test: `test/unit/test_prompts.py` (new)

- [ ] **Step 1: Write the failing test** in new file `test/unit/test_prompts.py`

```python
from agentic_librarian.agents.prompts import CRITIC_INSTRUCTION


def test_critic_commits_to_a_one_shot_recommendation():
    text = CRITIC_INSTRUCTION.lower()
    assert "best-effort" in text
    assert "never" in text  # never ask a clarifying question / never return empty
```

- [ ] **Step 2: Run to verify it fails**

Run: `docker exec agentic_librarian_app sh -lc 'cd /app && python -m pytest test/unit/test_prompts.py -v'`
Expected: FAIL (the phrase is not yet present).

- [ ] **Step 3: Implement.** In `src/agentic_librarian/agents/prompts.py`, change the final line of `CRITIC_INSTRUCTION` from:

```python
            Always end with a clear final recommendation naming the specific book(s) you recommend.
            """
```

to:

```python
            Always end with a clear final recommendation naming the specific book(s) you recommend.

            ONE-SHOT: This is a single-shot request, not a conversation. Always commit to a concrete
            best-effort recommendation from the candidates available — never ask a clarifying question
            and never return an empty response. If the evidence is thin, recommend the closest match
            and say so.
            """
```

- [ ] **Step 4: Run test to verify it passes**

Run: `docker exec agentic_librarian_app sh -lc 'cd /app && python -m pytest test/unit/test_prompts.py -v'`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/agentic_librarian/agents/prompts.py test/unit/test_prompts.py
SKIP=pytest git commit -m "feat: Critic commits to a best-effort one-shot recommendation (REC-016 #3)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 5: Docs — ADR-043 + resolve issues

**Files:**
- Modify: `docs/project_notes/decisions.md`
- Modify: `docs/project_notes/issues.md`

- [ ] **Step 1: Append ADR-043** to the end of `docs/project_notes/decisions.md`:

```markdown

### ADR-043: Hardcover Lookup via Fuzzy Search + Book-by-Id (2026-06-01)
**Context:**
- `HardcoverScout` filtered editions with three exact-match clauses (`book.title _eq` AND
  `edition_format _eq "ebook"` AND US `country _eq`). These almost never all matched real data, so
  Hardcover (priority-1 scout) silently contributed nothing to web-discovered books (REC-022). Hasura
  blocks `_ilike`/fuzzy operators on the editions filter, but Hardcover exposes a fuzzy `search` query.

**Decision:**
- Two-step lookup: (1) `search(query: <title>, query_type:"Book")` — by title only (adding the author
  surfaces companion "workbook" entries) — then select the hit whose `author_names` matches and that
  has the most `users_read_count`, excluding companion titles; (2) `books(where:{id:{_eq}})` for
  description/pages/contributions/cached_tags/editions. Format/country preference is applied in Python
  over the returned editions (prefer requested format + US, else format, else any).

**Consequences:**
- Hardcover now returns real metadata for known titles, including ones whose stored title differs
  (`&` vs "and", articles) since matching is fuzzy. Two API calls per book instead of one (acceptable —
  priority-1 short-circuits the other scouts; Hardcover quota is generous). Companion/workbook hits are
  filtered heuristically; a future refinement could weight series/edition signals.
```

- [ ] **Step 2: Resolve REC-021 / REC-022 and REC-016 #3** in `docs/project_notes/issues.md`. Set REC-022's `**Status**` to:

```
- **Status**: Resolved (2026-06-01) — HardcoverScout rewritten as a 2-step fuzzy search->books-by-id lookup with author-matched, read-count-ranked hit selection (companion titles excluded); format/country preference applied in Python. ADR-043. Live-verified for a known title.
```

REC-021's `**Status**`:

```
- **Status**: Resolved (2026-06-01) — StyleScout output normalized to {attr: str} (_flatten_style_map hoists one nested level, drops non-strings) and persist_enriched_work guards every style loop via _iter_style_items (skips+warns on non-string values). Unit + db_integration regression tests.
```

In REC-016's Notes, append after item 4:

```
    - **[2026-06-01]** Item 3 (one-shot commitment) addressed: the SequentialAgent already enforces step
      order, and CRITIC_INSTRUCTION now tells the Critic to always commit to a best-effort recommendation
      (never ask a clarifying question / never return empty). Item 4 (multi-agent final-text extraction)
      remains open — re-evaluate after a post-enrichment-hardening e2e.
```

- [ ] **Step 3: Commit**

```bash
git add docs/project_notes/decisions.md docs/project_notes/issues.md
SKIP=pytest git commit -m "docs: ADR-043 Hardcover lookup; resolve REC-021/022, REC-016 #3

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Final verification (after all tasks)

- [ ] **Run the full offline suite** to confirm no regressions:

Run: `docker exec agentic_librarian_app sh -lc 'cd /app && python -m pytest -q -p no:cacheprovider -m "not api_dependent and not db_integration"'`
Expected: all pass (≥ 164: prior 160 + the new style/Hardcover/google-books/prompt unit tests).

- [ ] **Run the db_integration tests** (needs Postgres up):

Run: `docker exec agentic_librarian_app sh -lc 'cd /app && python -m pytest -o addopts="" -m db_integration -q'`
Expected: pass (incl. the new persist regression).

- [ ] **Run pre-commit** on all changed files to confirm lint/format are clean:

Run: `docker exec agentic_librarian_app sh -lc 'cd /app && pre-commit run --all-files'` (or rely on the per-commit hooks).
Expected: ruff + ruff-format pass.

- [ ] Use **superpowers:finishing-a-development-branch** to open the PR.
