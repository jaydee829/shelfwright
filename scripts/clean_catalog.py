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

Run against LIVE prod via the app container + Cloud SQL proxy. Refuses --apply on sqlite/backup/localhost."""

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
    operator reviews before approving --apply (THE USER GATE, Spec 2026-07-12)."""
    reports_dir = Path("data/reports")
    reports_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
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

    path.write_text("\n".join(lines), encoding="utf-8")
    return path


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
            "apply. See docs/runbooks/phase6-3-schema-rollout.md."
        ),
    )
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--yes", action="store_true", help="required confirmation for --apply")
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

            report_path = _write_dedup_report(plan)
            print(f"\nfull plan (every id) written to {report_path} — review this before approving --apply.")

            early = _refuse(args, url, safe)
            if early is not None:
                return early
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
