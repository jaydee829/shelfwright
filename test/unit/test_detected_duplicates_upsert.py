"""GH #141: compile-inspect the ON CONFLICT DO NOTHING insert enrich_deep's redirect path
issues against detected_duplicates — house rule 4 (pg-dialect statements get compile-inspect
locally, execute in CI/db_integration). Mirrors test_availability_batch.py's pattern."""

from uuid import uuid4

from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import insert as pg_insert

from agentic_librarian.db.models import DetectedDuplicate


def test_detected_duplicates_insert_conflict_target_is_the_composite_pk():
    work_id_a, work_id_b = uuid4(), uuid4()
    stmt = (
        pg_insert(DetectedDuplicate)
        .values(work_id_a=work_id_a, work_id_b=work_id_b, source="deep_pass_redirect")
        .on_conflict_do_nothing(index_elements=["work_id_a", "work_id_b"])
    )

    compiled = str(stmt.compile(dialect=postgresql.dialect()))

    assert "INSERT INTO detected_duplicates" in compiled
    assert "ON CONFLICT (work_id_a, work_id_b) DO NOTHING" in compiled


def test_two_phase_redirect_issues_the_same_upsert_shape(monkeypatch):
    """Drives enrich_deep's real redirect branch and compile-inspects the ACTUAL statement
    it executes (not a standalone equivalent) — same conflict target, same source literal."""
    from unittest.mock import MagicMock

    import agentic_librarian.enrichment.two_phase as two_phase_mod

    captured = {}
    invoked_id, twin_id = uuid4(), uuid4()

    invoked_contributor = MagicMock(role="Author")
    invoked_contributor.author.name = "A"
    invoked_work = MagicMock(id=invoked_id, title="T", contributors=[invoked_contributor], editions=[])

    invoked_row_after_redirect = MagicMock(deep_enriched_at=None)
    twin = MagicMock(id=twin_id)

    class _ReadSession:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, model, work_id):
            return invoked_work

    class _WriteSession:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, model, work_id):
            return invoked_row_after_redirect

        def execute(self, stmt):
            captured["stmt"] = stmt

        def flush(self):
            pass

    sessions = [_ReadSession(), _WriteSession()]
    fake_manager = MagicMock()
    fake_manager.get_session = lambda: sessions.pop(0)
    monkeypatch.setattr(two_phase_mod, "db_manager", fake_manager)
    monkeypatch.setattr(two_phase_mod, "_run_scouts", lambda manager, **kwargs: {"row": "data"})
    monkeypatch.setattr(two_phase_mod, "_warm_embeddings", lambda row: None)
    monkeypatch.setattr(two_phase_mod, "_persist_row", lambda session, row: twin)

    result = two_phase_mod.enrich_deep(invoked_id)

    assert result == "redirected"
    compiled = str(captured["stmt"].compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))
    assert "INSERT INTO detected_duplicates" in compiled
    assert "ON CONFLICT (work_id_a, work_id_b) DO NOTHING" in compiled
    assert "'deep_pass_redirect'" in compiled
    assert str(invoked_id) in compiled
    assert str(twin_id) in compiled
