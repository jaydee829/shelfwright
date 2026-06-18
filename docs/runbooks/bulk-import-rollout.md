# Rollout Runbook — Bulk Reading-History Import

**Audience:** operator (you). **Goal:** make the bulk-import feature actually work in production.
**Why this is needed:** the per-row worker enqueues a Cloud Task per imported row. If the import
queue + its env var aren't wired, every commit silently leaves rows `pending` forever (the enqueue
helper logs `"import-row enqueue skipped — Cloud Tasks not configured"` and returns `False`). The
app code, CI wiring (`deploy.yml`), and infra script (`infra/08-cloud-tasks.sh`) are already in this
branch; the steps below are the GCP-side actions only **you** can do.

Related: spec `docs/superpowers/specs/2026-06-18-bulk-import-design.md` (§11), design plan
`docs/superpowers/plans/2026-06-18-bulk-import.md`.

---

## What's already wired in code (no action needed)

- `infra/08-cloud-tasks.sh` now also creates the **`librarian-import`** queue (separate from the
  enrich queue, so an import burst can't starve interactive deep-enrich) with a conservative
  dispatch rate (`--max-dispatches-per-second=2 --max-concurrent-dispatches=5`).
- The import queue **reuses** the existing invoker SA (`GCP_ENRICH_INVOKER_SA`), base URL
  (`GCP_RUN_BASE_URL`), and OIDC audience — and the runtime SA's existing **project-level**
  `cloudtasks.enqueuer` + `serviceAccountUser` grants cover the new queue. **No new IAM.**
- `deploy.yml` now sets `IMPORT_TASKS_QUEUE=${{ vars.GCP_IMPORT_TASKS_QUEUE }}` on the service and
  **requires** the `GCP_IMPORT_TASKS_QUEUE` repo Variable (fail-closed: a deploy aborts at the
  "Verify required repo Variables" step if it's unset — same pattern as the `ENRICH_*` vars).

---

## Ordering (important)

Do **Steps 1–3 BEFORE** merging this branch to `main`. Because `deploy.yml` now requires
`GCP_IMPORT_TASKS_QUEUE`, the first deploy after merge will **fail at the verify step** until that
repo Variable exists. The recommended order:

1. Provision the queue (Step 1)
2. Set the repo Variable (Step 2)
3. Apply the migration to prod (Step 3)
4. Merge → auto-deploy wires the env var (Step 4)
5. Smoke test (Step 5)

---

## Step 1 — Provision the import queue

From a shell with `gcloud` authenticated to the project (the same place you ran the Stage-4 infra
scripts; on this machine that's the WSL clone):

```bash
cd infra
./08-cloud-tasks.sh
```

The script is **idempotent** — it re-describes each resource and only creates what's missing, so
re-running it is safe and will simply create the new `librarian-import` queue alongside the existing
enrich queue. At the end it prints the repo Variables, including the new one:

```
  Set these GitHub repo Variables for deploy.yml:
    GCP_CLOUD_TASKS_QUEUE  = projects/<proj>/locations/us-central1/queues/librarian-enrich
    GCP_IMPORT_TASKS_QUEUE = projects/<proj>/locations/us-central1/queues/librarian-import   ← NEW
    GCP_ENRICH_INVOKER_SA  = ...
```

Copy the full `GCP_IMPORT_TASKS_QUEUE` value.

**Verify the queue exists:**
```bash
gcloud tasks queues describe librarian-import --location=us-central1
```

## Step 2 — Set the GitHub repo Variable

GitHub → repo **Settings → Secrets and variables → Actions → Variables → New repository variable**:

- **Name:** `GCP_IMPORT_TASKS_QUEUE`
- **Value:** the full path from Step 1 (e.g. `projects/<proj>/locations/us-central1/queues/librarian-import`)

(Or via CLI: `gh variable set GCP_IMPORT_TASKS_QUEUE --body "projects/.../queues/librarian-import"`.)

## Step 3 — Apply the database migration to prod

The new tables are `import_jobs` and `import_rows` (Alembic revision **`7b7b4d6ae6f6`**, "bulk import
tables", chained on the prior head `30f1e46533e9`). `deploy.yml` does **not** run migrations, so apply
it the same way you apply other prod migrations — via the Cloud SQL Auth Proxy against the prod DB:

```bash
# Terminal A: open the proxy to the prod instance
cloud-sql-proxy <GCP_CLOUDSQL_CONNECTION>

# Terminal B: point Alembic at prod and upgrade (use the prod DATABASE_URL / the librarian-db-url secret)
export DATABASE_URL="postgresql://<user>:<pass>@127.0.0.1:5432/agentic_librarian"
alembic upgrade head
```

**Verify:**
```bash
# both tables present
psql "$DATABASE_URL" -c "\dt import_*"
# Alembic head is the bulk-import revision
alembic current   # → 7b7b4d6ae6f6 (head)
```

> The migration is additive (two new tables + indexes, FKs to `users`/`import_jobs`); it touches no
> existing table, so it is safe to run while the service is live and needs no downtime.

## Step 4 — Merge & deploy

Merge the PR to `main`. The deploy workflow runs (it triggers on `src/**`, `frontend/**`,
`.github/workflows/deploy.yml`), the verify step now passes (the Variable is set), and the service
comes up with `IMPORT_TASKS_QUEUE` populated.

> ⚠️ Known CD note (from project memory): push-to-main auto-deploy reportedly stopped firing after
> PR #49. If the deploy doesn't start on merge, trigger it manually: **Actions → "Deploy to Cloud
> Run" → Run workflow** (workflow_dispatch). Confirm the run's Deploy step shows
> `IMPORT_TASKS_QUEUE=` in the `--set-env-vars`.

**Verify the env var landed on the service:**
```bash
gcloud run services describe librarian-api --region=us-central1 \
  --format='value(spec.template.spec.containers[0].env)' | tr ',' '\n' | grep -i IMPORT_TASKS_QUEUE
```

## Step 5 — Smoke test

1. In the app, go to **History → Import history**, upload a small (~5-row) Goodreads/CSV export,
   confirm the mapping, and start the import.
2. Watch the progress bar advance and rows move to "imported".
3. Confirm the queue is draining:
   ```bash
   gcloud tasks queues describe librarian-import --location=us-central1 \
     --format='value(stats.tasksCount, stats.oldestEstimatedArrivalTime)'
   ```
4. Confirm the imported books appear in History (with enrichment following over the next minute or
   two via the existing deep-enrich queue).

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Import commits but rows stay `pending` forever; progress never advances | `IMPORT_TASKS_QUEUE` not on the service | Re-check Step 2 + Step 4; look for log line `import-row enqueue skipped — Cloud Tasks not configured` |
| Deploy fails at "Verify required repo Variables" | `GCP_IMPORT_TASKS_QUEUE` Variable missing | Do Step 2, re-run the deploy |
| Worker tasks 403 in Cloud Run logs on `/internal/import-row/...` | OIDC invoker SA / audience mismatch | Same wiring as enrich — confirm `ENRICH_INVOKER_SA` / `ENRICH_OIDC_AUDIENCE` are set (they're shared); re-run `infra/08-cloud-tasks.sh` to re-apply the invoker's `run.invoker` binding |
| Rows stuck in `processing` (Cloud Tasks exhausted retries) | transient scout/API failures | In the import UI, the progress view shows "N row(s) appear stuck" with a **Retry failed/stalled** button (re-enqueues rows `processing` > 15 min); or POST `/import/{job_id}/retry` |
| 429s / quota errors during a large import | dispatch rate too high for current quota | Lower `--max-dispatches-per-second` on `librarian-import` (`gcloud tasks queues update librarian-import --location=us-central1 --max-dispatches-per-second=1`) |

## Rollback

The feature is additive and isolated: to disable it without a redeploy, **pause the queue**
(`gcloud tasks queues pause librarian-import --location=us-central1`) — in-flight imports stop
processing (rows stay `pending`, resumable on `resume`). The UI's Import entry can be hidden by
reverting the History nav-link commit if a full rollback is wanted. The migration need not be
reverted (the tables are inert when unused).
