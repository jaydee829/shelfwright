"""CLI-level guard tests for --merge-works-apply (Spec 2026-07-14 PR-2 part 2, H2) — mirrors
test_clean_catalog_repair_fallbacks_cli.py's structure exactly (same --yes / prod-url / --report
guard shape, same drift-refusal printout shape). The planner/composition/gate mechanics
themselves are covered by test/unit/test_works_merge.py (pure) and
test/integration/test_works_merge_apply.py (db_integration); this file is about the CLI's own
guards and its drift-refusal printout wiring."""

import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "clean_catalog", Path(__file__).resolve().parents[2] / "scripts" / "clean_catalog.py"
)
clean_catalog = importlib.util.module_from_spec(spec)
spec.loader.exec_module(clean_catalog)


class _Sess:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _patch_db(monkeypatch, *, url="postgresql://u:p@prod-host/db"):
    monkeypatch.setattr(clean_catalog, "resolve_database_url", lambda: url)
    monkeypatch.setattr(
        clean_catalog, "DatabaseManager", lambda db_url: type("M", (), {"get_session": lambda s: _Sess()})()
    )


@pytest.mark.parametrize(
    "argv, expected_rc, expected_message",
    [
        (
            ["--merge-works-apply", "--report", "data/reports/fake.txt"],
            2,
            "REFUSING --merge-works-apply without --yes",
        ),
        (
            ["--merge-works-apply", "--yes"],
            1,
            "REFUSING --merge-works-apply: --report is required",
        ),
    ],
    ids=["missing_yes", "missing_report"],
)
def test_merge_works_apply_refused(monkeypatch, capsys, argv, expected_rc, expected_message):
    _patch_db(monkeypatch)
    rc = clean_catalog.main(argv)
    assert rc == expected_rc
    assert expected_message in capsys.readouterr().out


def test_merge_works_apply_refused_on_non_prod_url(monkeypatch, capsys, tmp_path):
    _patch_db(monkeypatch, url="postgresql://u:p@localhost/db")
    report = tmp_path / "works-merge-fake.txt"
    report.write_text("== PLAN TOKENS ==\n== END PLAN TOKENS ==\n", encoding="utf-8")
    rc = clean_catalog.main(["--merge-works-apply", "--yes", "--report", str(report)])
    assert rc == 2
    assert "REFUSING --merge-works-apply: 'localhost/db' is not a live prod DB" in capsys.readouterr().out


def test_merge_works_apply_drift_refusal_printout(monkeypatch, capsys, tmp_path):
    """apply_works_merge raising WorksMergeDriftError is caught and its delta tokens + fresh
    report path are printed for re-review — mirrors --repair-fallbacks-apply's refusal UX. Only
    the CLI's own catch/print wiring is under test here; apply_works_merge itself is stubbed
    (its re-plan/refuse mechanics are covered by test/integration/test_works_merge_apply.py's
    drift e2e)."""
    _patch_db(monkeypatch)
    report = tmp_path / "works-merge-reviewed.txt"
    report.write_text("== PLAN TOKENS ==\n== END PLAN TOKENS ==\n", encoding="utf-8")
    fresh_report_path = tmp_path / "works-merge-fresh.txt"
    delta = {"delete_work:11111111-1111-1111-1111-111111111111"}

    def _raise_drift(session, reviewed_report_path):
        raise clean_catalog.dedup_backfill.WorksMergeDriftError(delta, fresh_report_path)

    monkeypatch.setattr(clean_catalog.dedup_backfill, "apply_works_merge", _raise_drift)

    rc = clean_catalog.main(["--merge-works-apply", "--yes", "--report", str(report)])

    assert rc == 1
    out = capsys.readouterr().out
    assert "REFUSING --merge-works-apply" in out
    assert "drifted" in out
    for token in delta:
        assert token in out
    assert str(fresh_report_path) in out
    assert f"--merge-works-apply --yes --report {fresh_report_path}" in out


def test_merge_works_apply_success_prints_applied_counts_and_orphan_pointer(monkeypatch, capsys, tmp_path):
    """The generic 'applied:' counts loop plus the orphan-author pointer sentence (deliverable
    1.6) — printed only when the count is non-zero."""
    _patch_db(monkeypatch)
    report = tmp_path / "works-merge-reviewed.txt"
    report.write_text("== PLAN TOKENS ==\n== END PLAN TOKENS ==\n", encoding="utf-8")

    applied = {
        "repoint_edition": 0,
        "merge_edition": 1,
        "repoint_read": 0,
        "drop_duplicate_read": 0,
        "repoint_narrator": 0,
        "drop_narrator": 0,
        "repoint_suggestion": 0,
        "drop_duplicate_suggestion": 0,
        "copy_link": 0,
        "drop_link": 0,
        "copy_contributor": 0,
        "drop_contributor": 0,
        "delete_detection": 0,
        "delete_work": 1,
        "skipped_stale": 0,
        "orphaned_authors_pointer": 2,
    }
    monkeypatch.setattr(clean_catalog.dedup_backfill, "apply_works_merge", lambda session, report_path: applied)

    rc = clean_catalog.main(["--merge-works-apply", "--yes", "--report", str(report)])

    assert rc == 0
    out = capsys.readouterr().out
    assert "merge_edition" in out
    assert "2 author(s) may be orphaned — run --dedup-for-constraints dry-run to sweep." in out
