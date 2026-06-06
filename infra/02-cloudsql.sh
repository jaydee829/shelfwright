#!/usr/bin/env bash
# Cloud SQL: Postgres 16, smallest shared-core tier (~$12/mo), 10GB SSD.
# Takes ~10 minutes — go make coffee.
set -euo pipefail
source "$(dirname "$0")/00-config.sh"

gcloud sql instances create "${SQL_INSTANCE}" \
  --database-version=POSTGRES_16 \
  --edition=enterprise \
  --tier=db-f1-micro \
  --region="${REGION}" \
  --storage-size=10GB \
  --storage-type=SSD

gcloud sql databases create "${DB_NAME}" --instance="${SQL_INSTANCE}"

echo "Connection name (needed for the GCP_CLOUDSQL_CONNECTION GitHub variable):"
gcloud sql instances describe "${SQL_INSTANCE}" --format='value(connectionName)'
