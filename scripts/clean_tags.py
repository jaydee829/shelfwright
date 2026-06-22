"""Operator backfill CLI for genre/mood cleaning (Spec 2026-06-22).

  python scripts/clean_tags.py --inventory      # read-only; distinct values + frequency
  python scripts/clean_tags.py --dry-run        # read-only; show changes, NO writes
  python scripts/clean_tags.py --apply --yes    # write cleaned values (idempotent)

Run against LIVE prod via the app container + Cloud SQL proxy (docs/runbooks/bulk-import-rollout.md §3).
Refuses --apply against a sqlite/backup/localhost DB."""

from __future__ import annotations

import argparse
import sys

from agentic_librarian.db.models import Work
from agentic_librarian.db.session import DatabaseManager, resolve_database_url
from agentic_librarian.etl import tag_backfill


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Clean Work.genres / Work.moods.")
    ap.add_argument("--inventory", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--yes", action="store_true", help="required confirmation for --apply")
    args = ap.parse_args(argv)

    url = resolve_database_url()
    safe = url.split("@")[-1] if "@" in url else url  # never print credentials
    manager = DatabaseManager(url)

    with manager.get_session() as session:
        print(f"DB target: …@{safe}")
        print(f"recency probe: works={session.query(Work).count()}  (confirm CURRENT prod, not a backup)")

        if args.inventory:
            genres, moods = tag_backfill.inventory(session)
            print("\n=== distinct GENRES (count | value) ===")
            for val, c in genres.most_common():
                print(f"{c:5d}  {val!r}")
            print("\n=== distinct MOODS (count | value) ===")
            for val, c in moods.most_common():
                print(f"{c:5d}  {val!r}")
            return 0

        changes = tag_backfill.plan_changes(session)
        print(f"\n{len(changes)} works would change.")
        for c in changes[:50]:
            if c.genres_before != c.genres_after:
                print(f"  [{c.title[:40]:40}] genres {c.genres_before} -> {c.genres_after}")
            if c.moods_before != c.moods_after:
                print(f"  [{c.title[:40]:40}] moods  {c.moods_before} -> {c.moods_after}")

        if args.dry_run or not args.apply:
            print("\n(dry-run: no writes)")
            return 0
        if not args.yes:
            print("\nREFUSING --apply without --yes.")
            return 2
        if not tag_backfill.is_prod_url(url):
            print(f"\nREFUSING --apply: '{safe}' is not a live prod DB (sqlite/backup/localhost).")
            return 2

        print(f"\napplied: {tag_backfill.apply_changes(session)} works updated.")
        return 0


if __name__ == "__main__":
    sys.exit(main())
