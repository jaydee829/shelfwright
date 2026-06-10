import pytest
from sqlalchemy import text

from agentic_librarian.db.session import DatabaseManager


@pytest.mark.db_integration
def test_database_manager_connection(db_url):
    """Verify DatabaseManager can connect to the database and create a session."""
    db_manager = DatabaseManager(db_url)
    with db_manager.get_session() as session:
        # Just check if we can execute a simple query
        result = session.execute(text("SELECT 1")).scalar()
        assert result == 1
