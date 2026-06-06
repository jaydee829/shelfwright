#!/usr/bin/env bash
# Upload the FINAL pg_dump and import it into Cloud SQL.
# PRE-FLIGHT (runbook §6): inspect the dump and ensure the vector extension exists —
#   zcat data/backups/${DUMP_FILE} | head -100
#   If 'CREATE EXTENSION ... vector' is NOT in the dump, create it first via:
#   gcloud sql connect librarian-sql --user=postgres --database=agentic_librarian
#   then: CREATE EXTENSION IF NOT EXISTS vector;
# The 'librarian' role must already exist (03-db-user-secret.sh) — the dump's
# ALTER ... OWNER TO librarian statements fail without it.
set -euo pipefail
source "$(dirname "$0")/00-config.sh"

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
gcloud storage cp "${REPO_ROOT}/data/backups/${DUMP_FILE}" "${BUCKET}/"

# Cloud SQL imports run as the instance's own service agent — it needs to read the bucket.
SQL_SA="$(gcloud sql instances describe "${SQL_INSTANCE}" --format='value(serviceAccountEmailAddress)')"
gcloud storage buckets add-iam-policy-binding "${BUCKET}" \
  --member="serviceAccount:${SQL_SA}" --role="roles/storage.objectViewer"

gcloud sql import sql "${SQL_INSTANCE}" "${BUCKET}/${DUMP_FILE}" --database="${DB_NAME}" --quiet

echo "Import complete. Now verify: infra/verify_restore.py (runbook §7)."
