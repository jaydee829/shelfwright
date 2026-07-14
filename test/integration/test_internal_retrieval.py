import json
from datetime import date
from pathlib import Path
from unittest.mock import patch

import pytest

from agentic_librarian.core.user_context import DEFAULT_USER_ID
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
def test_search_pool_selection_is_ranked_at_limit_one(db_url, monkeypatch):
    """#125 regression: the candidate POOL itself must be relevance-ranked. The old code
    joined works to the nearest tropes with an unordered LIMIT, so at limit=1 the pool was
    whatever the heap returned first (the grimdark work, seeded first) and the ranking pass
    never saw the romance work at all."""
    monkeypatch.setenv("GOOGLE_SEARCH_API_KEY", "dummy-key-for-construction")
    test_db_manager = DatabaseManager(db_url)
    set_db_manager(test_db_manager)

    with test_db_manager.get_session() as session:
        _seed_work(session, "The Long War", "Grimdark Author", GRIMDARK)
        _seed_work(session, "A Courtship", "Romance Author", ROMANCE)
        session.commit()

    def fake_embedding(self, text):
        return FIXTURE[text]

    with patch("agentic_librarian.mcp.server.TropeManager._get_embedding", fake_embedding):
        results = search_internal_database(target_tropes=["enemies to lovers"], limit=1)

    assert [r["title"] for r in results] == ["A Courtship"]


@pytest.mark.db_integration
def test_search_drops_candidates_matching_negative_targets(db_url, monkeypatch):
    """#125: 'less fantasy, more thriller' must structurally exclude, not politely request.
    A work whose tropes sit closer to an exclude target than to the positive target is
    dropped from the results entirely."""
    monkeypatch.setenv("GOOGLE_SEARCH_API_KEY", "dummy-key-for-construction")
    test_db_manager = DatabaseManager(db_url)
    set_db_manager(test_db_manager)

    with test_db_manager.get_session() as session:
        _seed_work(session, "The Long War", "Grimdark Author", GRIMDARK)
        _seed_work(session, "A Courtship", "Romance Author", ROMANCE)
        session.commit()

    def fake_embedding(self, text):
        return FIXTURE[text]

    with patch("agentic_librarian.mcp.server.TropeManager._get_embedding", fake_embedding):
        results = search_internal_database(target_tropes=["enemies to lovers"], exclude_tropes=["grimdark war"])

    titles = [r["title"] for r in results]
    assert "A Courtship" in titles
    assert "The Long War" not in titles


@pytest.mark.db_integration
def test_recommendation_candidates_exclude_actively_suggested_work(db_url, monkeypatch):
    """#125: a work with an active 'Suggested' row must not re-enter fresh candidate sets
    (Starsight was re-pitched after the user deflected it); once the suggestion is
    resolved (Dismissed), the work becomes a candidate again."""
    monkeypatch.setenv("GOOGLE_SEARCH_API_KEY", "dummy-key-for-construction")
    test_db_manager = DatabaseManager(db_url)
    set_db_manager(test_db_manager)

    with test_db_manager.get_session() as session:
        work = _seed_work(session, "A Courtship", "Romance Author", ROMANCE)
        session.add(Suggestions(work=work, user_id=DEFAULT_USER_ID, status="Suggested", justification="prior"))
        session.commit()
        work_id = work.id

    from agentic_librarian.mcp.server import get_recommendation_candidates

    def fake_embedding(self, text):
        return FIXTURE[text]

    with patch("agentic_librarian.mcp.server.TropeManager._get_embedding", fake_embedding):
        out = get_recommendation_candidates(target_tropes=["enemies to lovers"])
    assert all(c["title"] != "A Courtship" for c in out["candidates"]), out

    with test_db_manager.get_session() as session:
        row = session.query(Suggestions).filter(Suggestions.work_id == work_id).one()
        row.status = "Dismissed"

    with patch("agentic_librarian.mcp.server.TropeManager._get_embedding", fake_embedding):
        out = get_recommendation_candidates(target_tropes=["enemies to lovers"])
    assert any(c["title"] == "A Courtship" for c in out["candidates"]), out


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
        session.add(Suggestions(work=work, user_id=DEFAULT_USER_ID, status="Suggested", justification="prior"))
        session.commit()

    def fake_embedding(self, text):
        return FIXTURE[text]

    with patch("agentic_librarian.mcp.server.TropeManager._get_embedding", fake_embedding):
        results = get_unacted_suggestions(target_tropes=["enemies to lovers"])

    assert any(r["title"] == "A Courtship" for r in results)
