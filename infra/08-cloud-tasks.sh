#!/usr/bin/env bash
# Lift 2 Stage 4: provision the Cloud Tasks queue + the OIDC invoker SA, and grant the
# runtime SA the rights to enqueue tasks that call the internal enrich route as the invoker.
# One-shot (fails loud on ALREADY_EXISTS). Source-relative so it runs from anywhere.
set -euo pipefail
source "$(dirname "$0")/00-config.sh"

# 0) Enable the Cloud Tasks API — Stage 4 is the first lift to use it, so Lift 0's
#    01-project.sh enable-list predates it. (Enabling can take ~30s to propagate; if the
#    queue create below 403s immediately, wait and re-run — nothing before it has run yet.)
gcloud services enable cloudtasks.googleapis.com

# 1) The queue the fast /books pass enqueues onto.
gcloud tasks queues create "${TASKS_QUEUE_NAME}" --location="${REGION}"

# 2) The service account whose OIDC token authorizes calls to the internal enrich route.
gcloud iam service-accounts create "${ENRICH_INVOKER_SA_NAME}" \
  --display-name="Cloud Tasks → internal enrich invoker"

# New SAs propagate asynchronously; binding too fast intermittently 400s with
# "does not exist". Poll until describable before binding (mirrors 05-iam-wif.sh).
for i in $(seq 1 12); do
  if gcloud iam service-accounts describe "${ENRICH_INVOKER_SA}" >/dev/null 2>&1; then break; fi
  echo "Waiting for ${ENRICH_INVOKER_SA} to propagate (${i}/12)..."
  sleep 5
done

# 3) That invoker SA may invoke the Cloud Run service (the now-open IAM gate still gates
#    the internal route via this OIDC identity, verified in-app).
gcloud run services add-iam-policy-binding "${SERVICE}" \
  --region="${REGION}" \
  --member="serviceAccount:${ENRICH_INVOKER_SA}" \
  --role="roles/run.invoker"

# 4) The runtime SA (which runs /books) may enqueue tasks…
gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
  --member="serviceAccount:${RUNTIME_SA}" \
  --role="roles/cloudtasks.enqueuer"

# 5) …and may mint OIDC tokens AS the invoker SA when creating those tasks.
gcloud iam service-accounts add-iam-policy-binding "${ENRICH_INVOKER_SA}" \
  --member="serviceAccount:${RUNTIME_SA}" \
  --role="roles/iam.serviceAccountUser"

echo "Cloud Tasks provisioned."
echo "  Set these GitHub repo Variables for deploy.yml:"
echo "    GCP_CLOUD_TASKS_QUEUE = ${TASKS_QUEUE_PATH}"
echo "    GCP_ENRICH_INVOKER_SA = ${ENRICH_INVOKER_SA}"
echo "    GCP_RUN_BASE_URL      = (the Cloud Run service URL; used for ENRICH_TARGET_BASE_URL + ENRICH_OIDC_AUDIENCE)"
echo "    GCP_SEARCH_ENGINE_ID  = (the Programmable Search Engine id)"
