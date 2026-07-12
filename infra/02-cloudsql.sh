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
  --storage-type=SSD \
  --backup-start-time=09:00 \
  --enable-point-in-time-recovery

gcloud sql databases create "${DB_NAME}" --instance="${SQL_INSTANCE}"

echo "Connection name (needed for the GCP_CLOUDSQL_CONNECTION GitHub variable):"
gcloud sql instances describe "${SQL_INSTANCE}" --format='value(connectionName)'

# Backups (GH #91): nightly automated backups at 09:00 UTC + PITR (7-day WAL default).
# For a PRE-EXISTING instance apply the same with:
#   gcloud sql instances patch "${SQL_INSTANCE}" --backup-start-time=09:00 --enable-point-in-time-recovery
