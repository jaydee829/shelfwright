# Lift 0 — GCP Walking Skeleton

**Date:** 2026-06-05
**Status:** Approved (brainstormed with user)
**Parent:** `2026-06-05-product-roadmap-design.md` (ADR-046) — this is Lift 0 of that sequence.

## Goal

A real, access-gated URL serving the enriched catalog from a managed Postgres in GCP,
deployed automatically on every merge to `main`. Pipes and data — no agents, no auth
users, no UI. Lift 2 lights up the conversational mesh inside this same service later;
nothing here forecloses that.

## Decisions (brainstormed)

| Question (deferred by roadmap) | Decision |
|---|---|
| DB cost posture | **Cloud SQL, smallest tier (~$12/mo always-on)** — managed, boring, scales into multi-user. No stop-when-idle tricks, no third-party Postgres. |
| Access gate | **Cloud Run IAM** (`--no-allow-unauthenticated`) — platform-edge rejection, zero code to write or discard. Test via `gcloud auth print-identity-token` / `gcloud run services proxy`. Replaced by Firebase Auth in Lift 1. |
| CD shape | **GitHub Actions, auto-deploy on merge to `main`** (path-filtered; `workflow_dispatch` also enabled). Auth via Workload Identity Federation — keyless. |
| Region / budget | **us-central1**; billing budget **$25/month** with email alerts at 50/90/100% (warns, never blocks; expected normal bill ~$12–16). |
| API surface | **Scaffold endpoints + `GET /works`** — the skeleton literally serves the enriched catalog, and the endpoint doubles as restore verification. |
| Provisioning style | **Scripted `gcloud` + runbook** — numbered scripts in `infra/`, runbook in `docs/runbooks/`. Terraform deferred until environments multiply (~Lift 3). |

## Architecture

```
GitHub (merge to main)
   │  Actions: test → build image → push → deploy → smoke test
   │  (auth: Workload Identity Federation — keyless)
   ▼
┌─ GCP project: agentic-librarian-prod (us-central1) ──────────────┐
│                                                                  │
│  Artifact Registry ──► Cloud Run service "librarian-api"         │
│  (docker images)        FastAPI: /health /health/db              │
│                         /history /works                          │
│                         ingress: IAM-auth only (no public)       │
│                         min 0 / max 1 instances, 512Mi           │
│                            │ unix socket (built-in connector)    │
│                            ▼                                     │
│  Secret Manager ──────► Cloud SQL Postgres 16 + pgvector         │
│  (db password)          db-f1-micro, 10GB SSD, ~$12/mo           │
│                            ▲ one-time import                     │
│  Cloud Storage bucket ─────┘                                     │
│  (FINAL pg_dump staging)                                         │
│                                                                  │
│  Billing budget: $25/mo, alerts 50/90/100%                       │
└──────────────────────────────────────────────────────────────────┘
```

### Resources

| Resource | Job | Cost |
|---|---|---|
| Project `agentic-librarian-prod` | Blast radius + bill line for everything prod | $0 |
| Cloud SQL (Postgres 16, `db-f1-micro`, 10 GB SSD) | The catalog; `pgvector` enabled via `CREATE EXTENSION vector` | ~$12/mo |
| Cloud Run `librarian-api` | Runs the prod image; scale-to-zero; max 1 instance caps cost | ~$0 at this traffic |
| Artifact Registry | Image store; tags = git SHAs | cents |
| Secret Manager | DB password, injected into Cloud Run as env var **by reference** | cents |
| GCS bucket | pg_dump staging for `gcloud sql import sql` | cents |

### Service accounts (least privilege)

- **Runtime SA** (`librarian-api-runtime`): what the Cloud Run service runs as. Grants:
  Secret Manager accessor on the one DB-password secret + Cloud SQL Client. Nothing else.
- **Deployer SA** (`github-deployer`): impersonated by GitHub Actions via WIF. Grants:
  Artifact Registry writer + Cloud Run deployer + actAs on the runtime SA. Cannot read
  secrets or the DB — a compromised CI cannot reach data.
- **WIF pool**: trusts GitHub's OIDC issuer, with a condition pinning tokens to the
  `jaydee829/agentic_librarian` repo. GitHub secrets hold only non-sensitive IDs
  (project number, SA email). No stored keys anywhere.

### Connectivity

App → DB via Cloud Run's built-in Cloud SQL connection (Unix socket mounted at
`/cloudsql/PROJECT:REGION:INSTANCE`; encrypted, no IPs, no firewall rules). The service
gets a single env var:

```
DATABASE_URL=postgresql://librarian:<from-secret>@/agentic_librarian?host=/cloudsql/...
```

`DatabaseManager` already supports `DATABASE_URL` — but currently raises on missing
`POSTGRES_USER`/`POSTGRES_PASSWORD` *before* checking it, making the override
unreachable on its own. **Fix (TDD):** check `DATABASE_URL` first; fall back to
component vars. The password portion is composed at deploy time from the Secret Manager
reference (Cloud Run `--set-secrets`), never present in code, repo, image, or CI logs.

## Repo changes

1. **Port the API scaffold** from `origin/13-phase-4-web-interface-and-analysis` onto
   the Lift 0 branch — `src/agentic_librarian/api/main.py` (54 lines: `/health`,
   `/health/db`, `GET /history`) plus its two test files, adapted to current `main`
   (model/session imports are unchanged). The `conductor/` scaffolding on that branch is
   NOT ported. After this merges, delete the legacy branch — its cargo is on `main`.
2. **`GET /works`** — read-only catalog listing: `id, title, authors, tropes, styles,
   narrative_style` per work, ordered by title, with `limit` (default 50, max 200) and
   `offset` query params. Mirrors `/history`'s eager-loading pattern.
3. **`Dockerfile.api`** — new production image, dev `Dockerfile` untouched:
   `python:3.11-slim`, prod deps only (no build-essential/Node/Claude CLI/sudo),
   non-editable install, non-root user, `EXPOSE 8080`, entrypoint
   `uvicorn agentic_librarian.api.main:app --host 0.0.0.0 --port $PORT`.
4. **`pyproject.toml`** — `fastapi` and `uvicorn` join the main dependency list (the
   API is core surface now, not an extra).
5. **`DatabaseManager`** — the `DATABASE_URL`-priority reorder above.
6. **Untouched:** agents, MCP server, Dagster, MLflow, CLI. The prod image carries the
   code but the service serves only the four endpoints; the mesh deploys in Lift 2
   inside this same container. (The dump includes dev MLflow tracking tables — they
   ride along in the restore harmlessly; prod never reads them.)

## CD pipeline — `.github/workflows/deploy.yml`

- **Trigger:** push to `main`, path-filtered to `src/**`, `pyproject.toml`,
  `Dockerfile.api`, and the workflow file itself; plus `workflow_dispatch` for manual
  redeploys. Docs-only merges deploy nothing.
- **Concurrency:** `deploy-prod` group — near-simultaneous merges queue, never race.
- **Jobs:**
  1. **Test** — fast suite (`-m "not api_dependent and not slow"`), same gate PRs face.
  2. **Build & push** — build `Dockerfile.api`; smoke-test inside the runner (run the
     container, curl `/health`) so a broken image never reaches the registry; tag with
     the git SHA (traceable deploys, instant rollback targets); push to Artifact
     Registry.
  3. **Deploy** — `gcloud run deploy librarian-api --image …:SHA`. Cloud Run keeps
     traffic on the old revision until the new one is listening — failed deploys don't
     take the service down.
  4. **Smoke test** — mint an identity token, curl `/health` and `/health/db` on the
     live URL. Green means the new revision serves AND reaches the DB.

## Data restore (one-time, scripted)

1. **Create role + database first**: the dump contains `ALTER ... OWNER TO librarian`
   statements; create the `librarian` user (password → Secret Manager) and the
   `agentic_librarian` database before importing, or the import fails midway. Inspect
   the dump's first ~100 lines beforehand for other surprises (dumped from
   `pgvector/pgvector:pg16`, so versions align with Cloud SQL Postgres 16).
2. **Enable pgvector**: `CREATE EXTENSION vector;` (on Cloud SQL's supported list).
3. **Upload & import**: copy `data/backups/agentic_librarian_FINAL_20260605_014912.sql.gz`
   (8.5 MB) to the GCS bucket; `gcloud sql import sql` ingests `.gz` directly.
4. **On failure**: drop the database, fix, re-run. The prod DB is
   rebuildable-from-backup until verification passes.

## Verification — `infra/verify_restore.py`

Asserts, in order:
- Row counts match the known build: **326 works / 335 editions / 331 reading_history /
  230 authors**
- `vector` extension present; embedding column non-null count matches expected
- A live similarity query (`ORDER BY embedding <=> …`) returns sensibly ordered results
  — the operator works in Cloud SQL, not just that bytes arrived
- End-to-end through the deployed service with an identity token: `/works` → 326,
  `/history` → 331

## Provisioning artifacts

- `infra/` — numbered `gcloud` scripts (project + APIs, Cloud SQL, secrets, Artifact
  Registry + bucket, service accounts + WIF, Cloud Run first deploy, budget), each
  safe to re-run, each reviewed in PR like code.
- `docs/runbooks/gcp-walking-skeleton.md` — numbered sections matching the scripts:
  what each creates, why it exists, what it costs, how to tear it down. Written for
  you-in-six-months.

## Error handling

- **Deploy failures:** old revision keeps serving (Cloud Run rollout semantics);
  workflow goes red; rollback = redeploy a previous SHA tag.
- **DB unreachable:** `/health/db` reports it distinctly from app death (`/health`).
- **Restore failures:** drop-and-rerun procedure above; nothing depends on the prod DB
  until verification passes.
- **Cost anomalies:** budget alerts at $12.50 / $22.50 / $25 (50/90/100% of $25).

## Testing

1. **Unit/integration (offline):** ported scaffold tests + `/works` tests (db_integration
   marker, isolated test DB): content shape, ordering, pagination caps; the
   `DATABASE_URL`-priority test for `DatabaseManager`.
2. **CI:** in-runner image build + `/health` curl gate before push.
3. **Live (user-gated, with the user watching — their GCP account and billing):**
   provisioning run, restore, first deploy, `verify_restore.py`, smoke tests.

## Out of scope (deferred on purpose)

- Custom domain (`*.run.app` URL until Lift 2), agents/LLM keys in prod (Lift 2),
  Firebase Auth (Lift 1), monitoring beyond health endpoints + budget alerts,
  Terraform (~Lift 3), multi-region, the bulk-enrichment service (DEBT-001).
