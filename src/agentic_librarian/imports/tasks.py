"""Cloud Tasks enqueue for the per-row bulk-import worker (Spec 2026-06-18). One task per
importable row → POST /internal/import-row/{row_id} with the queue's OIDC token. Uses a
SEPARATE queue (IMPORT_TASKS_QUEUE) so an import burst can't starve interactive deep-enrich.
Reuses the enrich path's base-URL / SA / OIDC-audience env."""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)


def _client():
    from google.cloud import tasks_v2

    return tasks_v2.CloudTasksClient()


def enqueue_import_row(row_id: str) -> bool:
    """Enqueue the worker task for row_id. Returns False (logged no-op) when Cloud Tasks is
    unconfigured (local dev) — commit still succeeds; the row stays 'pending'."""
    queue = os.environ.get("IMPORT_TASKS_QUEUE")
    base = os.environ.get("ENRICH_TARGET_BASE_URL")
    sa = os.environ.get("ENRICH_INVOKER_SA")
    if not (queue and base and sa):
        logger.info("import-row enqueue skipped — Cloud Tasks not configured (row %s)", row_id)
        return False

    url = f"{base.rstrip('/')}/internal/import-row/{row_id}"
    audience = os.environ.get("ENRICH_OIDC_AUDIENCE") or url
    task = {
        "http_request": {
            "http_method": "POST",
            "url": url,
            "oidc_token": {"service_account_email": sa, "audience": audience},
        }
    }
    _client().create_task(parent=queue, task=task)
    logger.info("enqueued import-row worker for row %s", row_id)
    return True
