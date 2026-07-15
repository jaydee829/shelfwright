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
  python scripts/clean_catalog.py --repair-fallbacks --dry-run
  python scripts/clean_catalog.py --repair-fallbacks-apply --yes --report data/reports/fallback-repair-<ts>.txt
  python scripts/clean_catalog.py --merge-works
  python scripts/clean_catalog.py --merge-works-apply --yes --report data/reports/works-merge-<ts>.txt
  python scripts/clean_catalog.py --promote-pair WORK_ID_A WORK_ID_B --yes
  python scripts/clean_catalog.py --promote-pair A1 B1 --promote-pair A2 B2 --yes

Run against LIVE prod via the app container + Cloud SQL proxy. Refuses --apply on sqlite/backup/localhost.
--dedup-for-constraints --apply re-plans from scratch and cross-checks the fresh plan against
the reviewed --report (default: newest data/reports/dedup-*.txt) — refuses if the plan drifted.
--repair-fallbacks-apply likewise re-plans fresh and refuses on any drift from --report (required,
no default — GH #70). --merge-works (Spec 2026-07-14 PR-2 part 1) is detection/planning ONLY —
always a dry-run. --merge-works-apply (PR-2 part 2, H2) executes the merge composition (editions/
suggestions/trope+style links/contributors/detected_duplicates/Work-row deletion) behind the same
drift-refusing gate — requires --yes and --report (no default, same as --repair-fallbacks-apply).
--promote-pair (H4) is the operator's front door for hand-promoting a duplicate pair the detection
classes missed (e.g. a works_fuzzy_report_only pair, or one an operator just spots by eye): it
inserts a source='operator' row into the detected_duplicates feed --merge-works's
works_detected_duplicates class already reads — repeatable in one invocation, requires --yes."""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from sqlalchemy import func

from agentic_librarian.db.models import Trope, Work
from agentic_librarian.db.session import DatabaseManager, resolve_database_url
from agentic_librarian.etl import contributor_dedup, dedup_backfill, enrichment_sweep, fallback_repair, trope_backfill
from agentic_librarian.etl.tag_backfill import is_prod_url
from agentic_librarian.scouts.utils import EMBED_MODEL, get_cached_embedding


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


def _warm_fallback_repair_embeddings(manager: DatabaseManager) -> None:
    """Fix 2 (#123, honestly this time): a SHORT, DEDICATED session collects the cleaned
    genre/mood names that would actually need an embedding lookup (warm_fallback_repair_texts
    already excludes exact-name matches — Minor 7, see its docstring), and that session is
    CLOSED before the warm loop below makes any embedding network call. Only once warming is
    complete does the caller open the work session that runs plan/apply_fallback_repair — so no
    embedding call is ever made while a work session sits open, honoring #123 in fact, not just
    in a docstring's claim."""
    with manager.get_session() as warm_session:
        names = fallback_repair.warm_fallback_repair_texts(warm_session)
    for name in names:
        get_cached_embedding(EMBED_MODEL, name)


def _run_repair_fallbacks(manager: DatabaseManager, safe: str) -> int:
    """--repair-fallbacks: dry-run plan only, never applies. Warms BEFORE opening the work
    session — see _warm_fallback_repair_embeddings.

    DB-target visibility (final whole-branch review fix): this mode branches out of main()
    before the shared session block that prints `DB target: ...` for every other mode, so
    without this line it silently never told the operator which database it planned
    against. Print the exact same redacted `safe` string the shared block uses (built once in
    main() from `url.split("@")[-1]`) — never re-derive it here, so the redaction stays in
    exactly one place."""
    print(f"DB target: …@{safe}")
    _warm_fallback_repair_embeddings(manager)

    with manager.get_session() as session:
        plan = fallback_repair.plan_fallback_repair(session)
        summary = plan.summary()
        print("\n=== repair-fallbacks plan (THE USER GATE, GH #70) ===")
        for key, count in summary.items():
            print(f"  {key:32} {count}")

        print("\n--- delete_links samples ---")
        for d in plan.delete_links[:10]:
            print(f"  work={d.work_id}  trope={d.trope_id}  ({d.trope_name!r})")
        print("--- write_slugs samples ---")
        for w in plan.write_slugs[:10]:
            print(f"  work={w.work_id}  slug={w.trope_name!r}")
        print("--- clear_stamps samples ---")
        for c in plan.clear_stamps[:10]:
            print(f"  work={c.work_id}")
        print("--- prune_tropes samples ---")
        for p in plan.prune_tropes[:10]:
            print(f"  trope={p.trope_id}  ({p.trope_name!r})")
        print("--- reset (no-evidence) works samples ---")
        for r in plan.reset_works[:10]:
            print(f"  work={r.work_id}  title={r.title!r}")

        report_path = fallback_repair.write_report(plan, db_target=safe)
        print(f"\nfull plan (every token) written to {report_path} — review this before approving --apply.")
        print(f"\napply with: --repair-fallbacks-apply --yes --report {report_path}")
        return 0


def _run_merge_works(manager: DatabaseManager, safe: str) -> int:
    """--merge-works: DETECTION + COMPOSITION PLANNING (PR-2 parts 1+2) — always a dry-run,
    never applies. Prints the DB target before opening the work session, prints per-class
    cluster summaries + survivor picks, writes the full report (H1's human-readable cluster
    sections + H2's per-composition op summary + the machine-readable token block) to
    data/reports/works-merge-<UTC>.txt — the SAME report --merge-works-apply's drift gate
    cross-checks a fresh re-plan against (dedup_backfill.render_works_merge_apply_report /
    write_works_merge_apply_report), so a --merge-works dry-run report is always a valid
    --report argument for --merge-works-apply."""
    print(f"DB target: …@{safe}")
    with manager.get_session() as session:
        clusters = dedup_backfill.plan_works_merge(session)
        summary = clusters.summary()
        print("\n=== works-merge plan (detection — PR-2 part 1) ===")
        for key, count in summary.items():
            print(f"  {key:32} {count}")

        def _print_clusters(title: str, class_clusters, *, never_applied: bool) -> None:
            suffix = "  (NEVER APPLIED — operator triage only)" if never_applied else ""
            print(f"\n--- {title}{suffix} ---")
            for cluster in class_clusters[:20]:
                print(f"  cluster {cluster.work_ids}  survivor={cluster.survivor_id}  titles={cluster.titles}")

        _print_clusters("works_same_isbn", clusters.same_isbn, never_applied=False)
        _print_clusters("works_same_isbn_title_mismatch", clusters.same_isbn_title_mismatch, never_applied=True)
        _print_clusters("works_same_identity", clusters.same_identity, never_applied=False)
        _print_clusters("works_detected_duplicates", clusters.detected_duplicates, never_applied=False)
        _print_clusters("works_fuzzy_report_only", clusters.fuzzy_report_only, never_applied=True)

        compositions = [
            dedup_backfill.compose_cluster_merge(session, cluster)
            for cluster in dedup_backfill.applyable_works_merge_clusters(clusters)
        ]
        print(f"\n=== apply composition (PR-2 part 2) — {len(compositions)} applyable clusters ===")
        for comp in compositions[:20]:
            print(
                f"  survivor={comp.survivor_id}  losers={comp.loser_ids}  "
                f"repoint_editions={len(comp.repoint_edition_ids)}  merge_editions={len(comp.merge_editions)}  "
                f"dropped_duplicate_reads={comp.dropped_duplicate_reads}  "
                f"repoint_suggestions={len(comp.repoint_suggestion_ids)}  "
                f"drop_duplicate_suggestions={len(comp.drop_duplicate_suggestion_ids)}  "
                f"copy_links={len(comp.copy_trope_links) + len(comp.copy_style_links)}  "
                f"copy_contributors={len(comp.copy_contributors)}  "
                f"delete_detections={len(comp.delete_detection_pairs)}  "
                f"delete_works={len(comp.delete_work_ids)}"
            )
            if comp.malformed_author_candidates:
                print(f"    malformed_author_candidates={comp.malformed_author_candidates} (report-only)")

        report_path = dedup_backfill.write_works_merge_apply_report(clusters, compositions, db_target=safe)
        print(f"\nfull plan (every cluster + composition token) written to {report_path} — review before applying.")
        print(f"\napply with: --merge-works-apply --yes --report {report_path}")
        return 0


def _run_merge_works_apply(manager: DatabaseManager, args, url: str, safe: str) -> int:
    """--merge-works-apply: guards first (mirrors --repair-fallbacks-apply exactly), THEN opens
    the work session that re-plans fresh and applies — see dedup_backfill.apply_works_merge.
    No embedding warm-up needed here (unlike --repair-fallbacks-apply): works-merge composition
    never calls get_cached_embedding, only plan_works_merge's own detection queries, which are
    already embedding-free (survivor selection uses trope-link COUNTS, not similarity)."""
    print(f"DB target: …@{safe}")
    if not args.yes:
        print("\nREFUSING --merge-works-apply without --yes.")
        return 2
    if not is_prod_url(url):
        print(f"\nREFUSING --merge-works-apply: '{safe}' is not a live prod DB (sqlite/backup/localhost).")
        return 2
    if args.report is None:
        print(
            "\nREFUSING --merge-works-apply: --report is required (no default — "
            "name the reviewed --merge-works dry-run report explicitly)."
        )
        return 1

    with manager.get_session() as session:
        try:
            applied = dedup_backfill.apply_works_merge(session, args.report)
        except dedup_backfill.WorksMergeDriftError as exc:
            print(f"\nREFUSING --merge-works-apply: {exc}")
            print(f"New tokens present in the fresh plan but NOT in the reviewed report: +{len(exc.delta)}")
            for token in sorted(exc.delta)[:40]:
                print(f"  {token}")
            print(
                f"\nre-review {exc.fresh_report_path}, then re-run "
                f"--merge-works-apply --yes --report {exc.fresh_report_path}"
            )
            return 1
        except (OSError, ValueError) as exc:
            print(f"\nREFUSING --merge-works-apply: {exc}")
            return 1

        print("\napplied:")
        for key, count in applied.items():
            print(f"  {key:32} {count}")
        orphaned_pointer = applied.get("orphaned_authors_pointer", 0)
        if orphaned_pointer:
            print(f"\n{orphaned_pointer} author(s) may be orphaned — run --dedup-for-constraints dry-run to sweep.")
        return 0


def _run_promote_pair(manager: DatabaseManager, args, url: str, safe: str) -> int:
    """--promote-pair (H4): operator front door into the works-merge detection feed. WRITES one
    detected_duplicates row per pair (source='operator') — same --yes / is_prod_url guard shape
    as every other write mode (mirrors --repair-fallbacks-apply exactly), checked BEFORE any
    UUID parsing so a guard failure never depends on the pair arguments being well-formed.

    UUID parsing is pure string handling done HERE, before any session opens (a malformed token
    should never touch the DB). Everything DB-touching — the self-pair rejection, the
    both-ids-exist check, and the ON CONFLICT DO NOTHING insert itself — lives in
    dedup_backfill.promote_detected_duplicate_pair, the CLI helper function db_integration tests
    drive directly. All pairs are parsed before the session opens, and a validation failure on
    ANY pair (self-pair / unknown id) propagates OUT of the `with manager.get_session()` block
    uncaught, so DatabaseManager.get_session's own except-clause rolls back the whole
    transaction — a bad pair anywhere in a multi-pair invocation refuses ALL of it, never leaving
    earlier pairs promoted while a later one silently fails."""
    print(f"DB target: …@{safe}")
    if not args.yes:
        print("\nREFUSING --promote-pair without --yes.")
        return 2
    if not is_prod_url(url):
        print(f"\nREFUSING --promote-pair: '{safe}' is not a live prod DB (sqlite/backup/localhost).")
        return 2

    parsed_pairs: list[tuple[UUID, UUID]] = []
    for raw_a, raw_b in args.promote_pair:
        try:
            work_id_a = UUID(raw_a)
        except ValueError:
            print(f"\nREFUSING --promote-pair: {raw_a!r} is not a valid UUID.")
            return 1
        try:
            work_id_b = UUID(raw_b)
        except ValueError:
            print(f"\nREFUSING --promote-pair: {raw_b!r} is not a valid UUID.")
            return 1
        parsed_pairs.append((work_id_a, work_id_b))

    try:
        with manager.get_session() as session:
            for work_id_a, work_id_b in parsed_pairs:
                result = dedup_backfill.promote_detected_duplicate_pair(session, work_id_a, work_id_b)
                status = "already promoted" if result.already_existed else "promoted"
                print(f"{status}: {result.title_a} {result.work_id_a} + {result.title_b} {result.work_id_b}")
    except ValueError as exc:
        # dedup_backfill.promote_detected_duplicate_pair's self-pair ValueError and
        # UnknownWorkIdsError (a ValueError subclass) both land here — the session block above
        # has already been unwound (rolled back) by DatabaseManager.get_session's except-clause
        # by the time this runs.
        print(f"\nREFUSING --promote-pair: {exc}")
        return 1

    print("\nre-run --merge-works for a fresh gated report.")
    return 0


def _run_repair_fallbacks_apply(manager: DatabaseManager, args, url: str, safe: str) -> int:
    """--repair-fallbacks-apply: guards first (no session needed for those), THEN warms in its
    own short session, THEN opens the work session that re-plans fresh and applies — see
    _warm_fallback_repair_embeddings.

    DB-target visibility (final whole-branch review fix): same rationale as
    _run_repair_fallbacks — this mode also branches out of main() before the shared
    `DB target: ...` print, so print it here too, before the --yes/prod-url/--report guards,
    so the operator sees which DB was targeted even on an early refusal."""
    print(f"DB target: …@{safe}")
    if not args.yes:
        print("\nREFUSING --repair-fallbacks-apply without --yes.")
        return 2
    if not is_prod_url(url):
        print(f"\nREFUSING --repair-fallbacks-apply: '{safe}' is not a live prod DB (sqlite/backup/localhost).")
        return 2
    if args.report is None:
        print(
            "\nREFUSING --repair-fallbacks-apply: --report is required (no default — "
            "name the reviewed --repair-fallbacks dry-run report explicitly)."
        )
        return 1

    # #123: same warm-before-session discipline as the dry-run path — apply_fallback_repair
    # re-plans fresh internally, so its bogus_targets lookups must find warmed cache hits.
    _warm_fallback_repair_embeddings(manager)

    with manager.get_session() as session:
        try:
            applied = fallback_repair.apply_fallback_repair(session, args.report)
        except fallback_repair.FallbackRepairDriftError as exc:
            # Minor 5: mirrors clean_catalog.py's --dedup-for-constraints refusal — print the
            # offending delta TOKENS (not just per-class counts) so the operator can see exactly
            # which rows are new, plus the fresh report path already written for re-review.
            print(f"\nREFUSING --repair-fallbacks-apply: {exc}")
            print("New tokens present in the fresh plan but NOT in the reviewed report:")
            for class_name in fallback_repair.PLAN_TOKEN_CLASSES:
                new_tokens = sorted(exc.delta.get(class_name, set()))
                if new_tokens:
                    print(f"  [{class_name}] +{len(new_tokens)}: {new_tokens}")
            print(
                f"\nre-review {exc.fresh_report_path}, then re-run "
                f"--repair-fallbacks-apply --yes --report {exc.fresh_report_path}"
            )
            return 1
        except (OSError, ValueError) as exc:
            print(f"\nREFUSING --repair-fallbacks-apply: {exc}")
            return 1

        print("\napplied:")
        for key, count in applied.items():
            print(f"  {key:32} {count}")
        return 0


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
    ap.add_argument(
        "--repair-fallbacks",
        action="store_true",
        help=(
            "GH #70 (PR-D part 2): dry-run plan for the fallback-pollution repair — deletes "
            "NULL-justified work_tropes links that are a deterministic recompute of the OLD "
            "fallback writer's semantic redirect (see etl/fallback_repair.py's module "
            "docstring for the distinguisher), restores exact-name slug fallbacks on works left "
            "tropeless, clears falsely-backfilled deep_enriched_at stamps, and prunes orphaned "
            "tropes. Writes data/reports/fallback-repair-<UTC timestamp>.txt for review — this "
            "flag never applies; use --repair-fallbacks-apply for that."
        ),
    )
    ap.add_argument(
        "--merge-works",
        action="store_true",
        help=(
            "Spec 2026-07-14 PR-2 (hardened 2026-07-15 after a prod dry-run caught two "
            "amplifiers): DETECTION + COMPOSITION PLANNING for the works-merge tool — always a "
            "dry-run, this flag never applies. Prints the detection classes (strongest evidence "
            "first): works_same_isbn (now requires folded-title agreement within an ISBN group — "
            "shared ISBN alone is not applyable evidence, this catalog has ISBN pollution), "
            "works_same_isbn_title_mismatch (an ISBN-sharing pair whose titles DISAGREE — report "
            "only, e.g. a sequel carrying its predecessor's ISBN), works_same_identity, "
            "works_detected_duplicates (the #141/#143 feed), and works_fuzzy_report_only "
            "(NEVER applied by design — operator promotes pairs by hand). Report-only classes "
            "each cluster independently and can never grow an applyable cluster. Each cluster shows "
            "its deterministic survivor pick (most justified trope links -> newest "
            "deep_enriched_at -> most editions -> lowest UUID), plus the per-cluster merge "
            "composition (editions/suggestions/trope+style links/contributors/detected_"
            "duplicates/Work-row deletion) that --merge-works-apply would execute. Writes "
            "data/reports/works-merge-<UTC timestamp>.txt for review — the same report "
            "--merge-works-apply's drift gate cross-checks a fresh re-plan against."
        ),
    )
    ap.add_argument(
        "--merge-works-apply",
        action="store_true",
        help=(
            "Apply the works-merge composition (Spec 2026-07-14 PR-2 part 2, H2). A SEPARATE "
            "invocation from the reviewed --merge-works dry-run: re-plans (detection + "
            "composition) from scratch and REFUSES (exit 1) if the fresh plan contains any "
            "token the operator never reviewed (op-tagged, so even an operation flip on the "
            "same ids counts) — see etl/dedup_backfill.py's apply_works_merge. Requires --yes "
            "and --report (no default, same as --repair-fallbacks-apply — a distinct "
            "destructive operation that deletes Work rows carrying live user reading history; "
            "must name its reviewed report explicitly). works_same_isbn_title_mismatch and "
            "works_fuzzy_report_only clusters are structurally unreachable by this gate — they "
            "are never part of the applyable composition, by construction, not by a runtime check."
        ),
    )
    ap.add_argument(
        "--repair-fallbacks-apply",
        action="store_true",
        help=(
            "Apply the fallback-pollution repair (GH #70). A SEPARATE invocation from the "
            "reviewed --repair-fallbacks dry-run: re-plans from scratch and REFUSES (exit 1) if "
            "the fresh plan contains any token the operator never reviewed (op-tagged, so even "
            "an operation flip on the same ids counts) — see etl/fallback_repair.py's "
            "apply_fallback_repair. Requires --yes and --report (no default, unlike "
            "--dedup-for-constraints — this is a distinct destructive operation and must name "
            "its reviewed report explicitly)."
        ),
    )
    ap.add_argument(
        "--promote-pair",
        action="append",
        nargs=2,
        metavar=("WORK_ID_A", "WORK_ID_B"),
        default=None,
        help=(
            "H4: operator promotion of a hand-picked duplicate work pair into the works-merge "
            "detection feed (writes a source='operator' row into detected_duplicates — the same "
            "feed --merge-works's works_detected_duplicates class reads). Repeatable: pass "
            "--promote-pair twice for two pairs in one invocation. Each pair is validated (both "
            "ids must be well-formed UUIDs, distinct, and name existing works) before anything is "
            "written; a bad pair anywhere refuses the WHOLE invocation. Idempotent — re-running "
            "the same pair is a no-op (ON CONFLICT DO NOTHING), so it is safe to re-run. Requires "
            "--yes and a live prod DB (same is_prod_url guard as every other write mode). After "
            "promoting, re-run --merge-works for a fresh gated report — the pair only becomes "
            "applyable on the NEXT dry-run/apply cycle, never in this same invocation."
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
            "--dedup-for-constraints --apply: path to the reviewed dry-run report to "
            "cross-check the fresh plan against (THE USER GATE). Defaults to the newest "
            "data/reports/dedup-*.txt — the tool prints which one it used. "
            "--repair-fallbacks-apply: same idea, but REQUIRED (no default)."
        ),
    )
    args = ap.parse_args(argv)

    url = resolve_database_url()
    safe = url.split("@")[-1] if "@" in url else url  # never print credentials
    manager = DatabaseManager(url)

    # Fix 2 (#123, honestly): --repair-fallbacks / --repair-fallbacks-apply run OUTSIDE the
    # shared session block below — each warms its embeddings in its own short session (closed
    # before any embedding network call) and then opens its OWN work session for plan/apply, so
    # no embedding call is ever made while a work session is open. Handled here, before the
    # `with manager.get_session()` block every other mode shares, precisely so these two modes
    # never touch that shared session at all.
    if args.repair_fallbacks:
        return _run_repair_fallbacks(manager, safe)
    if args.repair_fallbacks_apply:
        return _run_repair_fallbacks_apply(manager, args, url, safe)
    if args.merge_works:
        return _run_merge_works(manager, safe)
    if args.merge_works_apply:
        return _run_merge_works_apply(manager, args, url, safe)
    if args.promote_pair:
        return _run_promote_pair(manager, args, url, safe)

    with manager.get_session() as session:
        print(f"DB target: …@{safe}")
        # Column-explicit counts, not session.query(Work).count() / session.query(Trope).count()
        # (entity loads): this probe runs for EVERY mode, including --dedup-for-constraints,
        # which by design runs BEFORE `alembic upgrade head` lands migration 48e3762d6c0c. An
        # entity load SELECTs every mapped column, including deep_enriched_at — a column that
        # migration hasn't added to prod yet — and dies with UndefinedColumn (caught live against
        # prod, GH #95). func.count(Work.id) never references it.
        works_count = session.query(func.count(Work.id)).scalar()
        tropes_count = session.query(func.count(Trope.id)).scalar()
        print(f"recency probe: works={works_count} tropes={tropes_count}")

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
            # GH #141: pending_merge works need the works-merge tool, not another paid deep
            # pass — split them out of the enqueue loop and print under their own heading.
            enrichable = [c for c in candidates if c.reason != "pending_merge"]
            pending_merge = [c for c in candidates if c.reason == "pending_merge"]
            print(f"\n{len(enrichable)} works would be requeued for deep enrichment.")
            for c in enrichable[:80]:
                # Adversarial-pass finding (#95 #97): show deep_enriched_at next to no_real_trope
                # entries so a REPEAT sweep lets the operator see "already re-attempted after X"
                # — see the runbook's step 6 repeat-cost warning (each re-enqueue costs up to 9
                # paid deep passes; a persistently-unknowable title should be fixed/removed, not
                # re-enqueued forever).
                stamp = f"  (deep_enriched_at={c.deep_enriched_at})" if c.deep_enriched_at else ""
                print(f"  [{c.reason:20}] {c.title[:60]}  ({c.work_id}){stamp}")
            print(f"\n{len(pending_merge)} works are pending a merge (see the works-merge tool) — NEVER enqueued:")
            for c in pending_merge[:80]:
                print(f"  [pending_merge        ] {c.title[:60]}  ({c.work_id})")
            early = _refuse(args, url, safe)
            if early is not None:
                return early
            from agentic_librarian.enrichment.tasks import enqueue_enrichment

            enqueued = sum(1 for c in enrichable if enqueue_enrichment(str(c.work_id)))
            print(f"\napplied: enqueued {enqueued}/{len(enrichable)} works (see logs for any that skipped).")
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
            "--requeue-unenriched, --dedup-for-constraints, --repair-fallbacks, "
            "--repair-fallbacks-apply, --merge-works, --merge-works-apply, or --promote-pair."
        )
        return 1


if __name__ == "__main__":
    sys.exit(main())
