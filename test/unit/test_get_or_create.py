"""Unit tests for the constraint-backed get_or_create/insert_or_requery helpers (GH #95).

Uses a STANDALONE sqlite model defined here (never the app models on sqlite — they're
pgvector/JSONB and won't create on sqlite). File-based sqlite (not :memory:) so a second
connection can see rows committed by the first, letting test_integrity_race_recovers
simulate a genuine concurrent-insert race.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import Column, String, create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from agentic_librarian.db.get_or_create import get_or_create, insert_or_requery


class _Base(DeclarativeBase):
    pass


class Widget(_Base):
    """Tiny standalone model with a unique column, just for exercising the helpers."""

    __tablename__ = "widgets"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    name = Column(String, unique=True, nullable=False)


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "get_or_create_test.db"


@pytest.fixture
def engine(db_path):
    eng = create_engine(f"sqlite:///{db_path}")
    _Base.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture
def session_factory(engine):
    return sessionmaker(bind=engine)


@pytest.fixture
def session(session_factory):
    s = session_factory()
    yield s
    s.close()


def test_returns_existing(session):
    """A pre-inserted row is returned as-is: (row, False), no new insert."""
    existing = Widget(name="alpha")
    session.add(existing)
    session.commit()

    instance, created = get_or_create(session, Widget, name="alpha")

    assert created is False
    assert instance.id == existing.id
    assert session.query(Widget).count() == 1


def test_empty_filters_raises(session):
    """No filter criteria at all means filter_by() matches an arbitrary first row -- reject
    it up front instead of silently returning/corrupting an unrelated row."""
    with pytest.raises(ValueError, match="filter criterion"):
        get_or_create(session, Widget)


def test_creates_when_missing(session):
    """An empty table creates the row: (row, True)."""
    instance, created = get_or_create(session, Widget, name="beta")

    assert created is True
    assert instance.name == "beta"
    assert session.query(Widget).filter_by(name="beta").count() == 1


def test_integrity_race_recovers(session_factory, engine):
    """Simulate the classic SELECT-then-INSERT race: session A's initial SELECT misses
    (row not yet there), a concurrent session B inserts and commits the same name, then
    session A's INSERT hits the unique constraint. The helper must recover by re-querying
    inside the except block -> (existing row from B, False) -- and the outer transaction on
    session A must still be usable afterward (proves begin_nested's SAVEPOINT rollback did
    not poison the outer transaction)."""
    session_a = session_factory()
    session_b = session_factory()
    try:
        # Pre-create the row that B will "concurrently" insert, but do it AFTER A's
        # first-query miss and BEFORE A's flush, by monkeypatching Session.flush on A's
        # session to insert-and-commit via B on first call.
        original_flush = Session.flush
        state = {"done": False}

        def _racy_flush(self, *args, **kwargs):
            if not state["done"]:
                state["done"] = True
                # Concurrent insert winner (session B) commits first.
                session_b.add(Widget(name="gamma"))
                session_b.commit()
            return original_flush(self, *args, **kwargs)

        session_a.flush = _racy_flush.__get__(session_a, Session)

        instance, created = get_or_create(session_a, Widget, name="gamma")

        assert created is False
        assert instance.name == "gamma"

        # Outer transaction on session_a must still be usable — begin_nested's SAVEPOINT
        # rollback must not have poisoned it.
        instance2, created2 = get_or_create(session_a, Widget, name="delta")
        assert created2 is True
        assert instance2.name == "delta"
        session_a.commit()

        assert session_a.query(Widget).filter_by(name="delta").count() == 1
    finally:
        session_a.close()
        session_b.close()


# --- insert_or_requery: for the case-insensitive authors/narrators sites ---


def test_insert_or_requery_creates_when_missing(session):
    requeried = {}

    def requery():
        requeried["called"] = True
        return session.query(Widget).filter(Widget.name == "case-test").first()

    instance = Widget(name="case-test")
    result, created = insert_or_requery(session, instance, requery)

    assert created is True
    assert result.name == "case-test"
    assert "called" not in requeried  # happy path never needs the requery lambda


def test_insert_or_requery_recovers_via_case_insensitive_requery(session_factory, engine):
    """The IntegrityError-recovery path must use the caller's requery callable (not an
    exact-match filter_by), because a functional unique on lower(name) can be violated by
    a DIFFERENT-CASE existing row that an exact filter_by(name=...) would miss entirely."""
    session_a = session_factory()
    session_b = session_factory()
    try:
        session_b.add(Widget(name="CasualFarmer"))
        session_b.commit()

        # session_a's app-level unique is only enforced at the DB via a plain UNIQUE column
        # here (sqlite doesn't support functional lower() unique indexes the same way), so
        # simulate the IntegrityError deterministically by attempting to insert a row whose
        # name collides post-normalization — the requery callable does the case-insensitive
        # lookup, mirroring func.lower(Author.name) == name.lower() in production.
        new_instance = Widget(id=str(uuid.uuid4()), name="CasualFarmer")

        def requery():
            return session_a.query(Widget).filter(Widget.name.ilike("casualfarmer")).first()

        result, created = insert_or_requery(session_a, new_instance, requery)

        assert created is False
        assert result.name == "CasualFarmer"
        assert result.id != new_instance.id  # got session_b's winner back, not a duplicate

        # Outer transaction still usable.
        another = Widget(name="unrelated")
        result2, created2 = insert_or_requery(
            session_a, another, lambda: session_a.query(Widget).filter_by(name="unrelated").first()
        )
        assert created2 is True
        session_a.commit()
    finally:
        session_a.close()
        session_b.close()


def test_insert_or_requery_reraises_when_requery_finds_nothing(session):
    """If the constraint fired but the caller's requery genuinely finds nothing, that's not
    the expected 'someone else won the race' case -- re-raise rather than silently return
    None (mirrors get_or_create's own re-raise for an unrelated constraint violation)."""

    class _BoomSession:
        def add(self, *_a, **_k):
            pass

        def flush(self):
            raise IntegrityError("stmt", "params", Exception("boom"))

        def begin_nested(self):
            import contextlib

            @contextlib.contextmanager
            def _cm():
                yield

            return _cm()

    def requery():
        return None

    with pytest.raises(IntegrityError):
        insert_or_requery(_BoomSession(), Widget(name="ghost"), requery)
