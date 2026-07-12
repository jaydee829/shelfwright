"""#94: scouts must run with NO session held; persist re-checks dedup in a fresh session."""

from unittest.mock import MagicMock, patch

from agentic_librarian.enrichment import two_phase


def test_enrich_fast_runs_scouts_outside_any_session(monkeypatch):
    """The scout call happens between the read session and the write session."""
    session_state = {"open": 0}

    class FakeSession:
        def __enter__(self):
            session_state["open"] += 1
            m = MagicMock()
            # the dedup query chain must return None (no existing work), or enrich_fast
            # early-returns before ever reaching the scouts
            m.query.return_value.join.return_value.join.return_value.filter.return_value.filter.return_value.first.return_value = None
            return m

        def __exit__(self, *a):
            session_state["open"] -= 1
            return False

    fake_manager = MagicMock()
    fake_manager.get_session = lambda: FakeSession()
    monkeypatch.setattr(two_phase, "db_manager", fake_manager)

    scout_seen = {}

    def fake_run_scouts(manager, **kwargs):
        scout_seen["open_sessions_during_scout"] = session_state["open"]
        return None  # scouts found nothing -> enrich_fast returns None

    monkeypatch.setattr(two_phase, "_run_scouts", fake_run_scouts)
    with patch.object(two_phase, "create_fast_scout_manager", return_value=MagicMock()):
        result = two_phase.enrich_fast("New Book", "New Author")
    assert result is None
    assert scout_seen["open_sessions_during_scout"] == 0  # THE #94 assertion


def test_enrich_deep_runs_scouts_outside_any_session(monkeypatch):
    session_state = {"open": 0}

    class FakeSession:
        def __init__(self, work):
            self._work = work

        def __enter__(self):
            session_state["open"] += 1
            m = MagicMock()
            m.get.return_value = self._work
            return m

        def __exit__(self, *a):
            session_state["open"] -= 1
            return False

    work = MagicMock()
    work.title = "T"
    work.contributors = [MagicMock(role="Author", author=MagicMock(name="A"))]
    work.contributors[0].author.name = "A"
    work.editions = []
    fake_manager = MagicMock()
    fake_manager.get_session = lambda: FakeSession(work)
    monkeypatch.setattr(two_phase, "db_manager", fake_manager)

    scout_seen = {}

    def fake_run_scouts(manager, **kwargs):
        scout_seen["open_sessions_during_scout"] = session_state["open"]
        return None

    monkeypatch.setattr(two_phase, "_run_scouts", fake_run_scouts)
    with patch.object(two_phase, "create_deep_scout_manager", return_value=MagicMock()):
        assert two_phase.enrich_deep(work_id=MagicMock()) == "empty"
    assert scout_seen["open_sessions_during_scout"] == 0
