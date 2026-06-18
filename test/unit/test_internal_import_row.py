from uuid import uuid4

from fastapi.testclient import TestClient

from agentic_librarian.api import internal as internal_mod
from agentic_librarian.api.main import app

client = TestClient(app)


def test_rejects_caller_without_bearer_token():
    r = client.post(f"/internal/import-row/{uuid4()}")
    assert r.status_code == 401


def test_invokes_worker_when_oidc_passes(monkeypatch):
    # Bypass the OIDC gate (its own dedicated tests cover verification).
    monkeypatch.setattr(internal_mod, "_require_queue_caller", lambda authorization: None)
    seen = {}

    def _fake_worker(row_id):
        seen["row_id"] = row_id
        return "done"

    monkeypatch.setattr(
        "agentic_librarian.imports.worker.process_import_row",
        _fake_worker,
    )
    rid = uuid4()
    r = client.post(f"/internal/import-row/{rid}", headers={"Authorization": "Bearer x"})
    assert r.status_code == 200
    assert r.json() == {"row_id": str(rid), "result": "done"}
    assert str(seen["row_id"]) == str(rid)


def test_missing_row_is_404(monkeypatch):
    monkeypatch.setattr(internal_mod, "_require_queue_caller", lambda authorization: None)

    def _raise(row_id):
        raise LookupError

    monkeypatch.setattr("agentic_librarian.imports.worker.process_import_row", _raise)
    r = client.post(f"/internal/import-row/{uuid4()}", headers={"Authorization": "Bearer x"})
    assert r.status_code == 404
