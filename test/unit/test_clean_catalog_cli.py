import importlib.util
from pathlib import Path
from uuid import uuid4

spec = importlib.util.spec_from_file_location(
    "clean_catalog", Path(__file__).resolve().parents[2] / "scripts" / "clean_catalog.py"
)
clean_catalog = importlib.util.module_from_spec(spec)
spec.loader.exec_module(clean_catalog)


def test_apply_refused_without_yes(monkeypatch, capsys):
    monkeypatch.setattr(clean_catalog, "resolve_database_url", lambda: "postgresql://u:p@prod-host/db")

    class _Sess:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def query(self, *a, **k):
            return self

        def filter_by(self, *a, **k):
            return self

        def all(self):
            return []

        def count(self):
            return 0

        def scalar(self):
            # the recency probe is column-explicit (func.count(...).scalar(), never an
            # entity-load .count()) so it works on the pre-migration prod schema (GH #95)
            return 0

    monkeypatch.setattr(
        clean_catalog, "DatabaseManager", lambda url: type("M", (), {"get_session": lambda s: _Sess()})()
    )
    rc = clean_catalog.main(["--contributors", "--apply"])  # no --yes
    assert rc == 2
    assert "REFUSING --apply without --yes" in capsys.readouterr().out


def test_requeue_never_enqueues_pending_merge_candidates(monkeypatch, capsys):
    """GH #141: plan_requeue can return a "pending_merge" candidate (a work on either side
    of detected_duplicates) alongside real enrichable candidates. The CLI's --apply enqueue
    loop must filter to the two enrichable reasons only — pending_merge candidates are
    reported under their own heading and NEVER passed to enqueue_enrichment."""
    monkeypatch.setattr(clean_catalog, "resolve_database_url", lambda: "postgresql://u:p@prod-host/db")

    class _Sess:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def query(self, *a, **k):
            return self

        def scalar(self):
            return 0

    monkeypatch.setattr(
        clean_catalog, "DatabaseManager", lambda url: type("M", (), {"get_session": lambda s: _Sess()})()
    )

    never_id, no_real_id, pending_id = uuid4(), uuid4(), uuid4()
    requeue_candidate = clean_catalog.enrichment_sweep.RequeueCandidate
    candidates = [
        requeue_candidate(never_id, "Never Enriched", "never_deep_enriched"),
        requeue_candidate(no_real_id, "No Real Trope", "no_real_trope"),
        requeue_candidate(pending_id, "Pending Merge", "pending_merge"),
    ]
    monkeypatch.setattr(clean_catalog.enrichment_sweep, "plan_requeue", lambda session: candidates)

    enqueued_ids: list[str] = []
    monkeypatch.setattr(
        "agentic_librarian.enrichment.tasks.enqueue_enrichment",
        lambda work_id: enqueued_ids.append(work_id) or True,
    )

    rc = clean_catalog.main(["--requeue-unenriched", "--apply", "--yes"])

    assert rc == 0
    assert str(pending_id) not in enqueued_ids
    assert str(never_id) in enqueued_ids
    assert str(no_real_id) in enqueued_ids
    assert len(enqueued_ids) == 2

    out = capsys.readouterr().out
    assert "pending a merge" in out
    assert "Pending Merge" in out
