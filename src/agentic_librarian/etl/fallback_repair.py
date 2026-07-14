"""Gated fallback-pollution repair backfill (PR-D part 2, GH #70). Deletes prod's inherited
damage from the OLD fallback writer, which called `standardize_trope`'s 0.85-cosine semantic
match for genre/mood slug tags (e.g. mood tag "Dark" landing on the real trope "The Dark Night
of the Soul" — 91 of 105 links on that one attractor). D1 (`TropeManager.get_or_create_fallback_
trope`, scouts/trope_manager.py) fixed the writer going forward; this module repairs what
already landed on prod.

THE DISTINGUISHER (binding, read before touching this file): a link is bogus if it is
NULL-justified AND its trope is a deterministic RECOMPUTE of what the OLD fallback writer would
have produced from THIS WORK's genres/moods — i.e. `bogus_targets(work)` walks
`clean_trope_name(tag)` for every tag in the work's genres∪moods, and for each cleaned name that
does NOT already exist as an exact-name Trope, finds the nearest trope by cosine distance and
marks it bogus if the distance is <= the redirect threshold `standardize_trope` used (0.15,
i.e. 0.85 similarity — see BOGUS_MATCH_THRESHOLD below, which mirrors scouts/trope_manager.py's
default `threshold=0.85` by DELIBERATE COUPLING, not coincidence: this module's whole point is
to find what THAT redirect would have produced). This is a STRUCTURAL distinguisher (the #69
lesson, memory verify-backfill-distinguisher) — NOT `justification IS NULL` alone. Justification
is unreliable on its own (many real scout tropes are NULL-justified "semantic collapse"
attractors, per trope_predicate.py's docstring); it is used here only as a NECESSARY co-condition
(a justified link is NEVER touched, full stop) alongside the structural recompute.

RESIDUAL (name it honestly): a real scout-supplied trope that happens to be NULL-justified AND
whose name coincides with a recomputed semantic-fallback target for THAT SAME work's own
genres/moods will be misclassified as bogus and deleted. This is a real, documented gap in the
distinguisher — a NULL-justified attractor trope that is ALSO close to one of the work's own
tags. It cannot be told apart from the actual pollution using only this work's data. The dry-run
report is the human gate for this residual: the operator reviews every planned delete_link before
approving --apply.

Four action classes, one plan (mirrors etl/dedup_backfill.py's op-tagged token gate EXACTLY —
see plan_id_set's docstring there for why the op-tag is load-bearing, not cosmetic):

  1. delete_link(work_id, trope_id): NULL-justified link whose trope_id is in
     bogus_targets(work).
  2. write_slug(work_id, trope_name): after (planned) deletions, the work would have NO real
     trope left (no remaining/surviving link that is_fallback_trope_name(...) is False for) ->
     plan the missing exact-name slug links for every name in clean_trope_name(tag),
     tag in genres|moods, not already linked. Applied via D1's get_or_create_fallback_trope
     (exact-name-only — never the semantic redirect that caused this mess).
  3. clear_stamp(work_id): work has no real trope remaining (post-delete) AND
     deep_enriched_at IS NOT NULL -> NULL it out. The 6.3 migration backfill stamped
     deep_enriched_at on any work with >=1 trope row; a fallback-only work is a false positive
     that must become visible to the #97 requeue sweep again.
  4. prune_trope(trope_id): a trope left with ZERO links after the PLANNED deletes (apply
     recomputes this at apply time — see apply_fallback_repair).

Read-only during planning: bogus_targets NEVER creates a Trope. Per #123 (memory: warm
embeddings before any session), every cleaned genre/mood name this plan might need to embed is
warmed via get_cached_embedding BEFORE plan_fallback_repair opens/uses its session — see
scripts/clean_catalog.py's --repair-fallbacks CLI wiring, which calls warm_fallback_repair_texts
first.

Apply is THE USER GATE (mirrors dedup_backfill/clean_catalog's --dedup-for-constraints exactly):
apply_fallback_repair(session, reviewed_report_path) re-plans FRESH against current DB state,
parses the reviewed report's token set (fail-closed — see parse_report), and REFUSES (raises,
no partial writes) if the fresh plan's token set contains ANY token absent from the reviewed
set. Op-tagged tokens make even an operation flip on the same ids (e.g. a row that was
`delete_link:` in review becomes eligible for a DIFFERENT bogus_target by apply time) a new,
visible token — see plan_tokens's docstring. fresh ⊆ reviewed is fine (skipped_stale is
reported, not refused)."""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from sqlalchemy.orm import Session

from agentic_librarian.db.models import Trope, Work, WorkTrope
from agentic_librarian.etl.tag_cleaning import clean_trope_name
from agentic_librarian.etl.trope_predicate import is_fallback_trope_name
from agentic_librarian.scouts.utils import EMBED_MODEL, get_cached_embedding

logger = logging.getLogger(__name__)

# Mirrors scouts/trope_manager.py TropeManager.standardize_trope's default `threshold=0.85`
# (cosine SIMILARITY) BY DELIBERATE COUPLING: this module exists to find exactly what that
# redirect would have matched, so the two constants must move together. cosine DISTANCE is
# (1 - similarity), hence 0.15 here.
BOGUS_MATCH_THRESHOLD = 0.15


# --------------------------------------------------------------------------------------------
# Plan dataclasses
# --------------------------------------------------------------------------------------------


@dataclass
class DeleteLink:
    work_id: UUID
    trope_id: UUID
    trope_name: str


@dataclass
class WriteSlug:
    work_id: UUID
    trope_name: str


@dataclass
class ClearStamp:
    work_id: UUID


@dataclass
class PruneTrope:
    trope_id: UUID
    trope_name: str


@dataclass
class FallbackRepairPlan:
    delete_links: list[DeleteLink] = field(default_factory=list)
    write_slugs: list[WriteSlug] = field(default_factory=list)
    clear_stamps: list[ClearStamp] = field(default_factory=list)
    prune_tropes: list[PruneTrope] = field(default_factory=list)

    def summary(self) -> dict[str, int]:
        return {
            "delete_links": len(self.delete_links),
            "write_slugs": len(self.write_slugs),
            "clear_stamps": len(self.clear_stamps),
            "prune_tropes": len(self.prune_tropes),
        }


# --------------------------------------------------------------------------------------------
# Embedding warm-up (#123: no embed network calls inside a session)
# --------------------------------------------------------------------------------------------


def _cleaned_tag_names(genres: list[str] | None, moods: list[str] | None) -> list[str]:
    """Every name clean_trope_name would produce for this work's genres|moods, de-duped,
    order-stable. Pure — no I/O."""
    names: list[str] = []
    seen: set[str] = set()
    for tag in list(genres or []) + list(moods or []):
        for name in clean_trope_name(tag):
            if name not in seen:
                seen.add(name)
                names.append(name)
    return names


def warm_fallback_repair_texts(session: Session) -> list[str]:
    """Every cleaned genre/mood name across every work — the full set plan_fallback_repair's
    bogus_targets scan might need to embed. Callers MUST warm these via get_cached_embedding
    BEFORE opening/using the session that will call plan_fallback_repair (#123). Read-only:
    only SELECTs Work.genres/moods, never touches Trope."""
    names: set[str] = set()
    for genres, moods in session.query(Work.genres, Work.moods).all():
        names.update(_cleaned_tag_names(genres, moods))
    return sorted(names)


# --------------------------------------------------------------------------------------------
# bogus_targets: the structural distinguisher
# --------------------------------------------------------------------------------------------


def _nearest_trope_by_name(session: Session, all_tropes_by_name: dict[str, Trope], name: str) -> Trope | None:
    """Exact-name match -> None (legitimate slug, never bogus — skip). Else the nearest trope
    by cosine distance if within BOGUS_MATCH_THRESHOLD, else None (no bogus target for this
    name). Read-only: never creates a Trope. Embeds `name` via the (already-warmed, per #123)
    get_cached_embedding cache."""
    if name in all_tropes_by_name:
        return None  # an exact-name slug trope already exists for this tag -> legitimate
    embedding = get_cached_embedding(EMBED_MODEL, name)
    nearest = (
        session.query(Trope)
        .filter(Trope.embedding.isnot(None))
        .filter(Trope.embedding.cosine_distance(embedding) <= BOGUS_MATCH_THRESHOLD)
        .order_by(Trope.embedding.cosine_distance(embedding))
        .first()
    )
    return nearest


def bogus_targets(session: Session, work: Work, all_tropes_by_name: dict[str, Trope]) -> set[UUID]:
    """The set of trope ids that are a bogus (deterministically re-derivable) semantic-redirect
    target for THIS work's genres|moods. Read-only."""
    out: set[UUID] = set()
    for name in _cleaned_tag_names(work.genres, work.moods):
        nearest = _nearest_trope_by_name(session, all_tropes_by_name, name)
        if nearest is not None:
            out.add(nearest.id)
    return out


# --------------------------------------------------------------------------------------------
# Plan
# --------------------------------------------------------------------------------------------


@dataclass
class _WorkData:
    """Pure input shape for _plan_from_data — a session-free snapshot of one work's identity,
    genres/moods, and links. Split out from plan_fallback_repair so the classification core
    (which action a link/work falls into) is unit-testable without faking SQLAlchemy's query
    API or a live Postgres cosine_distance operator."""

    work_id: UUID
    genres: list[str] | None
    moods: list[str] | None
    deep_enriched_at: object | None  # only ever compared to None; type left loose for callers
    # (trope_id, trope_name, justification) per link
    links: list[tuple[UUID, str, str | None]]


def _plan_from_data(
    works_data: list[_WorkData],
    all_tropes_by_id: dict[UUID, str],  # trope_id -> trope_name, every Trope in the DB
    bogus_targets_by_work: dict[UUID, set[UUID]],  # work_id -> bogus_targets(work) result
) -> FallbackRepairPlan:
    """Pure classification core (no I/O): given every work's links + genres/moods and each
    work's precomputed bogus_targets set, produce the four-class plan. See the module docstring
    for the exact rules; this function is the single place they're encoded, exercised directly
    by test/unit/test_fallback_repair.py and indirectly (via plan_fallback_repair) by the
    db_integration suite."""
    plan = FallbackRepairPlan()
    deleted_link_count_by_trope: dict[UUID, int] = defaultdict(int)
    total_link_count: dict[UUID, int] = defaultdict(int)

    for wd in works_data:
        for trope_id, _name, _justification in wd.links:
            total_link_count[trope_id] += 1

    for wd in works_data:
        targets = bogus_targets_by_work.get(wd.work_id, set())

        planned_deletes: set[UUID] = set()
        for trope_id, name, justification in wd.links:
            if justification is None and trope_id in targets:
                plan.delete_links.append(DeleteLink(work_id=wd.work_id, trope_id=trope_id, trope_name=name))
                planned_deletes.add(trope_id)
                deleted_link_count_by_trope[trope_id] += 1

        # A "real" trope survives if it's NOT planned for deletion AND is not itself a
        # fallback-name match for this work's own genres/moods (is_fallback_trope_name False).
        has_real_remaining = any(
            trope_id not in planned_deletes and is_fallback_trope_name(name, wd.genres, wd.moods) is False
            for trope_id, name, _justification in wd.links
        )

        if not has_real_remaining:
            existing_names = {name for trope_id, name, _j in wd.links if trope_id not in planned_deletes}
            for name in _cleaned_tag_names(wd.genres, wd.moods):
                if name not in existing_names:
                    plan.write_slugs.append(WriteSlug(work_id=wd.work_id, trope_name=name))
            if wd.deep_enriched_at is not None:
                plan.clear_stamps.append(ClearStamp(work_id=wd.work_id))

    # prune_trope: a trope with ZERO links remaining after the planned deletes (recomputed
    # against current total link counts minus planned deletes — apply recomputes fresh again).
    for trope_id, deleted_count in deleted_link_count_by_trope.items():
        if deleted_count >= total_link_count.get(trope_id, 0):
            trope_name = all_tropes_by_id.get(trope_id)
            if trope_name is not None:
                plan.prune_tropes.append(PruneTrope(trope_id=trope_id, trope_name=trope_name))

    return plan


def plan_fallback_repair(session: Session) -> FallbackRepairPlan:
    """READ ONLY. See the module docstring for the four action classes and the distinguisher.
    Embeddings for any not-yet-embedded cleaned tag name are fetched via get_cached_embedding —
    callers MUST have warmed these (warm_fallback_repair_texts) before opening this session
    (#123)."""
    all_tropes = session.query(Trope).order_by(Trope.id).all()
    all_tropes_by_name = {t.name: t for t in all_tropes}
    all_tropes_by_id = {t.id: t.name for t in all_tropes}

    # work_id -> [(trope_id, name, justification), ...] for every link, ordered for determinism
    # (mirrors dedup_backfill's Minor-3 order_by discipline — see _plan_authors's comment).
    links_by_work: dict[UUID, list[tuple[UUID, str, str | None]]] = defaultdict(list)
    for wt in (
        session.query(WorkTrope.work_id, WorkTrope.trope_id, Trope.name, WorkTrope.justification)
        .join(Trope, Trope.id == WorkTrope.trope_id)
        .order_by(WorkTrope.work_id, WorkTrope.trope_id)
        .all()
    ):
        work_id, trope_id, name, justification = wt
        links_by_work[work_id].append((trope_id, name, justification))

    works = session.query(Work).order_by(Work.id).all()

    works_data = [
        _WorkData(
            work_id=work.id,
            genres=work.genres,
            moods=work.moods,
            deep_enriched_at=work.deep_enriched_at,
            links=links_by_work.get(work.id, []),
        )
        for work in works
    ]
    bogus_targets_by_work = {work.id: bogus_targets(session, work, all_tropes_by_name) for work in works}

    return _plan_from_data(works_data, all_tropes_by_id, bogus_targets_by_work)


# --------------------------------------------------------------------------------------------
# Op-tagged tokens (mirrors etl/dedup_backfill.py's plan_id_set/plan_delta EXACTLY — see that
# module's docstring for why the op-tag prefix is load-bearing, not cosmetic: it turns an
# operation flip on the same underlying id into a NEW token, so plan_delta's refuse-on-addition
# policy catches it instead of a bare-id diff seeing "unchanged").
# --------------------------------------------------------------------------------------------

PLAN_TOKEN_CLASSES = ("delete_links", "write_slugs", "clear_stamps", "prune_tropes")


def plan_tokens(plan: FallbackRepairPlan) -> dict[str, set[str]]:
    """Every action the plan is 'about', per class, as an opaque op-tagged token string.
    Composite identifiers are stringified as `<op>:<tuple-repr>` (not decomposed), same
    convention as dedup_backfill.plan_id_set."""
    out: dict[str, set[str]] = {name: set() for name in PLAN_TOKEN_CLASSES}
    out["delete_links"].update(f"delete_link:{(d.work_id, d.trope_id)}" for d in plan.delete_links)
    out["write_slugs"].update(f"write_slug:{(w.work_id, w.trope_name)}" for w in plan.write_slugs)
    out["clear_stamps"].update(f"clear_stamp:{c.work_id}" for c in plan.clear_stamps)
    out["prune_tropes"].update(f"prune_trope:{p.trope_id}" for p in plan.prune_tropes)
    return out


def plan_delta(reviewed: dict[str, set[str]], fresh: FallbackRepairPlan) -> dict[str, set[str]]:
    """Per-class tokens present in the FRESH plan but NOT in the REVIEWED token set. Empty
    everywhere means fresh's tokens are a subset of reviewed's (safe to apply); any non-empty
    class means something new (including an operation flip on the same ids) appeared since the
    operator reviewed the report."""
    fresh_tokens = plan_tokens(fresh)
    return {name: fresh_tokens.get(name, set()) - reviewed.get(name, set()) for name in PLAN_TOKEN_CLASSES}


# --------------------------------------------------------------------------------------------
# Report: human-readable + machine-readable token block, fail-closed parser (mirrors
# scripts/clean_catalog.py's _write_dedup_report / _parse_plan_ids EXACTLY, including the
# explicit END marker and the same three fail-closed ValueError cases).
# --------------------------------------------------------------------------------------------


def write_report(plan: FallbackRepairPlan, reports_dir: Path | None = None) -> Path:
    """Every token in the plan, always written (dry-run AND apply) — this file is what the
    operator reviews before approving --repair-fallbacks-apply (THE USER GATE). Microsecond
    timestamp resolution, same collision-avoidance reasoning as _write_dedup_report."""
    reports_dir = reports_dir or Path("data/reports")
    reports_dir.mkdir(parents=True, exist_ok=True)
    now = datetime.now(UTC)
    ts = now.strftime("%Y%m%dT%H%M%S") + f"{now.microsecond:06d}Z"
    path = reports_dir / f"fallback-repair-{ts}.txt"

    lines: list[str] = [f"Fallback-repair plan report — {ts}", "=" * 60, ""]

    lines.append(f"delete_links: {len(plan.delete_links)} links (NULL-justified, recomputed-bogus)")
    for d in plan.delete_links:
        lines.append(f"  work_id={d.work_id}  trope_id={d.trope_id}  trope_name={d.trope_name!r}")
    lines.append("")

    lines.append(f"write_slugs: {len(plan.write_slugs)} exact-name slug links to restore")
    for w in plan.write_slugs:
        lines.append(f"  work_id={w.work_id}  trope_name={w.trope_name!r}")
    lines.append("")

    lines.append(f"clear_stamps: {len(plan.clear_stamps)} works' deep_enriched_at to NULL")
    for c in plan.clear_stamps:
        lines.append(f"  work_id={c.work_id}")
    lines.append("")

    lines.append(f"prune_tropes: {len(plan.prune_tropes)} tropes left with zero links")
    for p in plan.prune_tropes:
        lines.append(f"  trope_id={p.trope_id}  trope_name={p.trope_name!r}")
    lines.append("")

    lines.append("== PLAN TOKENS ==")
    tokens = plan_tokens(plan)
    for class_name in PLAN_TOKEN_CLASSES:
        class_tokens = sorted(tokens.get(class_name, set()))
        lines.append(f"[{class_name}] {len(class_tokens)}")
        lines.extend(class_tokens)
    lines.append("== END PLAN TOKENS ==")
    lines.append("")

    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def parse_report(report_text: str) -> dict[str, set[str]]:
    """Parse the '== PLAN TOKENS ==' block back into the same per-class token-set shape
    plan_tokens produces. Fail-closed (explicit, tested contract, mirrors
    scripts/clean_catalog.py._parse_plan_ids): raises ValueError if the start marker is
    missing, if the '== END PLAN TOKENS ==' terminator is missing (truncated report), or if a
    line that looks like a class header (starts with '[') doesn't actually parse as one."""
    lines = report_text.splitlines()
    try:
        start = lines.index("== PLAN TOKENS ==")
    except ValueError as exc:
        raise ValueError(
            "report has no '== PLAN TOKENS ==' section — not a fallback-repair report, or an old-format one"
        ) from exc

    try:
        end = lines.index("== END PLAN TOKENS ==", start + 1)
    except ValueError as exc:
        raise ValueError("report has no '== END PLAN TOKENS ==' terminator — truncated or corrupt report") from exc

    out: dict[str, set[str]] = {name: set() for name in PLAN_TOKEN_CLASSES}
    current: str | None = None
    for line in lines[start + 1 : end]:
        if line.startswith("["):
            if "]" not in line:
                raise ValueError(f"malformed class-header line in PLAN TOKENS block: {line!r}")
            current = line[1 : line.index("]")]
            if current not in out:
                out[current] = set()
            continue
        if current is not None and line:
            out[current].add(line)
    return out


# --------------------------------------------------------------------------------------------
# Apply — THE USER GATE
# --------------------------------------------------------------------------------------------


def apply_fallback_repair(session: Session, reviewed_report_path: Path) -> dict[str, int]:
    """--repair-fallbacks-apply is a SEPARATE invocation from the reviewed dry-run. Re-plans
    FRESH against current DB state, parses the reviewed report's token set (fail-closed — see
    parse_report), and REFUSES (raises ValueError, no partial writes — nothing is flushed
    before the drift check completes) if the fresh plan contains ANY token absent from the
    reviewed set. fresh_tokens ⊆ reviewed_tokens is fine (reported under skipped_stale, not
    refused). Executes delete_link -> write_slug -> clear_stamp -> prune_trope in ONE
    transaction (session.flush() between phases so later phases see earlier ones; the caller
    owns commit/rollback via the session context manager, mirroring every other gated tool in
    this package)."""
    reviewed_tokens = parse_report(Path(reviewed_report_path).read_text(encoding="utf-8"))

    fresh_plan = plan_fallback_repair(session)
    delta = plan_delta(reviewed_tokens, fresh_plan)
    if any(delta.values()):
        details = ", ".join(f"{name}: +{len(tokens)}" for name, tokens in delta.items() if tokens)
        raise ValueError(
            f"REFUSING apply_fallback_repair: fresh plan drifted from the reviewed report ({details}). "
            "Re-review a fresh report before applying."
        )

    from agentic_librarian.scouts.trope_manager import TropeManager

    tm = TropeManager(session=session)

    result = {
        "delete_links": 0,
        "write_slugs": 0,
        "clear_stamps": 0,
        "prune_tropes": 0,
        "skipped_stale": 0,
    }

    for d in fresh_plan.delete_links:
        wt = session.get(WorkTrope, {"work_id": d.work_id, "trope_id": d.trope_id})
        if wt is None:
            result["skipped_stale"] += 1
            continue
        session.delete(wt)
        result["delete_links"] += 1
    session.flush()

    for w in fresh_plan.write_slugs:
        if session.get(Work, w.work_id) is None:
            result["skipped_stale"] += 1
            continue
        trope = tm.get_or_create_fallback_trope(w.trope_name)
        existing_link = session.get(WorkTrope, {"work_id": w.work_id, "trope_id": trope.id})
        if existing_link is not None:
            result["skipped_stale"] += 1
            continue
        session.add(WorkTrope(work_id=w.work_id, trope_id=trope.id))
        result["write_slugs"] += 1
    session.flush()

    for c in fresh_plan.clear_stamps:
        work = session.get(Work, c.work_id)
        if work is None or work.deep_enriched_at is None:
            result["skipped_stale"] += 1
            continue
        work.deep_enriched_at = None
        result["clear_stamps"] += 1
    session.flush()

    for p in fresh_plan.prune_tropes:
        trope = session.get(Trope, p.trope_id)
        if trope is None:
            result["skipped_stale"] += 1
            continue
        # Recompute at apply time (the module docstring's contract for class 4): only prune if
        # STILL zero-linked now, after the deletes above actually ran — never trust the plan
        # snapshot's count alone.
        remaining = session.query(WorkTrope).filter_by(trope_id=p.trope_id).count()
        if remaining > 0:
            result["skipped_stale"] += 1
            continue
        session.delete(trope)
        result["prune_tropes"] += 1
    session.flush()

    return result
