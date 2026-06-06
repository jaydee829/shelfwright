"""Verify the Cloud SQL restore matches the known 2026-06-05 production build.

Usage (runbook §7):  DATABASE_URL=postgresql://... python infra/verify_restore.py
Exits non-zero on any failed check.
"""

import os
import sys

from sqlalchemy import create_engine, text

EXPECTED_ROW_COUNTS = {
    "works": 326,
    "editions": 335,
    "reading_history": 331,
    "authors": 230,
}

failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    status = "OK " if ok else "FAIL"
    print(f"[{status}] {label}" + (f" — {detail}" if detail else ""))
    if not ok:
        failures.append(label)


def main() -> int:
    url = os.environ.get("DATABASE_URL")
    if not url:
        print("Set DATABASE_URL (see runbook §7 for the Cloud SQL Auth Proxy invocation).")
        return 2

    engine = create_engine(url)
    with engine.connect() as conn:
        # 1. Known row counts from the completed production build (2026-06-05).
        for table, expected in EXPECTED_ROW_COUNTS.items():
            actual = conn.execute(text(f"SELECT count(*) FROM {table}")).scalar()  # noqa: S608 - fixed table names
            check(f"{table} row count", actual == expected, f"expected {expected}, got {actual}")

        # 2. pgvector extension present.
        ext = conn.execute(text("SELECT count(*) FROM pg_extension WHERE extname = 'vector'")).scalar()
        check("pgvector extension installed", ext == 1)

        # 3. Embeddings fully populated (the build's quality gate embedded every trope/style).
        for table in ("tropes", "styles"):
            total = conn.execute(text(f"SELECT count(*) FROM {table}")).scalar()  # noqa: S608
            nulls = conn.execute(text(f"SELECT count(*) FROM {table} WHERE embedding IS NULL")).scalar()  # noqa: S608
            check(f"{table} embeddings populated", total > 0 and nulls == 0, f"{total} rows, {nulls} NULL embeddings")

        # 4. Similarity search actually works (operator + data, not just bytes).
        rows = conn.execute(
            text(
                "SELECT t2.name, t1.embedding <=> t2.embedding AS dist "
                "FROM tropes t1, tropes t2 WHERE t1.id != t2.id "
                "AND t1.id = (SELECT id FROM tropes WHERE embedding IS NOT NULL LIMIT 1) "
                "ORDER BY dist ASC LIMIT 3"
            )
        ).fetchall()
        dists = [r.dist for r in rows]
        check(
            "similarity query returns ordered results",
            len(dists) == 3 and dists == sorted(dists) and all(0 <= d <= 2 for d in dists),
            f"top-3 distances: {dists}",
        )

    if failures:
        print(f"\n{len(failures)} check(s) FAILED: {failures}")
        return 1
    print("\nAll restore checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
