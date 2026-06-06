#!/usr/bin/env bash
# Shared configuration for all Lift 0 provisioning scripts. Source me: `source 00-config.sh`
set -euo pipefail

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
