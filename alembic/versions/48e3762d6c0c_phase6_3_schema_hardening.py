"""phase 6.3 schema hardening — uniques, FK indexes, timestamptz, deep_enriched_at

Revision ID: 48e3762d6c0c
Revises: c4f81a2d9b6e
Create Date: 2026-07-12 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "48e3762d6c0c"
down_revision: str | None = "c4f81a2d9b6e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# GH #108: 13 naive DateTime columns promoted to timestamptz (UTC-assumed on convert).
TIMESTAMPTZ_COLUMNS: list[tuple[str, str]] = [
    ("suggestions", "suggested_at"),
    ("conversations", "created_at"),
    ("conversations", "updated_at"),
    ("messages", "created_at"),
    ("users", "created_at"),
    ("usage", "created_at"),
    ("user_credentials", "created_at"),
    ("user_credentials", "updated_at"),
    ("user_libraries", "created_at"),
    ("availability_cache", "fetched_at"),
    ("import_jobs", "created_at"),
    ("import_rows", "created_at"),
    ("import_rows", "updated_at"),
]

# GH #109: FK/join-column indexes missing on the hot query paths. NOTE (final-review Minor 5):
# editions.work_id deliberately does NOT get its own ix_editions_work_id here — the
# uq_editions_work_format unique index below is (work_id, format), and a leading column of a
# composite/multi-column index already serves lookups on that column alone (standard btree
# behavior), so a separate single-column index would be redundant.
FK_INDEXES: list[tuple[str, str]] = [
    ("reading_history", "edition_id"),
    ("work_tropes", "trope_id"),
    ("work_contributors", "author_id"),
    ("suggestions", "work_id"),
    ("author_styles", "style_id"),
    ("work_styles", "style_id"),
    ("usage", "conversation_id"),
    ("narrator_styles", "style_id"),
    ("edition_narrators", "narrator_id"),
]

# GH #95: dedup-backstop unique constraints/indexes. Functional/partial/NULLS-NOT-DISTINCT
# ones can't be expressed via op.create_index — raw DDL via op.execute (both directions).
UNIQUE_INDEXES: list[tuple[str, str, str]] = [
    ("uq_authors_name_lower", "authors", "CREATE UNIQUE INDEX uq_authors_name_lower ON authors (lower(name))"),
    (
        "uq_narrators_name_lower",
        "narrators",
        "CREATE UNIQUE INDEX uq_narrators_name_lower ON narrators (lower(name))",
    ),
    (
        "uq_editions_work_format",
        "editions",
        "CREATE UNIQUE INDEX uq_editions_work_format ON editions (work_id, format) NULLS NOT DISTINCT",
    ),
    (
        "uq_reading_history_user_edition_date",
        "reading_history",
        "CREATE UNIQUE INDEX uq_reading_history_user_edition_date "
        "ON reading_history (user_id, edition_id, date_completed)",
    ),
    (
        "uq_suggestions_active",
        "suggestions",
        "CREATE UNIQUE INDEX uq_suggestions_active ON suggestions (user_id, work_id) WHERE status = 'Suggested'",
    ),
]


def upgrade() -> None:
    for table, column in TIMESTAMPTZ_COLUMNS:
        op.execute(f"ALTER TABLE {table} ALTER COLUMN {column} TYPE timestamptz USING {column} AT TIME ZONE 'UTC'")

    op.add_column("works", sa.Column("deep_enriched_at", sa.DateTime(timezone=True), nullable=True))

    # Backfill: works that already carry ANY trope link were built by the full ETL/deep
    # pipeline — stamp them so the first --requeue-unenriched sweep is signal, not the
    # whole catalog. Works with zero trope links stay NULL (genuinely never deep-enriched);
    # fallback-only works are stamped here but correctly surface under the sweep's
    # no_real_trope reason (the predicate runs in app code, not here).
    op.execute("UPDATE works SET deep_enriched_at = now() WHERE id IN (SELECT DISTINCT work_id FROM work_tropes)")

    for table, column in FK_INDEXES:
        op.create_index(f"ix_{table}_{column}", table, [column])

    for _name, _table, create_sql in UNIQUE_INDEXES:
        op.execute(create_sql)


def downgrade() -> None:
    for name, _table, _create_sql in reversed(UNIQUE_INDEXES):
        op.execute(f"DROP INDEX {name}")

    for table, column in reversed(FK_INDEXES):
        op.drop_index(f"ix_{table}_{column}", table_name=table)

    op.drop_column("works", "deep_enriched_at")

    for table, column in reversed(TIMESTAMPTZ_COLUMNS):
        op.execute(
            f"ALTER TABLE {table} ALTER COLUMN {column} TYPE timestamp without time zone "
            f"USING {column} AT TIME ZONE 'UTC'"
        )
