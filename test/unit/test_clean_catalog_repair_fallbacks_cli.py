"""CLI-level guard tests for --repair-fallbacks-apply (GH #70, PR-D part 2) — mirrors
test_clean_catalog_prune_cli.py's structure. The planner/report/apply mechanics themselves are
covered by test/unit/test_fallback_repair.py (pure) and test/integration/test_fallback_repair.py
(db_integration); this file is about the CLI's own --yes / prod-url / --report guards."""

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

    def query(self, *a, **k):
        return self

    def filter(self, *a, **k):
        return self

    def filter_by(self, *a, **k):
        return self

    def distinct(self):
        return self

    def all(self):
        return []

    def count(self):
        return 0

    def scalar(self):
        return 0

    def __iter__(self):
        return iter([])


def _patch_db(monkeypatch, *, url="postgresql://u:p@prod-host/db"):
    monkeypatch.setattr(clean_catalog, "resolve_database_url", lambda: url)
    monkeypatch.setattr(
        clean_catalog, "DatabaseManager", lambda db_url: type("M", (), {"get_session": lambda s: _Sess()})()
    )


@pytest.mark.parametrize(
    "argv, expected_rc, expected_message",
    [
        (
            ["--repair-fallbacks-apply", "--report", "data/reports/fake.txt"],
            2,
            "REFUSING --repair-fallbacks-apply without --yes",
        ),
        (
            ["--repair-fallbacks-apply", "--yes"],
            1,
            "REFUSING --repair-fallbacks-apply: --report is required",
        ),
    ],
    ids=["missing_yes", "missing_report"],
)
def test_repair_fallbacks_apply_refused(monkeypatch, capsys, argv, expected_rc, expected_message):
    _patch_db(monkeypatch)
    rc = clean_catalog.main(argv)
    assert rc == expected_rc
    assert expected_message in capsys.readouterr().out


def test_repair_fallbacks_apply_refused_on_non_prod_url(monkeypatch, capsys, tmp_path):
    _patch_db(monkeypatch, url="postgresql://u:p@localhost/db")
    report = tmp_path / "fallback-repair-fake.txt"
    report.write_text("== PLAN TOKENS ==\n== END PLAN TOKENS ==\n", encoding="utf-8")
    rc = clean_catalog.main(["--repair-fallbacks-apply", "--yes", "--report", str(report)])
    assert rc == 2
    assert "REFUSING --repair-fallbacks-apply: 'localhost/db' is not a live prod DB" in capsys.readouterr().out


def test_repair_fallbacks_dry_run_never_applies(monkeypatch, capsys, tmp_path):
    """--repair-fallbacks (no --apply variant exists for this flag) always writes a report and
    returns 0 without needing --yes or a prod URL — it's read-only by construction. plan
    construction itself does real SQLAlchemy query-building (order_by/join) — stub it, same as
    test_clean_catalog_prune_cli.py stubs plan_fallback_prune; this test is only about the CLI's
    report-write/return-0 wiring."""
    _patch_db(monkeypatch, url="postgresql://u:p@localhost/db")
    monkeypatch.setattr(clean_catalog.fallback_repair, "warm_fallback_repair_texts", lambda session: [])
    monkeypatch.setattr(
        clean_catalog.fallback_repair,
        "plan_fallback_repair",
        lambda session: clean_catalog.fallback_repair.FallbackRepairPlan(),
    )
    monkeypatch.chdir(tmp_path)
    rc = clean_catalog.main(["--repair-fallbacks"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "repair-fallbacks plan" in out
    assert (tmp_path / "data" / "reports").exists()
