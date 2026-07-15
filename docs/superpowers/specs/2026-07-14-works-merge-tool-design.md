# Works-Merge Tool + Duplicate-Work Prevention (Design)

**Date:** 2026-07-14 · **Issues:** #141 (unpinned deep pass) #142 (comma-joined authors) +
the dup triage backlog · **Spec status:** self-approved per ground rules (user directive
2026-07-14: "spec the works merge tool").

## Goal

One book = one Work row. Merge the known duplicate clusters (with live user history split
across copies), and close the two mechanisms that mint new duplicates so the merge isn't a
treadmill.

## Verified findings (prod forensics 2026-07-14)

1. **Known duplicate clusters:** *Beware of Chicken* pair `9e9cfc45` (slug-only, malformed
   author `'Casualfarmer, CasualFarmer'`, Justin's 2026-06-19 read) / `a5e56605` (15
   justified tropes, clean author, suggested 2026-06-12) — **same ISBN 9781039452275**;
   *We Are Legion* pair `14c8f3b5`/`7fc21c9e` (punctuation-variant titles); the 6.3
   report-only trio (*Calling Bullshit*, *Yellowface*, and what was then a Beware pair);
   *Wrath of Poseidon* duplicated contributor rows (related but a contributor-level fix).
   Note: *Beware of Chicken 2* is the sequel, NOT a dup — title-prefix matching must not
   collapse series entries.
2. **Why dups mint:** creation-time work matching is identity-based (normalized title +
   author); VARIANT identity defeats it — dirty author strings (#142's comma-joined
   artifact) and punctuation-variant titles. The 6.3 machinery (advisory lock,
   insert_or_requery, normalized-title re-check) prevents same-identity RACE dups only.
3. **Live bug (#141):** the deep-enrichment pass does not pin the invoked work id — its
   persist-side dedup re-check re-resolves by scout-canonical identity and can land
   tropes + the `deep_enriched_at` stamp on a DIFFERENT row (observed: sweep enqueued
   `9e9cfc45`, stamp+tropes landed on `a5e56605` at 21:03:05Z). The invoked row stays
   `never_deep_enriched` forever → requeue loop burning a paid pass per sweep.
4. **Reusable machinery already exists:** `etl/dedup_backfill.py` detects
   `duplicate_works` as a REPORT-ONLY class and owns the proven op-tagged-token gate
   (plan → persisted report → re-plan-fresh apply refusing on any drift, END-marker
   fail-closed parser). The 6.3 rollout also proved the cross-class composition hazard
   (narrator×edition, edition×reading-history cascades lose rows when composed from one
   snapshot) and the deferred-intersections loop discipline — a works merge triggers
   exactly those cascades and must own them internally.

## Delivery shape: two PRs + gated ops

### PR-1 `fix/enrich-pins-work-id` (#141 + #142 — prevention first, it's live)

1. **Pin the invoked work.** The deep pass persists tropes/styles/stamp against the work
   id it was invoked with, full stop. If the persist-side dedup re-check resolves the
   scout-canonical identity to a DIFFERENT existing work: do NOT write there; stamp the
   invoked row (the pass DID complete), and record the detection — insert into a small
   `detected_duplicates` side table (work_id_a, work_id_b, detected_at, source) or, if a
   table feels heavy, log at WARNING with a stable grep token AND surface it in the merge
   tool's plan as a high-confidence input. Recommended: the table — the merge tool then
   has a queryable feed, and the #97 sweep stops re-listing the row. (Migration: one
   table, nullable-free, no backfill.)
2. **Author-cell parsing (#142):** split/normalize comma-joined author fields at import
   parse time. Disambiguation rule: one comma + both tokens re-case-match a single
   plausible name ("Last, First") → single author; otherwise split on commas/`&`/" and ".
   When genuinely ambiguous, single-author wins (fail-safe: a wrong single author is
   mergeable; a wrongly-split author mints entities). Parametrized table of the real
   Goodreads/Libby author-cell shapes. Include a one-off repair query for EXISTING
   comma-joined author rows in the dup triage (report class in the merge tool, not
   auto-fixed).
3. Rehearsal rule 11 applies to the `detected_duplicates` migration: the merge tool (PR-2)
   must not entity-load any model this PR alters when run pre-migration.

### PR-2 `feat/works-merge` — the gated merge tool

Extend `etl/dedup_backfill.py` (same module, same gate, same CLI home in
`scripts/clean_catalog.py`) — do NOT build a parallel tool:

**Detection (plan classes), strongest-evidence first:**
1. `works_same_isbn`: two+ works sharing an `editions.isbn_13` (non-null). Highest
   confidence (catches the Beware pair).
2. `works_same_identity`: normalized(title) equal AND author-token-set overlap ≥ one
   full token (catches exact-title dups with dirty author variants). Normalization =
   the existing `_normalize` PLUS punctuation folding (`[;:()\[\]&.,!?'"-]` → space,
   whitespace collapse) so the *We Are Legion* pair matches. Series guard: works whose
   titles differ by a trailing number/volume token NEVER match (Beware of Chicken 2).
3. `works_detected_duplicates`: rows from #141's detection feed.
4. `works_fuzzy_report_only`: high-similarity title pairs (trigram or token-set ratio)
   — REPORT ONLY, never auto-applied; operator promotes pairs by hand if real.

**Survivor selection (deterministic, stated in the report):** most justified trope links,
then `deep_enriched_at` newest, then most editions, then lowest UUID (tie determinism).

**Merge composition (single transaction per cluster, internally ordered):**
1. Editions: repoint loser editions to survivor; on `uq_editions_work_format` collision,
   merge the edition pair — keep survivor's edition row, repoint the loser edition's
   `reading_history` and `edition_narrators` to it (reading_history collisions on
   `uq_reading_history_user_edition_date` = the same read event recorded twice → keep
   survivor's row, drop the loser's; COUNT and report as `dropped_duplicate_reads`).
2. Suggestions: repoint to survivor; on `uq_suggestions_active` collision keep the
   survivor's active row (drop loser's; count).
3. Trope/style links: union — insert loser links missing on survivor (carry
   justification/relevance), drop the rest.
4. Contributors: union by (author_id, role); the #142 malformed-author rows do NOT get
   copied when a clean equivalent exists on the survivor (report them).
5. Availability cache/user_libraries: keyed by title/author strings, not work id — no
   action (state this in the module docstring so nobody "fixes" it later).
6. Loser Work row deleted last; orphaned Authors reported via the existing
   `_plan_orphan_authors` loop discipline (re-run dry-run until clean, the 6.3 pattern).

**Gate:** identical to dedup/repair: op-tagged tokens per action (repoint/drop/link-copy
tagged with their op + ids), report under `data/reports/works-merge-<UTC>.txt` with the DB
target header, apply re-plans fresh and refuses on ANY addition or op-flip, single
transaction, per-class applied counts, convergence loop (steps 2→3 of the 6.3 runbook).
The deferred-intersections lesson applies WITHIN this tool: a cluster whose merge
composition would touch a reading_history row twice in one plan defers the second
composition to the next dry-run loop rather than composing from one snapshot.

**Tests (the 6.3 bar):** parametrized unit tables for detection (incl. the series guard,
the sequel non-match, punctuation folding) and survivor selection; e2e-shaped
db_integration round-trips: full merge of a Beware-shaped cluster (split read events →
one work, both reads preserved, no constraint violations), collision classes
(same-format editions, same-day duplicate reads, double active suggestions), drift-gate
refusal with zero mutations, convergence re-plan. Fixture test that DROPS
`detected_duplicates` to mirror pre-migration prod (rule 11) if PR-1's migration hasn't
been applied when the tool runs.

### Ops (after both PRs, the usual gate)

Deploy → dry-run → operator reviews the cluster report (expected: Beware pair,
We Are Legion pair, Calling Bullshit, Yellowface; fuzzy class report-only) → approval →
apply → convergence dry-run. Then one `--requeue-unenriched` check: `9e9cfc45` disappears
from the plan (merged away), closing the #141 requeue loop for good.

## Out of scope

- Wrath of Poseidon's duplicated CONTRIBUTOR rows (same author twice on one work) — a
  contributor-level dedup, add as a small report class if cheap, else separate.
- Retroactive global fuzzy dedup beyond the known clusters — the fuzzy class stays
  report-only until the operator has seen a few reports' precision.
