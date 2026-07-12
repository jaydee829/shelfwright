"""PR-D migration: constraints, indexes, timestamptz, deep_enriched_at (#95 #97 #108 #109)."""

import pytest
from sqlalchemy import inspect, text

pytestmark = pytest.mark.db_integration


def test_unique_indexes_exist(db_url):
    from agentic_librarian.db.session import DatabaseManager

    insp = inspect(DatabaseManager(db_url).engine)
    author_uniques = {i["name"] for i in insp.get_indexes("authors") if i.get("unique")}
    assert "uq_authors_name_lower" in author_uniques
    edition_uniques = {i["name"] for i in insp.get_indexes("editions") if i.get("unique")}
    assert "uq_editions_work_format" in edition_uniques
    rh = {i["name"] for i in insp.get_indexes("reading_history") if i.get("unique")}
    assert "uq_reading_history_user_edition_date" in rh
    sugg = {i["name"] for i in insp.get_indexes("suggestions") if i.get("unique")}
    assert "uq_suggestions_active" in sugg


def test_fk_indexes_exist(db_url):
    from agentic_librarian.db.session import DatabaseManager

    insp = inspect(DatabaseManager(db_url).engine)
    for table, col in [
        ("editions", "work_id"),
        ("reading_history", "edition_id"),
        ("work_tropes", "trope_id"),
        ("work_contributors", "author_id"),
        ("suggestions", "work_id"),
        ("author_styles", "style_id"),
        ("work_styles", "style_id"),
        ("usage", "conversation_id"),
        ("narrator_styles", "style_id"),
        ("edition_narrators", "narrator_id"),
    ]:
        names = {i["name"] for i in insp.get_indexes(table)}
        assert f"ix_{table}_{col}" in names, f"missing ix_{table}_{col}"


def test_timestamps_are_timestamptz(db_url):
    from agentic_librarian.db.session import DatabaseManager

    m = DatabaseManager(db_url)
    with m.get_session() as s:
        rows = s.execute(
            text(
                "SELECT table_name, column_name FROM information_schema.columns "
                "WHERE data_type = 'timestamp without time zone' AND table_schema = 'public'"
            )
        ).all()
    assert rows == [], f"still naive: {rows}"


def test_works_deep_enriched_at(db_url):
    from agentic_librarian.db.session import DatabaseManager

    insp = inspect(DatabaseManager(db_url).engine)
    cols = {c["name"] for c in insp.get_columns("works")}
    assert "deep_enriched_at" in cols
