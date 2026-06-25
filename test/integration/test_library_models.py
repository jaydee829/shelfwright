from datetime import UTC, datetime
from uuid import uuid4

import pytest

from agentic_librarian.db.models import AvailabilityCache, User, UserLibrary
from agentic_librarian.db.session import DatabaseManager

pytestmark = pytest.mark.db_integration


def test_user_library_roundtrip(db_url):
    manager = DatabaseManager(db_url)
    with manager.get_session() as session:
        user = User(id=uuid4(), email="t@example.com")
        session.add(user)
        session.flush()
        session.add(
            UserLibrary(
                user_id=user.id,
                provider="libby",
                library_slug="kcls",
                display_name="King County Library System",
                sort_order=0,
            )
        )
        session.flush()
        row = session.query(UserLibrary).filter_by(user_id=user.id).one()
        assert row.library_slug == "kcls"
        assert row.sort_order == 0


def test_availability_cache_roundtrip(db_url):
    manager = DatabaseManager(db_url)
    with manager.get_session() as session:
        session.add(
            AvailabilityCache(
                provider="libby",
                library_slug="kcls",
                norm_title="project hail mary",
                norm_author="andy weir",
                payload={"formats": [{"format": "Audiobook", "available": True}]},
                fetched_at=datetime.now(UTC),
            )
        )
        session.flush()
        row = session.query(AvailabilityCache).filter_by(library_slug="kcls").one()
        assert row.payload["formats"][0]["available"] is True
