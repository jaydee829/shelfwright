"""Dedup planner + applier for Phase 6.3's THE USER GATE (Spec 2026-07-12): finds prod duplicate
rows that would violate the incoming unique constraints (migration 48e3762d6c0c) and merges them.
Session in, plan/summary out; the CLI is scripts/clean_catalog.py's --dedup-for-constraints mode.

Structural distinguishers only, per the #69 lesson (memory: verify-backfill-distinguisher) — a
backfill once nearly deleted real rows because it trusted a sometimes-populated column
(work_tropes.justification) as a class label. Every class here groups on real relationships
(FK link counts) or normalized values (lower(name), COALESCE(format, '')) — never on a column
that is only sometimes populated.

plan_dedup is read-only. apply_dedup takes the PLAN — not the session's current state — and
touches only the exact ids the plan named ("apply what was shown"). If a planned row vanished
between plan and apply (e.g. a concurrent write), it is skipped and counted under
"skipped_stale", never re-derived.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from agentic_librarian.db.models import (
    Author,
    AuthorStyle,
    Edition,
    Narrator,
    NarratorStyle,
    ReadingHistory,
    Suggestions,
    Work,
    WorkContributor,
    edition_narrators,
)
from agentic_librarian.enrichment.two_phase import _normalize

# --------------------------------------------------------------------------------------------
# Plan dataclasses. Every entry carries the EXACT ids involved — apply_dedup never re-derives.
# --------------------------------------------------------------------------------------------


@dataclass
class ContributorMergeGroup:
    """One duplicate-name group for authors or narrators. repoint = (loser_id, [ids to
    re-point onto the survivor]); delete = (loser_id, [ids to delete outright — PK collisions
    with something the survivor already has])."""

    survivor_id: UUID
    survivor_name: str
    loser_ids: list[UUID]
    loser_names: list[str]
    # work_contributors (authors) / edition_narrators (narrators): (loser_id, pk_tuple) to repoint
    repoint_links: list[tuple[UUID, tuple]] = field(default_factory=list)
    delete_links: list[tuple[UUID, tuple]] = field(default_factory=list)
    # author_styles / narrator_styles
    repoint_styles: list[tuple[UUID, tuple]] = field(default_factory=list)
    delete_styles: list[tuple[UUID, tuple]] = field(default_factory=list)


@dataclass
class EditionMergeGroup:
    survivor_id: UUID
    work_id: UUID
    fmt: str | None
    loser_ids: list[UUID]
    # reading_history ids to repoint onto survivor_id, and ids to delete (date-collision)
    repoint_reading_history: list[UUID] = field(default_factory=list)
    delete_reading_history: list[UUID] = field(default_factory=list)
    # edition_narrators: (loser_edition_id, narrator_id) to repoint / delete
    repoint_narrators: list[tuple[UUID, UUID]] = field(default_factory=list)
    delete_narrators: list[tuple[UUID, UUID]] = field(default_factory=list)


@dataclass
class KeepDeleteGroup:
    """Exact-duplicate rows (reading_history, suggestions): keep one id, delete the rest."""

    survivor_id: UUID
    loser_ids: list[UUID]
    detail: str = ""


@dataclass
class WorkDupReport:
    """REPORT ONLY — never applied. Normalized title+author collisions for the operator to
    triage case by case (works carry no cross-table unique; see #95 decision 5)."""

    work_ids: list[UUID]
    titles: list[str]
    norm_key: str


@dataclass
class DedupPlan:
    duplicate_authors: list[ContributorMergeGroup] = field(default_factory=list)
    duplicate_narrators: list[ContributorMergeGroup] = field(default_factory=list)
    duplicate_editions: list[EditionMergeGroup] = field(default_factory=list)
    duplicate_reading_history: list[KeepDeleteGroup] = field(default_factory=list)
    duplicate_suggestions: list[KeepDeleteGroup] = field(default_factory=list)
    orphan_authors: list[UUID] = field(default_factory=list)
    duplicate_works_report_only: list[WorkDupReport] = field(default_factory=list)

    def summary(self) -> dict[str, int]:
        return {
            "duplicate_authors": len(self.duplicate_authors),
            "duplicate_narrators": len(self.duplicate_narrators),
            "duplicate_editions": len(self.duplicate_editions),
            "duplicate_reading_history": len(self.duplicate_reading_history),
            "duplicate_suggestions": len(self.duplicate_suggestions),
            "orphan_authors": len(self.orphan_authors),
            "duplicate_works_report_only": len(self.duplicate_works_report_only),
        }


# --------------------------------------------------------------------------------------------
# Survivor selection
# --------------------------------------------------------------------------------------------


def _pick_survivor_by_links(rows: list, link_counts: dict) -> object:
    """Survivor = the row referenced by the MOST FK links; tie-break lowest str(id). This is a
    structural choice (link count), not a sometimes-populated column — documented per the
    class docstrings below. Author/Narrator have no created_at, so "oldest" is not available;
    "most-linked" approximates "the one everything already points at" and minimizes repoints."""
    return sorted(rows, key=lambda r: (-link_counts.get(r.id, 0), str(r.id)))[0]


# --------------------------------------------------------------------------------------------
# Class 1 & 2: duplicate_authors / duplicate_narrators
# --------------------------------------------------------------------------------------------


def _plan_authors(session: Session) -> list[ContributorMergeGroup]:
    authors = session.query(Author).all()
    groups: dict[str, list[Author]] = defaultdict(list)
    for a in authors:
        groups[a.name.lower()].append(a)

    wc_counts = Counter(row.author_id for row in session.query(WorkContributor.author_id).all())
    as_counts = Counter(row.author_id for row in session.query(AuthorStyle.author_id).all())
    link_counts = Counter()
    for aid, n in wc_counts.items():
        link_counts[aid] += n
    for aid, n in as_counts.items():
        link_counts[aid] += n

    out: list[ContributorMergeGroup] = []
    for rows in groups.values():
        if len(rows) < 2:
            continue
        survivor = _pick_survivor_by_links(rows, link_counts)
        losers = [a for a in rows if a.id != survivor.id]
        group = ContributorMergeGroup(
            survivor_id=survivor.id,
            survivor_name=survivor.name,
            loser_ids=[loser.id for loser in losers],
            loser_names=[loser.name for loser in losers],
        )
        survivor_wc_keys = {
            (wc.work_id, wc.role) for wc in session.query(WorkContributor).filter_by(author_id=survivor.id).all()
        }
        survivor_style_keys = {
            (st.style_id, st.attribute_type) for st in session.query(AuthorStyle).filter_by(author_id=survivor.id).all()
        }
        for loser in losers:
            for wc in session.query(WorkContributor).filter_by(author_id=loser.id).all():
                key = (wc.work_id, wc.role)
                pk = (wc.work_id, wc.author_id, wc.role)
                if key in survivor_wc_keys:
                    group.delete_links.append((loser.id, pk))
                else:
                    group.repoint_links.append((loser.id, pk))
                    survivor_wc_keys.add(key)
            for st in session.query(AuthorStyle).filter_by(author_id=loser.id).all():
                key = (st.style_id, st.attribute_type)
                pk = (st.author_id, st.style_id, st.attribute_type)
                if key in survivor_style_keys:
                    group.delete_styles.append((loser.id, pk))
                else:
                    group.repoint_styles.append((loser.id, pk))
                    survivor_style_keys.add(key)
        out.append(group)
    return out


def _plan_narrators(session: Session) -> list[ContributorMergeGroup]:
    narrators = session.query(Narrator).all()
    groups: dict[str, list[Narrator]] = defaultdict(list)
    for n in narrators:
        groups[n.name.lower()].append(n)

    en_counts = Counter(row.narrator_id for row in session.execute(select(edition_narrators.c.narrator_id)).all())
    ns_counts = Counter(row.narrator_id for row in session.query(NarratorStyle.narrator_id).all())
    link_counts = Counter()
    for nid, n in en_counts.items():
        link_counts[nid] += n
    for nid, n in ns_counts.items():
        link_counts[nid] += n

    out: list[ContributorMergeGroup] = []
    for rows in groups.values():
        if len(rows) < 2:
            continue
        survivor = _pick_survivor_by_links(rows, link_counts)
        losers = [n for n in rows if n.id != survivor.id]
        group = ContributorMergeGroup(
            survivor_id=survivor.id,
            survivor_name=survivor.name,
            loser_ids=[loser.id for loser in losers],
            loser_names=[loser.name for loser in losers],
        )
        survivor_edition_ids = {
            row.edition_id
            for row in session.execute(
                select(edition_narrators.c.edition_id).where(edition_narrators.c.narrator_id == survivor.id)
            ).all()
        }
        survivor_style_keys = {
            (st.style_id, st.attribute_type)
            for st in session.query(NarratorStyle).filter_by(narrator_id=survivor.id).all()
        }
        for loser in losers:
            loser_edition_ids = [
                row.edition_id
                for row in session.execute(
                    select(edition_narrators.c.edition_id).where(edition_narrators.c.narrator_id == loser.id)
                ).all()
            ]
            for eid in loser_edition_ids:
                pk = (eid, loser.id)
                if eid in survivor_edition_ids:
                    group.delete_links.append((loser.id, pk))
                else:
                    group.repoint_links.append((loser.id, pk))
                    survivor_edition_ids.add(eid)
            for st in session.query(NarratorStyle).filter_by(narrator_id=loser.id).all():
                key = (st.style_id, st.attribute_type)
                pk = (st.narrator_id, st.style_id, st.attribute_type)
                if key in survivor_style_keys:
                    group.delete_styles.append((loser.id, pk))
                else:
                    group.repoint_styles.append((loser.id, pk))
                    survivor_style_keys.add(key)
        out.append(group)
    return out


def _apply_contributor_group(session: Session, group: ContributorMergeGroup, *, kind: str) -> dict[str, int]:
    """kind: 'author' | 'narrator'. Applies exactly the ids in `group`; anything vanished since
    planning is skipped and counted under skipped_stale."""
    stats = {"merged": 0, "skipped_stale": 0}
    survivor_id_present = session.get(Author if kind == "author" else Narrator, group.survivor_id) is not None
    if not survivor_id_present:
        stats["skipped_stale"] += 1
        return stats

    if kind == "author":
        for _loser_id, pk in group.repoint_links:
            work_id, author_id, role = pk
            wc = session.get(WorkContributor, {"work_id": work_id, "author_id": author_id, "role": role})
            if wc is None:
                stats["skipped_stale"] += 1
                continue
            wc.author_id = group.survivor_id
        for _loser_id, pk in group.delete_links:
            work_id, author_id, role = pk
            wc = session.get(WorkContributor, {"work_id": work_id, "author_id": author_id, "role": role})
            if wc is None:
                stats["skipped_stale"] += 1
                continue
            session.delete(wc)
        session.flush()
        for _loser_id, pk in group.repoint_styles:
            author_id, style_id, attribute_type = pk
            st = session.get(
                AuthorStyle, {"author_id": author_id, "style_id": style_id, "attribute_type": attribute_type}
            )
            if st is None:
                stats["skipped_stale"] += 1
                continue
            st.author_id = group.survivor_id
        for _loser_id, pk in group.delete_styles:
            author_id, style_id, attribute_type = pk
            st = session.get(
                AuthorStyle, {"author_id": author_id, "style_id": style_id, "attribute_type": attribute_type}
            )
            if st is None:
                stats["skipped_stale"] += 1
                continue
            session.delete(st)
        session.flush()
    else:
        for _loser_id, pk in group.repoint_links:
            edition_id, narrator_id = pk
            exists = session.execute(
                select(edition_narrators.c.edition_id).where(
                    edition_narrators.c.edition_id == edition_id,
                    edition_narrators.c.narrator_id == narrator_id,
                )
            ).first()
            if exists is None:
                stats["skipped_stale"] += 1
                continue
            session.execute(
                delete(edition_narrators).where(
                    edition_narrators.c.edition_id == edition_id, edition_narrators.c.narrator_id == narrator_id
                )
            )
            session.execute(edition_narrators.insert().values(edition_id=edition_id, narrator_id=group.survivor_id))
        for _loser_id, pk in group.delete_links:
            edition_id, narrator_id = pk
            session.execute(
                delete(edition_narrators).where(
                    edition_narrators.c.edition_id == edition_id, edition_narrators.c.narrator_id == narrator_id
                )
            )
        for _loser_id, pk in group.repoint_styles:
            narrator_id, style_id, attribute_type = pk
            st = session.get(
                NarratorStyle, {"narrator_id": narrator_id, "style_id": style_id, "attribute_type": attribute_type}
            )
            if st is None:
                stats["skipped_stale"] += 1
                continue
            st.narrator_id = group.survivor_id
        for _loser_id, pk in group.delete_styles:
            narrator_id, style_id, attribute_type = pk
            st = session.get(
                NarratorStyle, {"narrator_id": narrator_id, "style_id": style_id, "attribute_type": attribute_type}
            )
            if st is None:
                stats["skipped_stale"] += 1
                continue
            session.delete(st)
        session.flush()

    model = Author if kind == "author" else Narrator
    for loser_id in group.loser_ids:
        row = session.get(model, loser_id)
        if row is None:
            stats["skipped_stale"] += 1
            continue
        session.delete(row)
    session.flush()
    stats["merged"] = 1
    return stats


# --------------------------------------------------------------------------------------------
# Class 3: duplicate_editions
# --------------------------------------------------------------------------------------------


def _plan_editions(session: Session) -> list[EditionMergeGroup]:
    editions = session.query(Edition).all()
    groups: dict[tuple[UUID, str], list[Edition]] = defaultdict(list)
    for e in editions:
        groups[(e.work_id, e.format or "")].append(e)

    rh_counts = Counter(row.edition_id for row in session.query(ReadingHistory.edition_id).all())
    en_counts = Counter(row.edition_id for row in session.execute(select(edition_narrators.c.edition_id)).all())
    link_counts = Counter()
    for eid, n in rh_counts.items():
        link_counts[eid] += n
    for eid, n in en_counts.items():
        link_counts[eid] += n

    out: list[EditionMergeGroup] = []
    for (work_id, fmt), rows in groups.items():
        if len(rows) < 2:
            continue
        survivor = _pick_survivor_by_links(rows, link_counts)
        losers = [e for e in rows if e.id != survivor.id]
        group = EditionMergeGroup(
            survivor_id=survivor.id, work_id=work_id, fmt=fmt or None, loser_ids=[loser.id for loser in losers]
        )

        survivor_dates_by_user: dict[UUID, set] = defaultdict(set)
        for rh in session.query(ReadingHistory).filter_by(edition_id=survivor.id).all():
            survivor_dates_by_user[rh.user_id].add(rh.date_completed)

        survivor_narrator_ids = {
            row.narrator_id
            for row in session.execute(
                select(edition_narrators.c.narrator_id).where(edition_narrators.c.edition_id == survivor.id)
            ).all()
        }

        for loser in losers:
            for rh in session.query(ReadingHistory).filter_by(edition_id=loser.id).all():
                if rh.date_completed in survivor_dates_by_user.get(rh.user_id, set()):
                    group.delete_reading_history.append(rh.id)
                else:
                    group.repoint_reading_history.append(rh.id)
                    survivor_dates_by_user[rh.user_id].add(rh.date_completed)
            for row in session.execute(
                select(edition_narrators.c.narrator_id).where(edition_narrators.c.edition_id == loser.id)
            ).all():
                nid = row.narrator_id
                if nid in survivor_narrator_ids:
                    group.delete_narrators.append((loser.id, nid))
                else:
                    group.repoint_narrators.append((loser.id, nid))
                    survivor_narrator_ids.add(nid)
        out.append(group)
    return out


def _apply_edition_group(session: Session, group: EditionMergeGroup) -> dict[str, int]:
    stats = {"merged": 0, "skipped_stale": 0}
    if session.get(Edition, group.survivor_id) is None:
        stats["skipped_stale"] += 1
        return stats

    for rh_id in group.repoint_reading_history:
        rh = session.get(ReadingHistory, rh_id)
        if rh is None:
            stats["skipped_stale"] += 1
            continue
        rh.edition_id = group.survivor_id
    for rh_id in group.delete_reading_history:
        rh = session.get(ReadingHistory, rh_id)
        if rh is None:
            stats["skipped_stale"] += 1
            continue
        session.delete(rh)
    session.flush()

    for edition_id, narrator_id in group.repoint_narrators:
        exists = session.execute(
            select(edition_narrators.c.edition_id).where(
                edition_narrators.c.edition_id == edition_id, edition_narrators.c.narrator_id == narrator_id
            )
        ).first()
        if exists is None:
            stats["skipped_stale"] += 1
            continue
        session.execute(
            delete(edition_narrators).where(
                edition_narrators.c.edition_id == edition_id, edition_narrators.c.narrator_id == narrator_id
            )
        )
        session.execute(edition_narrators.insert().values(edition_id=group.survivor_id, narrator_id=narrator_id))
    for edition_id, narrator_id in group.delete_narrators:
        session.execute(
            delete(edition_narrators).where(
                edition_narrators.c.edition_id == edition_id, edition_narrators.c.narrator_id == narrator_id
            )
        )
    session.flush()

    for loser_id in group.loser_ids:
        e = session.get(Edition, loser_id)
        if e is None:
            stats["skipped_stale"] += 1
            continue
        session.delete(e)
    session.flush()
    stats["merged"] = 1
    return stats


# --------------------------------------------------------------------------------------------
# Class 4: duplicate_reading_history (exact (user_id, edition_id, date_completed) groups)
# --------------------------------------------------------------------------------------------


def _plan_reading_history(session: Session) -> list[KeepDeleteGroup]:
    rows = session.query(ReadingHistory).all()
    groups: dict[tuple, list[ReadingHistory]] = defaultdict(list)
    for rh in rows:
        groups[(rh.user_id, rh.edition_id, rh.date_completed)].append(rh)
    out: list[KeepDeleteGroup] = []
    for key, dup_rows in groups.items():
        if len(dup_rows) < 2:
            continue
        survivor = min(dup_rows, key=lambda r: str(r.id))
        losers = [r for r in dup_rows if r.id != survivor.id]
        out.append(KeepDeleteGroup(survivor_id=survivor.id, loser_ids=[loser.id for loser in losers], detail=str(key)))
    return out


# --------------------------------------------------------------------------------------------
# Class 5: duplicate_suggestions (per (user_id, work_id) WHERE status='Suggested')
# --------------------------------------------------------------------------------------------


def _plan_suggestions(session: Session) -> list[KeepDeleteGroup]:
    rows = session.query(Suggestions).filter_by(status="Suggested").all()
    groups: dict[tuple[UUID, UUID], list[Suggestions]] = defaultdict(list)
    for s in rows:
        groups[(s.user_id, s.work_id)].append(s)
    out: list[KeepDeleteGroup] = []
    for key, dup_rows in groups.items():
        if len(dup_rows) < 2:
            continue
        survivor = min(dup_rows, key=lambda r: (r.suggested_at, str(r.id)))
        losers = [s for s in dup_rows if s.id != survivor.id]
        out.append(KeepDeleteGroup(survivor_id=survivor.id, loser_ids=[loser.id for loser in losers], detail=str(key)))
    return out


def _apply_keep_delete(session: Session, model, group: KeepDeleteGroup) -> dict[str, int]:
    stats = {"deleted": 0, "skipped_stale": 0}
    for loser_id in group.loser_ids:
        row = session.get(model, loser_id)
        if row is None:
            stats["skipped_stale"] += 1
            continue
        session.delete(row)
        stats["deleted"] += 1
    session.flush()
    return stats


# --------------------------------------------------------------------------------------------
# Class 6: orphan_authors (zero work_contributors AND zero author_styles)
# --------------------------------------------------------------------------------------------


def _plan_orphan_authors(session: Session) -> list[UUID]:
    """Computed against CURRENT state — NOT simulated against class-1 merges. An author
    orphaned BY this run's own author-merge is caught only on a re-plan/re-apply; this is
    deliberate (documented here and echoed in the CLI output), not an oversight: simulating
    ahead of a not-yet-applied plan would make apply's ids depend on apply's own outcome,
    breaking the "apply exactly what plan showed" discipline."""
    linked_wc = {row.author_id for row in session.query(WorkContributor.author_id).all()}
    linked_as = {row.author_id for row in session.query(AuthorStyle.author_id).all()}
    linked = linked_wc | linked_as
    return [a.id for a in session.query(Author).all() if a.id not in linked]


def _apply_orphan_authors(session: Session, ids: list[UUID]) -> dict[str, int]:
    stats = {"deleted": 0, "skipped_stale": 0}
    for aid in ids:
        a = session.get(Author, aid)
        if a is None:
            stats["skipped_stale"] += 1
            continue
        # re-verify structurally at apply time too — never delete a row that gained a link
        # between plan and apply (still id-scoped to the plan; this is a safety re-check, not
        # a re-derivation of WHICH ids are orphans)
        has_wc = session.query(WorkContributor).filter_by(author_id=aid).first() is not None
        has_as = session.query(AuthorStyle).filter_by(author_id=aid).first() is not None
        if has_wc or has_as:
            stats["skipped_stale"] += 1
            continue
        session.delete(a)
        stats["deleted"] += 1
    session.flush()
    return stats


# --------------------------------------------------------------------------------------------
# Class 7: duplicate_works_report_only (normalized title+author; NEVER applied)
# --------------------------------------------------------------------------------------------


def _plan_duplicate_works(session: Session) -> list[WorkDupReport]:
    rows = (
        session.query(Work.id, Work.title, Author.name)
        .join(WorkContributor, WorkContributor.work_id == Work.id)
        .join(Author, Author.id == WorkContributor.author_id)
        .filter(WorkContributor.role == "Author")
        .all()
    )
    groups: dict[str, list[tuple[UUID, str]]] = defaultdict(list)
    for work_id, title, author_name in rows:
        key = f"{_normalize(title)}|{_normalize(author_name)}"
        groups[key].append((work_id, title))
    out: list[WorkDupReport] = []
    for key, entries in groups.items():
        seen_ids = {wid for wid, _ in entries}
        if len(seen_ids) < 2:
            continue
        out.append(WorkDupReport(work_ids=[wid for wid, _ in entries], titles=[t for _, t in entries], norm_key=key))
    return out


# --------------------------------------------------------------------------------------------
# Top-level plan / apply
# --------------------------------------------------------------------------------------------


def plan_dedup(session: Session) -> DedupPlan:
    """READ ONLY. Computes every class against the CURRENT db state. See _plan_orphan_authors
    for why orphans are not simulated against not-yet-applied author merges."""
    return DedupPlan(
        duplicate_authors=_plan_authors(session),
        duplicate_narrators=_plan_narrators(session),
        duplicate_editions=_plan_editions(session),
        duplicate_reading_history=_plan_reading_history(session),
        duplicate_suggestions=_plan_suggestions(session),
        orphan_authors=_plan_orphan_authors(session),
        duplicate_works_report_only=_plan_duplicate_works(session),
    )


def apply_dedup(session: Session, plan: DedupPlan) -> dict[str, int]:
    """Applies EXACTLY plan's ids — no re-derivation. Order: authors, narrators, editions,
    reading_history, suggestions, orphans. duplicate_works_report_only is NEVER applied.
    Rows that vanished between plan and apply are skipped and counted under skipped_stale."""
    result = {
        "duplicate_authors": 0,
        "duplicate_narrators": 0,
        "duplicate_editions": 0,
        "duplicate_reading_history": 0,
        "duplicate_suggestions": 0,
        "orphan_authors": 0,
        "skipped_stale": 0,
    }

    for group in plan.duplicate_authors:
        stats = _apply_contributor_group(session, group, kind="author")
        result["duplicate_authors"] += stats["merged"]
        result["skipped_stale"] += stats["skipped_stale"]

    for group in plan.duplicate_narrators:
        stats = _apply_contributor_group(session, group, kind="narrator")
        result["duplicate_narrators"] += stats["merged"]
        result["skipped_stale"] += stats["skipped_stale"]

    for group in plan.duplicate_editions:
        stats = _apply_edition_group(session, group)
        result["duplicate_editions"] += stats["merged"]
        result["skipped_stale"] += stats["skipped_stale"]

    for group in plan.duplicate_reading_history:
        stats = _apply_keep_delete(session, ReadingHistory, group)
        result["duplicate_reading_history"] += stats["deleted"]
        result["skipped_stale"] += stats["skipped_stale"]

    for group in plan.duplicate_suggestions:
        stats = _apply_keep_delete(session, Suggestions, group)
        result["duplicate_suggestions"] += stats["deleted"]
        result["skipped_stale"] += stats["skipped_stale"]

    orphan_stats = _apply_orphan_authors(session, plan.orphan_authors)
    result["orphan_authors"] += orphan_stats["deleted"]
    result["skipped_stale"] += orphan_stats["skipped_stale"]

    return result
