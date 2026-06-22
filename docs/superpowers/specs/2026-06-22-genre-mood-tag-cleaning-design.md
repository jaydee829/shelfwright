# Genre / Mood Tag Cleaning — Design

**Date:** 2026-06-22
**Status:** Approved (brainstormed), pending plan
**Origin:** Bug — `Work.genres`/`Work.moods` contain raw, un-canonicalized external tags (Hardcover
slug+UUID strings, Google Books BISAC paths, combos like "science fiction fantasy", non-genres like
"audiobook"). Tropes/styles are clean; genres/moods are not. Fix go-forward + correct the existing DB.

---

## 1. Background & Problem

`Work.genres` and `Work.moods` are `ARRAY(String)` columns populated verbatim from external scouts:

- **Hardcover** genre/mood tagSlugs — and in practice these arrive with a UUID appended, e.g.
  `science-fiction-fantasy-4c14c349-8d52-4893-aaf0-34f7e33bf275`,
  `audiobook-28d9b978-31e9-43d5-a61a-499c03237945`, `epic-cb4665c5-…`, `action-adventure-885c1c28-…`.
- **Google Books** `categories` — hierarchical BISAC paths, e.g. `Fiction / Science Fiction / General`,
  and ampersand forms like `Business & Economics`.

`ScoutManager` merges these into a set (exact-dedup only) and `persist_enriched_work` writes them
verbatim (`persist.py:140-141, 176-177`). There is **no normalization, no canonicalization, no junk
rejection**. Result: combos (`science fiction fantasy`), garbage (UUID-tailed slugs, digits),
non-genres (`audiobook`), case/spelling variants (`fiction` vs `Fiction`; `business-economics` vs
`Business & Economics`; a host of sci-fi variants), and over-broad umbrellas (`Fiction`, `General`) all
coexist. These surface raw in the UI/analysis (`analysis.py:74-75` counts `work.genres` directly).

**Why tropes/styles are NOT affected (verified):** there is no title-casing step anywhere in the code.
Tropes/styles are clean because they are **LLM-sourced** (consistent casing) **and** run through
`standardize_trope` / `standardize_style` (exact + semantic dedup). Genres/moods get none of that. So
the fix is scoped to **genres + moods**; tropes/styles are untouched.

**Note on coupling:** when a work has no enriched tropes, genres+moods are reused as *fallback tropes*
(`persist.py:286`). Cleaning genres/moods at persist therefore also improves those fallback tropes —
a free side benefit, no extra work.

## 2. Goals

- **QC / cleanup, not taxonomy.** Keep the existing emergent vocabulary; just clean it. No controlled
  allow-list, no new tables, no embeddings.
- **Go-forward:** every new enrichment write stores cleaned genres/moods.
- **Backfill:** correct the existing values already in the **live production** database.
- Concretely, per the operator's examples:
  - `science-fiction-fantasy-<uuid>` → `["Science Fiction", "Fantasy"]`
  - `audiobook-<uuid>` → dropped (not a genre)
  - `epic-<uuid>` → `["Epic"]`
  - `action-adventure-<uuid>` → `["Action & Adventure"]` (BISAC keeps it as one genre, not split)
  - `fiction` / `Fiction` → collapse to `Fiction`, and **drop if other genres are present**
  - `business-economics` + `Business & Economics` → `["Business & Economics"]`
  - sci-fi variants → `["Science Fiction"]`; `general` → dropped

## 3. Non-Goals (YAGNI)

- **No controlled-vocabulary allow-list** — unknown-but-valid tags pass through (cleaned), not rejected.
- **No genre hierarchy/taxonomy** — the only hierarchy concession is dropping over-broad `Fiction` when
  more specific genres exist.
- **No new tables / embeddings / semantic dedup** for genres/moods (that's the trope/style mechanism;
  we deliberately don't mirror it here — it wouldn't split combos and is overkill for QC).
- **No re-enrichment** — the raw values are already in the columns; backfill re-cleans in place.
- **No general greedy combo-splitter** — combos are handled by an explicit, curated `COMBO_MAP`
  (predictable, no surprises).
- **No changes to tropes/styles.**

## 4. Decisions (from brainstorm)

- **D1 — Mechanism:** a deterministic **cleaning pipeline** over the existing `ARRAY(String)` columns,
  parametrised by small curated lookup maps. Genres and moods share the pipeline with different configs.
- **D2 — Scope:** genres + moods only.
- **D3 — Combos:** explicit `COMBO_MAP` for true multi-genre slugs (`science-fiction-fantasy` →
  `[Science Fiction, Fantasy]`). Where BISAC keeps terms together as one genre, alias to the single
  BISAC form instead of splitting (`action-adventure` → `Action & Adventure`, via `ALIAS_MAP`).
- **D4 — Over-broad terms:** `General` → always drop; `Fiction` → drop **only if** other genres remain
  (sole-genre `Fiction` is kept); `Nonfiction` → kept as-is.
- **D5 — Maps seeded from data:** the alias/combo/deny maps are curated from a one-time **inventory** of
  the live DB's distinct values, not invented — "use what we already have as the baseline."
- **D6 — Graceful degradation:** the cleaner works before maps are complete (unknown tags get
  UUID-strip + Title-Case + junk-reject); maps grow from the inventory.
- **D7 — Backfill targets LIVE prod**, never a backup/snapshot; guarded by an explicit connection check.
- **D8 — Genre names ↔ design-work icons (relaxed):** `GenreIcon` resolves via fuzzy
  `canonicalizeGenre()` (token-contains, case-insensitive) — NOT exact strings — so canonical names need
  only *contain* the recognizable genre token (`Action & Adventure` → Adventure icon). The 13 icon
  genres are listed in Rollout notes; anything else degrades to a fallback star. (Confirmed with
  design-work, 2026-06-22.)
- **D9 — BISAC is the formatting authority:** canonical genre names follow BISAC spelling/format where a
  BISAC equivalent exists (`Business & Economics`, `Action & Adventure`); when unsure how to represent a
  genre, use BISAC for the canonical form.

## 5. Architecture & Placement

Two new pure modules + one persist hook + one operator script.

- **`src/agentic_librarian/etl/tag_cleaning.py`** — pure functions, no I/O:
  `clean_genres(raw: list[str]) -> list[str]` and `clean_moods(raw: list[str]) -> list[str]`.
- **`src/agentic_librarian/etl/tag_maps.py`** — the curated lookup tables (plain Python dicts/sets, so
  they version-control and diff cleanly): `ALIAS_MAP`, `COMBO_MAP`, `DENYLIST`, `CONDITIONAL_DROP`,
  plus the parallel mood maps. The right-hand sides of the maps constitute the **canonical set**.
- **`etl/persist.py`** — the single chokepoint. Replace lines 140-141:
  ```python
  genres = clean_genres(_nan_to_list(row.get("genres")))
  moods  = clean_moods(_nan_to_list(row.get("moods")))
  ```
  Every write path (fast `/books`, deep Cloud-Task enrich, ETL, and the backfill) funnels through
  `persist_enriched_work`, so this one change covers all go-forward writes.
- **`scripts/clean_tags.py`** — the operator backfill (inventory / dry-run / apply).

No schema change. `Work.genres`/`Work.moods` stay `ARRAY(String)`.

## 6. The Cleaning Pipeline

`clean_genres(raw_list)` — per tag, then a list-level pass:

1. **Strip trailing UUID/hex** — regex off a trailing `-<uuid>` (8-4-4-4-12 hex) and stray hex tails.
   `science-fiction-fantasy-4c14c3…` → `science-fiction-fantasy`.
2. **Normalize form** — hyphens/underscores → spaces; collapse whitespace; for BISAC paths
   (`A / B / C`) take the most specific meaningful segment (`Fiction / Science Fiction / General` →
   `Science Fiction`); lowercase for lookup.
3. **Alias lookup** (`ALIAS_MAP`) — snap known variants to one canonical (BISAC-formatted) spelling:
   `fiction`→`Fiction`, `sci-fi`/`scifi`/`science-fiction`→`Science Fiction`,
   `business-economics`→`Business & Economics`, `action-adventure`→`Action & Adventure`, `epic`→`Epic`.
4. **Combo split** (`COMBO_MAP`) — only true multi-genre slugs: `science-fiction-fantasy` →
   `[Science Fiction, Fantasy]`. (Each split member is itself canonical. `action-adventure` is NOT split
   — BISAC treats it as one genre, so it's an `ALIAS_MAP` entry above.)
5. **Reject junk** — drop the tag if: on `DENYLIST` (`audiobook`, `ebook`, `paperback`,
   `kindle edition`, format words, `general`, `books`…); contains digits/leftover hex after step 1;
   empty or a single stray char.
6. **Title-Case fallback** — unknown-but-valid tags (not in any map, passed QC) are kept, Title-Cased.
7. **List-level pass** — dedup (case-insensitive, order-preserving); then apply `CONDITIONAL_DROP`:
   remove `Fiction` iff the list has ≥1 other genre.

`clean_moods(raw_list)` — same pipeline, mood config: **more permissive** (there is no allow-list — keep
the tag unless it's clear junk), the same UUID/junk stripping and alias collapsing, **no combo-splitting
and no `CONDITIONAL_DROP`** (moods don't have the umbrella problem). QC, not collapsing.

## 7. Curated Maps (seeded from a DB inventory)

The maps in `tag_maps.py` are built from reality, in three steps:

1. **Inventory** (`clean_tags.py --inventory`) dumps every distinct `genres`/`moods` value + frequency
   from the live DB.
2. **Curate** — from that inventory we (operator + author) fill `ALIAS_MAP` / `COMBO_MAP` / `DENYLIST` /
   `CONDITIONAL_DROP`. Seeded with the known examples; expanded to cover the actual distinct values.
   Canonical (RHS) spellings follow **BISAC** where one exists (D9) — it is the formatting authority.
3. **Review** — operator eyeballs the maps and the dry-run diff before applying.

Map shapes:
```python
ALIAS_MAP: dict[str, str]          # normalized variant  -> canonical spelling
COMBO_MAP: dict[str, list[str]]    # normalized combo slug -> [canonical, canonical]
DENYLIST: set[str]                 # normalized tags to drop always
CONDITIONAL_DROP: set[str]         # canonical tags dropped iff other genres remain  ({"Fiction"})
# parallel: MOOD_ALIAS_MAP, MOOD_DENYLIST
```
Because lookups are by normalized form, the cleaner stays graceful: a tag absent from every map still
gets UUID-strip + Title-Case + junk-reject.

## 8. Backfill — Correcting the Live Database

One script, `scripts/clean_tags.py`, run via the app container against **live prod** (same wrapper as
the Alembic migrations — Cloud SQL Auth Proxy + the `librarian-db-url` secret; see
`docs/runbooks/bulk-import-rollout.md` §3). Modes:

- **`--inventory`** — read-only; prints distinct genres/moods + frequencies. (Step 0 for map curation.)
- **`--dry-run`** — read-only; for every `Work`, computes cleaned arrays and prints (a) a per-distinct
  value `raw → result` table, (b) a sample of per-work before→after, (c) a summary (counts of
  split/collapsed/dropped/unchanged). **No writes.**
- **`--apply`** — writes cleaned `genres`/`moods` back per `Work`. Idempotent (cleaned values clean to
  themselves), so re-running is safe.

**Live-DB safeguard (D7):** before `--apply` (and reported in `--dry-run`), the script prints the
connection target and a recency sanity check, and **refuses to run** unless it confirms it is the live
prod DB — concretely: print the host from the resolved `DATABASE_URL`, assert it is **not** a SQLite
file / `data/backups/*` path / `localhost` dev DB, and echo a recency probe (e.g. `COUNT(works)` and
the max `reading_history.date_completed`) for the operator to confirm against known-current values
before typing the confirmation. This prevents cleaning a stale snapshot.

## 9. Testing

The pure functions carry the weight (`test/unit/test_tag_cleaning.py`), tested against the operator's
real strings:

| input | expected |
|---|---|
| `["science-fiction-fantasy-<uuid>"]` | `["Science Fiction", "Fantasy"]` |
| `["audiobook-<uuid>"]` | `[]` |
| `["epic-<uuid>"]` | `["Epic"]` |
| `["action-adventure-<uuid>"]` | `["Action & Adventure"]` |
| `["fiction", "Fiction", "Fantasy"]` | `["Fantasy"]` |
| `["fiction"]` | `["Fiction"]` |
| `["business-economics", "Business & Economics"]` | `["Business & Economics"]` |
| `["sci-fi", "scifi", "Science-Fiction"]` | `["Science Fiction"]` |
| `["general-<uuid>", "Fiction / Science Fiction / General"]` | `["Science Fiction"]` |

Plus: **idempotency** (`clean(clean(x)) == clean(x)`); parallel **mood** tests (permissive QC — junk
stripping + alias collapse, no combo-split, no conditional drop); a `db_integration` test that persisting a messy work
stores clean arrays (proves the `persist.py` hook); and a backfill test (dry-run produces the right diff
and writes nothing; `--apply` writes cleaned values; second `--apply` is a no-op).

## 10. Rollout / Ops Notes

- **Sequence:** ship the cleaner + persist hook + seed maps (go-forward correctness) → operator runs
  `--inventory` on prod → expand the maps from the inventory (review) → `--dry-run` (eyeball) →
  `--apply` against **live** prod.
- **Cross-session (D8, resolved):** design-work's `GenreIcon` resolves genres via fuzzy
  `canonicalizeGenre()` (regex token-contains, case-insensitive) in `frontend/src/components/genreUtils.ts`
  — so the cleaning maps do **not** need exact-string parity with the icons; a cleaned canonical name just
  needs to *contain* the recognizable token. The **13 icon genres**: Fantasy, Science Fiction, Adventure,
  Mystery, Romance, Horror, Thriller, Literary, Historical, Young Adult, LGBTQ, War, Dystopian — anything
  else → a graceful fallback star. BISAC formatting (D9) is compatible (e.g. `Action & Adventure` →
  Adventure icon). Coordinated on the board, 2026-06-22.
- No migration, no env changes, no deploy gating — the persist-hook change rides a normal deploy; the
  backfill is a one-off operator script.
