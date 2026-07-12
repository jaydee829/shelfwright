"""Shared helper for tests that must seed pre-constraint duplicate rows against the #95
unique indexes (migration 48e3762d6c0c) — e.g. legacy dedup logic (contributor_dedup,
dedup_backfill) that is only meaningful in the window BEFORE those constraints land on a
real deploy. Drop the named indexes for the duration of a test, then recreate them with the
exact DDL from the migration so the schema is unchanged for every other test in the run."""

from sqlalchemy import Connection, text

# Name -> exact CREATE-INDEX DDL from alembic/versions/48e3762d6c0c_phase6_3_schema_hardening.py
# (UNIQUE_INDEXES). Keep in sync with the migration if it ever changes.
UNIQUE_INDEX_DDL: dict[str, str] = {
    "uq_authors_name_lower": "CREATE UNIQUE INDEX uq_authors_name_lower ON authors (lower(name))",
    "uq_narrators_name_lower": "CREATE UNIQUE INDEX uq_narrators_name_lower ON narrators (lower(name))",
    "uq_editions_work_format": (
        "CREATE UNIQUE INDEX uq_editions_work_format ON editions (work_id, format) NULLS NOT DISTINCT"
    ),
    "uq_reading_history_user_edition_date": (
        "CREATE UNIQUE INDEX uq_reading_history_user_edition_date "
        "ON reading_history (user_id, edition_id, date_completed)"
    ),
    "uq_suggestions_active": (
        "CREATE UNIQUE INDEX uq_suggestions_active ON suggestions (user_id, work_id) WHERE status = 'Suggested'"
    ),
}


def drop_unique_indexes(conn: Connection, names: list[str]) -> None:
    """Drop the given #95 unique indexes (by name) so duplicate-seeding tests can insert
    case/exact duplicate rows the live constraint would otherwise reject."""
    for name in names:
        conn.execute(text(f"DROP INDEX IF EXISTS {name}"))


def recreate_unique_indexes(conn: Connection, names: list[str]) -> None:
    """Recreate the given #95 unique indexes with the exact DDL from migration 48e3762d6c0c."""
    for name in names:
        conn.execute(text(f"DROP INDEX IF EXISTS {name}"))
        conn.execute(text(UNIQUE_INDEX_DDL[name]))
