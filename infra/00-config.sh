#!/usr/bin/env bash
# Shared configuration for all Lift 0 provisioning scripts. Source me: `source 00-config.sh`
set -euo pipefail

# These scripts are ONE-SHOT, not idempotent: re-running a partially-completed step
# fails on ALREADY_EXISTS by design (fail-loud beats silent-skip). To recover, delete
# the offending resource or skip past the completed commands.

export PROJECT_ID="${PROJECT_ID:-agentic-librarian-prod}"
export REGION="${REGION:-us-central1}"
export SQL_INSTANCE="librarian-sql"
export DB_NAME="agentic_librarian"
export DB_USER="librarian"
export SECRET_NAME="librarian-db-url"
export AR_REPO="librarian"
export BUCKET="gs://${PROJECT_ID}-backups"
export RUNTIME_SA_NAME="librarian-api-runtime"
export DEPLOYER_SA_NAME="github-deployer"
export RUNTIME_SA="${RUNTIME_SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
export DEPLOYER_SA="${DEPLOYER_SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
export SERVICE="librarian-api"
export GITHUB_REPO="jaydee829/agentic_librarian"
export DUMP_FILE="agentic_librarian_FINAL_20260605_014912.sql.gz"

# Re-assert the target project in every script — prevents a fresh shell from silently
# operating on whatever project the global gcloud config last pointed at.
# (config set does not validate existence, so this is safe before 01 creates it.)
gcloud config set project "${PROJECT_ID}" --quiet
