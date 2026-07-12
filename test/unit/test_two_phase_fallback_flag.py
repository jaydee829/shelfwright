from uuid import uuid4

from agentic_librarian.enrichment import two_phase


def test_run_scouts_forwards_fallback_flag(monkeypatch):
    mgr = type("M", (), {"enrich": lambda self, **k: {"genres": ["x"], "moods": []}})()

    row = two_phase._run_scouts(mgr, title="T", author="A", fmt="ebook", write_fallback_tropes=False)
    assert row["write_fallback_tropes"] is False


def test_enrich_fast_opts_out_of_fallback_tropes(monkeypatch):
    seen = {}

    def fake_run_scouts(manager, *, title, author, fmt, write_fallback_tropes=True):
        seen["wft"] = write_fallback_tropes
        return {"write_fallback_tropes": write_fallback_tropes}

    def fake_persist_row(session, row):
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

    monkeypatch.setattr(two_phase, "_run_scouts", fake_run_scouts)
    monkeypatch.setattr(two_phase, "_persist_row", fake_persist_row)
    monkeypatch.setattr(two_phase, "create_fast_scout_manager", lambda: None)
    two_phase.set_db_manager(type("M", (), {"get_session": lambda s: _Sess()})())

    two_phase.enrich_fast("Some Title", "Some Author", "ebook")
    assert seen["wft"] is False
