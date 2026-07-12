# Phase 6.1 — Prod-Risk Hotfixes (Design)

**Date:** 2026-07-12 · **Issues:** #89, #90, #91, #92 (folded in), #99 · **Roadmap:** plan.md Phase 6.1

## Goal

Close the five act-first production risks from the 2026-07-02 scaling review: config drift that
reverts the OOM fix on every deploy (#89), the `[skip ci]` squash-merge leak that silently kills
auto-deploy (#90), an unprotected prod database (#91), silent code-vs-schema mismatch on deploy
(#92), and import rows permanently stranded `pending` (#99).

**Delivery shape:** one PR (`fix/phase6-1-prod-hotfixes`) for all code changes; live operations
executed by Claude via WSL gcloud / gh with per-command notification (user-authorized 2026-07-12).
#92 is folded into this grouping (it was the only scaling-review issue not slotted into any 6.x
grouping; this PR already owns deploy.yml).

## Verified live state (2026-07-12, read-only describes)

- Cloud Run `librarian-api` memory: **512Mi** — the June OOM fix IS currently reverted in prod.
- Cloud SQL `librarian-sql`: `backupConfiguration.enabled=False`, no PITR — zero automated backups.
- Enrich queue `librarian-enrich`: 4 concurrent / 5.0 per sec — hand-applied tuning intact
  (queue settings are not touched by deploys).
- Repo squash setting: `squash_merge_commit_message=COMMIT_MESSAGES` — the leak vector is live.

## Design per issue

### #89 — deploy.yml memory drift + infra/08 queue codification

1. **Immediate stabilization (ops, before the PR merges):**
   `gcloud run services update librarian-api --region us-central1 --memory=2Gi`.
   Rationale: prod at 512Mi with the queue at 4 concurrent deep scouts (~½GiB each) is one bulk
   import away from a repeat OOM storm. Additive, reversible, identical to the June remediation.
2. **deploy.yml:** change `--memory=512Mi` → `--memory=2Gi` (line ~156). No other flags are
   added: `gcloud run deploy` retains existing service settings for unspecified flags, so
   timeout/concurrency stay as live. Memory only drifted because deploy.yml explicitly pins it.
3. **infra/08-cloud-tasks.sh:** pin the enrich-queue creation with
   `--max-concurrent-dispatches=4 --max-dispatches-per-second=5`, and add an update branch when
   the queue already exists (`gcloud tasks queues update` with the same flags) so re-runs
   converge live state instead of skipping it. **Decision:** 5/s per ADR-051 and verified live
   state — the issue body's `0.2/s` is treated as a typo (0.2/s would take ~83 min to dispatch a
   1000-row import's deep tasks).
4. **Verification:** after the PR's auto-deploy, re-describe the service and confirm memory is
   still 2Gi (proves the drift class is closed).

### #90 — durable [skip ci] squash fix (ops + docs)

1. **Ops:** `gh api -X PATCH repos/jaydee829/shelfwright -f squash_merge_commit_title=PR_TITLE
   -f squash_merge_commit_message=BLANK`. **Decision:** title = PR title (already carries the
   "(#N)" convention), body = blank — drops the commit-list body entirely, which is where
   `[skip ci]` bullets leaked from.
2. **Docs (in the PR):** append the "durable fix applied 2026-07-12" resolution to the bugs.md
   2026-06-17 entry.
3. **Verification:** the 6.1 PR itself will be squash-merged after the setting change — its push
   to main MUST trigger the deploy workflow. That is the acceptance test.

### #91 — Cloud SQL automated backups + PITR (ops + code)

1. **Ops:** `gcloud sql instances patch librarian-sql --backup-start-time=09:00
   --enable-point-in-time-recovery` (09:00 UTC ≈ 04:00 ET — quiet window). Defaults kept:
   7 retained daily backups, 7-day WAL retention for PITR.
   **Restart caveat:** if gcloud warns the patch requires a restart, pause and notify the user
   before confirming (brief downtime on a live beta). **Budget:** 10GB SSD instance — backup +
   WAL storage is small relative to the $25/mo guardrail; verify billing after a week.
2. **Code:** codify in `infra/02-cloudsql.sh` — add the same flags to the create command and a
   comment noting the patch command for pre-existing instances.
3. **Verification:** `describe` shows `backupConfiguration.enabled=True` +
   `pointInTimeRecoveryEnabled=True`; confirm the first nightly backup exists the next day
   (`gcloud sql backups list --instance=librarian-sql`).

### #92 — alembic startup migration guard (code; ADR-058)

**Chosen approach — startup guard in the app lifespan.** At container start, compare the DB's
`alembic_version` to the migration head shipped in the image; on mismatch, raise so the container
exits. Cloud Run then never marks the new revision ready, `deploy-cloudrun` fails the workflow
with the container log visible, and **traffic keeps serving from the previous revision** —
automatic rollback semantics with zero credentials handed to CI.

**Alternatives rejected:**
- *Workflow-side DB query via Cloud SQL Auth Proxy* (issue option a): requires giving the
  deployer SA access to prod DB credentials (`librarian-db-url` secret or a new DB user) — a
  security expansion for a guard; also adds proxy setup complexity to the workflow.
- */health/migrations endpoint asserted by the smoke test* (issue option b): checks AFTER
  traffic has shifted to the new revision (too late), or requires Firebase tokens the deployer
  cannot mint (the exact gap the issue notes for /health/db).

**Behavior matrix:**
| DB state at startup | Guard behavior |
|---|---|
| `alembic_version` == repo head | start normally |
| `alembic_version` behind/diverged | log the two versions, **raise** → container exits |
| `alembic_version` table absent | treat as mismatch (un-stamped DB should be loud) |
| DB unreachable / query error | **log warning, continue startup** — a transient DB blip must not kill scale-from-zero cold starts; DB health has its own signals |
| `MIGRATION_GUARD=off` env | skip entirely (emergency escape hatch, e.g. deploying the fix for a bad migration) |

**Implementation notes:**
- New module `src/agentic_librarian/db/migration_guard.py`: read the expected head via alembic's
  `ScriptDirectory` (from `alembic.ini` + `alembic/` shipped in the image), query
  `select version_num from alembic_version`, compare. Called from `main.py`'s `lifespan` before
  the pool handoff.
- `Dockerfile.api`: add `COPY alembic.ini ./` and `COPY alembic ./alembic` (small, no new deps —
  alembic is already a runtime dependency).
- Multi-head safety: if `ScriptDirectory.get_heads()` returns >1 head, fail loudly (a branched
  migration tree is itself a bug).
- The in-runner docker smoke test in deploy.yml uses a bogus `DATABASE_URL` (nohost) — the
  unreachable-DB row above means it keeps passing unchanged.

### #99 — stranded-pending import rows (code)

1. **Retry filter** (`api/imports.py` `retry()`): add
   `| ((ImportRow.status == "pending") & (ImportRow.updated_at < cutoff))` to the existing
   failed-or-stale-processing filter. Re-enqueueing a pending row is safe — the worker's status
   machine is idempotent by design (spec 2026-06-18).
2. **Status payload** (`get_status()`): redefine `stalled` as "rows a retry will re-drive" =
   stale `processing` + stale `pending` (same `STALLED_AFTER` cutoff). **Decision:** fold into
   the existing `stalled` number rather than adding a second field — the frontend already
   surfaces `stalled`, and the user-facing meaning ("stuck rows, press retry") is identical.
3. **Tests:** stranded-pending row (old `updated_at`, status `pending`) is picked up by retry
   and counted in `stalled`; fresh pending rows are NOT (they're normally in-flight).

## Execution order

1. Ops: live 2Gi bump (#89.1) — done first, independent of the PR.
2. Ops: squash setting (#90.1) — before the PR merges, so the merge itself verifies it.
3. Ops: Cloud SQL backups patch (#91.1) — any time; restart caveat applies.
4. PR: deploy.yml + infra/08 + infra/02 + migration guard + imports fix + docs
   (bugs.md #90 note, decisions.md ADR-058, key_facts.md deploy/backup facts).
5. Merge → push-triggered deploy MUST fire (#90 acceptance) → re-describe memory = 2Gi
   (#89 acceptance) → close #89, #90, #91, #92, #99 with PR references.

## Testing

- Migration guard: unit tests for all five behavior-matrix rows (fake ScriptDirectory/head +
  sqlite or mocked session; DB-free so they run locally, not CI-only).
- Imports: extend the existing imports API tests for the new retry filter + stalled count.
- infra scripts: `bash -n` syntax check (they run against live GCP only).
- Existing fast suite must stay green (runs in deploy.yml pre-deploy).

## Decisions delegated to Claude (for user review)

1. Immediate live 2Gi bump ahead of the PR (blocked by permission gate — handed to user).
2. Enrich queue codified at 4 concurrent / 5 per sec (ADR-051 + live state; issue's 0.2/s = typo).
3. Squash message = PR title + blank body (not "default to PR body").
4. PITR enabled alongside backups (7-day defaults), 09:00 UTC window; restart requires
   notification before proceeding.
5. #92 solved as a startup guard (ADR-058) instead of the issue's two suggested mechanisms.
6. #99 stalled-count folds stale-pending into the existing `stalled` field (no API shape change).
7. #92 folded into the 6.1 PR (only un-grouped scaling issue; this PR owns deploy.yml).
