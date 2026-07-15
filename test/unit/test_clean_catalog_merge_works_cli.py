"""CLI-level test for --merge-works (Spec 2026-07-14 PR-2 part 1) — mirrors
test_clean_catalog_repair_fallbacks_cli.py's dry-run-only structure. --merge-works is
detection/planning ONLY (no apply counterpart exists yet), so unlike --repair-fallbacks-apply
there is no --yes / prod-url / --report guard to test here — just the report-write/return-0
wiring. The planner/report mechanics themselves are covered by test/unit/test_works_merge.py
(pure) and test/integration/test_works_merge.py (db_integration)."""

import importlib.util
from pathlib import Path

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


def _patch_db(monkeypatch, *, url="postgresql://u:p@localhost/db"):
    monkeypatch.setattr(clean_catalog, "resolve_database_url", lambda: url)
    monkeypatch.setattr(
        clean_catalog, "DatabaseManager", lambda db_url: type("M", (), {"get_session": lambda s: _Sess()})()
    )


def test_merge_works_dry_run_never_applies(monkeypatch, capsys, tmp_path):
    """--merge-works always writes a report and returns 0 without --yes or a prod URL — it's
    read-only by construction, even against what CLI dispatch treats as a non-prod localhost
    URL. plan_works_merge itself does real SQLAlchemy query-building — stub it, same as the
    repair-fallbacks CLI test stubs plan_fallback_repair."""
    _patch_db(monkeypatch, url="postgresql://u:p@localhost/db")
    monkeypatch.setattr(
        clean_catalog.dedup_backfill,
        "plan_works_merge",
        lambda session: clean_catalog.dedup_backfill.WorksMergeClusters(),
    )
    monkeypatch.chdir(tmp_path)
    rc = clean_catalog.main(["--merge-works"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "works-merge plan (DETECTION ONLY" in out
    assert "apply arrives in a follow-up" in out
    assert (tmp_path / "data" / "reports").exists()
    written = list((tmp_path / "data" / "reports").glob("works-merge-*.txt"))
    assert len(written) == 1
    assert "NEVER APPLIED" in written[0].read_text(encoding="utf-8")
