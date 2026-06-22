"""plan_changes / apply_changes over real rows (Spec 2026-06-22)."""

import pytest

from agentic_librarian.db.models import Author, Edition, Work, WorkContributor
from agentic_librarian.db.session import DatabaseManager
from agentic_librarian.etl import tag_backfill

pytestmark = pytest.mark.db_integration

UUID = "4c14c349-8d52-4893-aaf0-34f7e33bf275"


def _seed(manager, genres, moods):
    with manager.get_session() as s:
        a = Author(name="B. Author")
        w = Work(
            title="Backfill Test", contributors=[WorkContributor(author=a, role="Author")], genres=genres, moods=moods
        )
        s.add_all([a, w, Edition(work=w, format="ebook")])
        s.flush()
        return w.id


def test_plan_and_apply(db_url):
    manager = DatabaseManager(db_url)
    wid = _seed(manager, [f"science-fiction-fantasy-{UUID}", f"audiobook-{UUID}"], [f"dark-{UUID}", "Dark"])

    with manager.get_session() as session:
        mine = [c for c in tag_backfill.plan_changes(session) if c.work_id == wid]
        assert len(mine) == 1
        assert mine[0].genres_after == ["Science Fiction", "Fantasy"]
        assert mine[0].moods_after == ["Dark"]

    with manager.get_session() as session:
        tag_backfill.apply_changes(session)

    with manager.get_session() as session:
        w = session.get(Work, wid)
        assert w.genres == ["Science Fiction", "Fantasy"]
        assert w.moods == ["Dark"]
        assert all(c.work_id != wid for c in tag_backfill.plan_changes(session))  # idempotent
