import pytest
from sqlalchemy import inspect

from agentic_librarian.db.session import DatabaseManager

pytestmark = pytest.mark.db_integration


def test_chat_tables_and_usage_fk_exist(db_url):
    engine = DatabaseManager(db_url).engine
    inspector = inspect(engine)
    names = set(inspector.get_table_names())
    assert {"conversations", "messages"} <= names
    fks = inspector.get_foreign_keys("usage")
    assert any(fk["referred_table"] == "conversations" for fk in fks)
