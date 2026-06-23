from uuid import uuid4

from agentic_librarian.enrichment import two_phase


def test_scout_and_persist_forwards_fallback_flag(monkeypatch):
    captured = {}

    def fake_persist(session, row, tm, sm):
        captured.update(row)
        return object()

    monkeypatch.setattr(two_phase, "persist_enriched_work", fake_persist)
    monkeypatch.setattr(two_phase, "TropeManager", lambda session: None)
    monkeypatch.setattr(two_phase, "StyleManager", lambda session: None)
    mgr = type("M", (), {"enrich": lambda self, **k: {"genres": ["x"], "moods": []}})()

    two_phase._scout_and_persist(None, mgr, title="T", author="A", fmt="ebook", write_fallback_tropes=False)
    assert captured["write_fallback_tropes"] is False


def test_enrich_fast_opts_out_of_fallback_tropes(monkeypatch):
    seen = {}

    def fake_sap(session, manager, *, title, author, fmt, write_fallback_tropes=True):
        seen["wft"] = write_fallback_tropes
        return type("W", (), {"id": uuid4()})()

    class _Sess:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def query(self, *a, **k):
            return self

        def join(self, *a, **k):
            return self

        def filter(self, *a, **k):
            return self

        def first(self):
            return None

        def flush(self):
            pass

    monkeypatch.setattr(two_phase, "_scout_and_persist", fake_sap)
    monkeypatch.setattr(two_phase, "create_fast_scout_manager", lambda: None)
    two_phase.set_db_manager(type("M", (), {"get_session": lambda s: _Sess()})())

    two_phase.enrich_fast("Some Title", "Some Author", "ebook")
    assert seen["wft"] is False
