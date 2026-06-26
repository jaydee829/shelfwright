# Rollout Runbook — Library Links + Live Availability (cut #1)

**Audience:** operator (you). **Goal:** make the library-links + live-availability feature (#57, PR #73)
work in production.
**Why this is needed:** the feature adds two new tables (`user_libraries`, `availability_cache`). The
new endpoints (`/availability`, `/me/libraries`, `/libraries/search`) and the `check_availability` MCP
tool read/write them, so the **Alembic migration must be applied to prod** or those paths error on
missing tables. Everything else is in the code; the only GCP-side action is the migration (plus the
usual deploy + smoke).

Related: spec `docs/superpowers/specs/2026-06-25-library-links-availability-design.md`, plan
`docs/superpowers/plans/2026-06-25-library-links-availability.md`.

---

## What's already wired in code (no action needed)

- **No new GCP resources, queues, IAM, secrets, or repo Variables.** Unlike the bulk-import rollout,
  this feature needs none of that.
- The only external dependency is an **outbound HTTPS call** from Cloud Run to
  `thunder.api.overdrive.com` (OverDrive's unofficial public "Thunder" API, isolated in
  `src/agentic_librarian/availability/overdrive.py`). Cloud Run has default internet egress, so no
  VPC/egress change is required — just confirm no egress restriction is configured on the service.
- `user_libraries` holds public library slugs (not secrets), so it does **not** touch the
  `user_credentials` keyring or KMS.
- **No data backfill.** `user_libraries` starts empty (each user adds their own libraries in
  Settings); `availability_cache` fills lazily on first view (read-through, 4h TTL).

---

## Ordering

The migration is **additive** (two brand-new tables, no change to existing tables), so it is safe to
apply while the service is live and in any order relative to the deploy. Apply it **before** the new
revision serves traffic so the new endpoints never hit missing tables:

1. Apply the migration to prod (Step 1)
2. Merge → deploy (Step 2)
3. Smoke test (Step 3)

---

## Step 1 — Apply the database migration to prod

The new tables are `user_libraries` and `availability_cache` (Alembic revision **`c4f81a2d9b6e`**,
"library links + availability", chained on the prior head `7b7b4d6ae6f6`). `deploy.yml` does **not**
run migrations, so apply it via the Cloud SQL Auth Proxy, routing through the **app container** (bare
WSL `python` has no `sqlalchemy`/`alembic`). The prod DB credentials are **not** in any local file —
they live in Secret Manager as `librarian-db-url`; pull the whole URL and rewrite only the host.

> **Prerequisite:** run this from a WSL clone that contains the migration file — it only exists on the
> PR branch until merge. Either `git checkout feat/library-links-availability` first, or merge to
> `main` and `git pull` before running. The migration is additive, so the deploy→migrate gap only
> affects the brand-new, unused tables.

```bash
# 1. Proxy to prod (CONNECTION_NAME = your GCP_CLOUDSQL_CONNECTION variable, project:region:instance)
./cloud-sql-proxy <CONNECTION_NAME> --port 5433 &

# 2. Build a proxy-routed DATABASE_URL straight from the secret (no manual password copying;
#    rewrites the unix-socket host to the proxy, container-routed via host.docker.internal)
export PROD_DB_URL="$(gcloud secrets versions access latest --secret=librarian-db-url \
  | sed -E 's#@/agentic_librarian\?host=.*#@host.docker.internal:5433/agentic_librarian#')"

# 3. Run the migration through the app container
docker run --rm -v "$PWD":/app -w /app --add-host=host.docker.internal:host-gateway \
  -e DATABASE_URL="$PROD_DB_URL" agentic_librarian-app:latest alembic upgrade head
```

**Verify** (same container wrapper — `psql`/`alembic` aren't on the WSL host):
```bash
docker run --rm -v "$PWD":/app -w /app --add-host=host.docker.internal:host-gateway \
  -e DATABASE_URL="$PROD_DB_URL" agentic_librarian-app:latest alembic current   # → c4f81a2d9b6e (head)
```

Prod is already tracked at `7b7b4d6ae6f6`, so `upgrade head` just applies this one new revision on top
— no `alembic stamp` needed. The migration touches no existing table, so it is safe to run while the
service is live and needs no downtime.

## Step 2 — Merge & deploy

Merge the PR to `main`. The deploy workflow runs (it triggers on `src/**`, `frontend/**`,
`.github/workflows/deploy.yml`). There are **no new env vars or repo Variables** required, so the
verify step passes unchanged.

> ⚠️ Known CD note (from project memory): push-to-main auto-deploy has intermittently stopped firing.
> If the deploy doesn't start on merge, trigger it manually: **Actions → "Deploy to Cloud Run" → Run
> workflow** (workflow_dispatch).

**(Optional) Tune the cache TTL** — the availability cache defaults to 4h (`14400` s). To override,
set `AVAILABILITY_TTL_SECONDS` on the service (longer TTL = fewer upstream Thunder calls; back it off
if Thunder shows rate-limiting):
```bash
gcloud run services update librarian-api --region=us-central1 \
  --update-env-vars AVAILABILITY_TTL_SECONDS=14400
```

## Step 3 — Smoke test

1. In the app, open **Libraries** (the new Settings nav entry). Search for a real library system you
   hold a Libby card for, **Add** it, reorder if you have several, and **Save**.
2. Open **Recommendations**. Within a moment each card should show a link row (Libby / Hoopla /
   Bookshop / Amazon) and — for titles your library carries — an availability badge (e.g. "Audiobook
   available now" or "eBook ~12wk wait").
3. In **Chat**, ask the Librarian *"where can I get \<a recommended title\>?"* and confirm it narrates
   availability + offers a link (it calls the `check_availability` tool).
4. (Optional) Confirm the cache is populating:
   ```bash
   docker run --rm -v "$PWD":/app -w /app --add-host=host.docker.internal:host-gateway \
     -e DATABASE_URL="$PROD_DB_URL" agentic_librarian-app:latest \
     python -c "from agentic_librarian.db.session import DatabaseManager; from agentic_librarian.db.models import AvailabilityCache; s=DatabaseManager().get_session().__enter__(); print('cache rows:', s.query(AvailabilityCache).count())"
   ```

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `/availability`, `/me/libraries`, or chat availability 500s; logs show `relation "user_libraries"/"availability_cache" does not exist` | migration not applied | Run Step 1; verify `alembic current` → `c4f81a2d9b6e` |
| Links render but **no** availability badge ever appears, for every book | Thunder unreachable, or its response shape changed | This is the intended graceful degradation (links-only). Check Cloud Run egress to `thunder.api.overdrive.com`; manually `curl "https://thunder.api.overdrive.com/v2/libraries/<slug>/media?query=dune&format=ebook-overdrive,audiobook-overdrive&x-client-id=dewey"` to see if the unofficial endpoint changed (it's isolated in `availability/overdrive.py` for exactly this reason) |
| Badge appears for some books but not others | no confident title match at that library, or the library doesn't carry the title | Expected — the matcher under-claims rather than show a wrong "available now" |
| Library search box returns nothing | Thunder directory unreachable (`/libraries/search` → 503) | Transient; retry. Confirm egress as above |
| Availability looks stale | cache TTL | Default is 4h; lower `AVAILABILITY_TTL_SECONDS` if you want fresher data (more upstream calls) |

## Rollback

The feature is additive and isolated. To disable the live-availability lookups without a redeploy,
the cleanest lever is to make the Thunder client unreachable (it already degrades to links-only on
failure) — but normally you'd just revert the PR. The migration need **not** be reverted: the two
tables are inert when the code isn't reading them. If a full rollback is wanted, reverting the PR
removes the endpoints, the MCP tool, the Settings nav entry, and the BookLinks rendering; the empty
`user_libraries`/`availability_cache` tables can be left in place or dropped via the migration's
`downgrade()` (`alembic downgrade 7b7b4d6ae6f6`).
