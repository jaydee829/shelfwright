#!/usr/bin/env bash
# Create the app DB user with a generated password, and store the FULL connection
# string in Secret Manager (Cloud Run --set-secrets injects it verbatim as DATABASE_URL).
# The password never touches disk or shell history beyond this process.
set -euo pipefail
source "$(dirname "$0")/00-config.sh"

DB_PASSWORD="$(openssl rand -hex 24)"  # 48 hex chars: fixed length, URL-safe (it lands inside DATABASE_URL)
gcloud sql users create "${DB_USER}" --instance="${SQL_INSTANCE}" --password="${DB_PASSWORD}"

CONNECTION_NAME="$(gcloud sql instances describe "${SQL_INSTANCE}" --format='value(connectionName)')"
DATABASE_URL="postgresql://${DB_USER}:${DB_PASSWORD}@/${DB_NAME}?host=/cloudsql/${CONNECTION_NAME}"

printf '%s' "${DATABASE_URL}" | gcloud secrets create "${SECRET_NAME}" --data-file=-

echo "Secret ${SECRET_NAME} created. The password exists ONLY inside it."
