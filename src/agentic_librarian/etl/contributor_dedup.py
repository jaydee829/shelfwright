"""Dedup Author/Narrator rows that differ only by case/whitespace, folding all links onto one
survivor (Spec 2026-06-23). Session in, summary out; the CLI is scripts/clean_catalog.py. Sibling
of etl/tag_backfill.py for the contributor tables."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from sqlalchemy.orm import Session

from agentic_librarian.db.models import Author, AuthorStyle, Narrator, WorkContributor


def norm_name(name: str | None) -> str:
    """Whitespace-collapsed, case-folded key. Two rows are 'the same' iff these match."""
    return " ".join((name or "").split()).casefold()


def _pick_survivor(rows: list):
    """Best-cased name wins (any uppercase > all-lowercase); deterministic id tiebreak."""
    return sorted(rows, key=lambda r: (0 if any(c.isupper() for c in r.name) else 1, str(r.id)))[0]


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
        losers = [a for a in group if a.id != survivor.id]
        for loser in losers:
            # work_contributors: re-point unless (work, survivor, role) already exists (true dup)
            for wc in session.query(WorkContributor).filter_by(author_id=loser.id).all():
                target = (
                    session.query(WorkContributor)
                    .filter_by(work_id=wc.work_id, author_id=survivor.id, role=wc.role)
                    .first()
                )
                session.delete(wc)
                if target is None:
                    session.add(WorkContributor(work_id=wc.work_id, author_id=survivor.id, role=wc.role))
            # author_styles: re-point unless (survivor, style, attr) already exists
            for st in session.query(AuthorStyle).filter_by(author_id=loser.id).all():
                target = (
                    session.query(AuthorStyle)
                    .filter_by(author_id=survivor.id, style_id=st.style_id, attribute_type=st.attribute_type)
                    .first()
                )
                session.delete(st)
                if target is None:
                    session.add(
                        AuthorStyle(author_id=survivor.id, style_id=st.style_id, attribute_type=st.attribute_type)
                    )
            session.flush()  # land re-points before deleting the loser row (no dangling FK)
            session.delete(loser)
        session.flush()
        changes.append(ContributorChange("author", survivor.name, [a.name for a in losers]))
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
    return _merge_authors(session)


def contributor_inventory(session: Session) -> dict:
    """Read-only: the duplicate groups for authors and narrators."""
    return {
        "authors": [[r.name for r in g] for g in _dup_groups(session.query(Author).all())],
        "narrators": [[r.name for r in g] for g in _dup_groups(session.query(Narrator).all())],
    }
