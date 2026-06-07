# Runbook: Lift 1 multi-user rollout

Operator-run, after PR approval but BEFORE merging (the migration must land before
the auto-deploy). Lift 0's runbook (`gcp-walking-skeleton.md`) §7 has the proxy + ADC
setup this reuses. Executed live 2026-06-07; corrections from that run are folded in.

**Where each section runs** (live-run lesson — the context changes per section):

| Section | Runs in |
|---|---|
| §1 Firebase console + IAM | browser + plain WSL shell (gcloud) |
| §2 dev rehearsal | dev container (alembic); psql via `docker exec` into the **db** container |
| §3 prod migration | **plain WSL shell** — gcloud/proxy/sed host-side; alembic docker-wrapped |
| §4 merge | GitHub |
| §5 live verification | plain WSL shell (docker-wrapped token helper + curl) |
| §6 invites | plain WSL shell, docker-wrapped |

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
3. **Register a web app** (live-run lesson: the Web API key does NOT appear until an
   app exists — and don't confuse it with Cloud Messaging's Web Push keys/sender ID):
   Project settings (gear) → General → Your apps → **Add app** → Web (`</>`), any
   nickname, skip Hosting. The shown `firebaseConfig.apiKey` (an `AIza...` string) is
   the **Web API Key**; it also now appears on the General tab. Export it:
   `export FIREBASE_WEB_API_KEY=<key>` (an identifier, not a secret — but don't
   commit it; if you ever add API restrictions to it, keep Identity Toolkit allowed).
4. **Token-minting prerequisites** (live-run lesson: `create_custom_token` must SIGN,
   and user-credential ADC has no private key — the helper signs via the IAM
   Credentials API as the firebase-adminsdk service account):

    ```bash
    gcloud services enable iamcredentials.googleapis.com --project agentic-librarian-prod
    export FIREBASE_SERVICE_ACCOUNT_ID=firebase-adminsdk-fbsvc@agentic-librarian-prod.iam.gserviceaccount.com
    gcloud iam service-accounts add-iam-policy-binding "$FIREBASE_SERVICE_ACCOUNT_ID" \
      --member="user:jaydee829@gmail.com" --role="roles/iam.serviceAccountTokenCreator" \
      --project agentic-librarian-prod
    ```

   (`serviceAccountTokenCreator` is deliberately excluded from Owner. Confirm the SA
   email with `gcloud iam service-accounts list` — the suffix may differ. IAM grants
   can take ~a minute to propagate.)

## §2 Rehearse the migration on the DEV database

The dev DB carries the same data prod was restored from — a free rehearsal. **Run
from:** the dev container (or a throwaway app container on the compose network).

**Orphaned `alembic_version` (live-run lesson):** the FINAL dump contains a stale
`alembic_version` row (`7d34483879f0`, from a pre-dump-era experiment) — so BOTH the
dev DB and anything restored from the dump (prod included) carry it, and `alembic
stamp` fails with `Can't locate revision identified by '7d34483879f0'`. Clear it
first (it is a one-row bookmark; deleting it is safe):

```bash
# from the dev container — for prod, run the same DELETE through the §3 docker wrapper
python -c 'from sqlalchemy import create_engine, text; from agentic_librarian.db.session import resolve_database_url; e = create_engine(resolve_database_url()); c = e.connect(); t = c.begin(); r = c.execute(text("DELETE FROM alembic_version")); t.commit(); print("cleared", r.rowcount, "row(s)"); c.close()'
```

Then:

```bash
alembic history                      # note the BASELINE revision id (the LAST entry — history prints newest first)
alembic stamp <baseline-rev>         # dev DB already has the baseline schema
alembic upgrade head
```

Verify — `psql` lives in the **db** container, not the app container:

```bash
# from a plain WSL shell (find the name with: docker ps --format '{{.Names}}' | grep db)
docker exec -it agentic_librarian-db-1 psql -U librarian -d agentic_librarian -c '\dt' \
  -c "SELECT count(*) AS null_user_rows FROM reading_history WHERE user_id IS NULL;" \
  -c "SELECT email, firebase_uid, display_name FROM users;"
# pass: users/usage/user_credentials listed; null_user_rows = 0; one row jaydee829@gmail.com
```

## §3 Migrate PROD

**Run from:** a plain WSL shell at `~/agentic_librarian` (NOT the dev container —
gcloud and the proxy binary live on the WSL host from the Lift 0 run).

1. Start the proxy (Lift 0 runbook §7): `./cloud-sql-proxy --port 5433 <CONNECTION_NAME>`
   — it sits on a blank line when serving. **Confirm it is actually listening**
   (live-run lesson — "connection refused" from the containers means it isn't):

    ```bash
    ss -tln | grep 5433     # no output = proxy not running; 127.0.0.1:5433 = loopback-bound
    ```

   If containers still can't reach it, restart with `--address 0.0.0.0` (exposes the
   port on your WSL interfaces for the session — Ctrl-C it when done; the proxy still
   requires IAM auth behind it).
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

5. **Verify prod** (live-run lesson — this step was missing; same wrapper, python
   instead of psql):

    ```bash
    docker run --rm -v "$PWD":/app -w /app --add-host=host.docker.internal:host-gateway \
      -e DATABASE_URL="$PROD_DB_URL" agentic_librarian-app:latest \
      python -c 'from sqlalchemy import create_engine, text, inspect; from agentic_librarian.db.session import resolve_database_url; e = create_engine(resolve_database_url()); i = inspect(e); print("multi-user tables:", [t for t in ("users","usage","user_credentials") if t in i.get_table_names()]); c = e.connect(); print("null user_id rows:", c.execute(text("SELECT count(*) FROM reading_history WHERE user_id IS NULL")).scalar()); print("users:", c.execute(text("SELECT email, firebase_uid IS NULL FROM users")).fetchall()); c.close()'
    # pass: all three tables; null user_id rows: 0; users: [('jaydee829@gmail.com', True)]
    ```

## §4 Merge → auto-deploy

Merge the PR. `.github/workflows/deploy.yml` deploys with `SIGNUP_MODE=invite` and
`GOOGLE_CLOUD_PROJECT` set. Watch the Actions run: the smoke now asserts `/health` 200
AND 401-enforcement on `/history`.

## §5 Verify live (the real-Firebase acceptance, spec §2)

**Run from:** a plain WSL shell with `FIREBASE_WEB_API_KEY` and
`FIREBASE_SERVICE_ACCOUNT_ID` (§1) exported — BOTH token commands need both vars.

```bash
# ADC if not already done: gcloud auth application-default login
TOKEN=$(docker run --rm -v "$PWD":/app -w /app \
  -v "$HOME/.config/gcloud:/home/appuser/.config/gcloud" \
  -e GOOGLE_CLOUD_PROJECT=agentic-librarian-prod -e FIREBASE_WEB_API_KEY -e FIREBASE_SERVICE_ACCOUNT_ID \
  agentic_librarian-app:latest python infra/get_firebase_token.py jaydee829@gmail.com)
IAM="$(gcloud auth print-identity-token)"
URL="https://librarian-api-hnucndzntq-uc.a.run.app"

# 1. no Firebase token → 401
curl -s -o /dev/null -w '%{http_code}\n' -H "X-Serverless-Authorization: Bearer $IAM" "$URL/history"   # 401
# 2. your token → 200 + YOUR 331 read events (claim-by-email links user #1 on this first call)
curl -s -H "X-Serverless-Authorization: Bearer $IAM" -H "Authorization: Bearer $TOKEN" "$URL/history" | head -c 400
# 3. a non-invited account → 403
TOKEN2=$(docker run --rm -v "$PWD":/app -w /app -v "$HOME/.config/gcloud:/home/appuser/.config/gcloud" \
  -e GOOGLE_CLOUD_PROJECT=agentic-librarian-prod -e FIREBASE_WEB_API_KEY -e FIREBASE_SERVICE_ACCOUNT_ID \
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
- **`metadata.google.internal` resolution error from the token helper** — the SDK is
  trying to sign via the GCP metadata server because it has no signing identity:
  `FIREBASE_SERVICE_ACCOUNT_ID` isn't set/passed (`-e` on the docker run) or the §1
  step-4 prerequisites (iamcredentials API + tokenCreator grant) are missing.
- **`Can't locate revision identified by '7d34483879f0'`** — the orphaned
  `alembic_version` row from the FINAL dump (see §2). Any future restore from that
  dump re-imports it; clear before stamping.
