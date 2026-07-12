"""#94: Thunder fetches happen with NO session open; cache reads/writes use short sessions."""

from unittest.mock import MagicMock

from agentic_librarian.availability import service


def test_batch_availability_fetches_outside_sessions(monkeypatch):
    session_state = {"open": 0}

    class FakeSession:
        def __enter__(self):
            session_state["open"] += 1
            m = MagicMock()
            m.get.return_value = None  # no cache rows -> everything is a miss
            return m

        def __exit__(self, *a):
            session_state["open"] -= 1
            return False

    fake_db = MagicMock()
    fake_db.get_session = lambda: FakeSession()

    fetch_seen = []

    def fake_fetch(slug, title):
        fetch_seen.append(session_state["open"])
        return []  # matched nothing (a real, cacheable result)

    monkeypatch.setattr(service.overdrive, "fetch_media", fake_fetch)

    libs = [{"slug": "lib1", "name": "Lib One"}]
    out = service.batch_availability(fake_db, libs, [("Dune", "Frank Herbert")])
    assert fetch_seen == [0]  # THE #94 assertion: no session open during Thunder call
    assert out[("lib1", "Dune", "Frank Herbert")] == []


def test_batch_availability_thunder_error_degrades_to_none(monkeypatch):
    fake_db = MagicMock()
    session = MagicMock()
    session.get.return_value = None
    fake_db.get_session.return_value.__enter__ = lambda s: session
    fake_db.get_session.return_value.__exit__ = lambda s, *a: False

    def boom(slug, title):
        raise service.ThunderError("down")

    monkeypatch.setattr(service.overdrive, "fetch_media", boom)
    out = service.batch_availability(fake_db, [{"slug": "l", "name": "L"}], [("T", "A")])
    assert out[("l", "T", "A")] is None  # ALWAYS-200 contract: badge degrades, links unaffected


def test_batch_availability_write_back_failure_still_returns_results(monkeypatch):
    calls = {"n": 0}

    class FakeSessionCtx:
        def __enter__(self):
            calls["n"] += 1
            m = MagicMock()
            m.get.return_value = None
            if calls["n"] == 2:  # phase-3 write session
                m.flush.side_effect = RuntimeError("duplicate key")
            return m

        def __exit__(self, *a):
            return False

    fake_db = MagicMock()
    fake_db.get_session = lambda: FakeSessionCtx()
    monkeypatch.setattr(service.overdrive, "fetch_media", lambda slug, title: [])
    out = service.batch_availability(fake_db, [{"slug": "l", "name": "L"}], [("T", "A")])
    assert out[("l", "T", "A")] == []  # fetched result survives the failed write-back
