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
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
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
    WorkStyle,
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
# Detection classes, evidence-strongest first — each produces UNORDERED work-pair groupings (a
# work id pair is a 2-tuple but is always compared/deduped as a frozenset so (A, B) and (B, A)
# collapse to the same pair). THREE are applyable; TWO are report-only forever:
#   1. works_same_isbn        — APPLYABLE. Two works sharing a non-null editions.isbn_13 AND
#                                agreeing folded titles (fold(title_a) == fold(title_b)). A prod
#                                dry-run (2026-07-15) found isbn_13 pollution in this catalog:
#                                distinct books share an ISBN (chained 14 different novels into
#                                one bogus cluster) and a sequel can carry its predecessor's ISBN
#                                (Beware of Chicken 2 sharing book 1's ISBN) — "shared ISBN" alone
#                                is no longer strong enough evidence to apply on its own.
#   1b. works_same_isbn_title_mismatch — REPORT ONLY. The other half of an ISBN group: two works
#                                sharing an ISBN whose folded titles DISAGREE. Never promoted to
#                                applyable by this tool — operator triage only (a real typo pair
#                                like 'Exit Stategy'/'Exit Strategy' the operator can promote by
#                                hand, vs. the pollution/sequel shapes above that must never
#                                merge). Structurally separate field, same treatment as fuzzy.
#   2. works_same_identity    — APPLYABLE. fold(title) equal AND author-token overlap >= 1 full
#                                token, with the series guard blocking sequel titles from matching.
#   3. works_detected_duplicates — APPLYABLE. Rows from the #141/#143 detected_duplicates feed,
#                                deduped as unordered pairs (the table's composite PK is
#                                (work_id_a, work_id_b), NOT order-normalized — both rows of one
#                                cluster can exist; see DetectedDuplicate's docstring in
#                                db/models.py).
#   4. works_fuzzy_report_only — REPORT ONLY. Token-set similarity on folded titles above a
#                                threshold, minus pairs already caught by a stronger class above
#                                (now including works_same_isbn_title_mismatch — a differing-title
#                                ISBN pair should not ALSO show up as a fuzzy pair). REPORT ONLY
#                                FOREVER: never promoted to an applyable class by this tool (the
#                                design spec: "operator promotes pairs by hand if real").
#
# A pair that matches more than one class is reported ONCE, in its single strongest class,
# ranked ONLY among the three applyable classes (works_same_isbn > works_same_identity >
# works_detected_duplicates). Applyable pairs are unioned into transitive clusters (A~B, B~C ->
# one {A, B, C} cluster) via ONE union-find over just those three classes; a cluster's reported
# class is the STRONGEST applyable class of any edge that built it.
#
# CONTRACT REVERSAL (2026-07-15, the same prod dry-run): report-only classes
# (works_same_isbn_title_mismatch, works_fuzzy_report_only) are NEVER part of that union-find —
# each clusters in its OWN INDEPENDENT union-find, entirely separate from the applyable one and
# from each other. The old contract ("a fuzzy-only edge pulled into a same_isbn cluster is
# reported under same_isbn") is REVERSED: a report-only edge can never grow, shrink, or relabel
# an applyable cluster — it is structurally incapable of adding a work to an applyable merge, not
# just excluded by convention. (Previously, ONE union-find ran over every class's pairs together,
# so a single report-only edge touching an applyable cluster silently added its other endpoint to
# that applyable merge — the exact amplification the prod dry-run caught.) A report-only pair
# whose both endpoints already sit together in one applyable cluster is dropped entirely (nothing
# left to triage, it's merging anyway); a pair spanning two different applyable clusters, or
# reaching a work outside any applyable cluster, is kept for operator-facing display.
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
    """True if `title_a`/`title_b` are different entries of one series — i.e. they differ
    ONLY by trailing volume/sequel tokens. Two shapes (Gemini review, PR #144):
      base vs volume:   'Beware of Chicken' vs 'Beware of Chicken 2' -> blocked;
      volume vs volume: 'Beware of Chicken 2' vs 'Beware of Chicken 3' -> blocked
                        (both carry volume tokens and share the stripped base).
    NOT blocked: identical folds ('Beware of Chicken 2' vs itself — a true same-volume
    duplicate must stay mergeable), and titles with no trailing volume token on either side
    ('We Are Legion (We Are Bob)' vs 'We are Legion; We are Bob' — the punctuation-fold
    case fold() already handles). Symmetric in its two arguments by construction."""
    fa, fb = _fold(title_a), _fold(title_b)
    if fa == fb:
        return False
    stripped_a, had_a = _strip_trailing_volume_token(fa)
    stripped_b, had_b = _strip_trailing_volume_token(fb)
    if had_a and stripped_a == fb:
        return True
    if had_b and stripped_b == fa:
        return True
    return bool(had_a and had_b and stripped_a == stripped_b)


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
    # (applyable) / "works_same_isbn_title_mismatch" / "works_fuzzy_report_only" (report only)
    work_ids: list[UUID]
    titles: list[str]
    survivor_id: UUID
    stats_by_work: dict[UUID, WorkStats] = field(default_factory=dict)


@dataclass
class WorksMergeClusters:
    """The detection classes' output, evidence-strongest first. Only same_isbn / same_identity /
    detected_duplicates are ever eligible for a future apply step; same_isbn_title_mismatch and
    fuzzy_report_only are STRUCTURALLY separate fields (never merged into the applyable three,
    never given an apply-shaped dataclass, and — since the 2026-07-15 hardening — clustered in
    their OWN independent union-finds rather than sharing the applyable one) so apply_works_merge
    cannot reach them by construction, not just by convention, and a report-only edge can never
    grow an applyable cluster."""

    same_isbn: list[WorksMergeCluster] = field(default_factory=list)
    same_identity: list[WorksMergeCluster] = field(default_factory=list)
    detected_duplicates: list[WorksMergeCluster] = field(default_factory=list)
    # REPORT ONLY (2026-07-15 hardening): an ISBN group whose members' folded titles disagree —
    # shared-ISBN pollution or a sequel carrying its predecessor's ISBN, never applyable.
    same_isbn_title_mismatch: list[WorksMergeCluster] = field(default_factory=list)
    fuzzy_report_only: list[WorksMergeCluster] = field(default_factory=list)
    # Self-referential detected_duplicates feed rows (work_id_a == work_id_b) skipped at
    # ingestion — see _dedupe_detected_duplicate_rows. Surfaced here (not just logged) so a bad
    # feed row is visible to the operator reading the plan/report, never silent.
    ignored_self_detections: int = 0

    def summary(self) -> dict[str, int]:
        return {
            "works_same_isbn": len(self.same_isbn),
            "works_same_isbn_title_mismatch": len(self.same_isbn_title_mismatch),
            "works_same_identity": len(self.same_identity),
            "works_detected_duplicates": len(self.detected_duplicates),
            "works_fuzzy_report_only": len(self.fuzzy_report_only),
            "ignored_self_detections": self.ignored_self_detections,
        }


# Applyable class strength order, strongest first — index is used as the precedence rank (lower
# wins) among the THREE classes eligible for a future apply step. Report-only classes are never
# ranked here (see _REPORT_ONLY_CLASSES) — each clusters independently, never joining this order.
_APPLYABLE_CLASS_ORDER = (
    "works_same_isbn",
    "works_same_identity",
    "works_detected_duplicates",
)

# Report-only classes, each clustered in its own independent union-find (see
# plan_works_merge_clusters) — never ranked against each other or against _APPLYABLE_CLASS_ORDER.
_REPORT_ONLY_CLASSES = (
    "works_same_isbn_title_mismatch",
    "works_fuzzy_report_only",
)

# Display ordering only (e.g. a future report iterating "every class in evidence-strength order")
# — NOT used for precedence ranking; see _APPLYABLE_CLASS_ORDER for that.
_WORKS_MERGE_CLASS_ORDER = _APPLYABLE_CLASS_ORDER + _REPORT_ONLY_CLASSES


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


def _build_cluster(class_name: str, members: set[UUID], stats_by_work: dict[UUID, WorkStats]) -> WorksMergeCluster:
    ordered = sorted(members, key=str)
    candidates = [stats_by_work[wid] for wid in ordered]
    survivor = pick_survivor(candidates)
    return WorksMergeCluster(
        class_name=class_name,
        work_ids=ordered,
        titles=[stats_by_work[wid].title for wid in ordered],
        survivor_id=survivor.work_id,
        stats_by_work={wid: stats_by_work[wid] for wid in ordered},
    )


def plan_works_merge_clusters(
    *,
    same_isbn_pairs: list[tuple[UUID, UUID]],
    same_identity_pairs: list[tuple[UUID, UUID]],
    detected_duplicate_pairs: list[tuple[UUID, UUID]],
    fuzzy_pairs: list[tuple[UUID, UUID]],
    stats_by_work: dict[UUID, WorkStats],
    same_isbn_mismatch_pairs: list[tuple[UUID, UUID]] = (),
) -> WorksMergeClusters:
    """Pure composition core (no DB access): given each class's already-detected pairs (as
    plain (id, id) 2-tuples — order does not matter, they are deduped as unordered pairs
    below), resolve cross-class overlap (a pair appears once, in its strongest class), collapse
    transitive clusters via union-find, and pick each cluster's survivor.

    Two SEPARATE union-finds, per the 2026-07-15 hardening (see the module-level comment above
    this section for the prod-dry-run contract reversal this fixes):

    1. APPLYABLE union-find over ONLY same_isbn_pairs + same_identity_pairs +
       detected_duplicate_pairs. A cluster's reported class is the STRONGEST of these three
       classes among the edges that built it — this is what
       test_fuzzy_edge_contagion_regression pins: a fuzzy-only edge touching an applyable
       cluster can never add its other endpoint to that applyable cluster.
    2. REPORT-ONLY clustering: same_isbn_mismatch_pairs and fuzzy_pairs each get their OWN
       independent union-find (never each other's, never the applyable one). A report-only pair
       whose both endpoints already sit together in one applyable cluster is dropped (nothing
       left to triage — it's merging anyway); any other report-only pair is kept for display."""
    applyable_pairs_by_class: dict[str, list[frozenset]] = {
        "works_same_isbn": [frozenset(p) for p in same_isbn_pairs],
        "works_same_identity": [frozenset(p) for p in same_identity_pairs],
        "works_detected_duplicates": [frozenset(p) for p in detected_duplicate_pairs],
    }

    # Cross-class precedence (applyable classes only): a pair keeps only its strongest class's edge.
    best_class_for_pair: dict[frozenset, str] = {}
    for class_name in _APPLYABLE_CLASS_ORDER:
        for pair in applyable_pairs_by_class[class_name]:
            if pair not in best_class_for_pair:
                best_class_for_pair[pair] = class_name

    uf = _UnionFind()
    for pair in best_class_for_pair:
        a, b = tuple(pair)
        uf.union(a, b)

    # Group surviving (deduped, precedence-resolved) pair edges by their cluster root, and
    # track the strongest applyable class among the edges that built each cluster.
    cluster_root_class_rank: dict[UUID, int] = {}
    cluster_members: dict[UUID, set[UUID]] = defaultdict(set)
    for pair, class_name in best_class_for_pair.items():
        a, b = tuple(pair)
        root = uf.find(a)
        cluster_members[root].update((a, b))
        rank = _APPLYABLE_CLASS_ORDER.index(class_name)
        cluster_root_class_rank[root] = min(rank, cluster_root_class_rank.get(root, rank))

    out = WorksMergeClusters()
    dest_by_class_name = {
        "works_same_isbn": out.same_isbn,
        "works_same_identity": out.same_identity,
        "works_detected_duplicates": out.detected_duplicates,
    }
    # applyable_root_by_work: which applyable cluster (by root id) each work currently sits in,
    # used below to drop report-only pairs that are entirely subsumed by one applyable cluster.
    applyable_root_by_work: dict[UUID, UUID] = {}
    # Deterministic ordering: iterate roots sorted by string so plan output (and therefore any
    # future report/token emission) is stable across re-plans of unchanged data.
    for root in sorted(cluster_members, key=str):
        members = cluster_members[root]
        class_name = _APPLYABLE_CLASS_ORDER[cluster_root_class_rank[root]]
        cluster = _build_cluster(class_name, members, stats_by_work)
        dest_by_class_name[class_name].append(cluster)
        for wid in members:
            applyable_root_by_work[wid] = root

    def _cluster_report_only(class_name: str, pairs: list[tuple[UUID, UUID]]) -> list[WorksMergeCluster]:
        """Independent union-find for ONE report-only class — never shares a union-find with any
        other class, applyable or report-only (see this function's docstring)."""
        uf_report = _UnionFind()
        kept_pairs: set[frozenset] = set()
        for a, b in pairs:
            pair = frozenset((a, b))
            if pair in kept_pairs:
                continue
            root_a = applyable_root_by_work.get(a)
            root_b = applyable_root_by_work.get(b)
            if root_a is not None and root_a == root_b:
                continue  # both endpoints already merging in the SAME applyable cluster
            kept_pairs.add(pair)
            uf_report.union(a, b)

        members_by_root: dict[UUID, set[UUID]] = defaultdict(set)
        for pair in kept_pairs:
            a, b = tuple(pair)
            root = uf_report.find(a)
            members_by_root[root].update((a, b))

        return [
            _build_cluster(class_name, members_by_root[root], stats_by_work)
            for root in sorted(members_by_root, key=str)
        ]

    out.same_isbn_title_mismatch = _cluster_report_only("works_same_isbn_title_mismatch", same_isbn_mismatch_pairs)
    out.fuzzy_report_only = _cluster_report_only("works_fuzzy_report_only", fuzzy_pairs)
    return out


def _classify_isbn_group_pairs(
    work_ids: list[UUID], fold_by_work: dict[UUID, str]
) -> tuple[list[tuple[UUID, UUID]], list[tuple[UUID, UUID]]]:
    """Pure (no session): given ONE ISBN group's work ids, classify every pairwise combination
    (full O(n^2) — groups are tiny in practice, see the module comment's Fix-1 note) by folded-
    title agreement into (applyable_pairs, mismatch_pairs). Star-pairing (only ordered[0] vs the
    rest) is NOT enough here even though the union-find would still transitively close the
    applyable side: the mismatch side needs every member compared against every OTHER member so
    a mismatching outlier (e.g. a sequel carrying its predecessor's ISBN) is visible against
    every equal-fold member it was grouped with, not just the group's first id."""
    ordered = sorted(work_ids, key=str)
    applyable: list[tuple[UUID, UUID]] = []
    mismatch: list[tuple[UUID, UUID]] = []
    for i, wa in enumerate(ordered):
        for wb in ordered[i + 1 :]:
            # Direct indexing on purpose (Gemini review, #146): callers guarantee every id is
            # in fold_by_work (unknown ids are dropped at the edition scan). A .get() here
            # once let two UNTRACKED ids match None == None into an APPLYABLE pair — the
            # wrong failure direction for a destructive tool; a KeyError is the honest one.
            if fold_by_work[wa] == fold_by_work[wb]:
                applyable.append((wa, wb))
            else:
                mismatch.append((wa, wb))
    return applyable, mismatch


def _detect_same_isbn_pairs(
    session: Session, fold_by_work: dict[UUID, str]
) -> tuple[list[tuple[UUID, UUID]], list[tuple[UUID, UUID]]]:
    """works_same_isbn (+ its report-only sibling works_same_isbn_title_mismatch): two+ works
    sharing a non-null editions.isbn_13. Column-explicit (no Work entity load) — matches the
    house convention elsewhere in this module, though this query does not touch
    deep_enriched_at itself (the caller gathers WorkStats separately).

    2026-07-15 hardening: shared ISBN alone is no longer applyable evidence — this prod catalog
    has ISBN pollution (distinct books sharing an isbn_13, a sequel carrying its predecessor's)
    that chained 14 unrelated books into one bogus applyable cluster in a real dry-run. A pair
    is applyable ONLY if its members' folded titles also agree; otherwise it is reported under
    works_same_isbn_title_mismatch (operator triage only, never applied by this tool). Returns
    (applyable_pairs, mismatch_pairs)."""
    rows = session.query(Edition.work_id, Edition.isbn_13).filter(Edition.isbn_13.isnot(None)).all()
    groups: dict[str, set[UUID]] = defaultdict(set)
    for work_id, isbn in rows:
        # Gemini review (#146): a work created between the caller's works scan and this
        # edition scan has no fold/stats entry — skip it (next plan run sees it) instead of
        # letting unknown ids match None == None into an applyable pair or KeyError at
        # cluster build.
        if work_id in fold_by_work:
            groups[isbn].add(work_id)
    applyable: list[tuple[UUID, UUID]] = []
    mismatch: list[tuple[UUID, UUID]] = []
    for work_ids in groups.values():
        if len(work_ids) < 2:
            continue
        group_applyable, group_mismatch = _classify_isbn_group_pairs(list(work_ids), fold_by_work)
        applyable.extend(group_applyable)
        mismatch.extend(group_mismatch)
    return applyable, mismatch


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


def _dedupe_detected_duplicate_rows(rows: list[tuple[UUID, UUID]]) -> tuple[list[tuple[UUID, UUID]], int]:
    """Pure (no session): dedupe raw detected_duplicates feed rows as UNORDERED pairs — both
    (A, B) and (B, A) rows can exist for the same cluster (composite PK is (work_id_a,
    work_id_b), not order-normalized; see DetectedDuplicate's docstring) — and SKIP any
    self-referential row (work_id_a == work_id_b). A work is never its own duplicate; a
    self-pair is a bad feed row, not a real detection. Left in, `frozenset((A, A))` collapses to
    size 1 and crashes plan_works_merge_clusters' union-find unpack (`a, b = tuple(pair)`) —
    skipping it here, at the feed-ingestion boundary, keeps the crash from ever reaching that
    pure composition core. Returns (pairs, ignored_self_count) so the caller can surface the
    count in the plan summary — a bad feed row should be VISIBLE to the operator, not silently
    dropped."""
    seen: set[frozenset] = set()
    pairs: list[tuple[UUID, UUID]] = []
    ignored_self = 0
    for a, b in rows:
        if a == b:
            ignored_self += 1
            continue
        pair = frozenset((a, b))
        if pair in seen:
            continue
        seen.add(pair)
        pairs.append(tuple(sorted((a, b), key=str)))
    return pairs, ignored_self


def _detect_detected_duplicate_pairs(session: Session) -> tuple[list[tuple[UUID, UUID]], int]:
    """works_detected_duplicates: rows from the #141/#143 detected_duplicates feed. See
    _dedupe_detected_duplicate_rows (kept session-free for a fast, deterministic unit-test
    surface) for the actual dedup/self-pair-skip logic. Returns (pairs, ignored_self_count)."""
    rows = session.query(DetectedDuplicate.work_id_a, DetectedDuplicate.work_id_b).all()
    return _dedupe_detected_duplicate_rows(rows)


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
    # Gemini review (#144): fold each title ONCE — refolding inside the O(n^2) loop was
    # redundant CPU per pair. Jaccard is computed on the precomputed token sets; the series
    # guard still gets the raw titles (it needs fold-internal volume-token structure).
    tokens_by_work = {wid: set(_fold(title_by_work[wid]).split()) for wid in ids}
    pairs: list[tuple[UUID, UUID]] = []
    for i, wa in enumerate(ids):
        tokens_a = tokens_by_work[wa]
        for wb in ids[i + 1 :]:
            pair = frozenset((wa, wb))
            if pair in already_paired:
                continue
            tokens_b = tokens_by_work[wb]
            if not tokens_a or not tokens_b:
                continue
            if len(tokens_a & tokens_b) / len(tokens_a | tokens_b) < threshold:
                continue
            if _series_guard_blocks(title_by_work[wa], title_by_work[wb]):
                continue
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
    # Column-explicit (Gemini review, #144): only three columns are needed — full entity
    # hydration was pure overhead in a whole-catalog scan.
    for work_id, title, deep_enriched_at in (
        session.query(Work.id, Work.title, Work.deep_enriched_at).order_by(Work.id).all()
    ):
        out[work_id] = WorkStats(
            work_id=work_id,
            title=title,
            justified_trope_links=trope_link_counts.get(work_id, 0),
            deep_enriched_at=deep_enriched_at,
            edition_count=edition_counts.get(work_id, 0),
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
    fold_by_work = {wid: _fold(title) for wid, title in title_by_work.items()}

    same_isbn_pairs, same_isbn_mismatch_pairs = _detect_same_isbn_pairs(session, fold_by_work)
    same_identity_pairs = [
        (a, b)
        for a, b in _detect_same_identity_pairs(session)
        if not _series_guard_blocks(title_by_work[a], title_by_work[b])
    ]
    detected_duplicate_pairs, ignored_self_detections = _detect_detected_duplicate_pairs(session)

    # already_paired also includes same_isbn_mismatch_pairs (Fix 2's note): a differing-title
    # ISBN pair is already visible under works_same_isbn_title_mismatch and should not ALSO show
    # up as a fuzzy pair.
    already_paired = {
        frozenset(p)
        for p in same_isbn_pairs + same_isbn_mismatch_pairs + same_identity_pairs + detected_duplicate_pairs
    }
    fuzzy_pairs = _detect_fuzzy_pairs(title_by_work, already_paired)

    clusters = plan_works_merge_clusters(
        same_isbn_pairs=same_isbn_pairs,
        same_isbn_mismatch_pairs=same_isbn_mismatch_pairs,
        same_identity_pairs=same_identity_pairs,
        detected_duplicate_pairs=detected_duplicate_pairs,
        fuzzy_pairs=fuzzy_pairs,
        stats_by_work=stats_by_work,
    )
    clusters.ignored_self_detections = ignored_self_detections
    return clusters


def render_works_merge_report(clusters: WorksMergeClusters, *, db_target: str | None = None) -> str:
    """Human-readable report text, consistent with this module's existing report format
    (dedup's `_write_dedup_report` in scripts/clean_catalog.py / fallback_repair's
    write_report): a summary block, then per-class cluster sections showing every work id +
    title in the cluster and which one was picked as survivor. No machine-readable token block
    here — plan_id_set-shaped token emission is H2's job (the apply step doesn't exist yet), so
    there is nothing to gate against drift yet. same_isbn_title_mismatch and fuzzy_report_only
    are both called out explicitly as NEVER APPLIED so an operator reading a persisted copy of
    this text (not just the live CLI output) sees the same warning."""
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
    _render_section("works_same_isbn_title_mismatch", clusters.same_isbn_title_mismatch, never_applied=True)
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


# --------------------------------------------------------------------------------------------
# Works-merge APPLY (H2, Spec 2026-07-14 "Merge composition" / "Gate" / item 6). Builds the
# actual merge composition on top of plan_works_merge's clusters (same module, same file, per
# the design spec — "extend etl/dedup_backfill.py... do NOT build a parallel tool").
#
# ONLY same_isbn / same_identity / detected_duplicates clusters are ever composed —
# fuzzy_report_only is STRUCTURALLY unreachable here: compose_cluster_merge and
# apply_works_merge only ever iterate WorksMergeClusters.same_isbn/.same_identity/
# .detected_duplicates (see applyable_works_merge_clusters below), never .fuzzy_report_only.
# There is no code path, however careless, that could reach it — the field simply isn't in the
# iteration.
#
# Deliverable 5 (spec item 5, stated here so nobody "fixes" it later): the availability cache
# (availability/overdrive.py's `availability_cache` table) and `user_libraries` are keyed by
# TITLE/AUTHOR STRINGS, not work ids — they have no FK to Work at all. A works-merge is
# deliberately silent about both: there is nothing to repoint, nothing to union, no row that
# could dangle. The survivor's title/author strings are what a "where to get it" lookup already
# keyed on before the merge, so availability continues to resolve correctly with zero action here.
#
# DISJOINTNESS PROOF (deliverable 2 — the deferred-intersections discipline, reasoned through
# rather than found empirically): after plan_works_merge_clusters' union-find, every cluster is a
# disjoint SET OF WORK IDS — no work id appears in two clusters (union-find guarantees this by
# construction: any pair that shares a work id gets unioned into the SAME cluster). Every row this
# composition touches via a SINGLE NOT-NULL foreign key (Edition.work_id, ReadingHistory.edition_id
# -> Edition.work_id transitively, Suggestions.work_id, WorkTrope.work_id, WorkStyle.work_id,
# WorkContributor.work_id) hangs off exactly one work id, so it can only ever be "in play" for the
# ONE cluster containing that id.
#
# detected_duplicates is the one exception worth stating precisely rather than folding into the
# above: each row carries TWO work ids (work_id_a, work_id_b), not one — so "hangs off exactly one
# work id" does not literally hold for it. The argument for detected_duplicates is the EDGE
# argument instead: every detected_duplicates row (after self-pair filtering, see
# _dedupe_detected_duplicate_rows) becomes a union-find edge in plan_works_merge_clusters — ALL of
# it, not just the pairs reported under works_detected_duplicates (a pair's CLASS LABEL is only its
# strongest matching class for display purposes; the UNION still runs on every pair from every
# class, see the union-find loop above). So if a detected_duplicates row names (X, Y), X and Y are
# ALWAYS unioned into the SAME cluster by construction — its two endpoints necessarily co-cluster.
# There is no detected_duplicates row whose two work ids could land in two DIFFERENT clusters of
# the same plan; therefore a detected_duplicates row is still "owned" by exactly one cluster, just
# via an edge argument rather than a single-FK argument. (compose_cluster_merge's own detected_
# duplicates deletion step, further below, is intentionally MORE permissive than this proof
# requires — it deletes any row naming a loser on EITHER side even against a work id outside the
# row's own cluster, e.g. a stale detection — apply's session.get() null-check on an
# already-deleted row from a different cluster's pass makes that safe too, via skipped_stale.)
#
# Since work_ids sets are disjoint across clusters in one plan, a row belonging to work W (or, for
# detected_duplicates, a row whose edge co-clusters with W) can only ever be named by the ONE
# cluster that contains W — never by two different clusters in the same plan. This rules out the
# dedup_backfill 6.3 hazard (a row touched by two independently-computed classes from the same
# snapshot) BY CONSTRUCTION, not by scanning for overlaps after the fact.
#
# The one genuine cross-cluster collision surface the spec calls out is a UNIQUENESS constraint
# spanning rows from DIFFERENT works: uq_suggestions_active is (user_id, work_id) WHERE
# status='Suggested' — repointing a loser's suggestion onto ITS OWN cluster's survivor can collide
# with that survivor's own pre-existing active suggestion (handled in-cluster, below, as
# drop_duplicate_suggestion). That collision is always WITHIN one cluster (the loser and survivor
# are members of the same cluster, by definition of the repoint) — never across two clusters,
# because the colliding row (survivor's own suggestion) only becomes "in play" for whichever
# cluster its OWN work id belongs to. A genuine cross-cluster version of this (same user, active
# suggestion on the survivors of TWO DIFFERENT clusters, where those two survivor works are
# somehow the same book split into two separate clusters) cannot arise: if two works were the same
# book, detection would have paired them and union-find would have merged them into ONE cluster.
# So there is nothing to defer here — the deferred_intersections mechanism dedup_backfill needed
# (two INDEPENDENTLY-classed groups touching the same row) has no analogue in this composition,
# and this section documents that rather than building unused deferral plumbing. If a future
# detection class ever produces overlapping (non-disjoint) clusters, this proof breaks and a
# deferral mechanism would need to be added then — not before.
# --------------------------------------------------------------------------------------------


@dataclass
class WorkMergeComposition:
    """Every op one cluster's merge composition performs, in apply order. Built by
    compose_cluster_merge against CURRENT session state (read-only) — apply_works_merge executes
    exactly what this names, same "apply what was shown" discipline as apply_dedup."""

    cluster: WorksMergeCluster
    survivor_id: UUID
    loser_ids: list[UUID]

    # 1. Editions: whole-edition repoints (no format collision) vs merges (loser edition dropped,
    # its reading_history/edition_narrators repointed onto the survivor's same-format edition).
    repoint_edition_ids: list[UUID] = field(default_factory=list)
    merge_editions: list[EditionMergeGroup] = field(default_factory=list)
    dropped_duplicate_reads: int = 0

    # 2. Suggestions.
    repoint_suggestion_ids: list[UUID] = field(default_factory=list)
    drop_duplicate_suggestion_ids: list[UUID] = field(default_factory=list)

    # 3. Trope/style links: union onto the survivor.
    copy_trope_links: list[tuple[UUID, UUID, float, str | None]] = field(default_factory=list)
    drop_trope_links: list[tuple[UUID, UUID]] = field(default_factory=list)
    copy_style_links: list[tuple[UUID, UUID, str]] = field(default_factory=list)
    drop_style_links: list[tuple[UUID, UUID, str]] = field(default_factory=list)

    # 4. Contributors: union by (author_id, role).
    copy_contributors: list[tuple[UUID, str]] = field(default_factory=list)
    drop_contributors: list[tuple[UUID, UUID, str]] = field(default_factory=list)
    malformed_author_candidates: list[UUID] = field(default_factory=list)

    # 5. detected_duplicates rows referencing ANY loser on either side — deleted before the losers.
    delete_detection_pairs: list[tuple[UUID, UUID]] = field(default_factory=list)

    # 6. Loser Work rows, deleted last.
    delete_work_ids: list[UUID] = field(default_factory=list)


def compose_cluster_merge(session: Session, cluster: WorksMergeCluster) -> WorkMergeComposition:
    """READ ONLY. Composes one cluster's merge — every op needed to fold cluster.work_ids onto
    cluster.survivor_id — against CURRENT session state. See the module-level disjointness proof
    above for why composing clusters independently (no cross-cluster deferral) is safe.

    Ordered exactly per the design spec: editions -> suggestions -> trope/style links ->
    contributors -> detected_duplicates -> Work rows. apply_works_merge executes in this order."""
    survivor_id = cluster.survivor_id
    loser_ids = sorted((wid for wid in cluster.work_ids if wid != survivor_id), key=str)
    comp = WorkMergeComposition(cluster=cluster, survivor_id=survivor_id, loser_ids=loser_ids)

    # --- 1. Editions -------------------------------------------------------------------------
    survivor_editions_by_fmt: dict[str, Edition] = {
        (e.format or ""): e for e in session.query(Edition).filter_by(work_id=survivor_id).order_by(Edition.id).all()
    }
    survivor_dates_by_edition_user: dict[UUID, dict[UUID, set]] = defaultdict(lambda: defaultdict(set))
    for survivor_edition in survivor_editions_by_fmt.values():
        for rh in (
            session.query(ReadingHistory).filter_by(edition_id=survivor_edition.id).order_by(ReadingHistory.id).all()
        ):
            survivor_dates_by_edition_user[survivor_edition.id][rh.user_id].add(rh.date_completed)
    survivor_narrators_by_edition: dict[UUID, set[UUID]] = {}
    for survivor_edition in survivor_editions_by_fmt.values():
        survivor_narrators_by_edition[survivor_edition.id] = {
            row.narrator_id
            for row in session.execute(
                select(edition_narrators.c.narrator_id)
                .where(edition_narrators.c.edition_id == survivor_edition.id)
                .order_by(edition_narrators.c.narrator_id)
            ).all()
        }

    for loser_id in loser_ids:
        for loser_edition in session.query(Edition).filter_by(work_id=loser_id).order_by(Edition.id).all():
            fmt_key = loser_edition.format or ""
            collision = survivor_editions_by_fmt.get(fmt_key)
            if collision is None:
                # No same-format edition on the survivor yet -> whole-edition repoint. Register
                # it as the survivor's edition for this format so a SECOND loser edition of the
                # same format (rare but possible across a 3+-work cluster) merges into THIS one
                # rather than repointing independently, per the module's collision handling.
                comp.repoint_edition_ids.append(loser_edition.id)
                survivor_editions_by_fmt[fmt_key] = loser_edition
                dates_by_user: dict[UUID, set] = defaultdict(set)
                for rh in session.query(ReadingHistory).filter_by(edition_id=loser_edition.id).all():
                    dates_by_user[rh.user_id].add(rh.date_completed)
                survivor_dates_by_edition_user[loser_edition.id] = dates_by_user
                survivor_narrators_by_edition[loser_edition.id] = {
                    row.narrator_id
                    for row in session.execute(
                        select(edition_narrators.c.narrator_id).where(
                            edition_narrators.c.edition_id == loser_edition.id
                        )
                    ).all()
                }
                continue

            # uq_editions_work_format collision: keep the survivor's (or the first-registered)
            # edition, merge the loser edition's reading_history + edition_narrators onto it.
            merge_group = next((mg for mg in comp.merge_editions if mg.survivor_id == collision.id), None)
            if merge_group is None:
                merge_group = EditionMergeGroup(
                    survivor_id=collision.id, work_id=survivor_id, fmt=fmt_key or None, loser_ids=[]
                )
                comp.merge_editions.append(merge_group)
            merge_group.loser_ids.append(loser_edition.id)

            dates_by_user = survivor_dates_by_edition_user[collision.id]
            for rh in (
                session.query(ReadingHistory).filter_by(edition_id=loser_edition.id).order_by(ReadingHistory.id).all()
            ):
                if rh.date_completed in dates_by_user.get(rh.user_id, set()):
                    merge_group.delete_reading_history.append(rh.id)
                    comp.dropped_duplicate_reads += 1
                else:
                    merge_group.repoint_reading_history.append(rh.id)
                    dates_by_user.setdefault(rh.user_id, set()).add(rh.date_completed)

            narrator_ids = survivor_narrators_by_edition.setdefault(collision.id, set())
            for row in session.execute(
                select(edition_narrators.c.narrator_id)
                .where(edition_narrators.c.edition_id == loser_edition.id)
                .order_by(edition_narrators.c.narrator_id)
            ).all():
                nid = row.narrator_id
                if nid in narrator_ids:
                    merge_group.delete_narrators.append((loser_edition.id, nid))
                else:
                    merge_group.repoint_narrators.append((loser_edition.id, nid))
                    narrator_ids.add(nid)

    # --- 2. Suggestions ------------------------------------------------------------------------
    survivor_active_users = {
        s.user_id
        for s in session.query(Suggestions)
        .filter_by(work_id=survivor_id, status="Suggested")
        .order_by(Suggestions.id)
        .all()
    }
    for loser_id in loser_ids:
        for s in (
            session.query(Suggestions).filter_by(work_id=loser_id, status="Suggested").order_by(Suggestions.id).all()
        ):
            if s.user_id in survivor_active_users:
                comp.drop_duplicate_suggestion_ids.append(s.id)
            else:
                comp.repoint_suggestion_ids.append(s.id)
                survivor_active_users.add(s.user_id)
        # Non-'Suggested' (Accepted/Rejected/etc.) suggestions carry no active-uniqueness
        # constraint — always repoint, never drop.
        for s in (
            session.query(Suggestions)
            .filter(Suggestions.work_id == loser_id, Suggestions.status != "Suggested")
            .order_by(Suggestions.id)
            .all()
        ):
            comp.repoint_suggestion_ids.append(s.id)

    # --- 3. Trope/style links: union ------------------------------------------------------------
    survivor_trope_ids = {
        wt.trope_id for wt in session.query(WorkTrope).filter_by(work_id=survivor_id).order_by(WorkTrope.trope_id).all()
    }
    survivor_style_keys = {
        (ws.style_id, ws.attribute_type)
        for ws in session.query(WorkStyle)
        .filter_by(work_id=survivor_id)
        .order_by(WorkStyle.style_id, WorkStyle.attribute_type)
        .all()
    }
    for loser_id in loser_ids:
        for wt in session.query(WorkTrope).filter_by(work_id=loser_id).order_by(WorkTrope.trope_id).all():
            if wt.trope_id in survivor_trope_ids:
                comp.drop_trope_links.append((loser_id, wt.trope_id))
            else:
                comp.copy_trope_links.append((loser_id, wt.trope_id, wt.relevance_score, wt.justification))
                survivor_trope_ids.add(wt.trope_id)
        for ws in (
            session.query(WorkStyle)
            .filter_by(work_id=loser_id)
            .order_by(WorkStyle.style_id, WorkStyle.attribute_type)
            .all()
        ):
            key = (ws.style_id, ws.attribute_type)
            if key in survivor_style_keys:
                comp.drop_style_links.append((loser_id, ws.style_id, ws.attribute_type))
            else:
                comp.copy_style_links.append((loser_id, ws.style_id, ws.attribute_type))
                survivor_style_keys.add(key)

    # --- 4. Contributors: union by (author_id, role) --------------------------------------------
    # Author names joined in one query per work (Gemini review, #144) — the previous
    # per-contributor session.get(Author, ...) was an N+1.
    survivor_contributors = (
        session.query(WorkContributor.author_id, WorkContributor.role, Author.name)
        .join(Author, Author.id == WorkContributor.author_id)
        .filter(WorkContributor.work_id == survivor_id)
        .order_by(WorkContributor.author_id, WorkContributor.role)
        .all()
    )
    survivor_keys = {(author_id, role) for author_id, role, _name in survivor_contributors}
    survivor_names_by_role: dict[str, set[str]] = defaultdict(set)
    for _author_id, role, name in survivor_contributors:
        survivor_names_by_role[role].add(name.strip().casefold())

    for loser_id in loser_ids:
        for author_id, role, name in (
            session.query(WorkContributor.author_id, WorkContributor.role, Author.name)
            .join(Author, Author.id == WorkContributor.author_id)
            .filter(WorkContributor.work_id == loser_id)
            .order_by(WorkContributor.author_id, WorkContributor.role)
            .all()
        ):
            key = (author_id, role)
            if key in survivor_keys:
                comp.drop_contributors.append((loser_id, author_id, role))
                continue
            name_cf = name.strip().casefold()
            if name_cf in survivor_names_by_role.get(role, set()):
                # A DIFFERENT Author row already on the survivor case-folds equal (the #142
                # malformed-comma-joined-author shape) — report, never mutate an Author here.
                comp.malformed_author_candidates.append(author_id)
                continue
            comp.copy_contributors.append((author_id, role))
            survivor_keys.add(key)
            survivor_names_by_role[role].add(name_cf)

    # --- 5. detected_duplicates: delete every row referencing ANY loser on either side. Not
    # restricted to rows where BOTH sides are cluster members: a loser could in principle be
    # named in a detection row against a work id outside this cluster (e.g. a stale detection
    # from before this cluster's shape settled) — the spec is unconditional ("delete ALL rows
    # referencing any loser on EITHER side"), since the FKs fail loud on a dangling reference
    # once the loser Work row is deleted below, and a dangling detection is never valid to keep.
    loser_id_set = set(loser_ids)
    for row in session.query(DetectedDuplicate.work_id_a, DetectedDuplicate.work_id_b).all():
        a, b = row
        if a in loser_id_set or b in loser_id_set:
            comp.delete_detection_pairs.append((a, b))

    # --- 6. Loser Work rows, last -----------------------------------------------------------------
    comp.delete_work_ids = list(loser_ids)

    return comp


def applyable_works_merge_clusters(clusters: WorksMergeClusters) -> list[WorksMergeCluster]:
    """The ONLY clusters compose_cluster_merge/apply_works_merge ever iterate — same_isbn,
    same_identity, detected_duplicates, in that (evidence-strongest-first) order.
    same_isbn_title_mismatch and fuzzy_report_only are NEVER included: this is the single choke
    point that keeps both report-only classes structurally unreachable by apply (see the module
    comment above compose_cluster_merge) — every caller (plan_works_merge_apply,
    apply_works_merge, the token/report functions below) goes through this function rather than
    reading WorksMergeClusters' fields directly, so "never apply report-only" is enforced in
    exactly one place."""
    return [*clusters.same_isbn, *clusters.same_identity, *clusters.detected_duplicates]


def plan_works_merge_apply(session: Session) -> list[WorkMergeComposition]:
    """READ ONLY. Detects clusters fresh (plan_works_merge) and composes each applyable one
    (applyable_works_merge_clusters) against CURRENT session state. This is what both the
    dry-run report and apply_works_merge's fresh re-plan call — always the same function, so
    "what dry-run showed" and "what a fresh re-plan sees" are computed identically."""
    clusters = plan_works_merge(session)
    return [compose_cluster_merge(session, cluster) for cluster in applyable_works_merge_clusters(clusters)]


# --------------------------------------------------------------------------------------------
# Op-tagged tokens (mirrors plan_id_set / fallback_repair.plan_tokens EXACTLY — see plan_id_set's
# docstring for why the op-tag is load-bearing: an operation FLIP on the same id between the
# reviewed report and a fresh apply-time re-plan must show up as a NEW token, not an unchanged
# bare id, so plan_delta's refuse-on-addition catches it.
# --------------------------------------------------------------------------------------------

WORKS_MERGE_OPS = (
    "repoint_edition",
    "merge_edition",
    "repoint_read",
    "drop_duplicate_read",
    "repoint_narrator",
    "drop_narrator",
    "repoint_suggestion",
    "drop_duplicate_suggestion",
    "copy_link",
    "drop_link",
    "copy_contributor",
    "drop_contributor",
    "delete_detection",
    "delete_work",
)


def works_merge_tokens(compositions: list[WorkMergeComposition]) -> set[str]:
    """Every op every composition performs, as one flat set of opaque `<op>:<...>` tokens — one
    set (not per-class, unlike plan_id_set/fallback_repair's PLAN_*_CLASSES) since this gate has
    a single applyable action surface, not several independently-reviewable classes. Composite
    identifiers stringify as `<op>:<tuple-repr>`, same convention as plan_id_set."""
    tokens: set[str] = set()
    for comp in compositions:
        tokens.add(f"merge_cluster:{comp.survivor_id}:{sorted(str(x) for x in comp.loser_ids)}")
        tokens.update(f"repoint_edition:{eid}" for eid in comp.repoint_edition_ids)
        for mg in comp.merge_editions:
            tokens.add(f"merge_edition:{(mg.survivor_id, sorted(str(x) for x in mg.loser_ids))}")
            tokens.update(f"repoint_read:{rh_id}" for rh_id in mg.repoint_reading_history)
            tokens.update(f"drop_duplicate_read:{rh_id}" for rh_id in mg.delete_reading_history)
            tokens.update(f"repoint_narrator:{item}" for item in mg.repoint_narrators)
            tokens.update(f"drop_narrator:{item}" for item in mg.delete_narrators)
        tokens.update(f"repoint_suggestion:{sid}" for sid in comp.repoint_suggestion_ids)
        tokens.update(f"drop_duplicate_suggestion:{sid}" for sid in comp.drop_duplicate_suggestion_ids)
        tokens.update(f"copy_link:trope:{item}" for item in comp.copy_trope_links)
        tokens.update(f"drop_link:trope:{item}" for item in comp.drop_trope_links)
        tokens.update(f"copy_link:style:{item}" for item in comp.copy_style_links)
        tokens.update(f"drop_link:style:{item}" for item in comp.drop_style_links)
        tokens.update(f"copy_contributor:{(comp.survivor_id, item)}" for item in comp.copy_contributors)
        tokens.update(f"drop_contributor:{item}" for item in comp.drop_contributors)
        tokens.update(f"delete_detection:{item}" for item in comp.delete_detection_pairs)
        tokens.update(f"delete_work:{wid}" for wid in comp.delete_work_ids)
    return tokens


def works_merge_delta(reviewed: set[str], fresh_compositions: list[WorkMergeComposition]) -> set[str]:
    """Tokens present in the FRESH compositions but NOT in the REVIEWED token set. Empty means
    fresh is a subset of reviewed — safe to apply. Mirrors plan_delta/fallback_repair.plan_delta,
    single flat set instead of per-class since works_merge_tokens is single-set (see its
    docstring)."""
    return works_merge_tokens(fresh_compositions) - reviewed


# --------------------------------------------------------------------------------------------
# Report: human-readable cluster sections (reuses render_works_merge_report's per-cluster
# rendering) + the machine-readable token block + fail-closed END-marker parser. Mirrors
# fallback_repair.write_report/parse_report EXACTLY.
# --------------------------------------------------------------------------------------------


def render_works_merge_apply_report(
    clusters: WorksMergeClusters, compositions: list[WorkMergeComposition], *, db_target: str | None = None
) -> str:
    """Human-readable cluster sections (H1's render_works_merge_report) PLUS a per-composition
    op summary PLUS the '== PLAN TOKENS ==' machine-readable block apply_works_merge's drift
    gate cross-checks a fresh re-plan against. fuzzy_report_only is rendered same as before
    (never-applied marker) — it is NEVER part of `compositions` (see
    applyable_works_merge_clusters), so it never appears in the token block."""
    lines: list[str] = [render_works_merge_report(clusters, db_target=db_target), ""]

    lines.append("=== apply composition (per applyable cluster) ===")
    for comp in compositions:
        lines.append(f"  cluster survivor={comp.survivor_id}  losers={comp.loser_ids}")
        lines.append(
            f"    repoint_editions={comp.repoint_edition_ids}  "
            f"merge_editions={len(comp.merge_editions)}  dropped_duplicate_reads={comp.dropped_duplicate_reads}"
        )
        lines.append(
            f"    repoint_suggestions={comp.repoint_suggestion_ids}  "
            f"drop_duplicate_suggestions={comp.drop_duplicate_suggestion_ids}"
        )
        lines.append(
            f"    copy_trope_links={len(comp.copy_trope_links)}  drop_trope_links={len(comp.drop_trope_links)}  "
            f"copy_style_links={len(comp.copy_style_links)}  drop_style_links={len(comp.drop_style_links)}"
        )
        lines.append(f"    copy_contributors={comp.copy_contributors}  drop_contributors={comp.drop_contributors}")
        if comp.malformed_author_candidates:
            lines.append(
                f"    malformed_author_candidates={comp.malformed_author_candidates}  "
                "(report-only — no author mutation)"
            )
        lines.append(f"    delete_detection_pairs={comp.delete_detection_pairs}")
        lines.append(f"    delete_work_ids={comp.delete_work_ids}")
    lines.append("")

    lines.append("== PLAN TOKENS ==")
    tokens = sorted(works_merge_tokens(compositions))
    lines.append(f"[works_merge] {len(tokens)}")
    lines.extend(tokens)
    lines.append("== END PLAN TOKENS ==")
    lines.append("")

    return "\n".join(lines)


def write_works_merge_apply_report(
    clusters: WorksMergeClusters,
    compositions: list[WorkMergeComposition],
    reports_dir: Path | None = None,
    db_target: str | None = None,
) -> Path:
    """Persists render_works_merge_apply_report to data/reports/works-merge-<UTC>.txt —
    SAME filename prefix as H1's planning-only report (_write_works_merge_report in
    scripts/clean_catalog.py), since this supersedes it as the operator-facing artifact once
    apply exists; microsecond timestamp keeps a dry-run's write and an apply's fresh-plan write
    from colliding on the same path (mirrors _write_dedup_report's collision-avoidance
    reasoning)."""
    reports_dir = reports_dir or Path("data/reports")
    reports_dir.mkdir(parents=True, exist_ok=True)
    now = datetime.now(UTC)
    ts = now.strftime("%Y%m%dT%H%M%S") + f"{now.microsecond:06d}Z"
    path = reports_dir / f"works-merge-{ts}.txt"
    path.write_text(render_works_merge_apply_report(clusters, compositions, db_target=db_target), encoding="utf-8")
    return path


def parse_works_merge_report(report_text: str) -> set[str]:
    """Parse the '== PLAN TOKENS ==' block back into the flat token set works_merge_tokens
    produces. Fail-closed (mirrors fallback_repair.parse_report EXACTLY): raises ValueError if
    the start marker is missing, if the END marker is missing (truncated report), or if a
    class-header-shaped line (`[...]`) doesn't actually parse as one."""
    lines = report_text.splitlines()
    try:
        start = lines.index("== PLAN TOKENS ==")
    except ValueError as exc:
        raise ValueError(
            "report has no '== PLAN TOKENS ==' section — not a works-merge apply report, or an old-format one"
        ) from exc

    try:
        end = lines.index("== END PLAN TOKENS ==", start + 1)
    except ValueError as exc:
        raise ValueError("report has no '== END PLAN TOKENS ==' terminator — truncated or corrupt report") from exc

    out: set[str] = set()
    for line in lines[start + 1 : end]:
        if line.startswith("["):
            if "]" not in line:
                raise ValueError(f"malformed class-header line in PLAN TOKENS block: {line!r}")
            continue
        if line:
            out.add(line)
    return out


# --------------------------------------------------------------------------------------------
# Apply — THE USER GATE (mirrors fallback_repair.apply_fallback_repair / apply_dedup exactly)
# --------------------------------------------------------------------------------------------


class WorksMergeDriftError(ValueError):
    """Raised instead of a bare ValueError when apply_works_merge's fresh re-plan drifted from
    the reviewed report — mirrors FallbackRepairDriftError exactly. Carries the offending delta
    TOKENS (not just a count) and the path of a fresh report written from the drifted
    compositions, ready for immediate re-review."""

    def __init__(self, delta: set[str], fresh_report_path: Path):
        self.delta = delta
        self.fresh_report_path = fresh_report_path
        super().__init__(
            f"REFUSING apply_works_merge: fresh plan drifted from the reviewed report (+{len(delta)} new "
            f"token(s)). A fresh report was written to {fresh_report_path} — re-review it before re-applying."
        )


def apply_works_merge(session: Session, reviewed_report_path: Path) -> dict[str, int]:
    """--merge-works-apply is a SEPARATE invocation from the reviewed --merge-works dry-run.
    Re-plans FRESH (plan_works_merge_apply — same function the dry-run report was built from)
    and REFUSES (raises WorksMergeDriftError, no partial writes — nothing is flushed before the
    drift check completes) if the fresh token set contains ANY token absent from the reviewed
    set — including an operation flip on the same id (op-tagged tokens, see works_merge_tokens).
    fresh ⊆ reviewed is fine (skipped_stale territory, reported not refused).

    Executes ALL applyable clusters' compositions in ONE transaction, in per-cluster order
    (editions -> suggestions -> links -> contributors -> detected_duplicates -> Work deletes),
    matching the design spec's ordered steps. Every row lookup re-verifies against the session
    at apply time (session.get) and anything vanished since the fresh plan was composed a moment
    ago is counted under skipped_stale rather than raising — the ordinary "apply what was shown,
    tolerate the sub-second race" discipline every other gated tool in this module follows.

    fuzzy_report_only clusters are never in `fresh_compositions` at all (applyable_works_merge_
    clusters) — the structural exclusion holds through this gate by construction, not by a
    runtime check here."""
    reviewed_tokens = parse_works_merge_report(Path(reviewed_report_path).read_text(encoding="utf-8"))

    fresh_clusters = plan_works_merge(session)
    fresh_compositions = [
        compose_cluster_merge(session, cluster) for cluster in applyable_works_merge_clusters(fresh_clusters)
    ]
    fresh_tokens = works_merge_tokens(fresh_compositions)
    delta = fresh_tokens - reviewed_tokens
    if delta:
        fresh_report_path = write_works_merge_apply_report(fresh_clusters, fresh_compositions)
        raise WorksMergeDriftError(delta, fresh_report_path)

    shrinkage = len(reviewed_tokens - fresh_tokens)

    # Orphan-author pointer (deliverable 1.6): every author id linked to ANY loser Work in this
    # apply, gathered NOW — before any loser Work row (and its cascaded WorkContributor rows)
    # gets deleted below. Re-checked AFTER the whole apply completes using the SAME structural
    # predicate _plan_orphan_authors uses (zero work_contributors AND zero author_styles), scoped
    # to just this apply's own candidates rather than a full-catalog scan — this is a POINTER for
    # the operator, not a delete: actual removal stays the existing --dedup-for-constraints
    # dry-run/apply loop's job (never mutate an Author here).
    candidate_author_ids: set[UUID] = set()
    for comp in fresh_compositions:
        if not comp.loser_ids:
            continue
        candidate_author_ids.update(
            row.author_id
            for row in session.query(WorkContributor.author_id)
            .filter(WorkContributor.work_id.in_(comp.loser_ids))
            .all()
        )

    result = {
        "repoint_edition": 0,
        "merge_edition": 0,
        "repoint_read": 0,
        "drop_duplicate_read": 0,
        "repoint_narrator": 0,
        "drop_narrator": 0,
        "repoint_suggestion": 0,
        "drop_duplicate_suggestion": 0,
        "copy_link": 0,
        "drop_link": 0,
        "copy_contributor": 0,
        "drop_contributor": 0,
        "delete_detection": 0,
        "delete_work": 0,
        "skipped_stale": shrinkage,
        "orphaned_authors_pointer": 0,
    }

    for comp in fresh_compositions:
        if session.get(Work, comp.survivor_id) is None:
            result["skipped_stale"] += 1
            continue

        # 1a. Whole-edition repoints.
        for eid in comp.repoint_edition_ids:
            e = session.get(Edition, eid)
            if e is None:
                result["skipped_stale"] += 1
                continue
            e.work_id = comp.survivor_id
            result["repoint_edition"] += 1
        session.flush()

        # 1b. Edition merges (format collision).
        for mg in comp.merge_editions:
            if session.get(Edition, mg.survivor_id) is None:
                result["skipped_stale"] += 1
                continue
            result["merge_edition"] += 1
            for rh_id in mg.repoint_reading_history:
                rh = session.get(ReadingHistory, rh_id)
                if rh is None:
                    result["skipped_stale"] += 1
                    continue
                rh.edition_id = mg.survivor_id
                result["repoint_read"] += 1
            for rh_id in mg.delete_reading_history:
                rh = session.get(ReadingHistory, rh_id)
                if rh is None:
                    result["skipped_stale"] += 1
                    continue
                session.delete(rh)
                result["drop_duplicate_read"] += 1
            session.flush()

            for edition_id, narrator_id in mg.repoint_narrators:
                exists = session.execute(
                    select(edition_narrators.c.edition_id).where(
                        edition_narrators.c.edition_id == edition_id, edition_narrators.c.narrator_id == narrator_id
                    )
                ).first()
                if exists is None:
                    result["skipped_stale"] += 1
                    continue
                session.execute(
                    delete(edition_narrators).where(
                        edition_narrators.c.edition_id == edition_id, edition_narrators.c.narrator_id == narrator_id
                    )
                )
                session.execute(edition_narrators.insert().values(edition_id=mg.survivor_id, narrator_id=narrator_id))
                result["repoint_narrator"] += 1
            for edition_id, narrator_id in mg.delete_narrators:
                session.execute(
                    delete(edition_narrators).where(
                        edition_narrators.c.edition_id == edition_id, edition_narrators.c.narrator_id == narrator_id
                    )
                )
                result["drop_narrator"] += 1
            session.flush()

            for loser_edition_id in mg.loser_ids:
                le = session.get(Edition, loser_edition_id)
                if le is None:
                    result["skipped_stale"] += 1
                    continue
                # Belt-and-braces (mirrors _apply_edition_group): refuse if an unplanned
                # edition_narrators row still hangs off this loser edition — deleting it would
                # cascade that unaccounted-for row away. Symmetric guard (Minor 6, final review)
                # for reading_history: unlike edition_narrators (a plain `secondary=` link table
                # that would silently cascade), ReadingHistory.edition_id has NO cascade at all —
                # an unplanned row here would make the delete raise an IntegrityError instead of
                # silently losing anything, but "refuse and count skipped_stale" is still the
                # right behavior (this composition already accounted for every row it saw at
                # compose time; an unplanned survivor here means something changed since).
                unplanned_narrators = session.execute(
                    select(edition_narrators.c.narrator_id).where(edition_narrators.c.edition_id == loser_edition_id)
                ).first()
                unplanned_reading_history = session.query(ReadingHistory).filter_by(edition_id=loser_edition_id).first()
                if unplanned_narrators is not None or unplanned_reading_history is not None:
                    result["skipped_stale"] += 1
                    continue
                session.delete(le)
            session.flush()

        # 2. Suggestions.
        for sid in comp.repoint_suggestion_ids:
            s = session.get(Suggestions, sid)
            if s is None:
                result["skipped_stale"] += 1
                continue
            s.work_id = comp.survivor_id
            result["repoint_suggestion"] += 1
        for sid in comp.drop_duplicate_suggestion_ids:
            s = session.get(Suggestions, sid)
            if s is None:
                result["skipped_stale"] += 1
                continue
            session.delete(s)
            result["drop_duplicate_suggestion"] += 1
        session.flush()

        # 3. Trope/style link union.
        for loser_work_id, trope_id, relevance, justification in comp.copy_trope_links:
            existing = session.get(WorkTrope, {"work_id": comp.survivor_id, "trope_id": trope_id})
            source = session.get(WorkTrope, {"work_id": loser_work_id, "trope_id": trope_id})
            if source is None:
                result["skipped_stale"] += 1
                continue
            if existing is None:
                session.add(
                    WorkTrope(
                        work_id=comp.survivor_id,
                        trope_id=trope_id,
                        relevance_score=relevance,
                        justification=justification,
                    )
                )
                result["copy_link"] += 1
            session.delete(source)
        for loser_work_id, trope_id in comp.drop_trope_links:
            wt = session.get(WorkTrope, {"work_id": loser_work_id, "trope_id": trope_id})
            if wt is None:
                result["skipped_stale"] += 1
                continue
            session.delete(wt)
            result["drop_link"] += 1
        session.flush()

        for loser_work_id, style_id, attribute_type in comp.copy_style_links:
            pk = {"work_id": comp.survivor_id, "style_id": style_id, "attribute_type": attribute_type}
            existing = session.get(WorkStyle, pk)
            source_pk = {"work_id": loser_work_id, "style_id": style_id, "attribute_type": attribute_type}
            source = session.get(WorkStyle, source_pk)
            if source is None:
                result["skipped_stale"] += 1
                continue
            if existing is None:
                session.add(WorkStyle(work_id=comp.survivor_id, style_id=style_id, attribute_type=attribute_type))
                result["copy_link"] += 1
            session.delete(source)
        for loser_work_id, style_id, attribute_type in comp.drop_style_links:
            ws = session.get(
                WorkStyle, {"work_id": loser_work_id, "style_id": style_id, "attribute_type": attribute_type}
            )
            if ws is None:
                result["skipped_stale"] += 1
                continue
            session.delete(ws)
            result["drop_link"] += 1
        session.flush()

        # 4. Contributors: union by (author_id, role).
        for author_id, role in comp.copy_contributors:
            pk = {"work_id": comp.survivor_id, "author_id": author_id, "role": role}
            if session.get(WorkContributor, pk) is not None:
                result["skipped_stale"] += 1
                continue
            session.add(WorkContributor(work_id=comp.survivor_id, author_id=author_id, role=role))
            result["copy_contributor"] += 1
        for loser_work_id, author_id, role in comp.drop_contributors:
            wc = session.get(WorkContributor, {"work_id": loser_work_id, "author_id": author_id, "role": role})
            if wc is None:
                result["skipped_stale"] += 1
                continue
            session.delete(wc)
            result["drop_contributor"] += 1
        session.flush()

        # (Loser WorkContributor rows not explicitly copied/dropped above — i.e. any row this
        # composition didn't already account for — are deleted via Work's own
        # cascade="all, delete-orphan" relationship when the loser Work row is deleted, below.
        # copy_contributors + drop_contributors together are exhaustive over the loser's
        # contributor rows AT COMPOSE TIME (compose_cluster_merge iterates every row), so this is
        # belt-and-braces for a row added between compose and this point, not an expected path.)

        # 5. detected_duplicates: deleted BEFORE the Work rows they reference (FKs fail loud
        # otherwise — see the module comment above compose_cluster_merge).
        for a, b in comp.delete_detection_pairs:
            row = session.get(DetectedDuplicate, {"work_id_a": a, "work_id_b": b})
            if row is None:
                result["skipped_stale"] += 1
                continue
            session.delete(row)
            result["delete_detection"] += 1
        session.flush()

        # 6. Loser Work rows, last.
        for wid in comp.delete_work_ids:
            w = session.get(Work, wid)
            if w is None:
                result["skipped_stale"] += 1
                continue
            session.delete(w)
            result["delete_work"] += 1
        session.flush()

    # Re-check the candidates gathered before the loop: an author now orphaned by THIS apply
    # (its only contributor links lived on losers this apply just deleted) — same predicate as
    # _plan_orphan_authors, applied to the narrower candidate set so the pointer is precise about
    # what THIS merge did, not the whole catalog's pre-existing orphan backlog.
    for author_id in candidate_author_ids:
        has_wc = session.query(WorkContributor).filter_by(author_id=author_id).first() is not None
        has_as = session.query(AuthorStyle).filter_by(author_id=author_id).first() is not None
        if not has_wc and not has_as:
            result["orphaned_authors_pointer"] += 1

    return result


# --------------------------------------------------------------------------------------------
# --promote-pair (H4): operator promotion of a hand-picked duplicate pair into the
# detected_duplicates feed. This is the ONLY way a works_fuzzy_report_only pair (or any pair the
# operator spots by eye, never surfaced by any detection class) becomes applyable — by writing a
# source='operator' row into the SAME feed the #141/#143 deep-pass redirect writes into, so
# plan_works_merge's existing works_detected_duplicates class picks it up on the next
# --merge-works dry-run with zero new planner logic. Mirrors two_phase.enrich_deep's ON CONFLICT
# DO NOTHING insert shape exactly (see test_detected_duplicates_upsert.py) — a re-run of the same
# ordered pair is a no-op, not a duplicate row.
# --------------------------------------------------------------------------------------------


class UnknownWorkIdsError(ValueError):
    """Raised by promote_detected_duplicate_pair when one or both work ids don't exist."""

    def __init__(self, missing_ids: list[UUID]):
        self.missing_ids = missing_ids
        joined = ", ".join(str(i) for i in missing_ids)
        super().__init__(f"unknown work id(s): {joined}")


@dataclass
class PromoteDuplicatePairResult:
    work_id_a: UUID
    work_id_b: UUID
    title_a: str
    title_b: str
    already_existed: bool  # True when the (work_id_a, work_id_b) row was already present


def promote_detected_duplicate_pair(session: Session, work_id_a: UUID, work_id_b: UUID) -> PromoteDuplicatePairResult:
    """The CLI helper function scripts/clean_catalog.py's --promote-pair mode calls per pair
    (after UUID parsing, which is pure string handling done by the caller before any session is
    open). Owns every DB-touching guard: rejects a self-pair (a work is never its own duplicate —
    the SAME structural rule _dedupe_detected_duplicate_rows already enforces on the read side),
    verifies BOTH work ids exist (raises UnknownWorkIdsError naming exactly which one(s) don't —
    never a bare 'not found'), then inserts a source='operator' detected_duplicates row with the
    same ON CONFLICT DO NOTHING shape two_phase.enrich_deep's redirect insert uses (conflict
    target = the composite PK), so re-running the exact same ordered pair is idempotent — no
    second row, no error. already_existed is determined by a pre-insert session.get check (not by
    trusting driver-specific rowcount semantics), so it's stable across DB-API drivers."""
    if work_id_a == work_id_b:
        raise ValueError(f"cannot promote {work_id_a} as a duplicate of itself")

    found = dict(session.query(Work.id, Work.title).filter(Work.id.in_([work_id_a, work_id_b])).all())
    missing = [wid for wid in (work_id_a, work_id_b) if wid not in found]
    if missing:
        raise UnknownWorkIdsError(missing)

    already_existed = session.get(DetectedDuplicate, {"work_id_a": work_id_a, "work_id_b": work_id_b}) is not None
    session.execute(
        pg_insert(DetectedDuplicate)
        .values(work_id_a=work_id_a, work_id_b=work_id_b, source="operator")
        .on_conflict_do_nothing(index_elements=["work_id_a", "work_id_b"])
    )
    session.flush()

    return PromoteDuplicatePairResult(
        work_id_a=work_id_a,
        work_id_b=work_id_b,
        title_a=found[work_id_a],
        title_b=found[work_id_b],
        already_existed=already_existed,
    )
