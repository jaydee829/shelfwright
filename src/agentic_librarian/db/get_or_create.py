"""Constraint-backed get-or-create (GH #95). The SELECT-then-INSERT races that used to
create duplicates are now backstopped by unique constraints; this helper turns the
IntegrityError loser into a clean re-query instead of a 500. SAVEPOINT (begin_nested)
so the caller's outer transaction survives the rolled-back insert.

Two helpers:

- `get_or_create` — filters and re-queries with the SAME exact-match predicate
  (`filter_by(**filters)`). Fine when the DB constraint matches the filter predicate
  1:1 (editions on (work_id, format), reading_history, suggestions).

- `insert_or_requery` — for sites whose DB constraint is NOT an exact-match predicate,
  e.g. authors/narrators' `uq_*_name_lower` fires on `lower(name)` while an exact
  `filter_by(name=name)` re-query would MISS a case-variant winner ("CasualFarmer" vs
  "casualfarmer" collide at the DB but not under `==`). Callers keep their existing
  case-insensitive first-query and pass a `requery` callable that performs the same
  case-insensitive lookup for the recovery path.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar

from sqlalchemy.exc import IntegrityError

T = TypeVar("T")


def get_or_create(session, model, defaults=None, **filters):
    instance = session.query(model).filter_by(**filters).first()
    if instance is not None:
        return instance, False
    params = dict(filters)
    params.update(defaults or {})
    try:
        with session.begin_nested():
            instance = model(**params)
            session.add(instance)
            session.flush()
        return instance, True
    except IntegrityError:
        instance = session.query(model).filter_by(**filters).first()
        if instance is None:  # constraint fired but filters don't match it (e.g. case-variant name)
            raise
        return instance, False


def insert_or_requery(session, instance: T, requery: Callable[[], T | None]) -> tuple[T, bool]:
    """Insert `instance` (already query-missed by the caller's own first lookup), wrapped
    in the same begin_nested/IntegrityError-requery pattern as get_or_create — but recovery
    uses the caller-supplied `requery` callable instead of an exact-match filter_by, since
    the backing constraint may not be an exact-match predicate (case-insensitive unique
    indexes on authors/narrators). Returns (instance, created)."""
    try:
        with session.begin_nested():
            session.add(instance)
            session.flush()
        return instance, True
    except IntegrityError:
        existing = requery()
        if existing is None:  # constraint fired but the requery genuinely finds nothing — not our race
            raise
        return existing, False
