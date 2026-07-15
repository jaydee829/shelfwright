"""GH #141: enrich_deep pins the invoked work id. If the write-session persist re-check
resolves the scout-canonical identity to a DIFFERENT existing work (a "redirect"), the
invoked row must still be stamped deep_enriched_at, the redirect recorded in
detected_duplicates, and "redirected" returned — never silently landing on the twin with
the invoked row left never_deep_enriched forever (the live #141 bug)."""

from unittest.mock import MagicMock

from agentic_librarian.enrichment import two_phase


class _FakeReadSession:
    """First `with db_manager.get_session()` block in enrich_deep: reads the invoked
    work's identity (title/author/format)."""

    def __init__(self, work):
        self._work = work

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get(self, model, work_id):
        return self._work


class _FakeWriteSession:
    """Second `with` block: persists via _persist_row, records seen calls for assertions."""

    def __init__(self, invoked_id, persisted_work, calls):
        self._invoked_id = invoked_id
        self._persisted_work = persisted_work
        self._calls = calls
        self._invoked_row = MagicMock(deep_enriched_at=None)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get(self, model, work_id):
        # used to re-load the invoked row by id when persist resolved elsewhere
        self._calls.append(("get", work_id))
        if work_id == self._invoked_id:
            return self._invoked_row
        return None

    def execute(self, *a, **k):
        self._calls.append(("execute", a, k))

    def flush(self):
        self._calls.append(("flush",))


def _make_work(work_id, title="T", author="A"):
    contributor = MagicMock(role="Author")
    contributor.author.name = author
    work = MagicMock()
    work.id = work_id
    work.title = title
    work.contributors = [contributor]
    work.editions = []
    return work


def test_enrich_deep_same_id_persist_reports_done_unchanged(monkeypatch):
    from uuid import uuid4

    work_id = uuid4()
    invoked_work = _make_work(work_id)
    calls = []

    persisted = MagicMock()
    persisted.id = work_id  # persist landed on the SAME row invoked

    sessions = [_FakeReadSession(invoked_work), _FakeWriteSession(work_id, persisted, calls)]
    fake_manager = MagicMock()
    fake_manager.get_session = lambda: sessions.pop(0)
    monkeypatch.setattr(two_phase, "db_manager", fake_manager)
    monkeypatch.setattr(two_phase, "_run_scouts", lambda manager, **kwargs: {"row": "data"})
    monkeypatch.setattr(two_phase, "_warm_embeddings", lambda row: None)
    monkeypatch.setattr(two_phase, "_persist_row", lambda session, row: persisted)

    result = two_phase.enrich_deep(work_id)

    assert result == "done"
    assert persisted.deep_enriched_at is not None
    # no detected_duplicates insert on the same-id path
    assert not any(c[0] == "execute" for c in calls)


def test_enrich_deep_none_persist_reports_missing_unchanged(monkeypatch):
    from uuid import uuid4

    work_id = uuid4()
    invoked_work = _make_work(work_id)
    calls = []

    sessions = [_FakeReadSession(invoked_work), _FakeWriteSession(work_id, None, calls)]
    fake_manager = MagicMock()
    fake_manager.get_session = lambda: sessions.pop(0)
    monkeypatch.setattr(two_phase, "db_manager", fake_manager)
    monkeypatch.setattr(two_phase, "_run_scouts", lambda manager, **kwargs: {"row": "data"})
    monkeypatch.setattr(two_phase, "_warm_embeddings", lambda row: None)
    monkeypatch.setattr(two_phase, "_persist_row", lambda session, row: None)

    result = two_phase.enrich_deep(work_id)

    assert result == "missing"
    assert not any(c[0] == "execute" for c in calls)


def test_enrich_deep_different_id_persist_redirects(monkeypatch):
    """The persist-side dedup re-check resolved a DIFFERENT existing work (the twin) —
    same book, dirty invoked-row identity. Must NOT undo the twin's write: instead record
    the redirect, stamp the INVOKED row, and return "redirected"."""
    from uuid import uuid4

    invoked_id = uuid4()
    twin_id = uuid4()
    invoked_work = _make_work(invoked_id, author="Casualfarmer, CasualFarmer")
    calls = []

    twin = MagicMock()
    twin.id = twin_id  # persist landed on the TWIN, not the invoked row

    sessions = [_FakeReadSession(invoked_work), _FakeWriteSession(invoked_id, twin, calls)]
    fake_manager = MagicMock()
    fake_manager.get_session = lambda: sessions.pop(0)
    monkeypatch.setattr(two_phase, "db_manager", fake_manager)
    monkeypatch.setattr(two_phase, "_run_scouts", lambda manager, **kwargs: {"row": "data"})
    monkeypatch.setattr(two_phase, "_warm_embeddings", lambda row: None)
    monkeypatch.setattr(two_phase, "_persist_row", lambda session, row: twin)

    result = two_phase.enrich_deep(invoked_id)

    assert result == "redirected"
    # the invoked row (re-loaded by id) is stamped, NOT the twin
    assert any(c[0] == "get" and c[1] == invoked_id for c in calls)
    assert any(c[0] == "execute" for c in calls)  # the ON CONFLICT DO NOTHING insert
    assert any(c[0] == "flush" for c in calls)


def test_enrich_deep_redirect_stamps_invoked_row_not_twin(monkeypatch):
    from uuid import uuid4

    invoked_id = uuid4()
    twin_id = uuid4()
    invoked_work = _make_work(invoked_id, author="Casualfarmer, CasualFarmer")
    calls = []

    twin = MagicMock()
    twin.id = twin_id
    twin.deep_enriched_at = None

    write_session = _FakeWriteSession(invoked_id, twin, calls)
    sessions = [_FakeReadSession(invoked_work), write_session]
    fake_manager = MagicMock()
    fake_manager.get_session = lambda: sessions.pop(0)
    monkeypatch.setattr(two_phase, "db_manager", fake_manager)
    monkeypatch.setattr(two_phase, "_run_scouts", lambda manager, **kwargs: {"row": "data"})
    monkeypatch.setattr(two_phase, "_warm_embeddings", lambda row: None)
    monkeypatch.setattr(two_phase, "_persist_row", lambda session, row: twin)

    result = two_phase.enrich_deep(invoked_id)

    assert result == "redirected"
    assert write_session._invoked_row.deep_enriched_at is not None
    assert twin.deep_enriched_at is None  # the twin's stamp is untouched by THIS function


def test_enrich_deep_redirect_invoked_row_vanished_mid_pass(monkeypatch):
    """If the invoked row vanished mid-pass (deleted while the slow scouts ran with no
    session held) by the time the redirect stamp tries to re-load it, treat this the same
    as the existing deleted-mid-pass path: "missing", not a lie."""
    from uuid import uuid4

    invoked_id = uuid4()
    twin_id = uuid4()
    invoked_work = _make_work(invoked_id, author="Casualfarmer, CasualFarmer")
    calls = []

    twin = MagicMock()
    twin.id = twin_id

    class _VanishedWriteSession(_FakeWriteSession):
        def get(self, model, work_id):
            calls.append(("get", work_id))
            return None  # invoked row is gone no matter which id is requested

    sessions = [_FakeReadSession(invoked_work), _VanishedWriteSession(invoked_id, twin, calls)]
    fake_manager = MagicMock()
    fake_manager.get_session = lambda: sessions.pop(0)
    monkeypatch.setattr(two_phase, "db_manager", fake_manager)
    monkeypatch.setattr(two_phase, "_run_scouts", lambda manager, **kwargs: {"row": "data"})
    monkeypatch.setattr(two_phase, "_warm_embeddings", lambda row: None)
    monkeypatch.setattr(two_phase, "_persist_row", lambda session, row: twin)

    result = two_phase.enrich_deep(invoked_id)

    assert result == "missing"
