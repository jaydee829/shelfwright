# Lift 2 Stage 4 — Rollout Runbook

Takes the friends-and-family beta live: opens the Cloud Run IAM gate, provisions async
enrichment, applies the first prod write-path migration, and verifies the live stack.

**Ordering is load-bearing (spec D2):** merging PR-B's deploy opens the gate *and* ships the
SPA/chat-reachable image at once. So **provision + migrate while the gate is still closed**,
then merge PR-B as the deliberate gate-opening act.

**Prereqs:** PR-A merged; PR-B reviewed/approved and **held** (not merged); `gcloud` authed to
`agentic-librarian-prod`; cloud-sql-proxy available.

**Run from (same model as the Lift 1 runbook):** a **plain WSL shell** at the WSL clone
(`~/agentic_librarian`) — **not** from inside the dev/app container. `gcloud` and the
cloud-sql-proxy binary live natively on the WSL host (from the Lift 0/1 runs); anything that
needs the Python deps (alembic, the live-verify token helper) is **docker-wrapped** —
`docker run --rm -v "$PWD":/app -w /app … agentic_librarian-app:latest <cmd>` — invoked *from*
that WSL shell. Browser steps (Firebase sign-in, setting repo Variables, merging PR-B) happen
on the host.

> **First, get the Stage 4 code into the WSL clone:** `infra/08`/`09` and this runbook live in
> the un-merged PR-B branch. In `~/agentic_librarian`:
> `git fetch origin && git checkout feat/lift2-stage4-pr-b` (run the provisioning from there),
> then merge PR-B at step 5.

| Step | Where |
|------|-------|
| `infra/08`/`09`, `gcloud sql export`, set repo Variables | plain WSL shell (`gcloud` native) |
| cloud-sql-proxy | native binary on the WSL host |
| `alembic current` / `upgrade head` | docker-wrapped, invoked from the WSL shell |
| Google sign-in + live verify, merge PR-B | browser / GitHub UI |

## 1. Provision (gate still CLOSED)
1. `bash infra/08-cloud-tasks.sh` — creates the queue, the invoker SA, and the IAM grants.
   Note the printed `GCP_CLOUD_TASKS_QUEUE` / `GCP_ENRICH_INVOKER_SA` values.
2. Export the three keys, then `bash infra/09-prod-secrets.sh` — creates the key secrets +
   grants the runtime SA `secretAccessor`.
3. Set the GitHub repo **Variables** PR-B's `deploy.yml` reads (the preflight step fails the
   deploy if any are unset):
   - `GCP_CLOUD_TASKS_QUEUE` = the full queue path
   - `GCP_RUN_BASE_URL` = the Cloud Run service base URL (used for BOTH `ENRICH_TARGET_BASE_URL`
     and `ENRICH_OIDC_AUDIENCE` — they MUST match or every enrichment 403s)
   - `GCP_ENRICH_INVOKER_SA` = the invoker SA email
   - `GCP_SEARCH_ENGINE_ID` = the Programmable Search Engine id

## 2. Back up prod (first prod write boundary)
- **One-time grant (first real `gcloud sql export`):** the Cloud SQL instance's own service
  account must be able to write the dump to the bucket, or export 412s
  ("service account does not have the required permissions for the bucket"):
  ```bash
  SQL_SA="$(gcloud sql instances describe librarian-sql --format='value(serviceAccountEmailAddress)')"
  gcloud storage buckets add-iam-policy-binding gs://agentic-librarian-prod-backups \
    --member="serviceAccount:${SQL_SA}" --role="roles/storage.objectAdmin"   # ~30s to propagate
  ```
- Export:
  ```bash
  gcloud sql export sql librarian-sql \
    gs://agentic-librarian-prod-backups/pre-stage4-$(date +%Y%m%d).sql.gz --database=agentic_librarian
  ```
- This rollout is the first prod write (chat). From here: **back up before every migration.**

## 3. Apply the migration (gate still CLOSED)
Mirrors the Lift 1 runbook §3 (proxy on the WSL host; alembic docker-wrapped). The proxy must
be **running for the whole step** — it's a foreground binary, so start it in its own WSL shell.

1. **Start the cloud-sql-proxy** (Lift 0 binary on the WSL host) in a dedicated shell — it sits
   on a blank line while serving. `CONNECTION_NAME` = the `GCP_CLOUDSQL_CONNECTION` repo Variable,
   or `gcloud sql instances describe librarian-sql --format='value(connectionName)'`:
   ```bash
   ./cloud-sql-proxy --port 5433 <CONNECTION_NAME>
   ```
   Confirm it's listening (a "connection refused" from the container means it isn't):
   ```bash
   ss -tln | grep 5433     # no output = not running. If the container still can't reach it,
                           # restart with --address 0.0.0.0 (Ctrl-C when done; IAM still gates it).
   ```
2. **Build the proxy-routed DATABASE_URL** from the secret (container-routed to host.docker.internal):
   ```bash
   export PROD_DB_URL="$(gcloud secrets versions access latest --secret=librarian-db-url \
     | sed -E 's#@/agentic_librarian\?host=.*#@host.docker.internal:5433/agentic_librarian#')"
   ```
3. **Confirm the starting point** (docker-wrapped — WSL python has no deps). It MUST print
   `c804d02d6fbb` (the Lift 1 head). If it prints `30f1e46533e9`, the migration is already done —
   skip to step 4. Anything else → STOP and investigate:
   ```bash
   docker run --rm -v "$PWD":/app -w /app --add-host=host.docker.internal:host-gateway \
     -e DATABASE_URL="$PROD_DB_URL" agentic_librarian-app:latest alembic current
   ```
4. **Upgrade** (moves prod `c804d02d6fbb` → `30f1e46533e9`: `conversations`, `messages`,
   `usage.conversation_id` FK):
   ```bash
   docker run --rm -v "$PWD":/app -w /app --add-host=host.docker.internal:host-gateway \
     -e DATABASE_URL="$PROD_DB_URL" agentic_librarian-app:latest alembic upgrade head
   ```
5. **Verify** `alembic current` now prints `30f1e46533e9` and the tables exist:
   ```bash
   docker run --rm -v "$PWD":/app -w /app --add-host=host.docker.internal:host-gateway \
     -e DATABASE_URL="$PROD_DB_URL" agentic_librarian-app:latest \
     python -c 'from sqlalchemy import create_engine, inspect; from agentic_librarian.db.session import resolve_database_url; i = inspect(create_engine(resolve_database_url())); print("chat tables:", [t for t in ("conversations","messages") if t in i.get_table_names()])'
   ```
   Ctrl-C the proxy when the step is done.

## 4. Sanity (gate still CLOSED)
A light "look before you flip the gate" check — the migration was additive (new tables the old
image doesn't use) and nothing was redeployed, so the running service is almost certainly fine.
Confirm it's still serving (token-free; `/health` is IAM-gated while the gate is closed and
minting a correctly-scoped IAM token as your user account isn't worth it here):
```bash
gcloud run services describe librarian-api --region=us-central1 \
  --format='table(status.conditions[].type, status.conditions[].status)'
```
Want `Ready = True` (+ `ConfigurationsReady`/`RoutesReady = True`). The DB side is already proven
by step 3 (alembic at `30f1e46533e9`, tables present). The CD's own live smoke re-checks `/health`
+ 401-enforcement automatically when PR-B deploys (step 5), so a manual `/health` curl is skippable.

## 5. Merge PR-B → CD opens the gate
- Merge PR-B. CD builds the multi-stage image, deploys with the new env/secrets, and flips to
  `--allow-unauthenticated`. The CD in-runner smoke asserts `/health` + `GET /` SPA; the live
  smoke asserts `/health` + `401`-without-Firebase.

## 6. Manual live verification (gate now OPEN)
Run through the browser as an invited user:
- [ ] Google sign-in succeeds; the SPA shell loads at `/`.
- [ ] A chat turn streams live activity then a reply.
- [ ] Add-a-book logs a read (fast pass returns in seconds).
- [ ] ~2 minutes later, deep enrichment has completed (tropes appear on the work) — confirms the
      Cloud Task fired and the queue-OIDC internal route accepted it.
- [ ] A metered `usage` row was written (check via a `GET /works` enriched payload or DB peek).
- [ ] `/history` paginates ("Load more" fetches the next page).

## 7. Cost watch
- Confirm the budget alert is live; eyeball the first real `usage` rows; confirm `max-instances=2`.

## Rollback (ONLY if step 5/6 fails or the deploy is broken)
- Revert the PR-B merge commit on `main`. The next CD deploy re-applies
  `--no-allow-unauthenticated` (re-closing the gate) and reverts the image in one move.
- The applied migration is **additive and safe to leave** — do not run a down-migration; the
  unused `conversations`/`messages` tables are harmless.
