"""#94: Thunder fetches happen with NO session open; cache reads/writes use short sessions."""

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

from sqlalchemy.dialects import postgresql

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


def test_write_back_uses_upsert(monkeypatch):
    """GH #110: the phase-3 write-back must be a durable postgresql ON CONFLICT upsert on
    the composite PK, not a racy add-or-update. We never execute the statement against
    sqlite — only compile it (postgresql dialect) and inspect the resulting SQL text."""
    executed = []

    class FakeSession:
        def get(self, *a, **kw):
            return None  # every row is a miss -> phase 3 always writes

        def execute(self, stmt):
            executed.append(stmt)

        def flush(self):
            pass

    class FakeSessionCtx:
        def __enter__(self):
            return FakeSession()

        def __exit__(self, *a):
            return False

    fake_db = MagicMock()
    fake_db.get_session = lambda: FakeSessionCtx()
    monkeypatch.setattr(service.overdrive, "fetch_media", lambda slug, title: [])

    out = service.batch_availability(fake_db, [{"slug": "l", "name": "L"}], [("T", "A")])
    assert out[("l", "T", "A")] == []

    # exactly one upsert statement was executed for the single fetched row (eviction is a
    # separate DELETE, asserted in test_eviction_runs_after_write_back)
    upserts = [s for s in executed if type(s).__name__ == "Insert" and hasattr(s, "on_conflict_do_update")]
    assert upserts, f"expected an INSERT..ON CONFLICT statement, got: {executed}"

    compiled = str(upserts[0].compile(dialect=postgresql.dialect()))
    assert "INSERT INTO availability_cache" in compiled
    assert "ON CONFLICT (provider, library_slug, norm_title, norm_author) DO UPDATE" in compiled


def test_eviction_runs_after_write_back(monkeypatch):
    """GH #110: after a successful phase-3 write, stale rows (fetched_at older than 30 days)
    are opportunistically evicted in the same session."""
    executed = []

    class FakeSession:
        def get(self, *a, **kw):
            return None

        def execute(self, stmt):
            executed.append(stmt)

        def flush(self):
            pass

    class FakeSessionCtx:
        def __enter__(self):
            return FakeSession()

        def __exit__(self, *a):
            return False

    fake_db = MagicMock()
    fake_db.get_session = lambda: FakeSessionCtx()
    monkeypatch.setattr(service.overdrive, "fetch_media", lambda slug, title: [])

    service.batch_availability(fake_db, [{"slug": "l", "name": "L"}], [("T", "A")])

    deletes = [s for s in executed if type(s).__name__ == "Delete"]
    assert deletes, f"expected a DELETE statement for eviction, got: {executed}"

    compiled_sql = str(deletes[0].compile(dialect=postgresql.dialect()))
    assert "DELETE FROM availability_cache" in compiled_sql
    assert "fetched_at" in compiled_sql

    # the cutoff bind value is ~30 days before "now" (compile with literal binds to inspect it)
    literal_sql = str(deletes[0].compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))
    # extract the timestamp literal and confirm it is close to now - 30 days
    import re

    m = re.search(r"fetched_at < '([^']+)'", literal_sql)
    assert m, f"could not find cutoff literal in: {literal_sql}"
    cutoff = datetime.fromisoformat(m.group(1))
    if cutoff.tzinfo is None:
        cutoff = cutoff.replace(tzinfo=UTC)
    expected = datetime.now(UTC) - timedelta(days=30)
    assert abs((cutoff - expected).total_seconds()) < 60  # within a minute of "now - 30d"


def test_eviction_does_not_run_when_write_back_fails(monkeypatch):
    """GH #110: eviction is piggybacked on a *successful* write-back only — if the upsert
    raises, no DELETE should be issued in that session."""
    executed = []

    class FakeSession:
        def get(self, *a, **kw):
            return None

        def execute(self, stmt):
            executed.append(stmt)
            if type(stmt).__name__ == "Insert":
                raise RuntimeError("boom")

        def flush(self):
            pass

    class FakeSessionCtx:
        def __enter__(self):
            return FakeSession()

        def __exit__(self, *a):
            return False

    fake_db = MagicMock()
    fake_db.get_session = lambda: FakeSessionCtx()
    monkeypatch.setattr(service.overdrive, "fetch_media", lambda slug, title: [])

    out = service.batch_availability(fake_db, [{"slug": "l", "name": "L"}], [("T", "A")])
    assert out[("l", "T", "A")] == []  # best-effort: fetched result still returned

    deletes = [s for s in executed if type(s).__name__ == "Delete"]
    assert not deletes, "eviction must not run after a failed write-back"
