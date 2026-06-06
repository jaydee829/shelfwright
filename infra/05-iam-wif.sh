#!/usr/bin/env bash
# Service accounts (least privilege) + Workload Identity Federation for GitHub Actions.
#   runtime SA:  what the Cloud Run service runs as — secret accessor + SQL client ONLY.
#   deployer SA: what CI impersonates — push images + deploy + invoke (smoke test).
#                It can NOT read secrets or the DB.
set -euo pipefail
source "$(dirname "$0")/00-config.sh"
PROJECT_NUMBER="$(gcloud projects describe "${PROJECT_ID}" --format='value(projectNumber)')"

gcloud iam service-accounts create "${RUNTIME_SA_NAME}" --display-name="librarian-api runtime"
gcloud iam service-accounts create "${DEPLOYER_SA_NAME}" --display-name="GitHub Actions deployer"

# New SAs propagate asynchronously; binding too fast intermittently 400s with
# "does not exist". Poll until both are describable before binding anything.
for sa in "${RUNTIME_SA}" "${DEPLOYER_SA}"; do
  for i in $(seq 1 12); do
    if gcloud iam service-accounts describe "${sa}" >/dev/null 2>&1; then break; fi
    echo "Waiting for ${sa} to propagate (${i}/12)..."
    sleep 5
  done
done

# Runtime: read the one secret + connect to Cloud SQL.
gcloud secrets add-iam-policy-binding "${SECRET_NAME}" \
  --member="serviceAccount:${RUNTIME_SA}" --role="roles/secretmanager.secretAccessor"
gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
  --member="serviceAccount:${RUNTIME_SA}" --role="roles/cloudsql.client"

# Deployer: push images, deploy the service, act-as the runtime SA, invoke for smoke tests.
gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
  --member="serviceAccount:${DEPLOYER_SA}" --role="roles/artifactregistry.writer"
gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
  --member="serviceAccount:${DEPLOYER_SA}" --role="roles/run.admin"
gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
  --member="serviceAccount:${DEPLOYER_SA}" --role="roles/run.invoker"
gcloud iam service-accounts add-iam-policy-binding "${RUNTIME_SA}" \
  --member="serviceAccount:${DEPLOYER_SA}" --role="roles/iam.serviceAccountUser"

# WIF: trust GitHub's OIDC issuer, pinned to exactly our repo.
gcloud iam workload-identity-pools create github --location=global --display-name="GitHub Actions"
gcloud iam workload-identity-pools providers create-oidc github-provider \
  --location=global \
  --workload-identity-pool=github \
  --display-name="GitHub OIDC" \
  --issuer-uri="https://token.actions.githubusercontent.com" \
  --attribute-mapping="google.subject=assertion.sub,attribute.repository=assertion.repository" \
  --attribute-condition="assertion.repository=='${GITHUB_REPO}'"

gcloud iam service-accounts add-iam-policy-binding "${DEPLOYER_SA}" \
  --role="roles/iam.workloadIdentityUser" \
  --member="principalSet://iam.googleapis.com/projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/github/attribute.repository/${GITHUB_REPO}"

CONNECTION_NAME="$(gcloud sql instances describe "${SQL_INSTANCE}" --format='value(connectionName)')"
echo ""
echo "=== Set these four GitHub repo VARIABLES (Settings > Secrets and variables > Actions > Variables) ==="
echo "GCP_PROJECT_ID=${PROJECT_ID}"
echo "GCP_WIF_PROVIDER=projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/github/providers/github-provider"
echo "GCP_DEPLOYER_SA=${DEPLOYER_SA}"
echo "GCP_CLOUDSQL_CONNECTION=${CONNECTION_NAME}"
