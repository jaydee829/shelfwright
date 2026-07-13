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
