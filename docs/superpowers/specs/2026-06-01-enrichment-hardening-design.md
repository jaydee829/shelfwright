# Enrichment Hardening — Design

**Date:** 2026-06-01
**Branch:** `spec/enrichment-hardening` (off `main` after PR #22/#23/#24 merged)
**Status:** Approved design — ready for implementation plan

## Context

The first live end-to-end recommendation runs (PR #22 ADK e2e, PR #23 Claude e2e) succeeded — they
returned real, on-target recommendations — but surfaced three enrichment-path defects that all
**degraded silently**: discovered books persisted with empty descriptions/page counts, and one work's
style persistence crashed (caught by a bare `except`). The recommendations still came through because
the Critic leaned on grounded discovery + LLM-scout tropes/styles, masking that the structured
metadata sources contributed little or nothing.

This branch hardens the enrichment path so web-discovered books actually get their structured
metadata. It addresses REC-021, REC-022, the Google Books 429 finding, and REC-016 item 3. REC-016
item 4 (multi-agent final-text extraction) is **explicitly deferred** for re-evaluation after these land.

### Root causes (verified during brainstorming)

- **REC-022 (Hardcover contributes nothing).** `HardcoverScout.search` filters editions with three
  exact-match clauses: `book.title _eq $title` **AND** `edition_format _eq $format` (`"ebook"`) **AND**
  `country.name _eq "United States of America"`. These almost never all match real data: e.g. *The
  Spanish Love Deception* exists (8 editions) but none have `edition_format == "ebook"` (values are
  `""`/`null`/`"Paperback"`) and most lack the US country row; *The Serpent & The Wings of Night*
  returns 0 on exact title (Hardcover stores it under an alternative title — the `&`-vs-"and"
  difference). The token is valid (HTTP 200); `_make_request` swallows the empty result. Hardcover
  blocks `_ilike`/fuzzy operators on the editions filter, **but exposes a fuzzy `search` query** that
  returns ranked book hits (the top hit for the *Serpent* title is correct, with `alternative_titles`
  containing the "and" spelling).
- **REC-021 (style name can be a dict).** `persist_enriched_work` (`etl/persist.py`) iterates
  `work_style` / `author_style` / `narrator_styles` as `{attr_type: "style_string"}` and passes each
  value to `style_manager.standardize_style(value, ...)`. `StyleScout.scout_work_style` /
  `scout_author_style` ask the LLM for attributes **and** "attributes where this book differs from the
  author baseline", so the model can return a **nested dict** for a value. A dict value becomes a
  `Style.name` → `psycopg2.ProgrammingError: can't adapt type 'dict'`, which the `enrich_and_persist_work`
  `except` swallows (that work's styles are lost; the run continues).
- **Google Books 429 (sustained).** `GOOGLE_BOOKS_API_KEY` is **unset**, so every call is
  unauthenticated against a tiny per-IP quota. The pipeline enriches each discovery in a loop
  (`for title, author in extract_discovery_pairs(...): enrich_and_persist_work(...)`), and each call
  runs the full ScoutManager — so ~5–8 unauthenticated Google Books calls burst per recommendation and
  sustain 429s that the PR #24 `urllib3` retry cannot recover (the quota does not reset within seconds).
  `GoogleBooksScout` already uses the key when present (`if self.api_key: params["key"] = ...`).

## Goals / Non-goals

**Goals**
1. Hardcover (priority-1 scout) returns real metadata for web-discovered books.
2. A nested/dict style value never crashes persistence, and valid scalar styles still persist.
3. The Google Books unauthenticated state is visible (not a silent 429 storm), and the key is the
   documented path.
4. The one-shot recommendation always commits to a best-effort answer (REC-016 #3).

**Non-goals (YAGNI / deferred)**
- Google Books throttling, caching, or call-count reduction (Hardcover carrying the load + a key is the
  real fix; #24's retry stays for transient blips).
- Style-only search schema flexibility (separate enhancement; see PR #23 review thread).
- REC-016 #4 (multi-agent final-text extraction) — re-evaluate after this branch.
- Broad enrichment observability/metrics framework (only the targeted Google Books warning here).

## Design

### Component 1 — Hardcover 2-step fuzzy lookup (REC-022)

Rewrite `HardcoverScout.search` (`scouts/metadata_scout.py`) to:

1. **Find the book (fuzzy):** `search(query: "<title> <author>", query_type: "Book", per_page: 5)`.
   Take the top hit and extract its book id from the returned `document`. If no hits → `return {}`.
2. **Fetch editions by book id:** `editions(where: {book_id: {_eq: <id>}})` selecting the same fields
   as today (isbn_13, title, edition_format, pages, audio_seconds, release_date, country, plus the
   book's description / `cached_tags` genres+moods / contributions). **No** `edition_format` or
   `country` filter in the query.
3. **Select an edition in Python** (the current body already loops editions): prefer an edition whose
   format matches the requested `format` *and* whose country is the US; then fall back to format-match
   only; then any edition. Keep the existing audiobook-vs-pages selection logic. Aggregate
   moods/genres across editions as today.
4. Return the same dict shape as today (so `persist_enriched_work` is unchanged for this path).

Interface unchanged: `search(title, author, format=...) -> dict`. Trade-off: two GraphQL calls instead
of one (acceptable — Hardcover quota is generous and priority-1 short-circuits the other scouts).

### Component 2 — Style-value guard + source normalization (REC-021)

Two layers, both small:

- **Defensive guard (safety net)** in `persist_enriched_work`: factor the repeated style-link loop
  (Author/Work/Narrator) so that before calling `standardize_style`, a value that is not a non-empty
  `str` is skipped with a single `print` warning naming the attribute. A malformed LLM response then
  degrades gracefully (that attribute dropped) instead of crashing the work's persistence.
- **Source normalization (root fix)** in `StyleScout`: after parsing each style dict
  (`scout_work_style`, `scout_author_style`, `scout_narrator_style`), coerce to `{attr: str}` — drop
  non-string values (or flatten an obvious nested "differences" structure into top-level `{attr: str}`
  entries if the LLM returns one). The scout owns producing a clean `{attr_type: style_string}` map.

### Component 3 — Google Books unauthenticated visibility

- Emit a **one-time** warning the first time `GoogleBooksScout` issues a request without `self.api_key`:
  e.g. "GoogleBooksScout: no GOOGLE_BOOKS_API_KEY — using the very low unauthenticated quota; expect
  429s. Get a free key at https://developers.google.com/books/docs/v1/using#APIKey". Use a module-level
  flag so it prints once per process, not per book.
- Update `.env.example`: move `GOOGLE_BOOKS_API_KEY` from "optional" to "recommended" with a one-line
  note that without it the enrichment burst will 429.
- No code change to how the key is used (already correct). User action: obtain a free key.

### Component 4 — Critic one-shot commitment (REC-016 #3)

Append one instruction to `CRITIC_INSTRUCTION` (`agents/prompts.py`, shared by both backends): on a
one-shot recommendation it must always return a concrete best-effort recommendation from the available
candidates — never ask a clarifying question and never return an empty response. The SequentialAgent
already enforces step order, so this is a prompt nudge only.

## Testing

Offline (CI, no live keys):
- **Hardcover parsing:** unit test mocking `_make_request` to return a canned `search` response then a
  canned `editions` response; assert `search` returns the expected dict (title, page_count/audio,
  genres, description) and selects the right edition. Add a "search returns no hits → `{}`" case.
- **Style guard:** unit test `persist_enriched_work` with a `row` whose `work_style` contains a
  dict-valued attribute alongside valid string attributes; assert no exception and the valid
  attributes persist as `WorkStyle` rows (db_integration), or assert the guard skips the dict
  (unit-level on the helper if factored out).
- **Style normalization:** unit test the StyleScout normalization helper maps a nested input to
  `{attr: str}` (drops/flattens non-strings).
- **Google Books warning:** unit test that constructing/calling `GoogleBooksScout` without a key emits
  the warning once (capture stdout / monkeypatch the flag).

Live (`api_dependent`, manual):
- `HardcoverScout().search("The Spanish Love Deception", "Elena Armas", format="ebook")` returns a
  non-empty dict with a description and page_count.
- Optional: re-run the recommendation e2e and confirm discovered works have Hardcover-sourced
  description/pages and no style-dict error in logs.

## Success criteria

- Hardcover returns real metadata for known titles (live) and is unit-covered for parsing.
- A dict-valued style never crashes persistence; valid scalar styles still persist (tested).
- Running unauthenticated against Google Books prints a clear one-time warning; `.env.example`
  documents the key.
- The Critic instruction commits to a best-effort one-shot recommendation.
- Offline suite green; no behavior change on the already-working trope/grounding paths.

## Issue tracking

Resolves REC-021, REC-022; mitigates the Google Books 429 finding (config + visibility) and REC-016 #3.
REC-016 #4 remains open (deferred). A new ADR will record the Hardcover 2-step lookup decision.
