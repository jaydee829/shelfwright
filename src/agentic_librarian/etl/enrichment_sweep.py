"""Requeue-planner for the deep-enrichment poison-task backstop (GH #97).

Two Cloud Tasks failure modes can leave a Work permanently under-enriched:
  1. It was never queued at all (e.g. a fast-add whose enqueue_enrichment call silently
     no-op'd because Cloud Tasks env wasn't configured, or failed — the 6.2b deferral).
  2. Its deep pass exhausted Cloud Tasks retries while still "empty" (api/internal.py's
     503 path, GH #97) — a poison task that never lands a real trope.

plan_requeue(session) surfaces both classes so an operator can re-enqueue them via
scripts/clean_catalog.py --requeue-unenriched. Read-only; session in, list out — the thin
CLI (scripts/clean_catalog.py) does the printing/gating/enqueue, per the tag_backfill /
trope_backfill house pattern. Join pattern for trope links borrowed from
etl/trope_backfill.py's plan_fallback_prune.

GH #141: a work appearing on EITHER side of detected_duplicates needs the works-merge tool,
not another paid deep pass — it is excluded from both enrichable classes above and reported
separately as "pending_merge". This query assumes the detected_duplicates migration is
already applied — see the migration's own docstring for why rule 11 pressure is nil here
(this module only ADDS a table; it alters nothing this branch's other queries touch)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import UUID

from sqlalchemy.orm import Session

from agentic_librarian.db.models import DetectedDuplicate, Trope, Work, WorkTrope
from agentic_librarian.etl.trope_predicate import is_fallback_trope_name

RequeueReason = Literal["never_deep_enriched", "no_real_trope", "pending_merge"]


@dataclass
class RequeueCandidate:
    work_id: UUID
    title: str
    reason: RequeueReason
    # Adversarial-pass finding (#95 #97 review): surfaced alongside each candidate so an
    # operator re-reviewing a REPEAT sweep can see "already re-attempted after X" for a
    # no_real_trope entry that keeps coming back — see plan_requeue's docstring and the
    # runbook's step 6 repeat-cost warning. None for never_deep_enriched (that's the whole
    # point of the class); set for no_real_trope (the prior stamp that didn't help).
    deep_enriched_at: datetime | None = None


def plan_requeue(session: Session) -> list[RequeueCandidate]:
    """Works that need their deep-enrichment pass (re)queued, or need the works-merge tool
    instead:

      - "pending_merge": the work appears on either side of detected_duplicates (GH #141) —
        it needs a merge, not another paid deep pass. Checked FIRST and wins over the other
        two reasons regardless of the work's own deep_enriched_at/trope state, so a
        redirected invoked row (stamped, zero real tropes of its own) is never re-enqueued.
      - "never_deep_enriched": deep_enriched_at IS NULL — no deep pass has ever completed
        (including a confirmed-empty one) for this work.
      - "no_real_trope": deep_enriched_at IS SET, but every trope link this work has is
        fallback/junk per the shared #111 predicate (or it has zero trope links at all) —
        a poison task that exhausted Cloud Tasks retries without ever landing a genuine
        narrative trope.

    A work matching both never_deep_enriched and no_real_trope is impossible (no_real_trope
    requires deep_enriched_at to be set), but if logic ever changes, never_deep_enriched wins
    and the work appears exactly once. Read-only."""
    rows = session.query(WorkTrope.work_id, Trope.name).join(Trope, Trope.id == WorkTrope.trope_id).all()
    trope_names_by_work: dict[UUID, list[str]] = {}
    for work_id, name in rows:
        trope_names_by_work.setdefault(work_id, []).append(name)

    pending_merge_ids: set[UUID] = set()
    for a_id, b_id in session.query(DetectedDuplicate.work_id_a, DetectedDuplicate.work_id_b).all():
        pending_merge_ids.add(a_id)
        pending_merge_ids.add(b_id)

    out: list[RequeueCandidate] = []
    for work in session.query(Work).all():
        if work.id in pending_merge_ids:
            out.append(RequeueCandidate(work.id, work.title, "pending_merge"))
            continue
        if work.deep_enriched_at is None:
            out.append(RequeueCandidate(work.id, work.title, "never_deep_enriched"))
            continue
        names = trope_names_by_work.get(work.id, [])
        has_real = any(is_fallback_trope_name(n, work.genres, work.moods) is False for n in names)
        if not has_real:
            out.append(RequeueCandidate(work.id, work.title, "no_real_trope", deep_enriched_at=work.deep_enriched_at))
    return out
