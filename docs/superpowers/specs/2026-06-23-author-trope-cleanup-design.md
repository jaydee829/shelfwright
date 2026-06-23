# Author Dedup & Trope Cleanup — Design

**Date:** 2026-06-23
**Status:** Draft for review
**Scope:** Backend-only data QC + persist guards. The **safe, non-destructive** subset of the
larger work-representation problem (see memory `work-representation-embedding-gap`). Deleting the
genre-as-trope fallback rows and refactoring how genres/moods represent a work are **explicitly out
of scope** — deferred to a separate spec.

## Problem

Two prod data bugs surfaced from History:

1. **Repeat authors on a work** (e.g. *Beware of Chicken* shows the author twice). `WorkContributor`
   PK is `(work_id, author_id, role)`. Dupes render when the same person is either (a) two `Author`
   rows whose names differ only by case/whitespace (`persist.py:111` matches `Author.name` exactly,
   no normalization), or (b) one author attached under two roles. The create path (`persist.py:133`)
   builds one `WorkContributor` per raw contributor with **no dedup**.

2. **Dirty trope names in History** — e.g. `science-fiction-fantasy-4c14c349-…`. These are `Trope`
   rows (a separate normalized table the genre/mood backfill never touched). `standardize_trope`
   stores the raw string verbatim, and `persist.py`'s fallback path dumps genre/mood slugs in as
   pseudo-tropes when a work has no enriched tropes. History surfaces `wt.trope.name` directly
   (`main.py:105`, `main.py:254`, `analysis.py:79`).

**Why not just delete the slug tropes:** they are currently the *only* path genre/mood signal reaches
the matching space (works have no embedding; matching = trope+style vectors only). Deleting them
strips signal. So we **clean** them (the same way we cleaned genres/moods) — preserving and
*improving* the signal — and defer deletion/refactor.

## Goals

- No duplicate authors on a work; a guard so it can't recur.
- Trope names cleaned with the **same pipeline as genres/moods**: UUID-strip → combo-split →
  canonicalize (alias) → drop junk → **title-case**. `work_tropes` links migrated, duplicates merged,
  changed names re-embedded. A guard so the fallback path can't write dirty tropes again.
- Operator workflow mirrors the genre backfill: read-only **inventory** → **`--dry-run`** → **`--apply --yes`**
  against live prod via the Cloud SQL proxy, with the `is_prod_url` safety guard and credentials never printed.

**Non-goals:** deleting genre-as-trope fallback rows; giving `Work` its own embedding; changing
candidate-search to use genres/moods. (Deferred — separate spec.)

---

## Part A — Author dedup + guard

### A1. Normalization
`_norm_author(name) = " ".join(name.split()).casefold()` — strip/collapse whitespace, case-insensitive.
Two authors are "the same" iff their `_norm_author` matches. (Conservative: only collapses true
case/whitespace variants — never merges distinct names like `J. Smith` vs `John Smith`.)

### A2. Data fix (`etl/author_dedup.py`, session-in/changes-out, mirrors `tag_backfill.py`)
- **Merge duplicate `Author` rows:** group all authors by `_norm_author`. For each group with >1 row,
  pick a **survivor** (the best-cased name: prefer one with mixed/Title case over all-lower; tie →
  lowest `id` for determinism). Re-point `work_contributors` and `author_styles` from the losers to
  the survivor, then delete the loser `Author` rows.
- **Preserve roles — collapse only TRUE duplicates.** Dedup `work_contributors` on the **full** PK
  `(work_id, author_id, role)`, never on `(work_id, author_id)` alone. So "Casualfarmer" + "Casualfarmer "
  *both as `Author`* collapse to one row (true dup), but the same person as `Author` **and** `Editor`
  keeps **both** rows — a contributor's distinct roles are real data we must not lose.
- **FK-collision safety = the dedup mechanism:** when re-pointing a loser `WorkContributor`/`AuthorStyle`
  to the survivor would collide with an existing row on its PK, drop the loser link (the target already
  covers it); otherwise re-point. Because the `WorkContributor` PK includes `role`, this automatically
  collapses same-role dupes while preserving different-role contributions.
- `plan_author_changes(session)` returns a preview (per work: author names before → after).
  `apply_author_changes(session, changes=None)` performs it. `author_inventory(session)` lists
  `(name, work_count)` and flags the duplicate groups.

### A3. Guard (`persist.py`)
Before building `work_contributors_list`, dedup `raw_contributors` by `(_norm_author(name), role)`
(first occurrence wins). Net effect: one `WorkContributor` per distinct **author+role** per work —
true dupes can't be written, but a contributor's legitimate multiple roles are preserved.

### A4. Display (no change needed)
History (`main.py:248-250`) and the rec card (`main.py:99`) already surface only `c.role == "Author"`
contributors, so editors/other roles never appear as authors. The role-preserving merge above keeps
that working: a person who is both author and editor of a work shows once (as author), with the editor
role retained in the data for future use.

---

## Part B — Trope cleaning + guard

### B1. Cleaning function (`etl/tag_cleaning.py`)
Add `clean_trope_name(name) -> list[str]` reusing the existing `_clean_one` machinery with the
**union** of genre + mood maps:
- `combo` = `COMBO_MAP` (enables `science-fiction-fantasy → [Science Fiction, Fantasy]`, the
  `fiction-` umbrella split, etc.).
- `alias` = `ALIAS_MAP | MOOD_ALIAS_MAP` (a slug that's really a mood, e.g. `fast-paced`, canonicalizes too).
- `denylist` = `DENYLIST | MOOD_DENYLIST` (drops `audiobook`, numeric junk, entity noise, etc.).
- Genuine narrative tropes (`enemies-to-lovers`, `chosen-one`) have no combo/alias/denylist hit →
  they just get UUID-stripped + title-cased (e.g. `Enemies To Lovers`). No bad splits.
- Returns 0..N names (0 = pure junk dropped; N>1 = a combo slug split). De-duped, order-preserving.

### B2. Migration (`etl/trope_backfill.py`) — the normalized-table crux
For each `Trope` row `T` (name `N`), compute `cleaned = clean_trope_name(N)`:

- **Unchanged** (`cleaned == [N]`): no-op.
- **Cosmetic rename** (single result, differs only by case/whitespace — no UUID stripped, no digits,
  no split): rename `T.name` in place, **keep the existing embedding** (avoid needless re-embed). If the
  new name collides with another `Trope`, **merge** (below) instead.
- **Material change** (UUID/digits stripped, alias-canonicalized, or split into ≥1 different names):
  for each canonical name `Ci`:
  - Find-or-create a `Trope` named `Ci`. New rows are **re-embedded** on the clean name
    (`TropeManager.standardize_trope` / `_get_embedding`). Existing target rows keep their embedding.
  - For every `work_tropes(work_id=W, trope_id=T)`: ensure `work_tropes(W, Ci)` exists — if it already
    does, keep the **max** `relevance_score` and first non-null `justification`; else create it.
  - After all of `T`'s links are migrated, the now-orphaned `T` (a *dirty* name, signal moved to `Ci`)
    is deleted. This is cleanup, **not** signal loss.
- **Merge helper:** moving links from a loser trope to a survivor must respect the `work_tropes` PK
  `(work_id, trope_id)` — on collision, fold scores/justification and drop the loser link.

`plan_trope_changes(session)` previews `(name_before → names_after, #works_affected, will_reembed)`.
`apply_trope_changes(session, changes=None)` performs it. `trope_inventory(session)` is a `Counter`
of trope names with work counts, flagging dirty ones (UUID/digit/combo/denylist hits).

**Embedding cost:** the dry-run/inventory reports how many **distinct new names** need embedding (=
embedding API calls) so the operator can gauge quota before `--apply`. Cosmetic renames cost zero.
`_safe_standardize`-style degrade-on-failure: an embedding error skips that one name, never aborts.

### B3. Guard (`persist.py`)
In the fallback-trope path (`persist.py:~314`), run each tag through `clean_trope_name` before
`standardize_trope`, so the fallback can never write a UUID-tailed / unsplit slug again. (Enriched
tropes from the LLM are already clean human names; optionally pass them through `clean_trope_name`
too for title-case consistency — low risk, decide in plan.)

---

## Shared — operator CLI

Extend the proven pattern (`scripts/clean_tags.py`). One script `scripts/clean_catalog.py` with:
- `--inventory` — print author dup-groups + dirty-trope counts + embedding-call estimate. Read-only.
- `--authors --dry-run` / `--tropes --dry-run` — preview changes, no writes.
- `--authors --apply --yes` / `--tropes --apply --yes` — write. Refuses without `--yes`, and refuses
  unless `is_prod_url(url)` (reuse from `tag_backfill`) — sqlite/backups/localhost are blocked.
- Always prints the DB target with credentials stripped (`url.split("@")[-1]`) + a recency probe
  (row counts) so the operator confirms it's live prod, not a backup.
- Re-runnable / convergent: `clean(clean(x)) == clean(x)`; a re-applied run is a near-no-op.

## Testing

- **Unit** (`test/unit/`): `_norm_author` cases; `clean_trope_name` (UUID strip, combo split,
  mood-alias, denylist drop, genuine-trope title-case, idempotency); author merge survivor selection;
  trope split/merge/collision logic against an in-memory model graph where practical.
- **Integration** (`db_integration`, runs on CI Postgres): seed dup authors + dirty tropes →
  `apply_*` → assert one author per work, clean trope names, `work_tropes` links preserved/merged, and
  a second apply is a no-op (idempotent).
- `uvx ruff@0.15.16 format` before every commit (CI pre-commit gate).

## Rollout

1. Merge the PR (deploys both persist guards — future writes stay clean).
2. Operator: proxy up → `--inventory` (sanity + cost) → `--authors --dry-run` → `--authors --apply --yes`
   → `--tropes --dry-run` (review the embedding-call count) → `--tropes --apply --yes`, all against
   live prod via the Cloud SQL proxy. Both convergent, so a retry after a transient blip is safe.
