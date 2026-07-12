"""Backfill logic for trope-name cleaning (Spec 2026-06-23): clean Trope.name with the genre/mood
pipeline, migrating work_tropes links and re-embedding materially-changed names. Session in,
summary out; the CLI is scripts/clean_catalog.py. Sibling of etl/tag_backfill.py."""

from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass, field
from uuid import UUID

from sqlalchemy import tuple_
from sqlalchemy.orm import Session

from agentic_librarian.db.models import Trope, Work, WorkTrope
from agentic_librarian.etl.tag_cleaning import _normalize, clean_trope_name
from agentic_librarian.etl.trope_predicate import is_fallback_trope_name

logger = logging.getLogger(__name__)


@dataclass
class TropeChange:
    trope_id: UUID
    name_before: str
    names_after: list[str]  # [] dropped, [x] rename, [x, y, …] split/canonicalised
    works_affected: int
    new_names: list[str] = field(default_factory=list)  # canonicals with no Trope row yet (need embedding)


def _is_cosmetic(before: str, after: list[str]) -> bool:
    """Single result whose only change is case/whitespace/hyphen formatting — embedding unchanged."""
    return len(after) == 1 and _normalize(after[0]) == _normalize(before)


def _safe_embed(trope_manager, name: str):
    if trope_manager is None:
        return None
    try:
        return trope_manager._get_embedding(name)
    except Exception:  # noqa: BLE001 - embedding failure degrades to a null-vector row, never aborts
        logger.warning("embedding failed for trope %r; creating with null vector", name, exc_info=True)
        return None


def plan_trope_changes(session: Session) -> list[TropeChange]:
    existing = {t.name for t in session.query(Trope).all()}
    out: list[TropeChange] = []
    for t in session.query(Trope).all():
        cleaned = clean_trope_name(t.name)
        if cleaned == [t.name]:
            continue
        works = session.query(WorkTrope).filter_by(trope_id=t.id).count()
        new = [] if _is_cosmetic(t.name, cleaned) else [c for c in cleaned if c not in existing]
        out.append(TropeChange(t.id, t.name, cleaned, works, new))
    return out


def embedding_call_estimate(session: Session) -> int:
    """Distinct brand-new canonical names a --apply would embed."""
    names: set[str] = set()
    for c in plan_trope_changes(session):
        names.update(c.new_names)
    return len(names)


def _fold_score(a: float | None, b: float | None) -> float:
    """Higher of two relevance scores, ignoring None. relevance_score is nullable=False with a
    1.0 default, so a None (e.g. an unflushed fallback WorkTrope) folds to the surviving value or
    the column default — never None (which would break the NOT NULL constraint)."""
    scores = [s for s in (a, b) if s is not None]
    return max(scores) if scores else 1.0


def _move_links(session: Session, src: Trope, dst: Trope) -> None:
    """Re-point every work_tropes(src) onto dst, folding score/justification on PK collision."""
    if src.id == dst.id:
        return
    for wt in session.query(WorkTrope).filter_by(trope_id=src.id).all():
        target = session.query(WorkTrope).filter_by(work_id=wt.work_id, trope_id=dst.id).first()
        if target is not None:
            target.relevance_score = _fold_score(target.relevance_score, wt.relevance_score)
            target.justification = target.justification or wt.justification
            session.delete(wt)
        else:
            wt.trope_id = dst.id  # re-point the PK in place (no collision)
    session.flush()


def _delete_trope(session: Session, t: Trope) -> None:
    # Delete links one-by-one (not a bulk query.delete) so the ORM session/identity map stays
    # consistent — a bulk delete would leave stale WorkTrope instances (e.g. the src_links snapshot).
    for wt in session.query(WorkTrope).filter_by(trope_id=t.id).all():
        session.delete(wt)
    session.flush()
    session.delete(t)
    session.flush()


def _get_or_create_trope(session: Session, trope_manager, name: str) -> Trope:
    t = session.query(Trope).filter_by(name=name).first()
    if t is not None:
        return t
    t = Trope(name=name, embedding=_safe_embed(trope_manager, name))
    session.add(t)
    session.flush()
    return t


def apply_trope_changes(session: Session, trope_manager=None, changes: list[TropeChange] | None = None) -> int:
    """Apply (or compute) the trope changes. trope_manager supplies embeddings for brand-new
    canonical names; pass None to create them with a null vector (re-embed later)."""
    if changes is None:
        changes = plan_trope_changes(session)
    n = 0
    for c in changes:
        src = session.get(Trope, c.trope_id)
        if src is None:  # already cleaned/deleted
            continue
        if not c.names_after:  # pure junk
            _delete_trope(session, src)
            n += 1
            continue
        if _is_cosmetic(c.name_before, c.names_after):
            new = c.names_after[0]
            clash = session.query(Trope).filter(Trope.name == new, Trope.id != src.id).first()
            if clash is not None:
                _move_links(session, src, clash)
                _delete_trope(session, src)
            else:
                src.name = new  # keep embedding
            n += 1
            continue
        # material: split/canonicalise. Snapshot src's links FIRST, then fan them out to every
        # canonical — otherwise the first canonical would consume them and the rest get none.
        src_links = session.query(WorkTrope).filter_by(trope_id=src.id).all()
        for name in c.names_after:
            dst = _get_or_create_trope(session, trope_manager, name)
            if dst.id == src.id:
                continue
            for wt in src_links:
                target = session.query(WorkTrope).filter_by(work_id=wt.work_id, trope_id=dst.id).first()
                if target is not None:
                    target.relevance_score = _fold_score(target.relevance_score, wt.relevance_score)
                    target.justification = target.justification or wt.justification
                else:
                    session.add(
                        WorkTrope(
                            work_id=wt.work_id,
                            trope_id=dst.id,
                            relevance_score=wt.relevance_score,
                            justification=wt.justification,
                        )
                    )
            session.flush()
        _delete_trope(session, src)  # removes src + its now-superseded work_tropes
        n += 1
    return n


def trope_inventory(session: Session) -> tuple[Counter, list]:
    counts: Counter = Counter()
    dirty: list = []
    for t in session.query(Trope).all():
        wc = session.query(WorkTrope).filter_by(trope_id=t.id).count()
        counts[t.name] = wc
        cleaned = clean_trope_name(t.name)
        if cleaned != [t.name]:
            dirty.append((t.name, cleaned, wc))
    return counts, dirty


@dataclass
class FallbackPrune:
    work_id: UUID
    title: str
    deleted: list[str]  # genre/mood-fallback trope names removed from this work
    deleted_trope_ids: list[UUID]  # the WorkTrope.trope_id values to delete (this work)
    real_kept: int  # genuine narrative tropes retained


def plan_fallback_prune(session: Session) -> list[FallbackPrune]:
    """A genre/mood "fallback" trope is one whose CLEANED name IS (a subset of) the work's own
    cleaned genres+moods — i.e. the two-phase fast pass re-encoded one of the work's genres/moods as
    a trope. Prune those, but ONLY on works that keep >=1 genuine narrative trope (one that cleans to
    something NOT in the work's genres/moods, e.g. 'The Dark Night of the Soul'); junk-named tropes
    (clean -> []) are left for the --tropes pass to drop.

    The justification column is deliberately NOT used: it is unreliable — many real scout tropes have
    NULL justification (semantic-collapse "attractor" tropes shared across books), so it conflates
    real tropes with fallbacks. We match by name/genre membership instead, via the shared
    is_fallback_trope_name predicate (GH #111). Read-only."""
    rows = (
        session.query(WorkTrope.work_id, WorkTrope.trope_id, Trope.name, Work.title, Work.genres, Work.moods)
        .join(Trope, Trope.id == WorkTrope.trope_id)
        .join(Work, Work.id == WorkTrope.work_id)
        .all()
    )
    by_work: dict[UUID, dict] = {}
    for work_id, trope_id, name, title, genres, moods in rows:
        w = by_work.setdefault(work_id, {"title": title, "genres": genres, "moods": moods, "links": []})
        w["links"].append((trope_id, name))
    out: list[FallbackPrune] = []
    for work_id, w in by_work.items():
        fallback: list[tuple[UUID, str]] = []
        real = 0
        for trope_id, name in w["links"]:
            verdict = is_fallback_trope_name(name, w["genres"], w["moods"])
            if verdict is True:  # the trope is (a clean of) one of this work's genres/moods
                fallback.append((trope_id, name))
            elif verdict is False:  # cleans to something outside genres/moods -> a genuine narrative trope
                real += 1
            # else: verdict is None (junk) -> leave it; the --tropes pass deletes junk-named tropes
        if fallback and real:  # never strip a work below its genuine tropes
            out.append(FallbackPrune(work_id, w["title"], [n for _, n in fallback], [tid for tid, _ in fallback], real))
    return out


def apply_fallback_prune(session: Session, changes: list[FallbackPrune] | None = None) -> int:
    """Delete the identified genre/mood-fallback links in one batch. Link deletion only — no Trope
    rows, no embeddings. Idempotent (a second run finds nothing left to prune)."""
    if changes is None:
        changes = plan_fallback_prune(session)
    pairs = [(c.work_id, tid) for c in changes for tid in c.deleted_trope_ids]
    if not pairs:
        return 0
    links = session.query(WorkTrope).filter(tuple_(WorkTrope.work_id, WorkTrope.trope_id).in_(pairs)).all()
    for wt in links:
        session.delete(wt)
    session.flush()
    return len(links)


def fallback_prune_inventory(session: Session) -> tuple[int, int]:
    """(polluted works, total fallback links that would be pruned)."""
    plan = plan_fallback_prune(session)
    return len(plan), sum(len(c.deleted) for c in plan)
