from agentic_librarian.imports import tasks


class _FakeClient:
    def __init__(self):
        self.created = []

    def create_task(self, *, parent, task):
        self.created.append((parent, task))
        return object()


def _set_env(monkeypatch):
    monkeypatch.setenv("IMPORT_TASKS_QUEUE", "projects/p/locations/us-central1/queues/import")
    monkeypatch.setenv("ENRICH_TARGET_BASE_URL", "https://librarian.example.run.app")
    monkeypatch.setenv("ENRICH_INVOKER_SA", "queue-invoker@p.iam.gserviceaccount.com")


def test_enqueue_builds_oidc_task_targeting_the_import_route(monkeypatch):
    _set_env(monkeypatch)
    fake = _FakeClient()
    monkeypatch.setattr(tasks, "_client", lambda: fake)

    assert tasks.enqueue_import_row("11111111-1111-4111-8111-111111111111") is True

    parent, task = fake.created[0]
    assert parent == "projects/p/locations/us-central1/queues/import"
    http = task["http_request"]
    assert http["url"] == "https://librarian.example.run.app/internal/import-row/11111111-1111-4111-8111-111111111111"
    assert http["oidc_token"]["service_account_email"] == "queue-invoker@p.iam.gserviceaccount.com"


def test_enqueue_skips_when_queue_not_configured(monkeypatch):
    monkeypatch.delenv("IMPORT_TASKS_QUEUE", raising=False)
    called = {"n": 0}
    monkeypatch.setattr(tasks, "_client", lambda: called.__setitem__("n", called["n"] + 1))

    assert tasks.enqueue_import_row("abc") is False
    assert called["n"] == 0
