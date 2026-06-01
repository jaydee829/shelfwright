"""Deterministic DB seed for recommendation tests. Uses Spec 3's real-embedding fixture
so vector search behaves realistically without API calls."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

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
from sqlalchemy import select

_FIXTURE = json.loads((Path(__file__).parent.parent / "data" / "trope_embeddings.json").read_text())
ROMANCE = ["enemies to lovers", "slow burn romance"]
GRIMDARK = ["grimdark war", "brutal military strategy"]


def _work(session, title, author_name, trope_names, *, read_on=None, suggested=False):
    author = Author(name=author_name)
    session.add(author)
    session.flush()
    work = Work(title=title, genres=["Fantasy"])
    session.add(work)
    session.flush()
    session.add(WorkContributor(work=work, author=author, role="Author"))
    for name in trope_names:
        trope = session.execute(select(Trope).where(Trope.name == name)).scalar_one_or_none()
        if trope is None:
            trope = Trope(name=name, embedding=_FIXTURE[name])
            session.add(trope)
            session.flush()
        session.add(WorkTrope(work=work, trope=trope, justification=f"{title} embodies {name}."))
    edition = Edition(work=work, format="hardcover")
    session.add(edition)
    session.flush()
    if read_on is not None:
        session.add(ReadingHistory(edition=edition, date_completed=read_on))
    if suggested:
        session.add(Suggestions(work=work, status="Suggested", justification="prior suggestion"))
    return work


def seed_recommendation_fixture(session):
    """Seed: one read grimdark book (history), one unacted romance suggestion, and a
    romance backlist title. Returns a dict of titles for assertions."""
    read = _work(session, "The Long War", "Grimdark Author", GRIMDARK, read_on=date(2020, 1, 1))
    suggested = _work(session, "A Courtship", "Romance Author", ROMANCE, suggested=True)
    backlist = _work(session, "Second Chances", "Other Romance Author", ROMANCE)
    session.commit()
    return {"read": read.title, "suggested": suggested.title, "backlist": backlist.title}
