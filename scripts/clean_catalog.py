"""Operator CLI for catalog cleanup (Spec 2026-06-23): contributor dedup + trope-name cleaning.

  python scripts/clean_catalog.py --inventory
  python scripts/clean_catalog.py --contributors --dry-run
  python scripts/clean_catalog.py --contributors --apply --yes
  python scripts/clean_catalog.py --tropes --dry-run
  python scripts/clean_catalog.py --tropes --apply --yes
  python scripts/clean_catalog.py --requeue-unenriched --dry-run
  python scripts/clean_catalog.py --requeue-unenriched --apply --yes
  python scripts/clean_catalog.py --dedup-for-constraints --dry-run
  python scripts/clean_catalog.py --dedup-for-constraints --apply --yes
  python scripts/clean_catalog.py --dedup-for-constraints --apply --yes --report data/reports/dedup-<ts>.txt

Run against LIVE prod via the app container + Cloud SQL proxy. Refuses --apply on sqlite/backup/localhost.
--dedup-for-constraints --apply re-plans from scratch and cross-checks the fresh plan against
the reviewed --report (default: newest data/reports/dedup-*.txt) — refuses if the plan drifted."""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path

from agentic_librarian.db.models import Trope, Work
from agentic_librarian.db.session import DatabaseManager, resolve_database_url
from agentic_librarian.etl import contributor_dedup, dedup_backfill, enrichment_sweep, trope_backfill
from agentic_librarian.etl.tag_backfill import is_prod_url


def _write_dedup_report(plan) -> Path:
    """Every id in the plan, always written (dry-run AND apply) — this file is what the
    operator reviews before approving --apply (THE USER GATE, Spec 2026-07-12).

    Filename includes microseconds: a scripted dry-run -> apply sequence (or a test) can
    complete both invocations within the same second. With a second-resolution timestamp, both
    writes would resolve to the IDENTICAL path, so apply's fresh-plan write would overwrite the
    dry-run's report file on disk before it's ever read back — the second write clobbers the
    first file, full stop, not a "which one is newest" lookup ambiguity: the path to read back
    is captured (`existing_report = args.report or _newest_dedup_report()`, in main() below) up
    front, BEFORE this same invocation's fresh write happens, so at read-back time there is only
    ever one candidate path in play. Microsecond resolution avoids the collision at its root by
    keeping the two filenames from ever being identical in the first place, making the
    apply gate's cross-check (Spec 2026-07-12 follow-up to #95) compare the dry-run's real
    reviewed content against the fresh plan instead of trivially comparing the fresh plan
    against itself."""
    reports_dir = Path("data/reports")
    reports_dir.mkdir(parents=True, exist_ok=True)
    now = datetime.now(UTC)
    ts = now.strftime("%Y%m%dT%H%M%S") + f"{now.microsecond:06d}Z"
    path = reports_dir / f"dedup-{ts}.txt"

    lines: list[str] = [f"Dedup plan report — {ts}", "=" * 60, ""]
    lines.append(f"duplicate_authors: {len(plan.duplicate_authors)} groups")
    for g in plan.duplicate_authors:
        lines.append(f"  survivor={g.survivor_id} ({g.survivor_name!r})  losers={g.loser_ids} ({g.loser_names!r})")
        lines.append(f"    repoint_links={g.repoint_links}  delete_links={g.delete_links}")
        lines.append(f"    repoint_styles={g.repoint_styles}  delete_styles={g.delete_styles}")
    lines.append("")

    lines.append(f"duplicate_narrators: {len(plan.duplicate_narrators)} groups")
    for g in plan.duplicate_narrators:
        lines.append(f"  survivor={g.survivor_id} ({g.survivor_name!r})  losers={g.loser_ids} ({g.loser_names!r})")
        lines.append(f"    repoint_links={g.repoint_links}  delete_links={g.delete_links}")
        lines.append(f"    repoint_styles={g.repoint_styles}  delete_styles={g.delete_styles}")
    lines.append("")

    lines.append(f"duplicate_editions: {len(plan.duplicate_editions)} groups")
    for g in plan.duplicate_editions:
        lines.append(f"  survivor={g.survivor_id}  work_id={g.work_id}  format={g.fmt!r}  losers={g.loser_ids}")
        lines.append(
            f"    repoint_reading_history={g.repoint_reading_history}  "
            f"delete_reading_history={g.delete_reading_history}"
        )
        lines.append(f"    repoint_narrators={g.repoint_narrators}  delete_narrators={g.delete_narrators}")
    lines.append("")

    lines.append(f"duplicate_reading_history: {len(plan.duplicate_reading_history)} groups")
    for g in plan.duplicate_reading_history:
        lines.append(f"  survivor={g.survivor_id}  losers={g.loser_ids}  ({g.detail})")
    lines.append("")

    lines.append(f"duplicate_suggestions: {len(plan.duplicate_suggestions)} groups")
    for g in plan.duplicate_suggestions:
        lines.append(f"  survivor={g.survivor_id}  losers={g.loser_ids}  ({g.detail})")
    lines.append("")

    lines.append(f"orphan_authors: {len(plan.orphan_authors)} ids (deleted on apply)")
    lines.append(f"  {plan.orphan_authors}")
    lines.append("")

    lines.append(f"duplicate_works_report_only: {len(plan.duplicate_works_report_only)} groups (NEVER applied)")
    for w in plan.duplicate_works_report_only:
        lines.append(f"  key={w.norm_key!r}  work_ids={w.work_ids}  titles={w.titles}")
    lines.append("")

    # Final-review Critical (GH #95 follow-up): groups dropped from their class because they
    # intersect another class's plan in a way that would compose into row loss if both applied
    # from this SAME snapshot (narrator x edition cascade; edition x reading_history
    # double-delete — see etl/dedup_backfill.py's _defer_intersecting_groups docstring). This is
    # EXPECTED on intersecting data, not an error: the runbook's existing dry-run/apply LOOP
    # re-plans after the intersecting class has already applied, resolving these on the next pass.
    total_deferred = sum(len(v) for v in plan.deferred_intersections.values())
    lines.append(f"deferred_intersections: {total_deferred} groups deferred to the next dry-run pass")
    if total_deferred:
        lines.append(
            "  (EXPECTED on intersecting data, not an error — re-run --dedup-for-constraints "
            "--dry-run after this apply and these will resolve; see docs/runbooks/"
            "phase6-3-schema-rollout.md step 3.)"
        )
    for class_name, entries in plan.deferred_intersections.items():
        lines.append(f"  [{class_name}] {len(entries)} deferred")
        for entry in entries:
            lines.append(f"    {entry}")
    lines.append("")

    # Machine-readable section (Spec 2026-07-12 follow-up to #95): the exact per-class id set
    # this plan is "about", one id per line, sorted for a stable diff-friendly file. --apply
    # parses this back into a dict[str, set[str]] (see _parse_plan_ids) and cross-checks a
    # FRESH re-plan against it via dedup_backfill.plan_delta — refusing if anything new shows
    # up. Keep this block below the human-readable summary above, not instead of it.
    lines.append("== PLAN IDS ==")
    id_set = dedup_backfill.plan_id_set(plan)
    for class_name in dedup_backfill.PLAN_ID_SET_CLASSES:
        ids = sorted(id_set.get(class_name, set()))
        lines.append(f"[{class_name}] {len(ids)}")
        lines.extend(ids)
    lines.append("== END PLAN IDS ==")
    lines.append("")

    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def _parse_plan_ids(report_text: str) -> dict[str, set[str]]:
    """Parse the '== PLAN IDS ==' block written by _write_dedup_report back into the same
    per-class id-set shape dedup_backfill.plan_id_set produces, so a fresh plan can be
    cross-checked against what the operator actually reviewed.

    Fail-closed is an explicit, tested contract here, not an accident of subtraction semantics
    (an empty/partial reviewed set would otherwise make plan_delta look "safe" just because
    there's nothing to diff against): raises ValueError if the '== PLAN IDS ==' start marker is
    missing (an old-format report, or the wrong file); if the '== END PLAN IDS ==' terminator is
    missing (a truncated report — e.g. a write that got cut off mid-file); or if a line that
    looks like a class header (starts with '[') doesn't actually parse as one (a malformed
    header must not be silently absorbed as a plain id token). --apply catches this ValueError
    and refuses rather than proceeding against a corrupt or incomplete reviewed set."""
    lines = report_text.splitlines()
    try:
        start = lines.index("== PLAN IDS ==")
    except ValueError as exc:
        raise ValueError("report has no '== PLAN IDS ==' section — not a dedup report, or an old-format one") from exc

    try:
        end = lines.index("== END PLAN IDS ==", start + 1)
    except ValueError as exc:
        raise ValueError("report has no '== END PLAN IDS ==' terminator — truncated or corrupt report") from exc

    out: dict[str, set[str]] = {name: set() for name in dedup_backfill.PLAN_ID_SET_CLASSES}
    current: str | None = None
    for line in lines[start + 1 : end]:
        if line.startswith("["):
            if "]" not in line:
                raise ValueError(f"malformed class-header line in PLAN IDS block: {line!r}")
            current = line[1 : line.index("]")]
            if current not in out:
                out[current] = set()
            continue
        if current is not None and line:
            out[current].add(line)
    return out


def _newest_dedup_report() -> Path | None:
    reports_dir = Path("data/reports")
    if not reports_dir.is_dir():
        return None
    candidates = sorted(reports_dir.glob("dedup-*.txt"))
    return candidates[-1] if candidates else None


def _refuse(args, url, safe) -> int | None:
    if args.dry_run or not args.apply:
        print("\n(dry-run: no writes)")
        return 0
    if not args.yes:
        print("\nREFUSING --apply without --yes.")
        return 2
    if not is_prod_url(url):
        print(f"\nREFUSING --apply: '{safe}' is not a live prod DB (sqlite/backup/localhost).")
        return 2
    return None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Dedup contributors / clean trope names.")
    ap.add_argument("--inventory", action="store_true")
    ap.add_argument("--contributors", action="store_true")
    ap.add_argument("--tropes", action="store_true")
    ap.add_argument("--prune-fallbacks", action="store_true")
    ap.add_argument(
        "--requeue-unenriched",
        action="store_true",
        help=(
            "GH #97 recovery path: list works that never completed a deep-enrichment pass "
            "(deep_enriched_at IS NULL) or completed one without landing a real trope (poison "
            "tasks that exhausted Cloud Tasks retries on the 503 empty-pass path). --apply --yes "
            "re-enqueues each via enqueue_enrichment — requires Cloud Tasks env "
            "(CLOUD_TASKS_QUEUE, ENRICH_TARGET_BASE_URL, ENRICH_INVOKER_SA) set, i.e. run this "
            "from the prod operator context, not local dev."
        ),
    )
    ap.add_argument(
        "--dedup-for-constraints",
        action="store_true",
        help=(
            "Phase 6.3 THE USER GATE (#95): plan+apply the pre-constraint dedup backfill "
            "(duplicate_authors/narrators/editions/reading_history/suggestions + orphan_authors; "
            "duplicate_works_report_only is a report-only detail list, never applied). Structural "
            "distinguishers only (the #69 lesson) — see etl/dedup_backfill.py. Sequence: PR-C "
            "deployed (pollution stopped) -> this dry-run on prod -> operator reviews the report "
            "and approves -> --apply --yes -> operator runs `alembic upgrade head` (lands the #95 "
            "unique constraints, now safe) -> merge PR-D -> deploy. Every id in the plan is always "
            "written to data/reports/dedup-<UTC timestamp>.txt for review, on both dry-run and "
            "apply. See docs/runbooks/phase6-3-schema-rollout.md. IMPORTANT: --apply --yes is a "
            "SEPARATE invocation that RE-PLANS from scratch — it does not reuse the dry-run's "
            "in-memory plan. To keep the review meaningful, --apply cross-checks the fresh plan's "
            "id set against the reviewed report (--report) and REFUSES (exit 1) if the fresh plan "
            "contains any id the operator never reviewed (e.g. a new duplicate from live traffic "
            "in the gap between dry-run and apply) — it prints the delta and writes a fresh report "
            "for re-review instead of applying. A plan that lost ids (rows deleted/changed since "
            "review) is fine and applies normally (ordinary skipped_stale)."
        ),
    )
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--yes", action="store_true", help="required confirmation for --apply")
    ap.add_argument(
        "--report",
        type=Path,
        default=None,
        help=(
            "--dedup-for-constraints --apply only: path to the reviewed dry-run report to "
            "cross-check the fresh plan against (THE USER GATE). Defaults to the newest "
            "data/reports/dedup-*.txt — the tool prints which one it used."
        ),
    )
    args = ap.parse_args(argv)

    url = resolve_database_url()
    safe = url.split("@")[-1] if "@" in url else url  # never print credentials
    manager = DatabaseManager(url)

    with manager.get_session() as session:
        print(f"DB target: …@{safe}")
        print(f"recency probe: works={session.query(Work).count()} tropes={session.query(Trope).count()}")

        if args.inventory:
            inv = contributor_dedup.contributor_inventory(session)
            print(f"\n=== duplicate AUTHOR groups ({len(inv['authors'])}) ===")
            for g in inv["authors"]:
                print(f"  {g}")
            print(f"\n=== duplicate NARRATOR groups ({len(inv['narrators'])}) ===")
            for g in inv["narrators"]:
                print(f"  {g}")
            _counts, dirty = trope_backfill.trope_inventory(session)
            print(f"\n=== dirty TROPES ({len(dirty)}) ===")
            for before, after, wc in dirty:
                print(f"  {wc:4d}  {before!r} -> {after}")
            print(f"\nembedding calls a --tropes --apply would make: {trope_backfill.embedding_call_estimate(session)}")
            pw, pl = trope_backfill.fallback_prune_inventory(session)
            print(
                f"\n=== fallback-trope POLLUTION ===\n  {pw} works with real+fallback layers, {pl} fallback links prunable"
            )
            return 0

        if args.contributors:
            changes = contributor_dedup.plan_contributor_changes(session)
            print(f"\n{len(changes)} contributor groups would merge.")
            for c in changes[:80]:
                print(f"  [{c.kind}] keep {c.survivor!r}  merge {c.merged}")
            early = _refuse(args, url, safe)
            if early is not None:
                return early
            applied = contributor_dedup.apply_contributor_changes(session)
            print(f"\napplied: merged {len(applied)} groups.")
            return 0

        if args.prune_fallbacks:
            changes = trope_backfill.plan_fallback_prune(session)
            total = sum(len(c.deleted) for c in changes)
            print(f"\n{len(changes)} works would have {total} fallback tropes pruned.")
            for c in changes[:80]:
                print(f"  [{c.title[:40]:40}] -{len(c.deleted)} fallback (keep {c.real_kept} real): {c.deleted}")
            early = _refuse(args, url, safe)
            if early is not None:
                return early
            print(f"\napplied: pruned {trope_backfill.apply_fallback_prune(session, changes)} fallback links.")
            return 0

        if args.tropes:
            changes = trope_backfill.plan_trope_changes(session)
            calls = trope_backfill.embedding_call_estimate(session)
            print(f"\n{len(changes)} trope rows would change ({calls} embedding calls).")
            for c in changes[:80]:
                print(f"  {c.works_affected:4d}  {c.name_before!r} -> {c.names_after}")
            early = _refuse(args, url, safe)
            if early is not None:
                return early
            from agentic_librarian.scouts.trope_manager import TropeManager

            tm = TropeManager(session)
            print(f"\napplied: {trope_backfill.apply_trope_changes(session, tm, changes)} trope rows cleaned.")
            return 0

        if args.requeue_unenriched:
            candidates = enrichment_sweep.plan_requeue(session)
            print(f"\n{len(candidates)} works would be requeued for deep enrichment.")
            for c in candidates[:80]:
                print(f"  [{c.reason:20}] {c.title[:60]}  ({c.work_id})")
            early = _refuse(args, url, safe)
            if early is not None:
                return early
            from agentic_librarian.enrichment.tasks import enqueue_enrichment

            enqueued = sum(1 for c in candidates if enqueue_enrichment(str(c.work_id)))
            print(f"\napplied: enqueued {enqueued}/{len(candidates)} works (see logs for any that skipped).")
            return 0

        if args.dedup_for_constraints:
            # Capture the reviewed report BEFORE this run writes its own — the default
            # --report resolution must point at the operator's PRIOR reviewed dry-run, never
            # at the report this same invocation is about to write from the fresh plan below
            # (which would make the cross-check trivially always pass against itself).
            existing_report = args.report or _newest_dedup_report()

            plan = dedup_backfill.plan_dedup(session)
            summary = plan.summary()
            print("\n=== dedup-for-constraints plan (THE USER GATE) ===")
            for key, count in summary.items():
                print(f"  {key:32} {count}")

            print("\n--- duplicate_authors samples ---")
            for g in plan.duplicate_authors[:10]:
                print(f"  keep {g.survivor_name!r} ({g.survivor_id})  merge {g.loser_names!r} ({g.loser_ids})")
            print("--- duplicate_narrators samples ---")
            for g in plan.duplicate_narrators[:10]:
                print(f"  keep {g.survivor_name!r} ({g.survivor_id})  merge {g.loser_names!r} ({g.loser_ids})")
            print("--- duplicate_editions samples ---")
            for g in plan.duplicate_editions[:10]:
                print(f"  work={g.work_id} format={g.fmt!r}  keep {g.survivor_id}  merge {g.loser_ids}")
            print("--- duplicate_reading_history samples ---")
            for g in plan.duplicate_reading_history[:10]:
                print(f"  keep {g.survivor_id}  delete {g.loser_ids}  ({g.detail})")
            print("--- duplicate_suggestions samples ---")
            for g in plan.duplicate_suggestions[:10]:
                print(f"  keep {g.survivor_id}  delete {g.loser_ids}  ({g.detail})")
            print("--- orphan_authors samples ---")
            for aid in plan.orphan_authors[:10]:
                print(f"  {aid}")
            print("--- duplicate_works_report_only samples (NEVER applied — operator triage) ---")
            for w in plan.duplicate_works_report_only[:10]:
                print(f"  {w.titles}  ({w.work_ids})")

            total_deferred = sum(len(v) for v in plan.deferred_intersections.values())
            if total_deferred:
                print(
                    f"\n--- deferred_intersections: {total_deferred} groups deferred "
                    "(EXPECTED on intersecting data — resolves on the next dry-run pass) ---"
                )
                for class_name, entries in plan.deferred_intersections.items():
                    print(f"  [{class_name}] {len(entries)} deferred")

            report_path = _write_dedup_report(plan)
            print(f"\nfull plan (every id) written to {report_path} — review this before approving --apply.")

            early = _refuse(args, url, safe)
            if early is not None:
                return early

            # THE USER GATE, cross-check half (Spec 2026-07-12 follow-up to #95): --apply is a
            # separate invocation and just computed a FRESH plan above (`plan`) — re-planned from
            # scratch against current DB state, not reused from the operator's reviewed dry-run.
            # Cross-check the fresh plan's id set against the reviewed report's id set; refuse on
            # any addition rather than silently applying duplicates the operator never saw.
            report_arg = existing_report
            if report_arg is None:
                print("\nREFUSING --apply: no dedup report found (data/reports/dedup-*.txt) — run --dry-run first.")
                return 1
            print(f"\ncross-checking fresh plan against reviewed report: {report_arg}")
            try:
                reviewed_ids = _parse_plan_ids(report_arg.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                print(f"\nREFUSING --apply: could not read/parse --report {report_arg}: {exc}")
                return 1

            delta = dedup_backfill.plan_delta(reviewed_ids, plan)
            if any(delta.values()):
                print("\nREFUSING --apply: plan changed since review — re-review the new report.")
                print("New ids present in the fresh plan but NOT in the reviewed report:")
                for class_name in dedup_backfill.PLAN_ID_SET_CLASSES:
                    new_ids = sorted(delta.get(class_name, set()))
                    if new_ids:
                        print(f"  [{class_name}] +{len(new_ids)}: {new_ids}")
                # report_path (written unconditionally above, from this SAME fresh plan) already
                # IS the fresh report the operator needs to re-review — no need to write another.
                print(f"\nre-review {report_path}, then re-run --apply --yes --report {report_path}")
                return 1

            applied = dedup_backfill.apply_dedup(session, plan)
            print("\napplied:")
            for key, count in applied.items():
                print(f"  {key:32} {count}")
            print(
                "\nNext: operator runs `alembic upgrade head` on prod to land the #95 unique "
                "constraints, then merge PR-D and deploy."
            )
            return 0

        print(
            "Nothing to do. Pass --inventory, --contributors, --prune-fallbacks, --tropes, "
            "--requeue-unenriched, or --dedup-for-constraints."
        )
        return 1


if __name__ == "__main__":
    sys.exit(main())
