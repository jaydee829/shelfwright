"""GET /works against the real schema in the isolated test DB (ADR-034)."""

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from agentic_librarian.api.main import app
from agentic_librarian.db.models import Author, Style, Trope, Work, WorkContributor, WorkStyle, WorkTrope
from agentic_librarian.db.session import DatabaseManager


@pytest.mark.db_integration
def test_get_works_end_to_end(db_url):
    manager = DatabaseManager(db_url)
    created = {}
    with manager.get_session() as session:
        author = Author(name="Lift Zero Author")
        trope = Trope(name="Lift Zero Trope")
        style = Style(name="Lift Zero Style", category="Work")
        work_a = Work(title="AAA Lift Zero First", original_publication_year=2001, genres=["Test"], moods=None)
        work_b = Work(title="ZZZ Lift Zero Last")
        work_a.contributors.append(WorkContributor(author=author, role="Author"))
        work_a.tropes.append(WorkTrope(trope=trope, relevance_score=1.0))
        work_a.styles.append(WorkStyle(style=style, attribute_type="perspective"))
        session.add_all([work_a, work_b])
        session.flush()
        # Keep raw UUID objects so work_id.in_() comparisons work; str() only for API assertions
        created["a_uuid"], created["b_uuid"] = work_a.id, work_b.id
        created["a"], created["b"] = str(work_a.id), str(work_b.id)

    try:
        with patch("agentic_librarian.api.main.db_manager", manager):
            client = TestClient(app)
            data = client.get("/works", params={"limit": 200}).json()
            by_id = {entry["id"]: entry for entry in data}

            assert created["a"] in by_id and created["b"] in by_id
            entry = by_id[created["a"]]
            assert entry["title"] == "AAA Lift Zero First"
            assert entry["authors"] == ["Lift Zero Author"]
            assert entry["publication_year"] == 2001
            assert entry["genres"] == ["Test"]
            assert entry["moods"] == []
            assert entry["tropes"] == ["Lift Zero Trope"]
            assert entry["styles"] == [{"attribute": "perspective", "name": "Lift Zero Style"}]

            # Ordering: AAA... before ZZZ... in the returned page
            ids_in_order = [e["id"] for e in data]
            assert ids_in_order.index(created["a"]) < ids_in_order.index(created["b"])

            # Pagination: limit=1 returns exactly one row
            assert len(client.get("/works", params={"limit": 1}).json()) == 1
    finally:
        uuid_values = [created["a_uuid"], created["b_uuid"]]
        with manager.get_session() as session:
            for model in (WorkStyle, WorkTrope, WorkContributor):
                session.query(model).filter(model.work_id.in_(uuid_values)).delete(synchronize_session=False)
            session.query(Work).filter(Work.id.in_(uuid_values)).delete(synchronize_session=False)
            session.query(Author).filter(Author.name == "Lift Zero Author").delete(synchronize_session=False)
            session.query(Trope).filter(Trope.name == "Lift Zero Trope").delete(synchronize_session=False)
            session.query(Style).filter(Style.name == "Lift Zero Style").delete(synchronize_session=False)
