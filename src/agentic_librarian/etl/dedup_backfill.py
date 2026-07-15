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

plan_id_set / plan_delta (Spec 2026-07-12 follow-up to #95): --apply is a SEPARATE invocation
from the reviewed dry-run and re-plans from scratch — new duplicates from live traffic in the
gap would otherwise be applied without operator review, defeating the gate. plan_id_set turns a
plan into a per-class id set; plan_delta cross-checks a fresh plan's id set against the ids the
operator actually reviewed (parsed back from the dry-run report) and returns what's new. See
scripts/clean_catalog.py's --apply flow for how a non-empty delta refuses.

Every token in that id set is TAGGED with its operation (`merge:` / `repoint:` / `delete:` /
`report:`, see plan_id_set's docstring) so an operation FLIP on the same underlying id between
the reviewed dry-run and the fresh apply-time plan (e.g. a concurrent write turns a reviewed
repoint into a delete) is visible as a new token, not hidden behind an unchanged bare id.

INVARIANT (found live against prod, GH #95): this module runs against the PRE-migration schema
— the gate precedes `alembic upgrade head` by design, so migration 48e3762d6c0c has NOT landed
yet when plan_dedup/apply_dedup run. Never entity-load Work here (`session.query(Work)` /
`session.get(Work, ...)`) — it must not reference post-migration columns (e.g.
deep_enriched_at), which don't exist on prod's works table at this point and raise
UndefinedColumn. The one Work reference in this module (_plan_duplicate_works) is already
column-explicit (`session.query(Work.id, Work.title, Author.name)`) — keep any future Work
query that shape. Author/Narrator/Edition/ReadingHistory/Suggestions entity loads are safe:
this migration adds no columns to those tables.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from agentic_librarian.db.models import (
    Author,
    AuthorStyle,
    DetectedDuplicate,
    Edition,
    Narrator,
    NarratorStyle,
    ReadingHistory,
    Suggestions,
    Work,
    WorkContributor,
    WorkTrope,
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
    # Final-review Critical (GH #95 follow-up): groups DROPPED from the classes above because
    # they intersect another class's plan in a way that would compose into row loss if both
    # applied from the SAME pre-apply snapshot (see _defer_intersecting_groups's docstring).
    # Keyed by the class name the group was dropped FROM ("duplicate_editions" /
    # "duplicate_reading_history"); each entry is {"reason": str, ...group-identifying fields}.
    # EXPECTED on intersecting data, not an error — a subsequent dry-run/apply pass (the
    # runbook's existing loop) re-plans after the intersecting class has already applied, so the
    # intersection is gone and the deferred group applies cleanly on the next pass.
    deferred_intersections: dict[str, list[dict]] = field(default_factory=dict)

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
    # Minor 3 (final review): every group query is order_by'd on its pk so dry-run and an
    # apply-time re-plan against unchanged data classify IDENTICALLY (which collision link goes
    # to repoint vs delete depends on iteration order when two losers/links race for the same
    # key) — without this, a Postgres row order that happens to differ between two plans over the
    # same data can flip a classification and trip the apply-gate's drift-refuse spuriously.
    authors = session.query(Author).order_by(Author.id).all()
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
        losers = sorted((a for a in rows if a.id != survivor.id), key=lambda a: str(a.id))
        group = ContributorMergeGroup(
            survivor_id=survivor.id,
            survivor_name=survivor.name,
            loser_ids=[loser.id for loser in losers],
            loser_names=[loser.name for loser in losers],
        )
        survivor_wc_keys = {
            (wc.work_id, wc.role)
            for wc in session.query(WorkContributor)
            .filter_by(author_id=survivor.id)
            .order_by(WorkContributor.work_id, WorkContributor.role)
            .all()
        }
        survivor_style_keys = {
            (st.style_id, st.attribute_type)
            for st in session.query(AuthorStyle)
            .filter_by(author_id=survivor.id)
            .order_by(AuthorStyle.style_id, AuthorStyle.attribute_type)
            .all()
        }
        for loser in losers:
            for wc in (
                session.query(WorkContributor)
                .filter_by(author_id=loser.id)
                .order_by(WorkContributor.work_id, WorkContributor.role)
                .all()
            ):
                key = (wc.work_id, wc.role)
                pk = (wc.work_id, wc.author_id, wc.role)
                if key in survivor_wc_keys:
                    group.delete_links.append((loser.id, pk))
                else:
                    group.repoint_links.append((loser.id, pk))
                    survivor_wc_keys.add(key)
            for st in (
                session.query(AuthorStyle)
                .filter_by(author_id=loser.id)
                .order_by(AuthorStyle.style_id, AuthorStyle.attribute_type)
                .all()
            ):
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
    # Minor 3 (final review): order_by + sorted losers — see _plan_authors's comment.
    narrators = session.query(Narrator).order_by(Narrator.id).all()
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
        losers = sorted((n for n in rows if n.id != survivor.id), key=lambda n: str(n.id))
        group = ContributorMergeGroup(
            survivor_id=survivor.id,
            survivor_name=survivor.name,
            loser_ids=[loser.id for loser in losers],
            loser_names=[loser.name for loser in losers],
        )
        survivor_edition_ids = {
            row.edition_id
            for row in session.execute(
                select(edition_narrators.c.edition_id)
                .where(edition_narrators.c.narrator_id == survivor.id)
                .order_by(edition_narrators.c.edition_id)
            ).all()
        }
        survivor_style_keys = {
            (st.style_id, st.attribute_type)
            for st in session.query(NarratorStyle)
            .filter_by(narrator_id=survivor.id)
            .order_by(NarratorStyle.style_id, NarratorStyle.attribute_type)
            .all()
        }
        for loser in losers:
            loser_edition_ids = [
                row.edition_id
                for row in session.execute(
                    select(edition_narrators.c.edition_id)
                    .where(edition_narrators.c.narrator_id == loser.id)
                    .order_by(edition_narrators.c.edition_id)
                ).all()
            ]
            for eid in loser_edition_ids:
                pk = (eid, loser.id)
                if eid in survivor_edition_ids:
                    group.delete_links.append((loser.id, pk))
                else:
                    group.repoint_links.append((loser.id, pk))
                    survivor_edition_ids.add(eid)
            for st in (
                session.query(NarratorStyle)
                .filter_by(narrator_id=loser.id)
                .order_by(NarratorStyle.style_id, NarratorStyle.attribute_type)
                .all()
            ):
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
    planning is skipped and counted under skipped_stale.

    Belt-and-braces (adversarial pass, GH #95 #97 follow-up): mirrors _apply_edition_group's
    unplanned-row re-verify. Author.styles / Narrator.styles cascade `all, delete-orphan` — a
    bare `session.delete(loser)` would silently cascade-delete ANY style row still attached to
    that loser at delete time, including one a concurrent write attached AFTER this group was
    planned (this group's own repoint_styles/delete_styles is silent about it, same as the
    edition case's silence about an unplanned narrator link). Before deleting each loser, check
    for a style row that survives and was NOT named by this group's own plan for that loser id;
    if found, refuse the delete and count it under skipped_unsafe instead."""
    stats = {"merged": 0, "skipped_stale": 0, "skipped_unsafe": 0}
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

    # Planned style pks per loser, so the re-verify below can tell "planned, already handled
    # above" apart from "unplanned, showed up since this group was planned".
    planned_style_pks_by_loser: dict[UUID, set[tuple]] = defaultdict(set)
    for loser_id, pk in group.repoint_styles:
        planned_style_pks_by_loser[loser_id].add(pk)
    for loser_id, pk in group.delete_styles:
        planned_style_pks_by_loser[loser_id].add(pk)

    model = Author if kind == "author" else Narrator
    style_model = AuthorStyle if kind == "author" else NarratorStyle
    style_fk = "author_id" if kind == "author" else "narrator_id"
    for loser_id in group.loser_ids:
        row = session.get(model, loser_id)
        if row is None:
            stats["skipped_stale"] += 1
            continue
        remaining_style_pks = {
            (getattr(st, style_fk), st.style_id, st.attribute_type)
            for st in session.query(style_model).filter_by(**{style_fk: loser_id}).all()
        }
        unplanned = remaining_style_pks - planned_style_pks_by_loser.get(loser_id, set())
        if unplanned:
            stats["skipped_unsafe"] += 1
            continue
        session.delete(row)
    session.flush()
    stats["merged"] = 1
    return stats


# --------------------------------------------------------------------------------------------
# Class 3: duplicate_editions
# --------------------------------------------------------------------------------------------


def _plan_editions(session: Session) -> list[EditionMergeGroup]:
    # Minor 3 (final review): order_by + sorted losers — see _plan_authors's comment. The
    # per-loser ReadingHistory ordering here is the SAME lever behind the edition x
    # reading_history intersection this plan defers (_defer_intersecting_groups): which row of
    # an exact-duplicate date-collision pair gets repoint vs delete depends on this order, so
    # dry-run and an apply-time re-plan must see the identical order every time.
    editions = session.query(Edition).order_by(Edition.id).all()
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
        losers = sorted((e for e in rows if e.id != survivor.id), key=lambda e: str(e.id))
        group = EditionMergeGroup(
            survivor_id=survivor.id, work_id=work_id, fmt=fmt or None, loser_ids=[loser.id for loser in losers]
        )

        survivor_dates_by_user: dict[UUID, set] = defaultdict(set)
        for rh in session.query(ReadingHistory).filter_by(edition_id=survivor.id).order_by(ReadingHistory.id).all():
            survivor_dates_by_user[rh.user_id].add(rh.date_completed)

        survivor_narrator_ids = {
            row.narrator_id
            for row in session.execute(
                select(edition_narrators.c.narrator_id)
                .where(edition_narrators.c.edition_id == survivor.id)
                .order_by(edition_narrators.c.narrator_id)
            ).all()
        }

        for loser in losers:
            for rh in session.query(ReadingHistory).filter_by(edition_id=loser.id).order_by(ReadingHistory.id).all():
                if rh.date_completed in survivor_dates_by_user.get(rh.user_id, set()):
                    group.delete_reading_history.append(rh.id)
                else:
                    group.repoint_reading_history.append(rh.id)
                    survivor_dates_by_user[rh.user_id].add(rh.date_completed)
            for row in session.execute(
                select(edition_narrators.c.narrator_id)
                .where(edition_narrators.c.edition_id == loser.id)
                .order_by(edition_narrators.c.narrator_id)
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
    stats = {"merged": 0, "skipped_stale": 0, "skipped_unsafe": 0}
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
        # Belt-and-braces (final-review Critical): by this point every edition_narrators row this
        # GROUP's plan named for `loser_id` has already been repointed or deleted above. If any
        # row for `loser_id` still exists now, it is UNPLANNED for this group — deleting the
        # edition would cascade it away via Edition.narrators' `secondary=` relationship, exactly
        # the narrator x edition composition loss the plan-time defer (see
        # _defer_intersecting_groups) is meant to prevent. Mirrors the existing orphan-author
        # re-verify-at-apply-time pattern: refuse and count separately from skipped_stale, since
        # this isn't "the row vanished" — it's "an unaccounted-for row is still here."
        unplanned = session.execute(
            select(edition_narrators.c.narrator_id).where(edition_narrators.c.edition_id == loser_id)
        ).first()
        if unplanned is not None:
            stats["skipped_unsafe"] += 1
            continue
        session.delete(e)
    session.flush()
    stats["merged"] = 1
    return stats


# --------------------------------------------------------------------------------------------
# Class 4: duplicate_reading_history (exact (user_id, edition_id, date_completed) groups)
# --------------------------------------------------------------------------------------------


def _plan_reading_history(session: Session) -> list[KeepDeleteGroup]:
    # Minor 3 (final review): order_by + sorted losers — see _plan_authors's comment. Survivor
    # selection here is already order-independent (min by str(id)); ordering keeps loser_ids
    # (and therefore the plan report / plan_id_set tokens) stable across re-plans too.
    rows = session.query(ReadingHistory).order_by(ReadingHistory.id).all()
    groups: dict[tuple, list[ReadingHistory]] = defaultdict(list)
    for rh in rows:
        groups[(rh.user_id, rh.edition_id, rh.date_completed)].append(rh)
    out: list[KeepDeleteGroup] = []
    for key, dup_rows in groups.items():
        if len(dup_rows) < 2:
            continue
        survivor = min(dup_rows, key=lambda r: str(r.id))
        losers = sorted((r for r in dup_rows if r.id != survivor.id), key=lambda r: str(r.id))
        out.append(KeepDeleteGroup(survivor_id=survivor.id, loser_ids=[loser.id for loser in losers], detail=str(key)))
    return out


# --------------------------------------------------------------------------------------------
# Class 5: duplicate_suggestions (per (user_id, work_id) WHERE status='Suggested')
# --------------------------------------------------------------------------------------------


def _plan_suggestions(session: Session) -> list[KeepDeleteGroup]:
    # Minor 3 (final review): order_by + sorted losers — see _plan_authors's comment. Survivor
    # selection here is already order-independent (min by (suggested_at, str(id))); ordering
    # keeps loser_ids stable across re-plans too.
    rows = session.query(Suggestions).filter_by(status="Suggested").order_by(Suggestions.id).all()
    groups: dict[tuple[UUID, UUID], list[Suggestions]] = defaultdict(list)
    for s in rows:
        groups[(s.user_id, s.work_id)].append(s)
    out: list[KeepDeleteGroup] = []
    for key, dup_rows in groups.items():
        if len(dup_rows) < 2:
            continue
        survivor = min(dup_rows, key=lambda r: (r.suggested_at, str(r.id)))
        losers = sorted((s for s in dup_rows if s.id != survivor.id), key=lambda s: str(s.id))
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
# Works-merge detection (PR-2 part 1, Spec 2026-07-14: docs/superpowers/specs/
# 2026-07-14-works-merge-tool-design.md). PLANNING/DETECTION ONLY — plan_works_merge is
# read-only, same as plan_dedup; there is no apply_works_merge here (that is a follow-up task,
# H2, which builds the merge COMPOSITION on top of this module's plan output).
#
# Deliberately independent of DedupPlan / plan_dedup / apply_dedup / PLAN_ID_SET_CLASSES above:
# those exist for the #95 pre-constraint-migration gate (a narrower, already-shipped tool with
# its own pinned tests — duplicate_works_report_only/_plan_duplicate_works/WorkDupReport are
# untouched by this section). The works-merge tool is a separate, later-stage cleanup with its
# own detection classes, its own survivor-selection rule, and its own eventual apply gate; it
# lives in the SAME FILE (per the design spec: "extend etl/dedup_backfill.py... do NOT build a
# parallel tool") but does not thread through the constraint-gate's plan/apply machinery.
#
# Four detection classes, evidence-strongest first — each produces UNORDERED work-pair
# groupings (a work id pair is a 2-tuple but is always compared/deduped as a frozenset so
# (A, B) and (B, A) collapse to the same pair):
#   1. works_same_isbn        — works sharing a non-null editions.isbn_13.
#   2. works_same_identity    — fold(title) equal AND author-token overlap >= 1 full token,
#                                with the series guard blocking sequel titles from matching.
#   3. works_detected_duplicates — rows from the #141/#143 detected_duplicates feed, deduped as
#                                unordered pairs (the table's composite PK is (work_id_a,
#                                work_id_b), NOT order-normalized — both rows of one cluster can
#                                exist; see DetectedDuplicate's docstring in db/models.py).
#   4. works_fuzzy_report_only — token-set similarity on folded titles above a threshold, minus
#                                pairs already caught by a stronger class above. REPORT ONLY
#                                FOREVER: never promoted to an applyable class by this tool (the
#                                design spec: "operator promotes pairs by hand if real"). Marked
#                                structurally via WorksMergeClusters.fuzzy_report_only being a
#                                SEPARATE field from the three applyable-shape lists, exactly the
#                                way duplicate_works_report_only is a separate field on DedupPlan
#                                — H2's future apply step can only reach the applyable fields.
#
# A pair that matches more than one class is reported ONCE, in its single strongest class
# (works_same_isbn > works_same_identity > works_detected_duplicates > works_fuzzy_report_only).
# Pairs are then unioned into transitive clusters (A~B, B~C -> one {A, B, C} cluster) via a
# simple union-find; a cluster's reported class is the STRONGEST class of any edge that built
# it, so a cluster with even one same_isbn edge is never fuzzy-report-only.
# --------------------------------------------------------------------------------------------

_SERIES_TOKEN_WORDS = {"book", "volume", "vol", "part", "no"}


def _fold(title: str) -> str:
    """fold() per the works-merge design spec: the existing _normalize (lowercase, strip,
    collapse whitespace) PLUS punctuation folding so title variants that differ only by
    punctuation choice compare equal — catches the real 'We Are Legion (We Are Bob)' vs
    'We are Legion; We are Bob' pair. Punctuation class matches the spec exactly:
    [;:()\\[\\]&.,!?'"-] -> space, then whitespace collapse (re-run through _normalize so a
    punctuation char abutting a word doesn't leave a double space)."""
    folded = re.sub(r"""[;:()\[\]&.,!?'"-]""", " ", title or "")
    return _normalize(folded)


def _roman_numeral_token(token: str) -> bool:
    """True if `token` is a (non-empty) run of valid roman-numeral letters. Deliberately loose
    (does not validate strict subtractive-notation ordering, e.g. accepts 'IIII') — this is a
    series-guard heuristic over real-world sequel titles, not a roman-numeral parser; the cost
    of a false positive (blocking a legitimate non-sequel match) is low and no observed catalog
    title needs the stricter form."""
    return bool(token) and all(ch in "ivxlcdm" for ch in token)


def _strip_trailing_volume_token(folded_title: str) -> tuple[str, bool]:
    """Given an already-`_fold`ed title, strip a trailing volume/sequel token if present:
    a bare number ('2'), a roman numeral ('ii'), a '#' + number (folded to '# 2' since '#' is
    not in the punctuation-fold set... but '#' IS punctuation-like; see note below), or a
    series word ('book'/'volume'/'vol'/'part'/'no') immediately followed by a number. Returns
    (title_with_token_removed, True) if a token was found, else (folded_title, False).

    Note: '#' is not in the fold-punctuation set (spec's list is `[;:()\\[\\]&.,!?'"-]`), so
    '#2' survives folding as a single token '#2' — handled directly below alongside the
    bare-number and roman-numeral cases."""
    tokens = folded_title.split(" ")
    if not tokens or tokens[-1] == "":
        return folded_title, False

    last = tokens[-1]
    # Series-word + number ('... book 2', '... volume ii') checked BEFORE the bare
    # number/roman-numeral case below, so 'beware of chicken volume 2' strips BOTH trailing
    # tokens ('volume' and '2') down to 'beware of chicken', not just the trailing '2' down to
    # the wrong 'beware of chicken volume'.
    if len(tokens) >= 2 and tokens[-2] in _SERIES_TOKEN_WORDS and (last.isdigit() or _roman_numeral_token(last)):
        return " ".join(tokens[:-2]).strip(), True
    if last.isdigit() or _roman_numeral_token(last):
        return " ".join(tokens[:-1]).strip(), True
    if last.startswith("#") and last[1:].isdigit():
        return " ".join(tokens[:-1]).strip(), True
    return folded_title, False


def _series_guard_blocks(title_a: str, title_b: str) -> bool:
    """True if `title_a`/`title_b` differ ONLY by a trailing volume/sequel token — i.e. one
    title, once its trailing volume token is stripped, equals the other's fold exactly, but the
    two folded titles were NOT already equal. Symmetric in its two arguments by construction
    (both directions are tried). 'Beware of Chicken' vs 'Beware of Chicken 2' -> blocked;
    'Beware of Chicken' vs 'Beware of Chicken' -> NOT blocked (nothing to guard against);
    'We Are Legion (We Are Bob)' vs 'We are Legion; We are Bob' -> NOT blocked (no trailing
    volume token on either side, this is the punctuation-fold case fold() already handles)."""
    fa, fb = _fold(title_a), _fold(title_b)
    if fa == fb:
        return False
    stripped_a, had_a = _strip_trailing_volume_token(fa)
    stripped_b, had_b = _strip_trailing_volume_token(fb)
    if had_a and stripped_a == fb:
        return True
    return bool(had_b and stripped_b == fa)


def fuzzy_similarity(title_a: str, title_b: str) -> float:
    """Token-set (Jaccard) similarity on folded titles: |intersection| / |union| of each
    title's fold()ed word set. Dependency-free (no rapidfuzz in this project) — the spec calls
    for "trigram or token-set ratio"; token-set is chosen since it needs no new dependency and
    is order-insensitive, which suits title variants that reorder words."""
    tokens_a = set(_fold(title_a).split())
    tokens_b = set(_fold(title_b).split())
    if not tokens_a or not tokens_b:
        return 0.0
    return len(tokens_a & tokens_b) / len(tokens_a | tokens_b)


FUZZY_SIMILARITY_THRESHOLD = 0.5


@dataclass
class WorkStats:
    """Per-work data survivor selection needs, gathered once per plan_works_merge run and
    passed around as a plain dict[UUID, WorkStats] so the pure clustering/survivor core
    (plan_works_merge_clusters, pick_survivor) never touches a Session."""

    work_id: UUID
    title: str
    justified_trope_links: int
    deep_enriched_at: datetime | None
    edition_count: int


def pick_survivor(candidates: list[WorkStats]) -> WorkStats:
    """Deterministic survivor selection (spec order): most justified trope links -> newest
    deep_enriched_at (NULLs sort LAST, i.e. a work that was never deep-enriched never wins this
    tiebreak over one that was) -> most editions -> lowest UUID string (final determinism).
    Pure function over WorkStats; no DB access."""

    def sort_key(w: WorkStats):
        # NULLs-last on a DESCENDING sort: pair (has_timestamp, timestamp) both negated-ish via
        # a tuple that puts "no timestamp" after "has timestamp" once the whole key is sorted
        # ascending on its negation — simplest correct form: sort ascending on
        # (-trope_links, null_last_date_key, -edition_count, str(id)).
        # NULLs sort after any real timestamp.
        date_key = (1, None) if w.deep_enriched_at is None else (0, -w.deep_enriched_at.timestamp())
        return (-w.justified_trope_links, date_key, -w.edition_count, str(w.work_id))

    return sorted(candidates, key=sort_key)[0]


@dataclass
class WorksMergeCluster:
    """One merge unit: every work id folded into this cluster (transitively, via unioned pairs
    across however many class edges connected them), the survivor plan_works_merge picked for
    it, and the per-work stats used for that selection (so the report/CLI can show its work)."""

    class_name: str  # one of "works_same_isbn" / "works_same_identity" / "works_detected_duplicates"
    work_ids: list[UUID]
    titles: list[str]
    survivor_id: UUID
    stats_by_work: dict[UUID, WorkStats] = field(default_factory=dict)


@dataclass
class WorksMergeClusters:
    """The four detection classes' output, evidence-strongest first. Only the first three are
    ever eligible for a future apply step; fuzzy_report_only is a STRUCTURALLY separate field
    (never merged into the other three, never given an apply-shaped dataclass) so a future
    apply_works_merge (H2) cannot reach it by construction, not just by convention."""

    same_isbn: list[WorksMergeCluster] = field(default_factory=list)
    same_identity: list[WorksMergeCluster] = field(default_factory=list)
    detected_duplicates: list[WorksMergeCluster] = field(default_factory=list)
    fuzzy_report_only: list[WorksMergeCluster] = field(default_factory=list)

    def summary(self) -> dict[str, int]:
        return {
            "works_same_isbn": len(self.same_isbn),
            "works_same_identity": len(self.same_identity),
            "works_detected_duplicates": len(self.detected_duplicates),
            "works_fuzzy_report_only": len(self.fuzzy_report_only),
        }


# Class strength order, strongest first — index is used as the precedence rank (lower wins).
_WORKS_MERGE_CLASS_ORDER = (
    "works_same_isbn",
    "works_same_identity",
    "works_detected_duplicates",
    "works_fuzzy_report_only",
)


class _UnionFind:
    """Minimal union-find (path compression, no union-by-rank — cluster sizes here are tiny)
    over arbitrary hashable ids, used to collapse transitive pairs (A~B, B~C) into one cluster
    without pulling in a dependency."""

    def __init__(self):
        self._parent: dict[UUID, UUID] = {}

    def find(self, x: UUID) -> UUID:
        self._parent.setdefault(x, x)
        while self._parent[x] != x:
            self._parent[x] = self._parent[self._parent[x]]
            x = self._parent[x]
        return x

    def union(self, a: UUID, b: UUID) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self._parent[ra] = rb


def plan_works_merge_clusters(
    *,
    same_isbn_pairs: list[tuple[UUID, UUID]],
    same_identity_pairs: list[tuple[UUID, UUID]],
    detected_duplicate_pairs: list[tuple[UUID, UUID]],
    fuzzy_pairs: list[tuple[UUID, UUID]],
    stats_by_work: dict[UUID, WorkStats],
) -> WorksMergeClusters:
    """Pure composition core (no DB access): given each class's already-detected pairs (as
    plain (id, id) 2-tuples — order does not matter, they are deduped as unordered pairs
    below), resolve cross-class overlap (a pair appears once, in its strongest class), collapse
    transitive clusters via union-find, and pick each cluster's survivor.

    A cluster's reported class is the STRONGEST class of any edge that built it (see the
    module-level comment above this section) — this is what makes
    test_transitive_cluster_takes_the_strongest_class_of_any_edge pass: a fuzzy-only edge that
    gets pulled into a same_isbn cluster is reported under same_isbn, not fuzzy."""
    pairs_by_class: dict[str, list[frozenset]] = {
        "works_same_isbn": [frozenset(p) for p in same_isbn_pairs],
        "works_same_identity": [frozenset(p) for p in same_identity_pairs],
        "works_detected_duplicates": [frozenset(p) for p in detected_duplicate_pairs],
        "works_fuzzy_report_only": [frozenset(p) for p in fuzzy_pairs],
    }

    # Cross-class precedence: a pair keeps only its strongest class's edge.
    best_class_for_pair: dict[frozenset, str] = {}
    for class_name in _WORKS_MERGE_CLASS_ORDER:
        for pair in pairs_by_class[class_name]:
            if pair not in best_class_for_pair:
                best_class_for_pair[pair] = class_name

    uf = _UnionFind()
    for pair in best_class_for_pair:
        a, b = tuple(pair)
        uf.union(a, b)

    # Group surviving (deduped, precedence-resolved) pair edges by their cluster root, and
    # track the strongest class among the edges that built each cluster.
    cluster_root_class_rank: dict[UUID, int] = {}
    cluster_members: dict[UUID, set[UUID]] = defaultdict(set)
    for pair, class_name in best_class_for_pair.items():
        a, b = tuple(pair)
        root = uf.find(a)
        cluster_members[root].update((a, b))
        rank = _WORKS_MERGE_CLASS_ORDER.index(class_name)
        cluster_root_class_rank[root] = min(rank, cluster_root_class_rank.get(root, rank))

    out = WorksMergeClusters()
    dest_by_class_name = {
        "works_same_isbn": out.same_isbn,
        "works_same_identity": out.same_identity,
        "works_detected_duplicates": out.detected_duplicates,
        "works_fuzzy_report_only": out.fuzzy_report_only,
    }
    # Deterministic ordering: iterate roots sorted by string so plan output (and therefore any
    # future report/token emission) is stable across re-plans of unchanged data.
    for root in sorted(cluster_members, key=str):
        members = cluster_members[root]
        class_name = _WORKS_MERGE_CLASS_ORDER[cluster_root_class_rank[root]]
        candidates = [stats_by_work[wid] for wid in members]
        survivor = pick_survivor(candidates)
        cluster = WorksMergeCluster(
            class_name=class_name,
            work_ids=sorted(members, key=str),
            titles=[stats_by_work[wid].title for wid in sorted(members, key=str)],
            survivor_id=survivor.work_id,
            stats_by_work={wid: stats_by_work[wid] for wid in members},
        )
        dest_by_class_name[class_name].append(cluster)
    return out


def _detect_same_isbn_pairs(session: Session) -> list[tuple[UUID, UUID]]:
    """works_same_isbn: two+ works sharing a non-null editions.isbn_13. Column-explicit (no
    Work entity load) — matches the house convention elsewhere in this module, though this
    query does not touch deep_enriched_at itself (the caller gathers WorkStats separately)."""
    rows = session.query(Edition.work_id, Edition.isbn_13).filter(Edition.isbn_13.isnot(None)).all()
    groups: dict[str, set[UUID]] = defaultdict(set)
    for work_id, isbn in rows:
        groups[isbn].add(work_id)
    pairs: list[tuple[UUID, UUID]] = []
    for work_ids in groups.values():
        if len(work_ids) < 2:
            continue
        ordered = sorted(work_ids, key=str)
        pairs.extend((ordered[0], wid) for wid in ordered[1:])
    return pairs


def _author_tokens(name: str) -> set[str]:
    return set(_fold(name).split())


def _detect_same_identity_pairs(session: Session) -> list[tuple[UUID, UUID]]:
    """works_same_identity: fold(title) equal AND author-token-set overlap >= 1 full token,
    with the series guard blocking sequel titles. O(n^2) within each fold(title) bucket (buckets
    are small in practice — exact-title collisions), never across the whole catalog."""
    rows = (
        session.query(Work.id, Work.title, Author.name)
        .join(WorkContributor, WorkContributor.work_id == Work.id)
        .join(Author, Author.id == WorkContributor.author_id)
        .filter(WorkContributor.role == "Author")
        .order_by(Work.id)
        .all()
    )
    authors_by_work: dict[UUID, set[str]] = defaultdict(set)
    title_by_work: dict[UUID, str] = {}
    for work_id, title, author_name in rows:
        authors_by_work[work_id] |= _author_tokens(author_name)
        title_by_work[work_id] = title

    fold_buckets: dict[str, list[UUID]] = defaultdict(list)
    for work_id, title in title_by_work.items():
        fold_buckets[_fold(title)].append(work_id)

    pairs: list[tuple[UUID, UUID]] = []
    for work_ids in fold_buckets.values():
        ordered = sorted(work_ids, key=str)
        for i, wa in enumerate(ordered):
            for wb in ordered[i + 1 :]:
                if authors_by_work[wa] & authors_by_work[wb]:
                    pairs.append((wa, wb))
    return pairs


def _detect_detected_duplicate_pairs(session: Session) -> list[tuple[UUID, UUID]]:
    """works_detected_duplicates: rows from the #141/#143 detected_duplicates feed, deduped as
    UNORDERED pairs — both (A, B) and (B, A) rows can exist for the same cluster (composite PK
    is (work_id_a, work_id_b), not order-normalized; see DetectedDuplicate's docstring)."""
    rows = session.query(DetectedDuplicate.work_id_a, DetectedDuplicate.work_id_b).all()
    seen: set[frozenset] = set()
    pairs: list[tuple[UUID, UUID]] = []
    for a, b in rows:
        pair = frozenset((a, b))
        if pair in seen:
            continue
        seen.add(pair)
        pairs.append(tuple(sorted((a, b), key=str)))
    return pairs


def _detect_fuzzy_pairs(
    title_by_work: dict[UUID, str], already_paired: set[frozenset], threshold: float = FUZZY_SIMILARITY_THRESHOLD
) -> list[tuple[UUID, UUID]]:
    """works_fuzzy_report_only: token-set similarity on folded titles above `threshold`, MINUS
    pairs already caught by a stronger class (`already_paired`, as a set of frozensets) and
    minus pairs the series guard blocks (a fuzzy-similar sequel title is not a duplicate).
    O(n^2) over every work — acceptable at catalog scale (hundreds, not millions) per the
    design spec's "retroactive global fuzzy dedup... stays report-only" framing; this is a
    detail-list class, not a hot path."""
    ids = sorted(title_by_work, key=str)
    pairs: list[tuple[UUID, UUID]] = []
    for i, wa in enumerate(ids):
        for wb in ids[i + 1 :]:
            pair = frozenset((wa, wb))
            if pair in already_paired:
                continue
            title_a, title_b = title_by_work[wa], title_by_work[wb]
            if _series_guard_blocks(title_a, title_b):
                continue
            if fuzzy_similarity(title_a, title_b) >= threshold:
                pairs.append((wa, wb))
    return pairs


def _gather_work_stats(session: Session) -> dict[UUID, WorkStats]:
    """One pass gathering everything pick_survivor needs, keyed by work id. Entity-loads Work
    (including deep_enriched_at) — safe here (unlike plan_dedup's classes) because the
    works-merge tool is a later-stage PR-2 cleanup that runs well after migration 48e3762d6c0c
    (which added deep_enriched_at) and f871fd59415e (which added detected_duplicates) have both
    landed; see this module's top docstring for the invariant that DOES still apply to
    plan_dedup's own pre-#95-migration classes, which this function does not touch."""
    trope_link_counts = Counter(
        row.work_id for row in session.query(WorkTrope.work_id).filter(WorkTrope.justification.isnot(None)).all()
    )
    edition_counts = Counter(row.work_id for row in session.query(Edition.work_id).all())
    out: dict[UUID, WorkStats] = {}
    for work in session.query(Work).order_by(Work.id).all():
        out[work.id] = WorkStats(
            work_id=work.id,
            title=work.title,
            justified_trope_links=trope_link_counts.get(work.id, 0),
            deep_enriched_at=work.deep_enriched_at,
            edition_count=edition_counts.get(work.id, 0),
        )
    return out


def plan_works_merge(session: Session) -> WorksMergeClusters:
    """READ ONLY. Top-level works-merge detection entry point (PR-2 part 1) — gathers each
    class's pairs from the DB, gathers per-work stats, and delegates to the pure
    plan_works_merge_clusters for precedence resolution / transitive collapse / survivor
    selection. No apply step exists in this module; H2 builds the merge composition on top of
    this plan's clusters."""
    stats_by_work = _gather_work_stats(session)
    title_by_work = {wid: s.title for wid, s in stats_by_work.items()}

    same_isbn_pairs = _detect_same_isbn_pairs(session)
    same_identity_pairs = [
        (a, b)
        for a, b in _detect_same_identity_pairs(session)
        if not _series_guard_blocks(title_by_work[a], title_by_work[b])
    ]
    detected_duplicate_pairs = _detect_detected_duplicate_pairs(session)

    already_paired = {frozenset(p) for p in same_isbn_pairs + same_identity_pairs + detected_duplicate_pairs}
    fuzzy_pairs = _detect_fuzzy_pairs(title_by_work, already_paired)

    return plan_works_merge_clusters(
        same_isbn_pairs=same_isbn_pairs,
        same_identity_pairs=same_identity_pairs,
        detected_duplicate_pairs=detected_duplicate_pairs,
        fuzzy_pairs=fuzzy_pairs,
        stats_by_work=stats_by_work,
    )


def render_works_merge_report(clusters: WorksMergeClusters, *, db_target: str | None = None) -> str:
    """Human-readable report text, consistent with this module's existing report format
    (dedup's `_write_dedup_report` in scripts/clean_catalog.py / fallback_repair's
    write_report): a summary block, then per-class cluster sections showing every work id +
    title in the cluster and which one was picked as survivor. No machine-readable token block
    here — plan_id_set-shaped token emission is H2's job (the apply step doesn't exist yet), so
    there is nothing to gate against drift yet. fuzzy_report_only is called out explicitly as
    NEVER APPLIED so an operator reading a persisted copy of this text (not just the live CLI
    output) sees the same warning."""
    lines: list[str] = ["Works-merge plan report", "=" * 60, ""]
    if db_target is not None:
        lines.append(f"db target: {db_target}")
        lines.append("")

    summary = clusters.summary()
    lines.append("summary:")
    for key, count in summary.items():
        lines.append(f"  {key:32} {count}")
    lines.append("")

    def _render_section(title: str, class_clusters: list[WorksMergeCluster], *, never_applied: bool) -> None:
        suffix = "  (NEVER APPLIED — operator triage only)" if never_applied else ""
        lines.append(f"=== {title} ({len(class_clusters)}){suffix} ===")
        for cluster in class_clusters:
            lines.append(f"  cluster: {cluster.work_ids}")
            for wid in cluster.work_ids:
                stats = cluster.stats_by_work[wid]
                marker = " <- survivor" if wid == cluster.survivor_id else ""
                lines.append(
                    f"    {wid}  {stats.title!r}  "
                    f"justified_tropes={stats.justified_trope_links} "
                    f"deep_enriched_at={stats.deep_enriched_at} "
                    f"editions={stats.edition_count}{marker}"
                )
        lines.append("")

    _render_section("works_same_isbn", clusters.same_isbn, never_applied=False)
    _render_section("works_same_identity", clusters.same_identity, never_applied=False)
    _render_section("works_detected_duplicates", clusters.detected_duplicates, never_applied=False)
    _render_section("works_fuzzy_report_only", clusters.fuzzy_report_only, never_applied=True)

    return "\n".join(lines)


# --------------------------------------------------------------------------------------------
# Cross-class intersection deferral (final-review Critical, GH #95 follow-up)
#
# Two of the seven classes are computed independently but share rows in a way that makes their
# COMPOSITION lossy when both are applied from the same plan snapshot:
#
# (a) narrator-merge x edition-merge: narrator-merge REWRITES an edition_narrators row's
#     narrator_id (delete old pk, insert new) on whatever edition it happens to live on. If that
#     edition is a LOSER in some edition-merge group, the edition group's plan (computed against
#     the SAME pre-apply snapshot) still names the OLD (loser_edition_id, loser_narrator_id) pk
#     in its own repoint_narrators/delete_narrators. By the time edition-merge applies, narrator-
#     merge already ran (apply order: authors, narrators, editions, ...) and that old pk is gone
#     -> edition-merge's repoint/delete for it goes skipped_stale, and the NEW row (same edition,
#     survivor narrator) is never named by the edition group at all. Deleting the loser edition
#     then cascades that unnamed row away via Edition.narrators' `secondary=` relationship — a
#     link the reviewed plan promised to repoint is silently lost.
#
# (b) edition-merge x reading_history-dedup: edition-merge's OWN in-group collision logic can
#     plan "repoint row R (first-seen for a user+date), delete row R2 (collides against R's
#     date)" for an exact-duplicate reading_history pair living on the same loser edition. Class 4
#     (duplicate_reading_history) independently groups the IDENTICAL pair by its own
#     (user_id, edition_id, date_completed) key and may pick the opposite survivor. Applying both
#     from the same snapshot: edition-merge deletes R2 directly, then class 4 (unaware R was
#     already repointed elsewhere — it only does a stale `session.get` lookup, and repointing
#     doesn't remove the row) deletes R too. Both copies of the user's read event are lost.
#
# The fix: compute the intersections at PLAN time and drop the affected groups from the classes
# below, recording them under `deferred_intersections`. This is EXPECTED on intersecting data,
# not an error — the runbook's existing dry-run/apply LOOP re-plans after the intersecting class
# has already applied, so the intersection is gone and the deferred group applies cleanly on the
# next pass (two-pass convergence, proven in test_dedup_backfill.py's intersection tests).
# --------------------------------------------------------------------------------------------


def _defer_intersecting_groups(
    duplicate_narrators: list[ContributorMergeGroup],
    duplicate_editions: list[EditionMergeGroup],
    duplicate_reading_history: list[KeepDeleteGroup],
) -> tuple[list[EditionMergeGroup], list[KeepDeleteGroup], dict[str, list[dict]]]:
    """Returns (filtered_editions, filtered_reading_history, deferred_intersections). Never
    mutates the inputs — builds fresh lists."""
    deferred: dict[str, list[dict]] = {}

    # (a) narrator ids touched by ANY narrator-merge group (survivor + losers) — a loser edition
    # link involving any of these narrator ids may be REWRITTEN by narrator-merge before
    # edition-merge applies.
    narrator_ids_in_play: set[UUID] = set()
    for g in duplicate_narrators:
        narrator_ids_in_play.add(g.survivor_id)
        narrator_ids_in_play.update(g.loser_ids)

    filtered_editions: list[EditionMergeGroup] = []
    for eg in duplicate_editions:
        touched_narrator_ids = {nid for _eid, nid in eg.repoint_narrators} | {nid for _eid, nid in eg.delete_narrators}
        if touched_narrator_ids & narrator_ids_in_play:
            deferred.setdefault("duplicate_editions", []).append(
                {
                    "work_id": eg.work_id,
                    "survivor_id": eg.survivor_id,
                    "loser_ids": eg.loser_ids,
                    "reason": (
                        "narrator-merge touches a narrator id referenced by this edition group's "
                        "narrator repoints/deletes — deferred to avoid the narrator x edition "
                        "cascade-loss composition; re-plan after this apply pass."
                    ),
                }
            )
            continue
        filtered_editions.append(eg)

    # (b) reading_history ids this (already-filtered) set of edition groups will repoint/delete —
    # a class-4 group sharing any of those ids would independently delete a row edition-merge
    # already repointed/deleted from the SAME snapshot.
    rh_ids_touched_by_editions: set[UUID] = set()
    for eg in filtered_editions:
        rh_ids_touched_by_editions.update(eg.repoint_reading_history)
        rh_ids_touched_by_editions.update(eg.delete_reading_history)

    filtered_reading_history: list[KeepDeleteGroup] = []
    for rg in duplicate_reading_history:
        group_ids = {rg.survivor_id, *rg.loser_ids}
        if group_ids & rh_ids_touched_by_editions:
            deferred.setdefault("duplicate_reading_history", []).append(
                {
                    "survivor_id": rg.survivor_id,
                    "loser_ids": rg.loser_ids,
                    "detail": rg.detail,
                    "reason": (
                        "an edition-merge group's own reading_history repoint/delete already "
                        "covers a row in this group — deferred to avoid the edition x "
                        "reading_history double-delete composition; re-plan after this apply pass."
                    ),
                }
            )
            continue
        filtered_reading_history.append(rg)

    return filtered_editions, filtered_reading_history, deferred


# --------------------------------------------------------------------------------------------
# Top-level plan / apply
# --------------------------------------------------------------------------------------------


def plan_dedup(session: Session) -> DedupPlan:
    """READ ONLY. Computes every class against the CURRENT db state. See _plan_orphan_authors
    for why orphans are not simulated against not-yet-applied author merges.

    See _defer_intersecting_groups for why duplicate_editions and duplicate_reading_history are
    filtered against each other (and duplicate_narrators) before the plan is returned — two
    deterministic lossy compositions (narrator x edition cascade; edition x reading_history
    double-delete) are caught here and deferred rather than applied."""
    duplicate_narrators = _plan_narrators(session)
    duplicate_editions = _plan_editions(session)
    duplicate_reading_history = _plan_reading_history(session)

    duplicate_editions, duplicate_reading_history, deferred_intersections = _defer_intersecting_groups(
        duplicate_narrators, duplicate_editions, duplicate_reading_history
    )

    return DedupPlan(
        duplicate_authors=_plan_authors(session),
        duplicate_narrators=duplicate_narrators,
        duplicate_editions=duplicate_editions,
        duplicate_reading_history=duplicate_reading_history,
        duplicate_suggestions=_plan_suggestions(session),
        orphan_authors=_plan_orphan_authors(session),
        duplicate_works_report_only=_plan_duplicate_works(session),
        deferred_intersections=deferred_intersections,
    )


def apply_dedup(session: Session, plan: DedupPlan) -> dict[str, int]:
    """Applies EXACTLY plan's ids — no re-derivation. Order: authors, narrators, editions,
    reading_history, suggestions, orphans. duplicate_works_report_only is NEVER applied.
    Rows that vanished between plan and apply are skipped and counted under skipped_stale.
    Rows that survived but weren't accounted for by the group's own plan (the
    _apply_edition_group / _apply_contributor_group belt-and-braces re-verify — see their
    docstrings) are counted separately under skipped_unsafe, distinct from skipped_stale."""
    result = {
        "duplicate_authors": 0,
        "duplicate_narrators": 0,
        "duplicate_editions": 0,
        "duplicate_reading_history": 0,
        "duplicate_suggestions": 0,
        "orphan_authors": 0,
        "skipped_stale": 0,
        "skipped_unsafe": 0,
    }

    for group in plan.duplicate_authors:
        stats = _apply_contributor_group(session, group, kind="author")
        result["duplicate_authors"] += stats["merged"]
        result["skipped_stale"] += stats["skipped_stale"]
        result["skipped_unsafe"] += stats["skipped_unsafe"]

    for group in plan.duplicate_narrators:
        stats = _apply_contributor_group(session, group, kind="narrator")
        result["duplicate_narrators"] += stats["merged"]
        result["skipped_stale"] += stats["skipped_stale"]
        result["skipped_unsafe"] += stats["skipped_unsafe"]

    for group in plan.duplicate_editions:
        stats = _apply_edition_group(session, group)
        result["duplicate_editions"] += stats["merged"]
        result["skipped_stale"] += stats["skipped_stale"]
        result["skipped_unsafe"] += stats["skipped_unsafe"]

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


# --------------------------------------------------------------------------------------------
# Apply-gate cross-check (#95 follow-up, Spec 2026-07-12): --apply must re-plan from scratch
# (live traffic may have created new duplicates since the operator's reviewed dry-run report),
# so apply cross-checks the FRESH plan's id set against the REVIEWED report's id set and
# refuses on any addition. plan_id_set/plan_delta are the pure (DB-free) half of that gate;
# scripts/clean_catalog.py owns parsing the report file and the refuse/re-report flow.
# --------------------------------------------------------------------------------------------

# The per-class keys used by both plan_id_set and the report's "== PLAN IDS ==" section — one
# source of truth so the report format and the gate's classes never drift apart.
PLAN_ID_SET_CLASSES = (
    "duplicate_authors",
    "duplicate_narrators",
    "duplicate_editions",
    "duplicate_reading_history",
    "duplicate_suggestions",
    "orphan_authors",
    "duplicate_works_report_only",
)


def plan_id_set(plan: DedupPlan) -> dict[str, set[str]]:
    """Every id the plan is 'about', per class, stringified AND TAGGED with the operation it
    belongs to — `merge:` for survivor+losers group identity, `repoint:` for a link/row being
    re-pointed onto a survivor, `delete:` for a link/row/id being deleted outright, `report:`
    for the never-applied duplicate_works_report_only class. The prefix is load-bearing, not
    cosmetic: without it, the SAME id appearing under a different operation (e.g. a row X that
    was `repoint:X` in the reviewed plan comes back as `delete:X` in the fresh plan — a
    concurrent write flipped which case applied) would diff as "unchanged" under a bare id
    comparison, because set-difference only sees the id, not what's about to happen to it. With
    the tag riding along as part of the token, a flip removes the old tagged token and adds a
    new one, so it surfaces as an addition in plan_delta and the existing refuse-on-addition
    policy catches it.

    Composite identifiers (e.g. a repoint's (loser_id, pk) pair) are stringified as
    `<op>:<tuple-repr>` rather than decomposed, so a link moving between two otherwise-unchanged
    ids still shows up as a new token in the delta. duplicate_works_report_only is included for
    a complete, honest diff even though apply_dedup never touches it.

    This is the DB-free half of the apply gate (Spec 2026-07-12 follow-up to #95): the CLI
    parses this same shape back out of a previously-written report and calls plan_delta to
    cross-check a fresh re-plan against what the operator actually reviewed. Tokens are opaque
    strings on both the write and read side — the report writer/parser never interprets the
    prefix, only carries it through unchanged."""
    out: dict[str, set[str]] = {name: set() for name in PLAN_ID_SET_CLASSES}

    for g in plan.duplicate_authors:
        s = out["duplicate_authors"]
        s.add(f"merge:{g.survivor_id}")
        s.update(f"merge:{lid}" for lid in g.loser_ids)
        s.update(f"repoint:{item}" for item in g.repoint_links)
        s.update(f"delete:{item}" for item in g.delete_links)
        s.update(f"repoint:{item}" for item in g.repoint_styles)
        s.update(f"delete:{item}" for item in g.delete_styles)

    for g in plan.duplicate_narrators:
        s = out["duplicate_narrators"]
        s.add(f"merge:{g.survivor_id}")
        s.update(f"merge:{lid}" for lid in g.loser_ids)
        s.update(f"repoint:{item}" for item in g.repoint_links)
        s.update(f"delete:{item}" for item in g.delete_links)
        s.update(f"repoint:{item}" for item in g.repoint_styles)
        s.update(f"delete:{item}" for item in g.delete_styles)

    for g in plan.duplicate_editions:
        s = out["duplicate_editions"]
        s.add(f"merge:{g.survivor_id}")
        s.update(f"merge:{lid}" for lid in g.loser_ids)
        s.update(f"repoint:{rh_id}" for rh_id in g.repoint_reading_history)
        s.update(f"delete:{rh_id}" for rh_id in g.delete_reading_history)
        s.update(f"repoint:{item}" for item in g.repoint_narrators)
        s.update(f"delete:{item}" for item in g.delete_narrators)

    for g in plan.duplicate_reading_history:
        s = out["duplicate_reading_history"]
        s.add(f"merge:{g.survivor_id}")
        s.update(f"delete:{lid}" for lid in g.loser_ids)

    for g in plan.duplicate_suggestions:
        s = out["duplicate_suggestions"]
        s.add(f"merge:{g.survivor_id}")
        s.update(f"delete:{lid}" for lid in g.loser_ids)

    out["orphan_authors"].update(f"delete:{aid}" for aid in plan.orphan_authors)

    for w in plan.duplicate_works_report_only:
        out["duplicate_works_report_only"].update(f"report:{wid}" for wid in w.work_ids)

    return out


def plan_delta(reviewed: dict[str, set[str]], fresh: DedupPlan) -> dict[str, set[str]]:
    """Per-class ids present in the FRESH plan but NOT in the REVIEWED id set (from the
    operator's approved report). Empty everywhere means fresh's id set is a subset of
    reviewed's — i.e. nothing new appeared since the operator looked at the report, and it is
    safe to apply the fresh plan (stale reviewed ids that vanished from fresh are fine; that's
    ordinary `skipped_stale` territory at apply time, not a plan drift)."""
    fresh_ids = plan_id_set(fresh)
    return {name: fresh_ids.get(name, set()) - reviewed.get(name, set()) for name in PLAN_ID_SET_CLASSES}
