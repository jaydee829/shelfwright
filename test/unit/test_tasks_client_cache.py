"""#93: Cloud Tasks clients are cached at module level (one gRPC channel per process,
not one per enqueued row)."""

from types import SimpleNamespace

from agentic_librarian.enrichment import tasks as enrich_tasks
from agentic_librarian.imports import tasks as import_tasks


def _fake_tasks_v2(counter):
    class FakeClient:
        def __init__(self):
            counter.append(1)

        def create_task(self, parent, task):
            return SimpleNamespace(name="t")

    return SimpleNamespace(CloudTasksClient=FakeClient)


def test_import_client_is_cached(monkeypatch):
    counter = []
    monkeypatch.setattr(import_tasks, "_client_cached", None)
    monkeypatch.setitem(__import__("sys").modules, "google.cloud.tasks_v2", _fake_tasks_v2(counter))
    monkeypatch.setenv("IMPORT_TASKS_QUEUE", "q")
    monkeypatch.setenv("ENRICH_TARGET_BASE_URL", "https://x")
    monkeypatch.setenv("ENRICH_INVOKER_SA", "sa@x")
    import_tasks.enqueue_import_row("r1")
    import_tasks.enqueue_import_row("r2")
    assert len(counter) == 1  # one client for both enqueues


def test_enrich_client_is_cached(monkeypatch):
    counter = []
    monkeypatch.setattr(enrich_tasks, "_client_cached", None)
    monkeypatch.setitem(__import__("sys").modules, "google.cloud.tasks_v2", _fake_tasks_v2(counter))
    monkeypatch.setenv("CLOUD_TASKS_QUEUE", "q")
    monkeypatch.setenv("ENRICH_TARGET_BASE_URL", "https://x")
    monkeypatch.setenv("ENRICH_INVOKER_SA", "sa@x")
    enrich_tasks.enqueue_enrichment("w1")
    enrich_tasks.enqueue_enrichment("w2")
    assert len(counter) == 1
