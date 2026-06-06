#!/usr/bin/env bash
# $25/month budget with email alerts at 50% / 90% / 100% (warns billing admins; never blocks).
# Requires: BILLING_ACCOUNT_ID env var.
set -euo pipefail
source "$(dirname "$0")/00-config.sh"
: "${BILLING_ACCOUNT_ID:?Set BILLING_ACCOUNT_ID (see: gcloud billing accounts list)}"
PROJECT_NUMBER="$(gcloud projects describe "${PROJECT_ID}" --format='value(projectNumber)')"

gcloud billing budgets create \
  --billing-account="${BILLING_ACCOUNT_ID}" \
  --display-name="${PROJECT_ID}-monthly" \
  --budget-amount=25USD \
  --filter-projects="projects/${PROJECT_NUMBER}" \
  --threshold-rule=percent=0.5 \
  --threshold-rule=percent=0.9 \
  --threshold-rule=percent=1.0
