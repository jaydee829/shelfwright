# Lift 2 Stage 4 — Rollout Runbook

Takes the friends-and-family beta live: opens the Cloud Run IAM gate, provisions async
enrichment, applies the first prod write-path migration, and verifies the live stack.

**Ordering is load-bearing (spec D2):** merging PR-B's deploy opens the gate *and* ships the
SPA/chat-reachable image at once. So **provision + migrate while the gate is still closed**,
then merge PR-B as the deliberate gate-opening act.

**Prereqs:** PR-A merged; PR-B reviewed/approved and **held** (not merged); `gcloud` authed to
`agentic-librarian-prod`; cloud-sql-proxy available.

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
- `gcloud sql export sql librarian-sql gs://agentic-librarian-prod-backups/pre-stage4-$(date +%Y%m%d).sql.gz --database=agentic_librarian`
- This rollout is the first prod write (chat). From here: **back up before every migration.**

## 3. Apply the migration (gate still CLOSED)
- **First confirm the starting point:** with cloud-sql-proxy up, `alembic current` MUST show
  `c804d02d6fbb` (the Lift 1 head). If it shows `30f1e46533e9` already, the migration is done —
  skip the upgrade. If it shows anything else, STOP and investigate before proceeding.
- Start cloud-sql-proxy to the prod instance; run `alembic upgrade head` via the docker wrapper
  (Lift 1 runbook pattern). This moves prod from the Lift 1 head `c804d02d6fbb` to the Stage 1
  head `30f1e46533e9` (`conversations`, `messages`, `usage.conversation_id` FK).
- Verify: `alembic current` shows `30f1e46533e9`; the `conversations`/`messages` tables exist.

## 4. Sanity (gate still CLOSED)
- The current (old) image is still healthy: minted-IAM-token `GET /health` returns ok.

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
