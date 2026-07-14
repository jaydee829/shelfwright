# Recommendation Quality — bad recs (#125) + trope pollution (#70) (Design)

**Date:** 2026-07-14 · **Issues:** #125 #70 · **Spec status:** self-approved per ground
rules (user authorized design 2026-07-14: "start designing and speccing the fixes, only
come to me with big questions").

## Goal

A user who says "less fantasy, more thriller or romantasy" must get recommendations that
track that request. Today they get the admin's sci-fi back. Fix the three layers that
produce this: retrieval that ignores relevance and feedback (PR-A), a prompt that mandates
recs and never seeks feedback (PR-B), a taste profile computed from polluted frequency
(PR-C), and the pollution source itself plus data repair (PR-D).

## Verified findings (prod investigation 2026-07-14, user-approved read-only queries)

The full #125 session was reconstructed from `suggestions` + `messages` for the affected
user (125 history rows, all trope-tagged; thriller/romantasy reader — Katee Robert, Lisa
Unger, Alice Feeney, Ruth Ware, Riley Sager, Lucy Foley).

1. **No admin hardcoding.** Identity is fail-closed (`user_context.py`), `DEFAULT_USER_ID`
   reaches only CLI + Dagster. All per-user tools filter correctly. The "admin taste"
   effect is catalog composition + the defects below.
2. **The taste profile is an attractor echo.** Every rec round's justification cites
   "The Dark Night of the Soul, Found Family, and Mysterious Origins" — even her
   climate-fiction request got funneled through them. `get_user_trope_preferences` ranks
   by raw frequency; her computed top-5 are all attractors; her real signal (Crime
   Thrillers, Warrior Romance, Unreliable Narrator, Gothic) sits at ranks 8–18.
3. **#70's dominant mechanism is FALLBACK-TAG pollution, not scout-trope collapse.**
   The genre/mood fallback writer (`persist.py:418-430`) pushes mood tags through
   `standardize_trope`, whose 0.85 semantic match lands them on real dramatic tropes:
   `Dark`→The Dark Night of the Soul, `Reflective`→Mirror/Shadow Self,
   `Romance`→Warrior Romance, `Challenging`→Myth-Busting Quest. Evidence: on her works,
   84 have mood `Dark`, 76 have Dark Night links, 74 are the NULL-justification overlap.
   Catalog-wide NULL-justification share of top attractors: Dark Night 91/105,
   Fantasy Kitchen Sink 76/77, Mysterious Origins 79/88, Pioneer/Frontier 66/75,
   Myth-Busting 66/88. **Found Family is the exception: 239/239 justified (real scout
   output — a genuine preference signal).** Raising the 0.85 threshold is NOT the fix and
   re-running enrichment is NOT required for repair (see PR-D).
4. **Specimen:** *Lessons in Chemistry* has ZERO real scout tropes — its whole
   "fingerprint" is semantically-warped mood/genre fallbacks (incl. junk trope
   `Comics Graphic Novels`), yet it is stamped `deep_enriched_at` because the 6.3
   migration backfill stamped any work with ≥1 trope row. "Stamped but fallback-only" is
   exactly the #97 requeue sweep's detection class.
5. **Candidate pools are selected arbitrarily.** `search_internal_database`
   (`mcp/server.py:122-124`, styles 137-151) joins works to the top-5 nearest tropes with
   `.limit(n)` and NO ordering — pool membership is arbitrary DB order among all works
   sharing those tropes; ranking happens only within the arbitrary pool. Attractor
   targets (works sharing Dark Night: 71 Fantasy, 40 SF, 36 Thriller…) therefore retrieve
   essentially anything — hence *We Are Legion* for a thriller/romantasy request.
6. **Feedback is structurally discarded.** No negative-target path exists (Analyst
   captures `session_constraints`, tools accept only positive targets).
   `get_unacted_suggestions` re-injects prior suggestions into every fresh candidate set
   (unread-first ordering floats them up) — Starsight was re-presented after the user
   deflected; the duplicate `log_suggestion` was absorbed by `uq_suggestions_active`.
7. **The retrievable thriller supply is thin.** 201/800 works have zero tropes (196 never
   deep-enriched — largely wishlist/to-read imports, incl. 4 of 8 Ruth Ware titles);
   zero-trope works are invisible to trope retrieval. Her read thrillers are correctly
   excluded (<2y re-read rule). User decision 2026-07-14: **enrich wishlist works** to
   build the catalog and reduce in-conversation Explorer dependence.
8. **Bonus:** *We Are Legion* exists as TWO work rows (`14c8f3b5…`, `7fc21c9e…` —
   punctuation variant defeated title dedup), each 1 edition, different trope sets. Added
   to the dup-works triage list (report-only class; works-merge tooling still doesn't
   exist).

## Delivery shape: four PRs

Sequenced so each ships value alone; A+B+C fix the live experience, D fixes the data.

### PR-A `fix/rec-retrieval-correctness` — retrieval ranking, negative targets, no re-suggest (#125)

1. **Rank into the pool, not after it.** Rewrite the candidate selection in
   `search_internal_database`: works are scored by
   `min(trope.embedding <-> query_vec) * f(relevance_score)` across their trope links
   (and analogously for styles) and ORDER BY that score BEFORE `LIMIT`. Keep a top-K
   trope prefilter for index use but widen it (top-5 tropes is a second collapse point);
   ranking must happen at every stage that limits. pgvector SQL is pg-only: compile-assert
   locally (`postgresql.dialect()`), execute in `db_integration` (CLAUDE.md rule 4).
2. **Negative targets.** Add `exclude_tropes: list[str]` / `exclude_styles: list[str]`
   to `search_internal_database`, `get_recommendation_candidates`, `curate_candidates`.
   Embed each exclusion; a candidate whose best trope/style match to any negative vector
   is closer than its best positive match is dropped; near-misses are demoted below all
   clean candidates. Thread the Analyst's `session_constraints` into these params in both
   backends (ADK + Claude; `candidates.py` is the shared seam).
3. **No re-suggesting.** Fresh candidate sets EXCLUDE works with an active `Suggested`
   suggestion for the current user: `curate_candidates` stops unioning
   `get_unacted_suggestions`, and the catalog search filters them out.
   `get_unacted_suggestions` remains available as an explicit tool ("what did you already
   suggest me?"), no longer an implicit candidate source.
4. Tests: parametrized unit tests on the pure seams (curate_candidates with faked rows);
   compiled-SQL assertions for the ranked pool query; db_integration tests that execute
   ranked retrieval + exclusion + no-resuggest against real pgvector.

### PR-B `feat/librarian-conversational-charter` — prompt overhaul (#125)

Rewrite `LIBRARIAN_INSTRUCTION` (and the ADK orchestrator instruction inline in
`services.py` — the prompts.py docstring's parity rule) around the user's expanded
charter (2026-07-14):

- **Overarching goal:** find recommendations the user will like that match the soft
  preferences expressed across the conversation — not "produce 3 recs per turn."
- Recommendations are conditional: conversational turns (feedback, chat, questions) get
  conversational replies; a fresh rec set only when the user is asking for one.
- Clarifying questions are encouraged (drop "at most one" phrasing; keep them purposeful).
- **After recommending, seek feedback; ACT on that feedback** in all subsequent recs
  (feed deflections into exclude_* targets; never re-suggest deflected titles — pairs
  with PR-A's structural guarantee).
- The Librarian is authorized to run **multiple rounds** with analyst/critic/explorer
  until the set makes sense in the broader conversation context (bounded by judgment,
  not a fixed pipeline).
- `CRITIC_INSTRUCTION` gains the exclusion contract (apply negative targets as hard
  filters, not soft penalties).

Honest verification note: prompt changes are validated structurally in tests (the
instructions mention the new tool params; parity between backends) plus an operator chat
smoke on prod — LLM behavior itself is not unit-testable (rule 10).

### PR-C `fix/preference-signal-quality` — `get_user_trope_preferences` (#125/#70 bridge)

Two changes, both non-destructive (aggregation-only — no data touched):

1. **Aggregate over justified links only** (`work_tropes.justification IS NOT NULL`).
   In current prod this removes the fallback pollution from the profile (the top
   attractors are 75–99% NULL-justification) while keeping real signals (Found Family
   239/239 justified). Known limitation, stated honestly: legacy scout links with NULL
   justification are under-counted until PR-D repairs the data; acceptable for a
   preference ranking (not deletion — the #69 lesson applies to destructive ops).
2. **Lift over raw frequency.** Score = smoothed
   `(user_links/user_works) / (catalog_links/catalog_works)`; return top-N by lift,
   tie-broken by raw count. This preserves the user's concern case — a genuine
   Found Family lover over-indexes vs the catalog baseline and keeps it — while
   deflating tropes that are merely ubiquitous. With few users the catalog baseline is
   admin-skewed; lift is still ordinal-correct for that exact reason (admin-common tropes
   get deflated for everyone else). Revisit baseline choice when user count grows.

### PR-D `fix/fallback-trope-pollution` — write path + gated repair + sweep (#70)

1. **Write path:** fallback genre/mood tags NEVER go through semantic standardization.
   New `get_or_create_fallback_trope` (exact cleaned-name match, create slug trope if
   missing) replaces `standardize_trope` in persist's fallback branch. Genre-as-trope
   rows keep existing (the work-representation constraint: they are how genres/moods
   reach matching) — they just can't land on real tropes anymore. `standardize_trope`
   itself (real scout tags) keeps 0.85; no threshold change (finding 3).
2. **Gated repair backfill** (destructive → dry-run report → USER approval → apply,
   clean_catalog plan/apply house pattern): for every NULL-justification link, recompute
   what the OLD fallback writer would have produced from the work's genres∪moods
   (clean → embed → nearest trope ≥0.85) — a deterministic, structural distinguisher
   (recompute-and-match, not a sometimes-populated column; #69 rule satisfied). Delete
   exactly the recomputed matches; leave legitimate exact-name slug fallbacks and all
   justified links untouched. Plan persists full id list; apply refuses on any drift.
   **This is why no paid re-enrichment is needed for repair** (user Q1 answered).
3. **`deep_enriched_at` honesty:** clear the stamp on works whose remaining tropes are
   all fallback-shaped after repair (the migration backfill's false positives, finding 4)
   so the #97 sweep sees them.
4. **Enrichment sweep (ops, user-approved policy):** after repair, run the #97
   `--requeue-unenriched` flow over the ~196 never-deep-enriched works + the newly
   unstamped ones. Paid bulk operation: present the count + cost estimate before firing
   (ties into Phase 6.4 #100 cost-guard decisions). Expected effect: wishlist thrillers
   become retrievable, reducing Explorer dependence in conversation.

## Sequencing & interactions

A → B → C are independent of D and ship the visible fix (the #125 session replayed
against A+B+C would have honored "less fantasy, more thriller or romantasy"). D makes the
data honest; after D, PR-C's justified-only filter becomes redundant but harmless (all
surviving links carry real provenance or are exact-name slugs excluded by lift anyway).
The sweep runs last so re-enriched works land on the fixed write path.

## Out of scope

- Works-merge tooling for the dup pair (triage list, with the 6.3 trio).
- Work-level representation refactor (work embeddings; memory
  `work-representation-embedding-gap`) — the durable fix for genre/mood matching, still
  deferred.
- Semantic over-collapse among REAL scout tropes (the original #70 framing): mild in
  current data (Found Family is legitimately common); reassess from the post-repair
  distribution before spending on it.
