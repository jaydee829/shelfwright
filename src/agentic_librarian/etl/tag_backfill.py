"""Backfill logic for genre/mood cleaning (Spec 2026-06-22): session in, lists out, no CLI/I-O of
its own. The thin operator CLI is scripts/clean_tags.py."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from uuid import UUID

from agentic_librarian.db.models import Work
from agentic_librarian.etl.tag_cleaning import clean_genres, clean_moods


@dataclass
class Change:
    work_id: UUID
    title: str
    genres_before: list[str]
    genres_after: list[str]
    moods_before: list[str]
    moods_after: list[str]


def plan_changes(session) -> list[Change]:
    """Works whose cleaned genres/moods differ from what's stored."""
    out: list[Change] = []
    for w in session.query(Work).all():
        gb, mb = list(w.genres or []), list(w.moods or [])
        ga, ma = clean_genres(gb), clean_moods(mb)
        if ga != gb or ma != mb:
            out.append(Change(w.id, w.title, gb, ga, mb, ma))
    return out


def apply_changes(session, changes: list[Change] | None = None) -> int:
    """Apply the given changes (or compute them if not supplied). Passing the list the caller
    already previewed guarantees applied == previewed."""
    if changes is None:
        changes = plan_changes(session)
    n = 0
    for c in changes:
        w = session.get(Work, c.work_id)  # identity-map hit — plan_changes already loaded it
        if w is None:  # deleted between plan and apply — skip
            continue
        w.genres, w.moods = c.genres_after, c.moods_after
        n += 1
    return n


def inventory(session) -> tuple[Counter, Counter]:
    genres: Counter = Counter()
    moods: Counter = Counter()
    for w in session.query(Work).all():
        genres.update(w.genres or [])
        moods.update(w.moods or [])
    return genres, moods


def is_prod_url(url: str) -> bool:
    """True only for a real remote/proxy Postgres — NOT sqlite, backups, or a local dev DB."""
    u = (url or "").lower()
    if u.startswith("sqlite") or "/backups/" in u or "data/backups" in u:
        return False
    if "@localhost" in u or "@127.0.0.1" in u:
        return False
    return u.startswith("postgresql")
