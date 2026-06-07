# Runbook: Lift 1 multi-user rollout

Operator-run, from the WSL clone (`~/agentic_librarian`), after PR approval but
BEFORE merging (the migration must land before the auto-deploy). Lift 0's runbook
(`gcp-walking-skeleton.md`) §7 has the proxy + ADC setup this reuses.

**No pre-migration backup for Lift 1** (spec §6): prod is byte-identical to
`agentic_librarian_FINAL_20260605_014912.sql.gz` (the Lift 0 API is read-only) and that
dump exists in the WSL clone, the Windows clone, and `gs://agentic-librarian-prod-backups`.
**This reasoning expires at prod's first write — from Lift 2 onward, `gcloud sql export`
before every migration.**

## §1 Firebase console setup (one-time, manual)

1. <https://console.firebase.google.com> → **Add project** → select the EXISTING
   project `agentic-librarian-prod` (Firebase is a console layer over the GCP project —
   no new project, no new billing).
2. Build → **Authentication** → Get started → Sign-in method → enable **Google**.
3. Project settings (gear) → General → note the **Web API Key** → export it in your
   WSL shell: `export FIREBASE_WEB_API_KEY=<key>` (an identifier, not a secret — but
   don't commit it).

## §2 Rehearse the migration on the DEV database

The dev DB carries the same data prod was restored from — a free rehearsal. From the
dev container (or a throwaway app container on the compose network):

```bash
alembic history                      # note the BASELINE revision id (the LAST entry — history prints newest first)
alembic stamp <baseline-rev>         # dev DB already has the baseline schema
alembic upgrade head
# verify: psql → \dt shows users/usage/user_credentials; then:
#   SELECT count(*) FROM reading_history WHERE user_id IS NULL;   -- must be 0
#   SELECT email FROM users;                                      -- jaydee829@gmail.com
```

## §3 Migrate PROD

1. Start the proxy (Lift 0 runbook §7): `./cloud-sql-proxy --port 5433 <CONNECTION_NAME>`
2. Build the proxy-routed DATABASE_URL from the secret (same sed as Lift 0 §7,
   container-routed so the URL targets host.docker.internal):

    ```bash
    export PROD_DB_URL="$(gcloud secrets versions access latest --secret=librarian-db-url \
      | sed -E 's#@/agentic_librarian\?host=.*#@host.docker.internal:5433/agentic_librarian#')"
    ```

3. Stamp + upgrade, routed through the app container (WSL python has no deps —
   live-run lesson 2026-06-06):

    ```bash
    docker run --rm -v "$PWD":/app -w /app --add-host=host.docker.internal:host-gateway \
      -e DATABASE_URL="$PROD_DB_URL" agentic_librarian-app:latest alembic stamp <baseline-rev>
    docker run --rm -v "$PWD":/app -w /app --add-host=host.docker.internal:host-gateway \
      -e DATABASE_URL="$PROD_DB_URL" agentic_librarian-app:latest alembic upgrade head
    ```

4. The OLD deployed code keeps serving its read-only GETs against the migrated schema —
   no downtime window to manage.

## §4 Merge → auto-deploy

Merge the PR. `.github/workflows/deploy.yml` deploys with `SIGNUP_MODE=invite` and
`GOOGLE_CLOUD_PROJECT` set. Watch the Actions run: the smoke now asserts `/health` 200
AND 401-enforcement on `/history`.

## §5 Verify live (the real-Firebase acceptance, spec §2)

```bash
# ADC if not already done: gcloud auth application-default login
TOKEN=$(docker run --rm -v "$PWD":/app -w /app \
  -v "$HOME/.config/gcloud:/home/appuser/.config/gcloud" \
  -e GOOGLE_CLOUD_PROJECT=agentic-librarian-prod -e FIREBASE_WEB_API_KEY \
  agentic_librarian-app:latest python infra/get_firebase_token.py jaydee829@gmail.com)
IAM="$(gcloud auth print-identity-token)"
URL="https://librarian-api-hnucndzntq-uc.a.run.app"

# 1. no Firebase token → 401
curl -s -o /dev/null -w '%{http_code}\n' -H "X-Serverless-Authorization: Bearer $IAM" "$URL/history"   # 401
# 2. your token → 200 + YOUR 331 read events (claim-by-email links user #1 on this first call)
curl -s -H "X-Serverless-Authorization: Bearer $IAM" -H "Authorization: Bearer $TOKEN" "$URL/history" | head -c 400
# 3. a non-invited account → 403
TOKEN2=$(docker run --rm -v "$PWD":/app -w /app -v "$HOME/.config/gcloud:/home/appuser/.config/gcloud" \
  -e GOOGLE_CLOUD_PROJECT=agentic-librarian-prod -e FIREBASE_WEB_API_KEY \
  agentic_librarian-app:latest python infra/get_firebase_token.py stranger-test@example.com)
curl -s -o /dev/null -w '%{http_code}\n' -H "X-Serverless-Authorization: Bearer $IAM" \
  -H "Authorization: Bearer $TOKEN2" "$URL/history"   # 403
# 4. /health/db with your token → {"status": "connected"} (replaces the CD smoke's DB check)
curl -s -H "X-Serverless-Authorization: Bearer $IAM" -H "Authorization: Bearer $TOKEN" "$URL/health/db"
# 5. live pytest variant (optional, same proof):
#    FIREBASE_TEST_ID_TOKEN="$TOKEN" pytest test/live -m live   (in-container)
```

## §6 Inviting a friend (when the time comes)

```bash
docker run --rm -v "$PWD":/app -w /app --add-host=host.docker.internal:host-gateway \
  -e DATABASE_URL="$PROD_DB_URL" agentic_librarian-app:latest \
  librarian user invite friend@example.com --name "Friend"
```

## Account recovery (manual, rare)

Claim-by-email is ONE-SHOT: if a friend deletes/recreates their Firebase account (new
uid, same email), sign-in 403s by design (uid rotation must not re-claim accounts). To
recover a genuinely lost account, NULL the stored uid so the next verified sign-in
re-claims it:

```sql
UPDATE users SET firebase_uid = NULL WHERE email = 'friend@example.com';
```

## Troubleshooting

- **Two-token collision:** Cloud Run's IAM gate normally reads `Authorization`; when
  `X-Serverless-Authorization` is present it uses THAT and passes `Authorization`
  through to the app. Both headers, always, until Lift 2 opens the gate.
- **403 on YOUR first call:** decode the token (jwt.io) — claim-by-email needs `email`
  + `email_verified: true`. If the custom-token exchange didn't include them, the
  helper sets email_verified on the user record; re-mint and inspect again (REC-019).
- **`gcloud run services proxy` clobbers Authorization** — don't use it for Firebase
  testing; use the two-header curl form.
