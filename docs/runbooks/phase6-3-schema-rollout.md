# Runbook — Phase 6.3 schema rollout (GH #95 #97 #108 #109)

**Spec:** [2026-07-12 Phase 6.3 — Data Integrity (Design)](../superpowers/specs/2026-07-12-phase6-3-data-integrity-design.md)
**Audience:** the operator. Every step explains *what you're doing, why, how, and what
"done" looks like*. Steps marked **[Claude]** are done in-session; steps marked **[You]**
need your accounts/browser/approval and can't be delegated.

## The big picture (read this first)

PR-C (`fix/phase6-3a-integrity-code`) already shipped and is live in prod — it stopped the
pollution sources (#96 contributor-merge, #98 garbage-title gate, #111 one real-trope
predicate, #112 reading-status correctness, #110 availability upsert, #123 embeds out of
sessions). PR-D (`feat/phase6-3b-schema-integrity`, this branch) adds the migration that
finally backs get-or-create with real unique constraints (#95), two systemic schema debts
(#108 timestamptz, #109 FK indexes), and the #97 enrichment-completion column
(`deep_enriched_at`).

The catch: years of races under the old unguarded get-or-create almost certainly left
**duplicate rows in prod** that the new unique constraints would reject outright — the
migration would fail to apply. So this rollout has **THE USER GATE** in the middle: a
dry-run dedup plan that you review and approve BEFORE anything is deleted or merged, per
the 2026-07-12 ground rule that prod backfills/migrations require a dry-run report +
user approval before applying. Sequence, at a glance:

```
PR-C already deployed (pollution stopped)
        |
        v
  [0] preconditions check
        |
        v
  [1] pg_dump snapshot                              [You/Claude]
        |
        v
  [2] dedup DRY-RUN --dedup-for-constraints           [Claude]
        |
        v
  [ You review the report file ]                      [You]
        |
        v
  [3] dedup --apply --yes --report <file>             [Claude]  (refuses on drift -> loop to [2])
        |
        v
  [4] alembic upgrade head (lands the #95 uniques)     [You]
        |
        v
  [5] merge PR-D -> deploy -> ADR-058 guard passes     [You]
        |
        v
  [6] post-checks + first #97 requeue report          [Claude]
```

---

## Step 0 — Preconditions **[You/Claude]**

**What:** confirm the ground under this rollout is solid before touching prod data.
**Why:** the dedup gate assumes PR-C's pollution sources are already closed off — running
the dry-run before PR-C is live would count duplicates that are still actively being
created, making the report stale before you finish reading it.
**How, check all three:**

1. PR #124 (PR-C) is deployed and serving — confirm the running revision is `00031-p29`
   or later (`gcloud run services describe librarian-api --region us-central1
   --format='value(status.latestReadyRevisionName)'`).
2. This branch's CI is green (migration `48e3762d6c0c` runs cleanly in CI's full-chain
   schema rebuild; `get_or_create`/dedup unit tests pass).
3. Pick a quiet-ish window. Nothing here takes the app offline, but the dedup apply step
   (step 3) merges/deletes rows — a lower-traffic window shrinks the odds of a drift-refuse
   loop in step 3 caused by concurrent writes.

**Done when:** all three confirmed.

## Step 1 — pg_dump snapshot **[You or Claude]**

**What:** export prod to `gs://agentic-librarian-prod-backups/` before any write.
**Why:** house rule since Lift 2 (prod's first write) — back up before every migration,
and doubly so here since step 3 deletes/merges rows. Nightly automated backups + 7-day
PITR exist (GH #91) but a fresh named snapshot immediately before this operation is the
one you'd actually reach for on rollback, and it's cheap.
**How:**

```bash
gcloud sql export sql librarian-sql \
  gs://agentic-librarian-prod-backups/pre-phase6-3-$(date +%Y%m%d).sql.gz \
  --database=agentic_librarian
```

**Done when:** the export command completes (can take a few minutes) and the object
appears in the bucket (`gcloud storage ls gs://agentic-librarian-prod-backups/`).

## Step 2 — Dedup dry-run **[Claude]**

**What:** run `scripts/clean_catalog.py --dedup-for-constraints --dry-run` against prod to
compute the merge/delete plan the incoming unique constraints require, without writing
anything.
**Why:** this is the read-only half of THE USER GATE — it's the report you (the operator)
review before anything is touched. `--dedup-for-constraints` is read-only by default;
nothing is written to the database on this invocation regardless of `--apply`/`--yes`
flags (they're for step 3).
**How:** from a plain WSL shell with the Cloud SQL proxy running and `PROD_DB_URL` set
(same pattern as the lift1/lift2 runbooks — proxy on the WSL host, docker-wrapped
Python):

```bash
docker run --rm -v "$PWD":/app -w /app --add-host=host.docker.internal:host-gateway \
  -e DATABASE_URL="$PROD_DB_URL" agentic_librarian-app:latest \
  python scripts/clean_catalog.py --dedup-for-constraints --dry-run
```

The tool prints a summary to stdout AND writes the FULL plan (every id involved) to
`data/reports/dedup-<UTC-timestamp-with-microseconds>.txt` — that file is what you
actually review; stdout only shows the first 10 of each class.

**Done when:** the command exits 0 and prints the path to the written report.

### WHAT TO REVIEW **[You]**

Open `data/reports/dedup-<ts>.txt` and check:

- **Per-class counts** (`duplicate_authors`, `duplicate_narrators`, `duplicate_editions`,
  `duplicate_reading_history`, `duplicate_suggestions`, `orphan_authors`,
  `duplicate_works_report_only`) — do the magnitudes look plausible for a catalog this
  size? A number in the thousands for author/narrator dedup, on a catalog of a few hundred
  works, would be a red flag worth investigating before approving.
- **`duplicate_works_report_only`** — this class is REPORT ONLY and is never applied by
  `--apply` (works carry no cross-table unique constraint; see ADR-060 decision 5). Read
  through the listed title/author groups. These are candidates for a future manual/case-by-
  case work merge, not something this rollout touches — just confirm nothing here looks
  like it should have been blocking (it isn't; the advisory lock in PR-C prevents *new*
  work duplicates going forward, this list is just visibility into what already exists).
- **Expected, not-a-bug artifacts** (per PR-C's final review, folded into this design):
  - **`orphan_authors` may legitimately be nonzero even post-PR-C.** The Dagster ETL's
    `skip_enrichment=True` branch (operator-curated re-runs only; prod's normal paths
    always set it False) can still flush an unlinked Author on an existing work. Don't
    treat a small nonzero orphan count as evidence PR-C didn't ship correctly.
  - **"Unknown"-format editions** may appear among `duplicate_editions` groups.
    `update_reading_status` historically minted editions with `format="Unknown"` when no
    real format was known. PR-C's fix reuses a sole existing edition's format going
    forward, but legacy rows — and any work with zero or multiple editions at the time —
    can still produce an `Unknown`-format edition that collides with another under the
    incoming `(work_id, format)` unique index. This is expected; the plan's survivor
    selection (most-linked, tie-break lowest id) handles it the same as any other
    duplicate-format group.
- **Sample rows within a few groups** — spot-check that a `duplicate_authors`/
  `duplicate_narrators` survivor pick looks right (same person, not a same-surname
  coincidence — the class keys on `lower(name)` exact match, so false positives would
  require two contributors sharing an identical case-folded name, which is unlikely but
  worth a glance).

**Approve or don't.** If anything looks wrong, stop here — do not proceed to step 3. Come
back with questions/corrections; the plan can be re-run any time (it's read-only) as prod
data or your understanding changes.

## Step 3 — Apply **[Claude, only after You approve]**

**What:** re-run the same tool with `--apply --yes --report <the file you reviewed>` to
actually merge/delete the planned rows.
**Why:** `--apply` is a SEPARATE invocation from the dry-run — it re-plans from scratch
against prod's current state (not a replay of the dry-run's in-memory plan), because
minutes may have passed and live traffic could have created new rows. To keep your review
meaningful, `--apply` cross-checks the FRESH plan's id set against the id set parsed back
out of the report you reviewed, and **refuses (exit 1) if the fresh plan contains any id
you never saw** — including an id that changed *operation* (e.g. something that was a
`repoint` in your reviewed report shows up as a `delete` in the fresh plan; a concurrent
write flipped which case applies). A plan that merely lost ids since your review (rows
deleted/changed in the gap) is fine and applies normally under ordinary `skipped_stale`
accounting.
**How:**

```bash
docker run --rm -v "$PWD":/app -w /app --add-host=host.docker.internal:host-gateway \
  -e DATABASE_URL="$PROD_DB_URL" agentic_librarian-app:latest \
  python scripts/clean_catalog.py --dedup-for-constraints --apply --yes \
  --report data/reports/dedup-<the-ts-you-reviewed>.txt
```

**If it refuses with "plan changed since review":** the tool prints exactly which ids are
new (with their operation tag) and writes a FRESH report from the same re-plan it just
refused to apply. **Loop back to step 2's review** — read the new report (particularly
the flagged new/changed ids), and if it still looks right, re-run step 3 pointing
`--report` at the new file. This is not a bug — it's the gate doing its job. In a
quiet-ish window (step 0) this should be rare.

**Orphan-author note:** a merge in this same run can create a NEW orphan author (e.g. two
authors merge, and the loser was the only thing keeping some third row's join alive) —
`_plan_orphan_authors` computes against current state, not simulated ahead of this run's
own not-yet-applied merges, by design (see `etl/dedup_backfill.py`'s docstring). **Re-run
the dry-run again after applying** (step 2) — if it comes back clean (all per-class counts
zero), you're done; if it finds a fresh, small orphan-author batch, that's expected
follow-on cleanup, not a sign something went wrong. Loop steps 2→3 until a dry-run comes
back clean.

**Deferred-intersections note:** the report's `deferred_intersections` section lists
`duplicate_editions`/`duplicate_reading_history` groups the planner deliberately DROPPED
from this run because they intersect a `duplicate_narrators`/`duplicate_editions` group
also in play — applying both compositions from the same plan snapshot could lose rows (a
narrator-merge repoint living on a loser edition; an edition-merge's own reading_history
collision-delete colliding with class 4's independent pick). **A nonzero
`deferred_intersections` count is EXPECTED on intersecting data, not an error** — it is
resolved the exact same way as a fresh orphan-author batch: this same steps 2→3 loop.
Once the intersecting class (narrators, or editions) has applied, the deferred group no
longer intersects anything on the next dry-run and applies normally.

**Done when:** the apply command exits 0, prints per-class applied counts, and a
follow-up dry-run (step 2) comes back with all classes empty except
`duplicate_works_report_only` (never applied, expected to persist until a future manual
work-merge effort).

## Step 4 — Migrate **[You]**

**What:** run `alembic upgrade head` from this branch against prod. This lands the #95
unique constraints (now safe — prod has no more duplicates to violate them), the #109 FK
indexes, the #108 timestamptz conversions, and the #97 `works.deep_enriched_at` column.
**Why manual, not part of deploy.yml:** migrations in this project are always
operator-run before merge (lift1 runbook §3 mechanics) — deploy.yml deliberately has no
alembic step; ADR-058's startup guard is what makes the ordering safe rather than a race.
**How:** mirrors lift1 runbook §3 exactly — same proxy, same docker-wrapped alembic
invocation, on THIS branch's checkout:

```bash
# proxy already running from step 1/2 (or start it: ./cloud-sql-proxy --port 5433 <CONNECTION_NAME>)
docker run --rm -v "$PWD":/app -w /app --add-host=host.docker.internal:host-gateway \
  -e DATABASE_URL="$PROD_DB_URL" agentic_librarian-app:latest alembic current
# expect: c4f81a2d9b6e (this migration's down_revision)

docker run --rm -v "$PWD":/app -w /app --add-host=host.docker.internal:host-gateway \
  -e DATABASE_URL="$PROD_DB_URL" agentic_librarian-app:latest alembic upgrade head
# expect: now at 48e3762d6c0c
```

**Verify the objects landed** (`\d` output or `information_schema`), expected:

- **5 unique indexes**: `uq_authors_name_lower`, `uq_narrators_name_lower`,
  `uq_editions_work_format`, `uq_reading_history_user_edition_date`,
  `uq_suggestions_active`.
- **10 new FK indexes** (`ix_<table>_<column>`): `editions.work_id`,
  `reading_history.edition_id`, `work_tropes.trope_id`, `work_contributors.author_id`,
  `suggestions.work_id`, `author_styles.style_id`, `work_styles.style_id`,
  `usage.conversation_id`, `narrator_styles.style_id`, `edition_narrators.narrator_id`.
- **13 columns converted to timestamptz**: `suggestions.suggested_at`,
  `conversations.created_at`/`updated_at`, `messages.created_at`, `users.created_at`,
  `usage.created_at`, `user_credentials.created_at`/`updated_at`,
  `user_libraries.created_at`, `availability_cache.fetched_at`, `import_jobs.created_at`,
  `import_rows.created_at`/`updated_at`. (`availability_cache.fetched_at` deliberately
  keeps NO column default — don't be alarmed it's the one without.)
- **`works.deep_enriched_at`** — new nullable `timestamptz` column. The migration itself
  backfills it to `now()` for every pre-existing work with at least one `work_tropes` row
  (evidence it already went through the full deep pipeline); only works with zero trope
  links stay NULL. The step 6 requeue report surfaces those NULLs plus any stamped work
  whose only tropes are fallback/junk — see step 6 for what to expect.

**Since ADR-058**, the startup migration guard tolerates a migrated-ahead DB during this
window — the still-running old revision keeps cold-starting normally (its
`alembic_version` is merely unknown to it, not behind). Emergency bypass if the guard ever
misfires: `MIGRATION_GUARD=off`.

**Done when:** `alembic current` on prod reports `48e3762d6c0c` and the objects above are
confirmed present.

## Step 5 — Merge PR-D **[You]**

**What:** merge the `feat/phase6-3b-schema-integrity` PR to `main` (after CI green +
Gemini review, per the house PR workflow).
**Why:** the migration must land BEFORE the merge (step 4 already did that) so that
ADR-058's guard sees a DB at-or-ahead of the deploying image's head, not behind it — a
behind DB fails the new revision outright.
**How:** normal squash-merge via GitHub (title = PR title, blank body — the #90 durable
fix, no `[skip ci]` risk).

**Done when:** merged; the deploy workflow fires automatically (path-filtered on
`src/**`/`pyproject.toml`/`Dockerfile.api`).

### Verify the deploy **[You]**

- Watch the Actions run go green, including the smoke test.
- Confirm the new revision is ready and serving (`gcloud run services describe
  librarian-api --region us-central1 --format='value(status.latestReadyRevisionName,
  status.conditions)'`) — ADR-058's guard should pass silently (DB is at head, not
  behind).
- Confirm memory is still pinned at **2Gi** (ADR-051/GH #89 — deploy.yml drift class);
  `gcloud run services describe librarian-api --region us-central1
  --format='value(spec.template.spec.containers[0].resources.limits.memory)'` should read
  `2Gi`.

**Done when:** revision ready, guard passed (no forced rollback), memory confirmed 2Gi.

## Step 6 — Post-checks **[Claude]**

**What:** confirm the new constraints/indexes are live and kick off the first #97
enrichment-completeness report.
**Why:** cheap, fast confirmation that the migration is exactly what was intended, plus
turning `deep_enriched_at`/the real-trope predicate into an operator-actionable backlog
for the works that need a deep pass (re)run.
**How:**

1. **Constraint sanity** — a `\di` (or equivalent `information_schema.pg_indexes`) listing
   confirms the 5 unique indexes + 10 FK indexes from step 4 exist with the expected
   definitions (`lower(name)` expressions, the `NULLS NOT DISTINCT` composite, the partial
   `WHERE status = 'Suggested'` predicate). No need to attempt actual duplicate-insert
   probes — the migration's own `op.execute`d DDL either applied or the migration would
   have failed outright in step 4; a listing is enough to confirm what's live.
2. **First `--requeue-unenriched` dry-run** — this doubles as the very first #97 report:

   ```bash
   docker run --rm -v "$PWD":/app -w /app --add-host=host.docker.internal:host-gateway \
     -e DATABASE_URL="$PROD_DB_URL" agentic_librarian-app:latest \
     python scripts/clean_catalog.py --requeue-unenriched --dry-run
   ```

   The migration's `upgrade()` backfills `deep_enriched_at = now()` for every work that
   already carries at least one `work_tropes` row (the structural signal that it was built
   by the full ETL/deep pipeline before this column existed — see the migration's inline
   comment) — so this first report should be SMALL, not the whole catalog. Expect only:
   - **`never_deep_enriched`**: works with zero trope links at all (never ran through the
     deep pass, or a #123-style warm failure that skipped every embedding).
   - **`no_real_trope`**: works the backfill stamped (they had *a* trope link) but every
     linked trope is fallback/junk per the #111 predicate — a poison task that exhausted
     Cloud Tasks retries without ever landing a genuine narrative trope.

   Both classes are genuinely actionable — this is the actual #97 poison-task recovery list,
   not a baseline artifact to set aside. `--apply --yes` re-enqueues deep enrichment via
   Cloud Tasks (`CLOUD_TASKS_QUEUE`, `ENRICH_TARGET_BASE_URL`, `ENRICH_INVOKER_SA` — already
   set in the prod container env, nothing extra to configure) for exactly these works; it is
   safe to apply directly against the full report rather than reserving it for a subset,
   since the backfill already excluded already-enriched works from appearing at all.
3. **Close the issues** — after acceptance, close #95 #96 #97 #98 #108 #109 #110 #111 #112
   with references to the resolving PRs (#124 for PR-C's #96/#98/#110/#111/#112/#123; this
   PR for #95/#97/#108/#109), and comment on #88 noting `log_suggestion` dedup closes its
   root cause.

**Done when:** index listing confirms all 15 new objects; the first requeue report is
generated and reviewed; issues closed with PR references.

---

## Rollback

- **Migration:** the migration has a symmetric hand-written `downgrade()` — drops the 5
  unique indexes, drops the 10 FK indexes, drops `deep_enriched_at`, converts the 13
  timestamptz columns back to naive `timestamp` (`AT TIME ZONE 'UTC'` both directions).
  `alembic downgrade -1` from this revision if needed.
- **Dedup apply (step 3):** not naturally reversible (rows were merged/deleted) — this is
  exactly why steps 1 (pg_dump) and 2 (review-before-apply) exist. Restore from the
  step 1 snapshot if a mistaken apply needs undoing (`gcloud sql import sql`, same pattern
  as the walking-skeleton runbook's restore script).
- **Deploy:** ADR-058's guard already covers the ordinary case (an old image tolerates a
  migrated-ahead DB, so a bad PR-D deploy can be rolled back to the pre-merge image without
  `MIGRATION_GUARD=off`).
