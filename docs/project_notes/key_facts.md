# Key Project Facts

This file tracks important project configuration, constants, and environment details.

## Project Overview
- **Project Name**: agentic_librarian
- **Description**: An agentic system for processing reading history and providing personalized book recommendations.

## Local Development
- **OS**: Windows host + WSL2; the dev container is Debian (`python:3.11`).
- **Primary Workflow**: VS Code **Dev Container** (compose-integrated) on a WSL2 clone of the repo. "Reopen in Container" builds the `app` image and starts `db` (pgvector) and `mlflow` together on one Docker network (see `docker-compose.yml` / `.devcontainer/devcontainer.json`, PR #15).
  - **Setup**: `cp .env.example .env` and fill in the DB password + API keys *before* opening the container.
  - **Execution Rule**: Run commands (`pytest`, `ruff`, `dagster dev`) **directly** inside the container. Dependencies are installed `--system` via `uv` (editable `-e ".[dev]"`); there is **no conda env** in the container.
  - **DB host**: inside the container the app reaches Postgres at host `db`; from the Windows/WSL host use `localhost` against the published port.
- **Legacy (deprecated)**: An earlier machine used a Conda env `agentic_librarian` with `conda run -n agentic_librarian <cmd>`. This does **not** apply in the devcontainer. (Docs paths referencing `Justin.Merrick` are from that machine.)

## Technology Stack
- **Database**: PostgreSQL with `pgvector` (1536 dims for tropes)
- **MLOps**: DVC (Data Versioning), MLFlow (Experiment Tracking), Dagster (Orchestration)
  - **Dagster Assets**:
    - `raw_history`: CSV loading and cleaning via `HistoryIngestor`.
    - `enriched_metadata`: Metadata enrichment via `ScoutManager`.
    - `vectorized_tropes`: Trope standardization and vectorization via `TropeManager`.
  - **Dagster Resources**:
    - `db_manager`: Provides SQLAlchemy sessions to assets.
  - **Dagster Partitions**:
    - `csv_files`: Dynamic partitions based on raw CSV filenames.
- **Interface**: Web UI (Vite/React/Next.js), FastAPI (Backend)
- **AI/LLM**: `google-genai` (Gemini), Google AI Agent SDK, LangChain
- **Protocols**: MCP (Data Access), A2A (Agent Mesh)
  - **Data Access Strategy**: Hybrid (Direct ORM for Flow 1 Ingest; Coarse-Grained MCP for Flow 2 Agents).
  - **Agent Mesh**: 4-Agent Specialist Model (Librarian, Analyst, Explorer, Critic).
- **Testing**: Pytest (Unit, Integration), Playwright (E2E)
- **Containerization**: Docker, Docker Compose
  - **Ports**:
    - Postgres: `5432` (Bound to `127.0.0.1`)
    - MLFlow: `5000` (Bound to `127.0.0.1`)
- **Configuration**:
  - `python-dotenv` loads `.env` files.
  - `DB_SSL_MODE`: Support for `sslmode` in SQLAlchemy (e.g., `require`).

## Core Entities
- **Authors**: Bio, JSONB style attributes (pacing, tone, style).
- **Works**: Metadata, Genres, Moods, Tropes (Vectorized).
- **Editions**: ISBN, Format, Page/Audio length.
- **Narrators**: JSONB style attributes (voice diff, accent, etc.).
- **ReadingHistory**: User ratings, notes, completion dates.
- **Suggestions**: Log of agent recommendations with justifications.

## Data Ingestion Assumptions
- **Chronological Density**: The system assumes that raw CSV rows are chronologically clustered.
- **Year Inference**: Dates missing a year (e.g., `4-Jan`) are contextually inferred from the nearest unambiguous date (e.g., `1/7/2020`) using forward and backward fills.
- **Reference Date**: If no contextual year is found, the system defaults to the current year.
- **History source of truth (2026-06-05)**: the DATABASE. Single-title adds happen via
  `add_book_to_history` (conversationally or `librarian add`) and do NOT update the
  DVC-tracked CSVs — accepted drift; `pg_dump` snapshots are the backup. Bulk imports
  still go through the CSV/Dagster path. Reading history is a log of READ EVENTS: a
  re-read inserts a new row (re-read count = rows per work).

## Production (GCP — Lift 0 live 2026-06-06; Lift 1 multi-user live 2026-06-07)
- **Project**: `agentic-librarian-prod` (us-central1); ADR-047; runbook
  `docs/runbooks/gcp-walking-skeleton.md`; provisioning scripts in `infra/`.
- **Service URL**: <https://librarian-api-hnucndzntq-uc.a.run.app> — Cloud Run
  `librarian-api`, IAM-gated (`--no-allow-unauthenticated`); call with
  `Authorization: Bearer $(gcloud auth print-identity-token)` or
  `gcloud run services proxy librarian-api --region us-central1`.
- **Endpoints**: `/health` (open); `/health/db`, `/history` (caller-scoped), `/works`
  (shared catalog) — all Firebase-gated (Lift 1, ADR-048). `SIGNUP_MODE=invite` on the
  service; invites via `librarian user invite <email>` (runbook
  `docs/runbooks/lift1-multi-user-rollout.md`). Operator account (user #1,
  `jaydee829@gmail.com`) claimed via Google sign-in 2026-06-07; token-minting for
  live tests needs `FIREBASE_WEB_API_KEY` + `FIREBASE_SERVICE_ACCOUNT_ID` (runbook §1).
- **Database**: Cloud SQL Postgres 16 `librarian-sql` (db-f1-micro, 10GB SSD) +
  pgvector; restored 2026-06-06 from `agentic_librarian_FINAL_20260605_014912.sql.gz`
  and verified (326 works / 335 editions / 331 reading_history / 230 authors; 556
  tropes + 508 styles fully embedded). App connects via the `librarian-db-url` secret
  (full `DATABASE_URL`, Cloud SQL unix socket); schema managed by Alembic (Lift 1+).
  Nightly automated backups (09:00 UTC) + 7-day PITR enabled 2026-07-12 (GH #91; codified in
  `infra/02-cloudsql.sh`).
  Engine pools: `pool_pre_ping` + 30-min recycle, 5+2 per engine, one shared engine per API process (GH #102/#94) — 2 instances × 7 = 14 of db-f1-micro's ~25 connections; #123 landed (embeds warmed into the LRU before write/search sessions, so overflow tightened from 5).
  5 unique indexes (authors/narrators case-insensitive name, editions work+format, reading_history, active suggestions), 10 FK indexes, 13 timestamptz columns, and `works.deep_enriched_at` live as of the Phase 6.3 migration (`48e3762d6c0c`, GH #95/#97/#108/#109) — see ADR-060 and `docs/runbooks/phase6-3-schema-rollout.md`.
- **Deploys**: automatic on merge to `main` touching `src/**`/`pyproject.toml`/
  `Dockerfile.api` (`.github/workflows/deploy.yml`, WIF keyless); manual redeploy via
  the Actions tab (`workflow_dispatch`). Images in Artifact Registry, tags = git SHAs.
  deploy.yml pins `--memory=2Gi` (ADR-051/GH #89 — was 512Mi drift) and the lifespan migration
  guard (ADR-058) fails the revision if prod's alembic_version is behind the image's head.
- **Async enrichment scaling** (ADR-051, tuned 2026-06-23 after an OOM storm on the first real bulk
  import): Cloud Run `librarian-api` memory = **2Gi** (was 512Mi — too small for the deep LLM scouts);
  Cloud Tasks `librarian-enrich` queue = **max-concurrent-dispatches=4 / max-dispatches-per-second=5**
  (was 1000/500). `librarian-import` queue stays 5/2. Deep tasks share ONE instance (Cloud Run won't
  scale out below its concurrency target of 80), so budget **~½ GiB per concurrent deep scout**; the
  queue's concurrency is the tuning knob (NEVER lower service-wide `containerConcurrency`). **Preserve
  these on any redeploy/queue edit or the OOM 503-storm returns.**
- **Cost guardrail**: $25/mo budget, email alerts at 50/90/100% (~$12–16/mo expected).

## Frontend Theming (light/dark)
- **Design tokens**: semantic CSS custom properties live in `frontend/src/index.css` —
  `:root` holds the LIGHT palette (values equal the pre-theme colors, so light is unchanged),
  `:root[data-theme="dark"]` overrides for dark. All component CSS uses `var(--token)` (no
  hardcoded hex outside `index.css`). Text-on-color is split by role: `--on-accent` / `--on-danger`
  (dark text in dark mode) vs `--on-badge` (white in both — badges stay medium-dark) for WCAG contrast.
- **Theme control** (`frontend/src/theme.ts`): on load, resolve `localStorage['theme']` else the OS
  `prefers-color-scheme`; `setTheme` sets `data-theme` on `<html>` + persists (storage/matchMedia in
  try/catch). `main.tsx` applies it before render (no flash). ☀/🌙 toggle in the TopBar.
- This token layer is the **foundation for "Visual Identity v2"** (the planned redesign extends it with
  a real palette + type/spacing scales + component restyle).

## CI/CD note (deploy anomaly, 2026-06-17, RESOLVED — durable fix applied 2026-07-12)
- Root cause (diagnosed 2026-06-22, bugs.md): `[skip ci]` from branch commits leaked into squash-merge
  bodies, and GitHub skips ALL push workflows when a skip directive appears anywhere in the HEAD commit
  message. **Durable fix applied 2026-07-12 (GH #90): repo setting → squash title = PR title, message =
  blank** — commit bodies no longer enter merge commits. Recovery for an already-skipped merge remains
  manual `workflow_dispatch` of "Deploy to Cloud Run".
- The related #89 drift (deploy.yml pinning 512Mi) was fixed the same day: deploy.yml now pins
  `--memory=2Gi` (see the Deploys bullet above).

## Security Guidelines
- **DO NOT** store real passwords or secrets here.
- **DO NOT** store PII.
- Use environment variables or secret managers for sensitive info.
