# Fallback-Trope Prune & Two-Phase Persist Fix — Design

**Date:** 2026-06-23
**Status:** Draft for review
**Issue:** #65 — two-phase import leaves a genre/mood-as-trope fallback layer on imported works.
**Scope:** Backend-only. A persist logic fix + a one-time data prune. Sibling of the author/trope
cleanup (PR #63/#64).

## Problem

Books added via the bulk-import / two-phase enrichment path accumulate a large layer of genre/mood
"fallback" tropes that the original single-pass catalog never produced. Prod audit
(`scripts/trope_audit.py`): the most recent import averages **7.58 fallback tropes/work** vs **1.01**
for the rest of the catalog, on top of a healthy ~7 real tropes/work. The deep enrichment works
correctly — this is an *excess of redundant fallback rows*, not thin enrichment.

## Root cause

Tropes persist as `if enriched_tropes: <real> else: <write genres|moods as fallback tropes>`
(`persist.py:310` / `:333`) — one persist writes **either** real **or** fallback tropes.

- **Two-phase import** calls persist twice: the **fast pass** (no trope scout) hits the `else` and
  writes a genre/mood fallback layer; the **deep pass** then takes the `if` branch and *adds* real
  scout tropes **without removing** the fallbacks. Imported works carry **both** layers.
- The **original 330-book catalog** was built **single-pass** (full scout manager in one persist), so
  works with real tropes took the `if` branch and the `else` never fired → ~0 fallback/work.

## Key principle (why this is safe, not the deferred refactor)

In the single-pass catalog, a work got **either** real tropes **or** fallbacks — never both. So
**real-trope works have never had a genre/mood-as-trope layer**, and their matching has always run on
real tropes + styles with zero genre/mood signal. The two-phase bug bolted an *extra* layer onto
works that already have real tropes — a layer their single-pass siblings lack.

Therefore: **prune the fallback layer only on works that have ≥1 real trope.** This restores parity
with the rest of the catalog and removes no signal that comparable works ever had. The
work-representation gap (genres/moods reach matching *only* via fallbacks — see memory
`work-representation-embedding-gap`) still applies, but **only to works with no real tropes**, which
keep their fallbacks as the stopgap. This fix is **not** the deferred refactor.

## Goals / Non-goals

**Goals:**
- Imported works with real tropes end up with **only** real tropes (parity with single-pass catalog).
- The two-phase persist path can never re-accumulate the double layer, regardless of pass order.
- A previewable, prod-safe one-time prune of the existing pollution.

**Non-goals:**
- The work-representation refactor (deferred). Works with **no** real tropes keep their fallbacks.
- Deleting orphaned `Trope` rows (a fallback Trope still linked to fallback-only works stays; a
  fully-orphaned one is harmless dead weight — left for the `--tropes` pass / a later sweep).

## Distinguisher

`work_tropes.justification`: scout (real) tropes set it (`persist.py:327`); genre/mood fallbacks
leave it `NULL` (`persist.py:347`, no justification arg). The `trope_audit.py` rollup validates this
holds in prod (real tropes consistently carry justification). **Safety net:** the data prune is
dry-run-previewable per work, so the operator eyeballs the exact trope names being deleted before
applying — a genuine trope that somehow lacked a justification would be visible in the preview.

---

## Part A — Persist logic fix (`persist.py`, both directions)

Make the two-phase passes converge to "real tropes win, fallbacks are the stopgap":

- **Real branch (`if enriched_tropes:`):** *before* adding the scout tropes, delete the work's
  existing fallback links — `WorkTrope` rows for this work with `justification IS NULL`. (Delete
  first, then add, so a scout trope that itself happens to lack a justification — added in this same
  pass — survives.) Net: the deep pass clears the fast pass's fallback layer.
- **Fallback branch (`else:`):** only write fallbacks if the work has **no** real trope yet — i.e.
  skip the loop when a `WorkTrope` with `justification IS NOT NULL` already exists for this work. Net:
  a fast pass can't bolt a fallback layer onto a work that already has real tropes (covers the
  deep-then-fast order too).

This keeps the stopgap intact: a work that only ever gets a fast pass (no real tropes) still receives
its genre/mood fallback layer.

## Part B — One-time data prune

New backfill logic (mirrors `etl/tag_backfill.py` / `etl/trope_backfill.py`; session-in, summary-out):

- **Criterion:** delete `work_tropes` rows where `justification IS NULL` **and** the same `work_id`
  has ≥1 `work_tropes` row with `justification IS NOT NULL`. Pure link deletion — no `Trope` rows, no
  embeddings touched.
- `plan_fallback_prune(session)` → preview: per polluted work, `(title, [fallback trope names being
  deleted], #real_kept)`. Read-only. `apply_fallback_prune(session, changes=None)` → deletes the
  links individually (`session.delete`, keeping the identity map consistent), returns the count.
- Idempotent/convergent: after a run, no work has both a real and a NULL trope → a second run is a no-op.
- **CLI:** add a `--prune-fallbacks` mode to `scripts/clean_catalog.py` — `--inventory` reports the
  count of polluted works + total fallback links; `--prune-fallbacks --dry-run` previews; `--apply
  --yes` writes. Reuses the `is_prod_url` guard, `--yes` gate, and credential-stripped target print.

## Sequencing (operator)

Run the prune **before** `clean_catalog --tropes`:

1. (deploy this PR — the persist fix stops future imports re-polluting)
2. `clean_catalog.py --prune-fallbacks --dry-run` → `--prune-fallbacks --apply --yes`
3. *then* `clean_catalog.py --tropes --dry-run` → `--tropes --apply --yes` (the trope-name cleaning,
   now operating on a much smaller, un-muddied set)

Rationale: the `--tropes` name-cleaning migrates/merges links (`_move_links` folds NULL justification
into a real row on a name collision) — running it first would corrupt the `justification IS NULL`
distinguisher and waste embedding calls on fallback rows about to be deleted.

## Testing

- **db_integration** (`test/integration/`): seed a work with real (justified) + fallback (NULL) tropes
  → `apply_fallback_prune` deletes only the NULL ones, keeps the real; a work with **only** fallbacks
  is untouched; a second apply is a no-op. Persist: a deep pass after a fast pass leaves only real
  tropes; a fast pass on a work that already has real tropes adds no fallback layer; a fast-pass-only
  work keeps its fallbacks.
- **Unit** where pure logic is extractable (e.g. the polluted-work selection).
- `uvx ruff@0.15.16 format` before each commit (CI gate).

## Rollout

After merge + deploy: proxy up → `--prune-fallbacks` (dry-run → apply --yes) → then the deferred
`--tropes` cleaning. Both convergent / retry-safe.
