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

    def __iter__(self):
        return iter([])


def test_prune_fallbacks_refused_without_yes(monkeypatch, capsys):
    monkeypatch.setattr(clean_catalog, "resolve_database_url", lambda: "postgresql://u:p@prod-host/db")
    monkeypatch.setattr(
        clean_catalog, "DatabaseManager", lambda url: type("M", (), {"get_session": lambda s: _Sess()})()
    )
    # plan does real SQLAlchemy query construction (subquery/joins) — stub it; this test is about the
    # CLI's --apply/--yes guard, not the query.
    monkeypatch.setattr(clean_catalog.trope_backfill, "plan_fallback_prune", lambda session: [])
    rc = clean_catalog.main(["--prune-fallbacks", "--apply"])  # no --yes
    assert rc == 2
    assert "REFUSING --apply without --yes" in capsys.readouterr().out
