"""The bulk-import tables exist in the migrated schema (Spec 2026-06-18)."""

import pytest
from sqlalchemy import create_engine, inspect

pytestmark = pytest.mark.db_integration


def test_import_tables_present(db_url):
    insp = inspect(create_engine(db_url))
    tables = set(insp.get_table_names())
    assert {"import_jobs", "import_rows"} <= tables
    row_cols = {c["name"] for c in insp.get_columns("import_rows")}
    assert {"status", "destination", "date_completed", "work_id"} <= row_cols
