"""Cloud Tasks enqueue for the deep-enrichment pass (Lift 2 Stage 3).

The fast /books pass enqueues a task that re-enters the service as POST
/internal/enrich/{work_id} with the queue's OIDC token. Cloud Run throttles CPU after a
response, so an in-process background thread is unreliable; a queued task runs as a fresh
request with full CPU + long timeout.

Config (wired in prod in Stage 4; absent in local dev → enqueue is a logged no-op):
  CLOUD_TASKS_QUEUE     full queue path projects/<p>/locations/<loc>/queues/<q>
  ENRICH_TARGET_BASE_URL  the Cloud Run base URL (no trailing slash)
  ENRICH_INVOKER_SA     service-account email the queue signs the OIDC token as
  ENRICH_OIDC_AUDIENCE  the OIDC audience. The enqueue side defaults it to the full task
                        URL, but the receiver (api/internal.py) hard-requires it and 403s
                        when unset — so in prod it must be set to match on both sides."""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)


def _client():
    """Seam for tests. Lazily imports google-cloud-tasks so the dependency is only
    needed where enqueue actually runs."""
    from google.cloud import tasks_v2

    return tasks_v2.CloudTasksClient()


def enqueue_enrichment(work_id: str) -> bool:
    """Enqueue the deep-enrichment task for work_id. Returns True if enqueued, False if
    Cloud Tasks is not configured (local dev) — the caller treats a False/raised result as
    non-fatal so the fast add still succeeds."""
    queue = os.environ.get("CLOUD_TASKS_QUEUE")
    base = os.environ.get("ENRICH_TARGET_BASE_URL")
    sa = os.environ.get("ENRICH_INVOKER_SA")
    if not (queue and base and sa):
        logger.info("enrichment enqueue skipped — Cloud Tasks not configured (work %s)", work_id)
        return False

    url = f"{base.rstrip('/')}/internal/enrich/{work_id}"
    audience = os.environ.get("ENRICH_OIDC_AUDIENCE") or url
    task = {
        "http_request": {
            "http_method": "POST",
            "url": url,
            "oidc_token": {"service_account_email": sa, "audience": audience},
        }
    }
    _client().create_task(parent=queue, task=task)
    logger.info("enqueued deep enrichment for work %s", work_id)
    return True
