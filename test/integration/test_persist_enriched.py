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


@pytest.mark.db_integration
def test_persist_tolerates_nan_enrichment_fields(db_url, monkeypatch):
    # Regression (PR #27 review, gemini-code-assist): with skip_enrichment=False, the enrichment
    # columns enriched_tropes/genres/moods arriving as pandas NaN (float) must not crash with
    # 'float object is not iterable', and the scalar columns original_publication_year/user_rating/
    # publication_date/page_count as NaN must not raise DatatypeMismatch on the typed columns.
    monkeypatch.setenv("GOOGLE_SEARCH_API_KEY", "dummy-key-for-construction")
    dbm = DatabaseManager(db_url)
    with dbm.get_session() as session:
        tm = TropeManager(session=session)
        sm = StyleManager(session=session)
        row = {
            "Title": "NaN Enrichment Book",
            "Author_1": "Nan Enrich Author",
            "format": "ebook",
            "skip_enrichment": False,
            "date_completed": None,
            "contributors": [{"name": "Nan Enrich Author", "role": "Author"}],
            "enriched_tropes": float("nan"),
            "genres": float("nan"),
            "moods": float("nan"),
            "original_publication_year": float("nan"),
            "user_rating": float("nan"),
            "publication_date": float("nan"),
            "page_count": float("nan"),
        }
        work = persist_enriched_work(session, row, tm, sm)
        session.flush()
        assert work is not None and work.title == "NaN Enrichment Book"
        # Scalars coerced to NULL, not NaN.
        assert work.original_publication_year is None
        assert work.genres == []


@pytest.mark.db_integration
def test_persist_skips_nameless_contributors(db_url, monkeypatch):
    # Regression: malformed scout output can include a contributor with a None/blank name. Inserting
    # it violates the authors.name NOT NULL constraint and crashes the partition. Persist must skip
    # nameless contributors and still create the work from the valid ones.
    monkeypatch.setenv("GOOGLE_SEARCH_API_KEY", "dummy-key-for-construction")
    dbm = DatabaseManager(db_url)
    with dbm.get_session() as session:
        tm = TropeManager(session=session)
        sm = StyleManager(session=session)
        row = {
            "Title": "Nameless Contributor Book",
            "Author_1": "Real Author",
            "format": "audiobook",
            "skip_enrichment": True,
            "date_completed": None,
            "contributors": [
                {"name": None, "role": "Author"},
                {"name": "Real Author", "role": "Author"},
                {"name": "   ", "role": "Narrator"},
            ],
        }
        work = persist_enriched_work(session, row, tm, sm)
        session.flush()
        assert work is not None and work.title == "Nameless Contributor Book"
        names = [c.author.name for c in work.contributors]
        assert names == ["Real Author"]  # the None and blank contributors are skipped


@pytest.mark.db_integration
def test_persist_defaults_blank_or_invalid_role_to_author(db_url, monkeypatch):
    # Regression (PR #30 review, gemini-code-assist): a whitespace-only role is truthy, so
    # `role or "Author"` would persist it as-is — a malformed role value in work_contributors.
    # Non-string roles from malformed scout output must also fall back to "Author"; a valid
    # role keeps its stripped value.
    monkeypatch.setenv("GOOGLE_SEARCH_API_KEY", "dummy-key-for-construction")
    dbm = DatabaseManager(db_url)
    with dbm.get_session() as session:
        tm = TropeManager(session=session)
        sm = StyleManager(session=session)
        row = {
            "Title": "Blank Role Book",
            "Author_1": "Whitespace Role Author",
            "format": "ebook",
            "skip_enrichment": True,
            "date_completed": None,
            "contributors": [
                {"name": "Whitespace Role Author", "role": "   "},
                {"name": "Padded Role Editor", "role": " Editor "},
                {"name": "Numeric Role Author", "role": 7},
            ],
        }
        work = persist_enriched_work(session, row, tm, sm)
        session.flush()
        assert work is not None and work.title == "Blank Role Book"
        roles = {c.author.name: c.role for c in work.contributors}
        assert roles == {
            "Whitespace Role Author": "Author",
            "Padded Role Editor": "Editor",
            "Numeric Role Author": "Author",
        }
