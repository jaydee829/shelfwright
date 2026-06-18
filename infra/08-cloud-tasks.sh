#!/usr/bin/env bash
# Lift 2 Stage 4: provision the Cloud Tasks queue + the OIDC invoker SA, and grant the
# runtime SA the rights to enqueue tasks that call the internal enrich route as the invoker.
# IDEMPOTENT + retry-safe (unlike the Lift 0 one-shot scripts): a brand-new SA propagates
# asynchronously, so resource-level bindings can transiently 400 "does not exist" — re-running
# must be safe. Source-relative so it runs from anywhere.
set -euo pipefail
source "$(dirname "$0")/00-config.sh"

# Retry a command to ride out IAM / new-SA propagation (a describe-poll isn't enough — the
# resource-level run.invoker binding lags longer than the SA becoming describable).
_retry() {
  local n=0
  until "$@"; do
    n=$((n + 1))
    if [ "$n" -ge 12 ]; then echo "giving up after $n attempts: $*" >&2; return 1; fi
    echo "retry $n/12 (waiting on propagation): $*"
    sleep 5
  done
}

# 0) Enable the Cloud Tasks API — Stage 4 is the first lift to use it, so Lift 0's
#    01-project.sh enable-list predates it. (Idempotent; ~30s to propagate.)
gcloud services enable cloudtasks.googleapis.com

# 1) The queue the fast /books pass enqueues onto (create only if absent).
gcloud tasks queues describe "${TASKS_QUEUE_NAME}" --location="${REGION}" >/dev/null 2>&1 \
  || gcloud tasks queues create "${TASKS_QUEUE_NAME}" --location="${REGION}"

# 1b) The queue the bulk-import per-row worker enqueues onto — SEPARATE from the enrich queue
#     so a large import burst can't starve interactive deep-enrich (Spec 2026-06-18 D7). The
#     conservative dispatch rate is the quota-safety lever: it caps the parallel shallow-scout
#     burst under the Books/Hardcover + Gemini limits (raise it once comfortable on paid tier).
#     Reuses the SAME invoker SA + runtime-SA enqueuer/impersonation grants created below
#     (project-level enqueuer + the invoker's run.invoker are queue-independent), so it needs
#     no extra IAM. Idempotent: create only if absent.
IMPORT_TASKS_QUEUE_NAME="librarian-import"
IMPORT_TASKS_QUEUE_PATH="projects/${PROJECT_ID}/locations/${REGION}/queues/${IMPORT_TASKS_QUEUE_NAME}"
gcloud tasks queues describe "${IMPORT_TASKS_QUEUE_NAME}" --location="${REGION}" >/dev/null 2>&1 \
  || gcloud tasks queues create "${IMPORT_TASKS_QUEUE_NAME}" --location="${REGION}" \
       --max-dispatches-per-second=2 --max-concurrent-dispatches=5

# 2) The service account whose OIDC token authorizes calls to the internal enrich route.
gcloud iam service-accounts describe "${ENRICH_INVOKER_SA}" >/dev/null 2>&1 \
  || gcloud iam service-accounts create "${ENRICH_INVOKER_SA_NAME}" \
       --display-name="Cloud Tasks → internal enrich invoker"

# 3) That invoker SA may invoke the Cloud Run service (the now-open IAM gate still gates the
#    internal route via this OIDC identity, verified in-app). Retried: the binding can 400
#    "does not exist" while a freshly-created SA propagates. add-iam-policy-binding is idempotent.
_retry gcloud run services add-iam-policy-binding "${SERVICE}" \
  --region="${REGION}" \
  --member="serviceAccount:${ENRICH_INVOKER_SA}" \
  --role="roles/run.invoker"

# 4) The runtime SA (which runs /books) may enqueue tasks…
gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
  --member="serviceAccount:${RUNTIME_SA}" \
  --role="roles/cloudtasks.enqueuer"

# 5) …and may mint OIDC tokens AS the invoker SA when creating those tasks (also retried).
_retry gcloud iam service-accounts add-iam-policy-binding "${ENRICH_INVOKER_SA}" \
  --member="serviceAccount:${RUNTIME_SA}" \
  --role="roles/iam.serviceAccountUser"

# Look up the live service URL (the service already exists from Lift 0/1); SEARCH_ENGINE_ID
# is YOUR Programmable Search Engine id (config, e.g. from .env) — not derivable from GCP.
RUN_BASE_URL="$(gcloud run services describe "${SERVICE}" --region="${REGION}" --format='value(status.url)' 2>/dev/null || true)"

echo "Cloud Tasks provisioned."
echo "  Set these GitHub repo Variables for deploy.yml:"
echo "    GCP_CLOUD_TASKS_QUEUE  = ${TASKS_QUEUE_PATH}"
echo "    GCP_IMPORT_TASKS_QUEUE = ${IMPORT_TASKS_QUEUE_PATH}"
echo "    GCP_ENRICH_INVOKER_SA  = ${ENRICH_INVOKER_SA}"
echo "    GCP_RUN_BASE_URL      = ${RUN_BASE_URL:-<gcloud run services describe ${SERVICE} --region=${REGION} --format='value(status.url)'>}"
echo "    GCP_SEARCH_ENGINE_ID  = ${SEARCH_ENGINE_ID:-<your Programmable Search Engine id, e.g. .env SEARCH_ENGINE_ID>}"
