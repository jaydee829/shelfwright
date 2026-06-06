import json
from datetime import date
from pathlib import Path
from unittest.mock import patch

import pytest
from agentic_librarian.db.models import (
    Author,
    Edition,
    ReadingHistory,
    Suggestions,
    Trope,
    Work,
    WorkContributor,
    WorkTrope,
)
from agentic_librarian.db.session import DatabaseManager
from agentic_librarian.mcp.server import (
    get_unacted_suggestions,
    get_user_trope_preferences,
    search_internal_database,
    set_db_manager,
)

FIXTURE = json.loads((Path(__file__).parent.parent / "data" / "trope_embeddings.json").read_text())
ROMANCE = ["enemies to lovers", "slow burn romance"]
GRIMDARK = ["grimdark war", "brutal military strategy"]


def _seed_work(session, title, author_name, trope_names):
    author = Author(name=author_name)
    session.add(author)
    session.flush()
    work = Work(title=title)
    session.add(work)
    session.flush()
    session.add(WorkContributor(work=work, author=author, role="Author"))
    for name in trope_names:
        trope = Trope(name=name, embedding=FIXTURE[name])
        session.add(trope)
        session.flush()
        session.add(WorkTrope(work=work, trope=trope))
    return work


@pytest.mark.db_integration
def test_search_ranks_semantically_near_work_first(db_url, monkeypatch):
    # TropeManager/StyleManager read GOOGLE_SEARCH_API_KEY to construct a genai.Client in
    # __init__ (no network call at construction); set a dummy so the tool can be called.
    monkeypatch.setenv("GOOGLE_SEARCH_API_KEY", "dummy-key-for-construction")
    test_db_manager = DatabaseManager(db_url)
    set_db_manager(test_db_manager)

    with test_db_manager.get_session() as session:
        # Seed the FAR (grimdark) work first so insertion/heap order is the opposite of the
        # expected ranked order. The assertion then only holds if cosine ranking is applied
        # — an unordered IN-filter would return the grimdark work first and fail.
        _seed_work(session, "The Long War", "Grimdark Author", GRIMDARK)
        _seed_work(session, "A Courtship", "Romance Author", ROMANCE)
        session.commit()

    # The query-side embedding resolves a known string to the same cached real vector,
    # so cosine distances are deterministic.
    def fake_embedding(self, text):
        return FIXTURE[text]

    with patch("agentic_librarian.mcp.server.TropeManager._get_embedding", fake_embedding):
        results = search_internal_database(target_tropes=["enemies to lovers"])

    titles = [r["title"] for r in results]
    assert titles[:2] == ["A Courtship", "The Long War"], titles


@pytest.mark.db_integration
def test_user_trope_preferences_ranked_by_frequency(db_url):
    test_db_manager = DatabaseManager(db_url)
    set_db_manager(test_db_manager)

    with test_db_manager.get_session() as session:
        # "Fantasy" appears in 2 read works, "Mystery" in 1 -> Fantasy ranks first.
        fantasy = Trope(name="Fantasy")
        mystery = Trope(name="Mystery")
        session.add_all([fantasy, mystery])
        session.flush()
        for i, tropes in enumerate([[fantasy, mystery], [fantasy]]):
            author = Author(name=f"Auth {i}")
            session.add(author)
            session.flush()
            work = Work(title=f"Book {i}")
            session.add(work)
            session.flush()
            session.add(WorkContributor(work=work, author=author, role="Author"))
            for t in tropes:
                session.add(WorkTrope(work=work, trope=t))
            edition = Edition(work=work, format="hardcover")
            session.add(edition)
            session.flush()
            from agentic_librarian.core.user_context import DEFAULT_USER_ID
            session.add(ReadingHistory(edition=edition, user_id=DEFAULT_USER_ID, date_completed=date(2020, 1, 1)))
        session.commit()

    prefs = get_user_trope_preferences()
    assert prefs[0] == "Fantasy", prefs
    assert set(prefs) == {"Fantasy", "Mystery"}, prefs


@pytest.mark.db_integration
def test_get_unacted_suggestions_scores_embedded_suggestion(db_url, monkeypatch):
    # Regression: a Suggested work whose trope carries a real (array-valued) embedding must be
    # scorable. `if wt.trope.embedding` raised "truth value of an array is ambiguous" — surfaced by
    # the live recommendation e2e. The fix is an `is not None` check.
    monkeypatch.setenv("GOOGLE_SEARCH_API_KEY", "dummy-key-for-construction")
    test_db_manager = DatabaseManager(db_url)
    set_db_manager(test_db_manager)
    with test_db_manager.get_session() as session:
        author = Author(name="Romance Author")
        session.add(author)
        session.flush()
        work = Work(title="A Courtship")
        session.add(work)
        session.flush()
        session.add(WorkContributor(work=work, author=author, role="Author"))
        trope = Trope(name="enemies to lovers", embedding=FIXTURE["enemies to lovers"])
        session.add(trope)
        session.flush()
        session.add(WorkTrope(work=work, trope=trope))
        from agentic_librarian.core.user_context import DEFAULT_USER_ID
        session.add(Suggestions(work=work, user_id=DEFAULT_USER_ID, status="Suggested", justification="prior"))
        session.commit()

    def fake_embedding(self, text):
        return FIXTURE[text]

    with patch("agentic_librarian.mcp.server.TropeManager._get_embedding", fake_embedding):
        results = get_unacted_suggestions(target_tropes=["enemies to lovers"])

    assert any(r["title"] == "A Courtship" for r in results)
