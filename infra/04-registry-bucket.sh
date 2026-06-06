#!/usr/bin/env bash
# Artifact Registry (images) and the backups bucket (pg_dump staging for import).
set -euo pipefail
source "$(dirname "$0")/00-config.sh"

gcloud artifacts repositories create "${AR_REPO}" \
  --repository-format=docker \
  --location="${REGION}" \
  --description="librarian-api images (tags = git SHAs)"

gcloud storage buckets create "${BUCKET}" \
  --location="${REGION}" \
  --uniform-bucket-level-access
