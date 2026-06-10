#!/usr/bin/env bash
# Lift 2 Stage 4: create the three deep-scout key secrets from the operator's environment
# (never hardcoded), add the first version, and grant the runtime SA read access.
# Export the three source vars before running, e.g.:
#   export GOOGLE_SEARCH_API_KEY=... GOOGLE_BOOKS_API_KEY=... HARDCOVER_API_KEY=...
# One-shot (fails loud on ALREADY_EXISTS).
set -euo pipefail
source "$(dirname "$0")/00-config.sh"

: "${GOOGLE_SEARCH_API_KEY:?export GOOGLE_SEARCH_API_KEY before running}"
: "${GOOGLE_BOOKS_API_KEY:?export GOOGLE_BOOKS_API_KEY before running}"
: "${HARDCOVER_API_KEY:?export HARDCOVER_API_KEY before running}"

create_secret () {
  local name="$1" value="$2"
  gcloud secrets create "${name}" --replication-policy="automatic"
  printf '%s' "${value}" | gcloud secrets versions add "${name}" --data-file=-
  gcloud secrets add-iam-policy-binding "${name}" \
    --member="serviceAccount:${RUNTIME_SA}" \
    --role="roles/secretmanager.secretAccessor"
}

create_secret "${SECRET_GOOGLE_SEARCH}" "${GOOGLE_SEARCH_API_KEY}"
create_secret "${SECRET_GOOGLE_BOOKS}"  "${GOOGLE_BOOKS_API_KEY}"
create_secret "${SECRET_HARDCOVER}"     "${HARDCOVER_API_KEY}"

echo "Prod key secrets created and granted to ${RUNTIME_SA}."
