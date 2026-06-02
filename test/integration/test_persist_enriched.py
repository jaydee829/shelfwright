import pytest
from agentic_librarian.db.session import DatabaseManager
from agentic_librarian.etl.persist import persist_enriched_work
from agentic_librarian.scouts.style_manager import StyleManager
from agentic_librarian.scouts.trope_manager import TropeManager


@pytest.mark.db_integration
def test_persist_tolerates_dict_style_value(db_url, monkeypatch):
    # Regression (REC-021): a work_style attribute whose value is a dict must not crash persistence.
    monkeypatch.setenv("GOOGLE_SEARCH_API_KEY", "dummy-key-for-construction")
    dbm = DatabaseManager(db_url)
    with dbm.get_session() as session:
        tm = TropeManager(session=session)
        sm = StyleManager(session=session)
        from agentic_librarian.db.models import Style

        monkeypatch.setattr(
            sm, "standardize_style", lambda raw, category, threshold=0.85: Style(name=raw, category=category)
        )
        row = {
            "Title": "Dict Style Book",
            "Author_1": "Some Author",
            "format": "ebook",
            "skip_enrichment": False,
            "date_completed": None,
            "contributors": [{"name": "Some Author", "role": "Author"}],
            "work_style": {"perspective": "1st person", "differences": {"pacing": "fast"}},
        }
        work = persist_enriched_work(session, row, tm, sm)
        session.flush()
        assert work is not None and work.title == "Dict Style Book"

        # The valid scalar attribute persists; the dict-valued "differences" is skipped by the guard.
        from agentic_librarian.db.models import WorkStyle

        attr_types = {ws.attribute_type for ws in session.query(WorkStyle).filter_by(work_id=work.id).all()}
        assert "perspective" in attr_types
        assert "differences" not in attr_types


@pytest.mark.db_integration
def test_persist_tolerates_nan_narrator_fields(db_url, monkeypatch):
    # Regression: pandas fills narrator_names/narrator_styles with NaN (float) for rows that lack them
    # (e.g. a skip_enrichment row mixed with audiobook rows in the same partition DataFrame). Persist
    # must coerce non-list/dict to empty rather than crash on `for n_name in narrator_names`.
    monkeypatch.setenv("GOOGLE_SEARCH_API_KEY", "dummy-key-for-construction")
    dbm = DatabaseManager(db_url)
    with dbm.get_session() as session:
        tm = TropeManager(session=session)
        sm = StyleManager(session=session)
        row = {
            "Title": "NaN Narrator Book",
            "Author_1": "Nan Author",
            "format": "hardcover",
            "skip_enrichment": True,
            "date_completed": None,
            "contributors": [{"name": "Nan Author", "role": "Author"}],
            "narrator_names": float("nan"),
            "narrator_styles": float("nan"),
        }
        work = persist_enriched_work(session, row, tm, sm)
        session.flush()
        assert work is not None and work.title == "NaN Narrator Book"
