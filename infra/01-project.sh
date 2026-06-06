#!/usr/bin/env bash
# Create the project, link billing, enable the needed APIs.
# Requires: BILLING_ACCOUNT_ID env var (find yours: `gcloud billing accounts list`).
set -euo pipefail
source "$(dirname "$0")/00-config.sh"
: "${BILLING_ACCOUNT_ID:?Set BILLING_ACCOUNT_ID (see: gcloud billing accounts list)}"

# Project IDs are globally unique; if taken, override with PROJECT_ID=... before running.
gcloud projects create "${PROJECT_ID}" || echo "Project may already exist — continuing."
gcloud billing projects link "${PROJECT_ID}" --billing-account="${BILLING_ACCOUNT_ID}"
gcloud config set project "${PROJECT_ID}"

gcloud services enable \
  run.googleapis.com \
  sqladmin.googleapis.com \
  secretmanager.googleapis.com \
  artifactregistry.googleapis.com \
  iamcredentials.googleapis.com \
  billingbudgets.googleapis.com \
  storage.googleapis.com

echo "Project ${PROJECT_ID} ready."
