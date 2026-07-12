"""Integration tests exercising the real Task-1 constraints (48e3762d6c0c) against the
Task-2 get_or_create/insert_or_requery adoption sites (GH #95; log_suggestion dedup
closes #88's root cause). Sequential double-insert (not concurrent-thread races — the
unit suite already proves the SAVEPOINT recovery mechanics; this file proves the real
Postgres constraints actually fire and the adoption sites recover from them)."""

from __future__ import annotations

import pytest
from sqlalchemy import func

from agentic_librarian.core.user_context import DEFAULT_USER_ID
from agentic_librarian.db.get_or_create import get_or_create, insert_or_requery
from agentic_librarian.db.models import Author, Edition, Suggestions, Work, WorkContributor
from agentic_librarian.db.session import DatabaseManager
from agentic_librarian.mcp.server import log_suggestion, set_db_manager

pytestmark = pytest.mark.db_integration


def _seed_work(manager, title="Constraint Test Work", author="Constraint Test Author"):
    with manager.get_session() as s:
        a = Author(name=author)
        w = Work(title=title, contributors=[WorkContributor(author=a, role="Author")])
        s.add_all([a, w])
        s.flush()
        s.commit()
        return w.id


def test_same_cased_author_double_insert_resolves_to_one_row(db_url):
    manager = DatabaseManager(db_url)
    with manager.get_session() as s1:
        s1.add(Author(name="Duplicate Author"))
        s1.commit()

    with manager.get_session() as s2:
        author, created = insert_or_requery(
            s2,
            Author(name="Duplicate Author"),
            lambda: s2.query(Author).filter(func.lower(Author.name) == "duplicate author").first(),
        )
        assert created is False
        assert author.name == "Duplicate Author"

    with manager.get_session() as s3:
        assert s3.query(Author).filter(Author.name.ilike("duplicate author")).count() == 1


def test_case_variant_author_double_insert_resolves_to_one_row(db_url):
    """uq_authors_name_lower fires on lower(name) -- a case-variant second insert must be
    caught by insert_or_requery's case-insensitive requery, not missed like an exact
    filter_by(name=...) would miss it."""
    manager = DatabaseManager(db_url)
    with manager.get_session() as s1:
        s1.add(Author(name="CasualFarmer"))
        s1.commit()

    with manager.get_session() as s2:
        name = "casualfarmer"  # different case than the pre-existing row
        author, created = insert_or_requery(
            s2,
            Author(name=name),
            lambda: s2.query(Author).filter(func.lower(Author.name) == name.lower()).first(),
        )
        assert created is False
        assert author.name == "CasualFarmer"  # the ORIGINAL row's casing, not a new row

    with manager.get_session() as s3:
        assert s3.query(Author).filter(Author.name.ilike("casualfarmer")).count() == 1


def test_duplicate_active_suggestion_via_log_suggestion_resolves_to_one_row(db_url):
    """GH #88 root cause: log_suggestion previously had zero dedup and would blindly
    INSERT a second active suggestion for the same (user, work). uq_suggestions_active is
    the partial unique backstop; log_suggestion must now dedup via get_or_create and
    return the 'not duplicated' message on the second call."""
    manager = DatabaseManager(db_url)
    set_db_manager(manager)
    work_id = _seed_work(manager)

    first = log_suggestion(work_id=str(work_id), context="first rec", justification="reason one")
    assert "Logged suggestion" in first

    second = log_suggestion(work_id=str(work_id), context="second rec", justification="reason two")
    assert "Already an active suggestion" in second
    assert "not duplicated" in second

    with manager.get_session() as s:
        rows = s.query(Suggestions).filter_by(work_id=work_id, user_id=DEFAULT_USER_ID, status="Suggested").all()
        assert len(rows) == 1
        # The FIRST call's context/justification won -- the second call did not overwrite.
        assert rows[0].context == "first rec"


def test_duplicate_edition_via_get_or_create_resolves_to_one_row(db_url):
    """uq_editions_work_format backstop: two sequential get_or_create calls for the same
    (work_id, format) must resolve to a single Edition row (mirrors the ingest/persist/
    add_read_event adoption sites)."""
    manager = DatabaseManager(db_url)
    work_id = _seed_work(manager, title="Edition Constraint Work")

    with manager.get_session() as s1:
        edition1, created1 = get_or_create(s1, Edition, work_id=work_id, format="ebook")
        assert created1 is True
        s1.flush()
        edition1_id = edition1.id  # capture the scalar before the session closes (detached-instance rule)

    with manager.get_session() as s2:
        edition2, created2 = get_or_create(s2, Edition, work_id=work_id, format="ebook")
        assert created2 is False
        assert edition2.id == edition1_id

    with manager.get_session() as s3:
        assert s3.query(Edition).filter_by(work_id=work_id, format="ebook").count() == 1
