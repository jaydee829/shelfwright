"""Dedup Author/Narrator rows that differ only by case/whitespace, folding all links onto one
survivor (Spec 2026-06-23). Session in, summary out; the CLI is scripts/clean_catalog.py. Sibling
of etl/tag_backfill.py for the contributor tables."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from agentic_librarian.db.models import Author, AuthorStyle, Narrator, NarratorStyle, WorkContributor, edition_narrators


def norm_name(name: str | None) -> str:
    """Whitespace-collapsed, case-folded key. Two rows are 'the same' iff these match."""
    return " ".join((name or "").split()).casefold()


def _pick_survivor(rows: list):
    """Cleanest display name wins: no surrounding whitespace first, then any-uppercase over
    all-lowercase; deterministic id tiebreak (so it never depends on random UUID order)."""
    return sorted(
        rows,
        key=lambda r: (
            0 if r.name == r.name.strip() else 1,
            0 if any(c.isupper() for c in r.name) else 1,
            str(r.id),
        ),
    )[0]


def _dup_groups(rows: list) -> list[list]:
    groups: dict[str, list] = defaultdict(list)
    for r in rows:
        groups[norm_name(r.name)].append(r)
    return [g for g in groups.values() if len(g) > 1]


@dataclass
class ContributorChange:
    kind: str  # "author" | "narrator"
    survivor: str
    merged: list[str]  # loser display names folded into the survivor


def _merge_authors(session: Session) -> list[ContributorChange]:
    changes: list[ContributorChange] = []
    for group in _dup_groups(session.query(Author).all()):
        survivor = _pick_survivor(group)
        survivor.name = survivor.name.strip()  # store a clean display name (no stray whitespace)
        losers = [a for a in group if a.id != survivor.id]
        for loser in losers:
            # work_contributors: re-point unless (work, survivor, role) already exists (true dup)
            for wc in session.query(WorkContributor).filter_by(author_id=loser.id).all():
                target = (
                    session.query(WorkContributor)
                    .filter_by(work_id=wc.work_id, author_id=survivor.id, role=wc.role)
                    .first()
                )
                if target is not None:
                    session.delete(wc)  # survivor already has this (work, role) — drop the true dup
                else:
                    wc.author_id = survivor.id  # re-point the PK in place
            # author_styles: re-point unless (survivor, style, attr) already exists
            for st in session.query(AuthorStyle).filter_by(author_id=loser.id).all():
                target = (
                    session.query(AuthorStyle)
                    .filter_by(author_id=survivor.id, style_id=st.style_id, attribute_type=st.attribute_type)
                    .first()
                )
                if target is not None:
                    session.delete(st)
                else:
                    st.author_id = survivor.id
            session.flush()  # land re-points before deleting the loser row (no dangling FK)
            session.delete(loser)
        session.flush()
        changes.append(ContributorChange("author", survivor.name, [a.name for a in losers]))
    return changes


def _merge_narrators(session: Session) -> list[ContributorChange]:
    changes: list[ContributorChange] = []
    for group in _dup_groups(session.query(Narrator).all()):
        survivor = _pick_survivor(group)
        survivor.name = survivor.name.strip()  # store a clean display name (no stray whitespace)
        losers = [n for n in group if n.id != survivor.id]
        for loser in losers:
            # edition_narrators is a Core association table -> operate via Core statements
            edition_ids = (
                session.execute(
                    select(edition_narrators.c.edition_id).where(edition_narrators.c.narrator_id == loser.id)
                )
                .scalars()
                .all()
            )
            for eid in edition_ids:
                exists = session.execute(
                    select(edition_narrators.c.edition_id).where(
                        edition_narrators.c.edition_id == eid,
                        edition_narrators.c.narrator_id == survivor.id,
                    )
                ).first()
                session.execute(
                    delete(edition_narrators).where(
                        edition_narrators.c.edition_id == eid,
                        edition_narrators.c.narrator_id == loser.id,
                    )
                )
                if not exists:
                    session.execute(edition_narrators.insert().values(edition_id=eid, narrator_id=survivor.id))
            for st in session.query(NarratorStyle).filter_by(narrator_id=loser.id).all():
                target = (
                    session.query(NarratorStyle)
                    .filter_by(narrator_id=survivor.id, style_id=st.style_id, attribute_type=st.attribute_type)
                    .first()
                )
                if target is not None:
                    session.delete(st)
                else:
                    st.narrator_id = survivor.id
            session.flush()
            session.delete(loser)
        session.flush()
        changes.append(ContributorChange("narrator", survivor.name, [n.name for n in losers]))
    return changes


def plan_contributor_changes(session: Session) -> list[ContributorChange]:
    """Read-only preview of the merges apply would perform (authors + narrators)."""
    out: list[ContributorChange] = []
    for kind, rows in (("author", session.query(Author).all()), ("narrator", session.query(Narrator).all())):
        for group in _dup_groups(rows):
            survivor = _pick_survivor(group)
            out.append(ContributorChange(kind, survivor.name, [r.name for r in group if r.id != survivor.id]))
    return out


def apply_contributor_changes(session: Session) -> list[ContributorChange]:
    """Merge author then narrator dup-groups (narrators added in Task 3). Returns what was merged."""
    return _merge_authors(session) + _merge_narrators(session)


def contributor_inventory(session: Session) -> dict:
    """Read-only: the duplicate groups for authors and narrators."""
    return {
        "authors": [[r.name for r in g] for g in _dup_groups(session.query(Author).all())],
        "narrators": [[r.name for r in g] for g in _dup_groups(session.query(Narrator).all())],
    }
